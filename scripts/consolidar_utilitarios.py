import os
import re
import sys
import pandas as pd
from dotenv import load_dotenv

# Añadir el directorio raíz al path para importaciones
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv()

def _normalize_eco(val):
    s = str(val).strip().upper().replace(' ', '').replace('.', '')
    m = re.match(r'^(AU|CA)-?(\d{1,3})(?!\d)', s)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(3)}"
    return s

def _clean_eco_key(val):
    return re.sub(r'[^A-Z0-9]', '', str(val).upper())

def consolidar_todo():
    print("=== INICIANDO PROCESO DE CONSOLIDACIÓN LOCAL ===")
    
    # 1. Obtener rutas desde el archivo .env
    maestro_path = os.getenv('EXCEL_MAESTRO_PATH')
    mantenimiento_path = os.getenv('EXCEL_MANTENIMIENTO_PATH')
    
    # Validar que los archivos existan
    if not maestro_path or not os.path.exists(maestro_path):
        print(f"❌ Error: No se encontró el archivo de Tabla Maestra en la ruta: {maestro_path}")
        return
        
    if not mantenimiento_path or not os.path.exists(mantenimiento_path):
        print(f"⚠️ Advertencia: No se encontró el archivo de Mantenimientos en la ruta: {mantenimiento_path}. Se procederá sin mantenimientos.")
        df_mantenimientos_raw = pd.DataFrame()
    else:
        try:
            # Leer la pestaña BASE del Excel de Mantenimientos
            df_mantenimientos_raw = pd.read_excel(mantenimiento_path, sheet_name='BASE')
            print(f"✅ Archivo de Mantenimientos (pestaña BASE) cargado con éxito ({len(df_mantenimientos_raw)} filas).")
        except PermissionError:
            print(f"❌ Error de permisos: El archivo de Mantenimientos '{mantenimiento_path}' está siendo usado por otro programa (probablemente está abierto en Excel). Por favor, ciérralo e intenta de nuevo.")
            df_mantenimientos_raw = pd.DataFrame()
        except Exception as e:
            print(f"❌ Error al leer la pestaña BASE del archivo de Mantenimientos: {e}")
            df_mantenimientos_raw = pd.DataFrame()

    try:
        # Cargar Tabla Maestra (hoja Datos_asignación, Datos_asignacion o la primera por defecto)
        xls = pd.ExcelFile(maestro_path)
        sheet_to_use = None
        for name in ['Datos_asignación', 'Datos_asignacion', 'Datos asignación', 'Datos asignacion', 'Datos_Asignación', 'Datos_Asignacion']:
            if name in xls.sheet_names:
                sheet_to_use = name
                break
        if not sheet_to_use:
            sheet_to_use = xls.sheet_names[0]
            print(f"⚠️ Hoja maestra 'Datos_asignación' no encontrada. Usando la primera hoja: {sheet_to_use}")
            
        df_maestra = pd.read_excel(xls, sheet_name=sheet_to_use)
        print(f"✅ Archivo de Tabla Maestra (hoja {sheet_to_use}) cargado con éxito ({len(df_maestra)} filas).")
    except PermissionError:
        print(f"❌ Error de permisos: El archivo de Tabla Maestra '{maestro_path}' está siendo usado por otro programa (probablemente está abierto en Excel). Por favor, ciérralo e intenta de nuevo.")
        return
    except Exception as e:
        print(f"❌ Error crítico al leer el archivo de Tabla Maestra: {e}")
        return

    # 2. Cargar Consumos Consolidados (Pase, Supramax, Edenred)
    lista_consumos = []
    
    for sistema, archivo in [('Pase', 'CONSOLIDADO_CRUDO_PASE.csv'), 
                             ('Supramax', 'CONSOLIDADO_CRUDO_SUPRAMAX.csv'), 
                             ('Edenred', 'CONSOLIDADO_LIMPIO_EDENRED.csv')]:
        if os.path.exists(archivo):
            try:
                df_c = pd.read_csv(archivo)
                df_c['Sistema'] = sistema
                
                # Normalizar nombres de columnas a minúsculas para búsqueda flexible
                cols_lower = {c.lower(): c for c in df_c.columns}
                
                # Detectar columna ECO/PLACAS
                col_eco = None
                for kw in ['eco', 'placas', 'placa']:
                    if kw in cols_lower:
                        col_eco = cols_lower[kw]
                        break
                
                # Detectar columna Fecha
                col_fecha = None
                for kw in ['fecha', 'datetime', 'date']:
                    if kw in cols_lower:
                        col_fecha = cols_lower[kw]
                        break
                
                # Detectar columna Importe
                col_importe = None
                for kw in ['importe', 'total', 'monto', 'subtotal']:
                    if kw in cols_lower:
                        col_importe = cols_lower[kw]
                        break
                
                if not col_eco or not col_fecha or not col_importe:
                    print(f"⚠️ Columnas requeridas no detectadas en {archivo}. Se necesitan equivalentes a ECO, Fecha e Importe.")
                    continue
                
                # Estandarizar columnas y tipos
                df_c[col_fecha] = pd.to_datetime(df_c[col_fecha], errors='coerce')
                
                # Mapeo de columnas específicas
                if 'Tarjeta IDMX' in df_c.columns:
                    df_c['Concepto'] = 'PEAJE'
                    df_c['Tipo'] = None
                    df_c['Cantidad'] = None
                elif sistema == 'Supramax':
                    if 'Concepto' not in df_c.columns:
                        df_c['Concepto'] = 'COMBUSTIBLE'
                
                df_std = pd.DataFrame()
                df_std['ECO'] = df_c[col_eco].apply(_normalize_eco)
                df_std['Fecha'] = df_c[col_fecha]
                df_std['Concepto'] = df_c.get('Concepto', 'COMBUSTIBLE')
                df_std['Tipo'] = df_c.get('Tipo', None)
                df_std['Importe'] = pd.to_numeric(df_c[col_importe], errors='coerce')
                df_std['Sistema'] = sistema
                df_std['Cantidad'] = pd.to_numeric(df_c.get('Cantidad'), errors='coerce')
                
                df_std = df_std.dropna(subset=['Importe', 'Fecha', 'ECO'])
                lista_consumos.append(df_std)
                print(f"✅ Cargados {len(df_std)} registros de consumos desde {archivo} ({sistema}).")
            except Exception as e:
                print(f"⚠️ Error al leer {archivo}: {e}")

    # 3. Formatear y alinear Mantenimientos
    df_mantenimientos = pd.DataFrame()
    if not df_mantenimientos_raw.empty:
        try:
            # Buscar columnas necesarias
            col_eco = next((c for c in df_mantenimientos_raw.columns if 'eco' in c.lower()), None)
            col_fecha = next((c for c in df_mantenimientos_raw.columns if 'fecha' in c.lower()), None)
            col_importe = next((c for c in df_mantenimientos_raw.columns if any(kw in c.lower() for kw in ['importe', 'precioneto', 'costo'])), None)
            
            if col_eco and col_fecha and col_importe:
                df_mantenimientos['ECO'] = df_mantenimientos_raw[col_eco].apply(_normalize_eco)
                df_mantenimientos['Fecha'] = pd.to_datetime(df_mantenimientos_raw[col_fecha], errors='coerce')
                df_mantenimientos['Concepto'] = 'MANTENIMIENTO'
                df_mantenimientos['Tipo'] = None
                df_mantenimientos['Importe'] = pd.to_numeric(df_mantenimientos_raw[col_importe], errors='coerce')
                df_mantenimientos['Sistema'] = 'Excel Mantenimientos'
                df_mantenimientos['Cantidad'] = None
                df_mantenimientos = df_mantenimientos.dropna(subset=['Importe', 'Fecha', 'ECO'])
                print(f"✅ Procesados {len(df_mantenimientos)} registros de Mantenimiento local.")
            else:
                print("⚠️ No se pudieron identificar las columnas requeridas (ECO, Fecha, Importe/PrecioNeto) en Mantenimientos.")
        except Exception as e:
            print(f"⚠️ Error al alinear la estructura de Mantenimientos: {e}")

    # 4. UNION ALL (Concatenación)
    todas_fuentes = lista_consumos + ([df_mantenimientos] if not df_mantenimientos.empty else [])
    if not todas_fuentes:
        print("❌ Error: No se encontraron datos de consumos ni de mantenimientos para unificar.")
        return
        
    df_consumos_total = pd.concat(todas_fuentes, ignore_index=True)
    
    # 5. Cruce con Tabla Maestra (FULL OUTER JOIN por ECO normalizado)
    df_maestra['ECO_key'] = df_maestra['ECO'].apply(_clean_eco_key)
    df_consumos_total['ECO_key'] = df_consumos_total['ECO'].apply(_clean_eco_key)
    
    # Hacer el merge
    df_merge = pd.merge(df_maestra, df_consumos_total, on='ECO_key', how='outer', suffixes=('_maestra', '_consumo'))
    
    # Resolver columna ECO unificada (COALESCE en SQL)
    df_merge['ECO'] = df_merge['ECO_maestra'].fillna(df_merge['ECO_consumo'])
    df_merge = df_merge.drop(columns=['ECO_maestra', 'ECO_consumo', 'ECO_key'])
    
    # Rellenar valores nulos para columnas de importe y concepto
    df_merge['Importe'] = df_merge['Importe'].fillna(0)
    df_merge['Concepto'] = df_merge['Concepto'].fillna('SIN ACTIVIDAD')
    df_merge['Sistema'] = df_merge['Sistema'].fillna('N/A')
    df_merge['Cantidad'] = df_merge['Cantidad'].fillna(0)
    
    # 6. Crear Columnas de Dashboard e Indicadores
    today = pd.Timestamp.now()
    df_merge['Fecha'] = pd.to_datetime(df_merge['Fecha'], errors='coerce')
    df_merge['Anio'] = df_merge['Fecha'].dt.year
    df_merge['Mes'] = df_merge['Fecha'].dt.month
    
    # es_ytd: Año actual y mes menor/igual al actual, o años anteriores
    df_merge['es_ytd'] = ((df_merge['Anio'] == today.year) & (df_merge['Mes'] <= today.month)) | (df_merge['Anio'] < today.year)
    
    # incluir_en_kpi
    df_merge['incluir_en_kpi'] = df_merge['Mes'] <= today.month
    
    # Nombre_Mes
    meses_nombres = {
        1: '01 - Enero', 2: '02 - Febrero', 3: '03 - Marzo', 4: '04 - Abril',
        5: '05 - Mayo', 6: '06 - Junio', 7: '07 - Julio', 8: '08 - Agosto',
        9: '09 - Septiembre', 10: '10 - Octubre', 11: '11 - Noviembre', 12: '12 - Diciembre'
    }
    df_merge['Nombre_Mes'] = df_merge['Mes'].map(meses_nombres)
    
    # 7. Exportar a Excel Final
    output_path = os.getenv('EXCEL_OUTPUT_PATH')
    if not output_path:
        output_dir = os.path.join(PROJECT_ROOT, "Reportes_Ejecutable")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "Reporte_Dashboard_Final.xlsx")
    else:
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
    
    try:
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            df_merge.to_excel(writer, sheet_name='Datos', index=False)
            
            # Autoajustar anchos de columnas
            worksheet = writer.sheets['Datos']
            for col in worksheet.columns:
                max_len = max(len(str(cell.value or '')) for cell in col)
                col_letter = col[0].column_letter
                worksheet.column_dimensions[col_letter].width = max(max_len + 3, 10)
                
        print(f"\n✅ ¡Proceso global completado exitosamente!")
        print(f"📊 Reporte Dashboard generado en: {output_path}")
    except PermissionError:
        print(f"❌ Error de permisos: El archivo de salida '{output_path}' está siendo usado por otro programa (probablemente está abierto en Excel). Por favor, ciérralo e intenta de nuevo.")
    except Exception as e:
        print(f"❌ Error crítico al exportar el archivo de reporte final: {e}")

if __name__ == "__main__":
    consolidar_todo()
