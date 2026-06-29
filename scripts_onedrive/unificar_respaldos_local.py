import os
import re
import sys
import pandas as pd
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from pase_utils import parse_pase_fecha, read_pase_csv_lossless

load_dotenv()

def _normalize_eco(val):
    s = str(val).strip().upper().replace('.', '')
    # Check if it has LZC
    has_lzc = 'LZC' in s
    s_clean = s.replace('LZC', '').replace(' ', '')
    
    m = re.match(r'^(AU|CA)-?(\d{1,3})(?!\d)', s_clean)
    if m:
        eco = f"{m.group(1)}-{m.group(2).zfill(3)}"
        if has_lzc:
            return f"{eco} LZC"
        return eco
    return str(val).strip().upper()

def _limpiar_edenred(df):
    df = df.copy()
    df.columns = df.columns.str.strip()
    limpio = pd.DataFrame()
    
    if 'Vehículo' in df.columns:
        limpio['ECO'] = df['Vehículo'].apply(_normalize_eco)
    else:
        limpio['ECO'] = df.iloc[:, 7].apply(_normalize_eco)
        
    limpio['Fecha'] = pd.to_datetime(df['Fecha Transacción'], dayfirst=True, errors='coerce')
    limpio['Concepto'] = "COMBUSTIBLE"
    limpio['Tipo'] = df.get('Mercancía')
    limpio['Cantidad'] = pd.to_numeric(df.get('Cantidad Mercancía'), errors='coerce')
    limpio['Importe'] = pd.to_numeric(df.get('Importe Transacción'), errors='coerce')
    limpio['Sistema'] = "Edenred"
    if 'Archivo_Origen' in df.columns:
        limpio['Archivo_Origen'] = df['Archivo_Origen']
        
    limpio = limpio.dropna(subset=['Importe', 'Fecha', 'ECO'])
    limpio = limpio[limpio['ECO'].str.match(r'^(AU|CA)-\d{3}(?:\s*LZC)?$', na=False)]
    return limpio

import json

REGISTRY_PATH = os.path.join(PROJECT_ROOT, "processed_files_registry.json")

def load_registry():
    if os.path.exists(REGISTRY_PATH):
        try:
            with open(REGISTRY_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Error al leer el registro de archivos procesados: {e}")
    return {}

def save_registry(registry):
    try:
        with open(REGISTRY_PATH, 'w', encoding='utf-8') as f:
            json.dump(registry, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Error al guardar el registro de archivos procesados: {e}")

def is_file_processed(filepath, registry):
    if filepath not in registry:
        return False
    try:
        stat = os.stat(filepath)
        recorded = registry[filepath]
        return recorded.get("size") == stat.st_size and recorded.get("mtime") == stat.st_mtime
    except Exception:
        return False

def mark_file_processed(filepath, registry):
    try:
        stat = os.stat(filepath)
        registry[filepath] = {
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "processed_at": pd.Timestamp.now().isoformat()
        }
    except Exception as e:
        print(f"⚠️ Error al registrar {filepath}: {e}")

def unificar_respaldos_desde_onedrive():
    respaldos_dir = os.getenv('ONEDRIVE_RESPALDOS_DIR')
    if not respaldos_dir or not os.path.exists(respaldos_dir):
        print(f"Error: La ruta local de respaldos en OneDrive no existe: {respaldos_dir}")
        return
        
    print(f"=== UNIFICANDO RESPALDOS LOCALES DESDE ONEDRIVE ({respaldos_dir}) ===")
    
    registry = load_registry()
    registry_updated = False
    
    # 1. SUPRAMAX
    supramax_dir = os.path.join(respaldos_dir, 'Supramax')
    if os.path.exists(supramax_dir):
        all_supra_files = []
        for root, dirs, files in os.walk(supramax_dir):
            for file in files:
                if file.endswith('.xls') or file.endswith('.xlsx'):
                    full_path = os.path.join(root, file)
                    if not is_file_processed(full_path, registry):
                        all_supra_files.append(full_path)
                    
        if all_supra_files:
            print(f"Procesando {len(all_supra_files)} nuevos/modificados archivos de Supramax...")
            lista_supra = []
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                for local_path in all_supra_files:
                    file = os.path.basename(local_path)
                    try:
                        raw = pd.read_excel(local_path, engine='xlrd', header=None)
                        header_row = next(i for i, row in raw.iterrows() if row.astype(str).str.strip().eq('PLACAS').any())
                        df = raw.iloc[header_row+1:].copy()
                        df.columns = raw.iloc[header_row].astype(str).str.strip()
                        df['Archivo_Origen'] = file
                        lista_supra.append(df)
                        mark_file_processed(local_path, registry)
                        registry_updated = True
                    except Exception as e:
                        try:
                            raw = pd.read_excel(local_path, engine='openpyxl', header=None)
                            header_row = next(i for i, row in raw.iterrows() if row.astype(str).str.strip().eq('PLACAS').any())
                            df = raw.iloc[header_row+1:].copy()
                            df.columns = raw.iloc[header_row].astype(str).str.strip()
                            df['Archivo_Origen'] = file
                            lista_supra.append(df)
                            mark_file_processed(local_path, registry)
                            registry_updated = True
                        except Exception as e2:
                            print(f"  Error en {file}: {e} | {e2}")
            if lista_supra:
                existing_df = pd.DataFrame()
                if os.path.exists("CONSOLIDADO_CRUDO_SUPRAMAX.csv"):
                    try:
                        existing_df = pd.read_csv("CONSOLIDADO_CRUDO_SUPRAMAX.csv")
                    except Exception:
                        pass
                new_df = pd.concat(lista_supra, ignore_index=True)
                combined = pd.concat([existing_df, new_df], ignore_index=True).drop_duplicates().copy()
                combined.to_csv("CONSOLIDADO_CRUDO_SUPRAMAX.csv", index=False, encoding='utf-8-sig')
                print(f"Actualizado: CONSOLIDADO_CRUDO_SUPRAMAX.csv (Total: {len(combined)} registros)")
        else:
            print("Supramax: Sin nuevos archivos por procesar.")
    else:
        print("No existe la carpeta Supramax en la ruta de respaldos.")

    # 2. PASE
    pase_dir = os.path.join(respaldos_dir, 'Pase')
    if os.path.exists(pase_dir):
        all_csv_files = []
        for root, dirs, files in os.walk(pase_dir):
            for file in files:
                if file.endswith('.csv'):
                    full_path = os.path.join(root, file)
                    if not is_file_processed(full_path, registry):
                        all_csv_files.append(full_path)
        
        if all_csv_files:
            print(f"Procesando {len(all_csv_files)} nuevos/modificados archivos de Pase...")
            lista_pase = []
            for local_path in all_csv_files:
                file = os.path.basename(local_path)
                try:
                    df = read_pase_csv_lossless(local_path)
                    cols = {c: c.lower().replace(' ', '').replace('ó', 'o').replace('.', '') for c in df.columns}
                    col_eco = next((c for c, n in cols.items() if 'noeconomico' in n or 'economico' in n), None)
                    col_fecha = next((c for c, n in cols.items() if 'fechadecruce' in n or n == 'fecha'), None)
                    col_importe = next((c for c, n in cols.items() if 'importeal100' in n or n == 'importe'), None)
                    col_tarjeta = next((c for c, n in cols.items() if 'tarjetaidmx' in n), None)
                    if not col_tarjeta:
                        col_tarjeta = next((c for c, n in cols.items() if n == 'tarjeta' or 'tarjeta' in n or n == 'tag'), None)
                    
                    df_std = pd.DataFrame()
                    df_std['ECO'] = df[col_eco].astype(str).str.strip() if col_eco else None
                    df_std['Fecha'] = parse_pase_fecha(df[col_fecha]) if col_fecha else None
                    if col_importe:
                        df_std['Importe'] = pd.to_numeric(df[col_importe].astype(str).str.replace(r'[$,]','',regex=True), errors='coerce').abs()
                    if col_tarjeta:
                        df_std['Tarjeta IDMX'] = df[col_tarjeta].astype(str).str.strip().str.rstrip('.')
                    df_std['Archivo_Origen'] = file
                    df_std = df_std.dropna(subset=['Importe', 'Fecha', 'ECO'])
                    df_std['ECO'] = df_std['ECO'].apply(_normalize_eco)
                    lista_pase.append(df_std)
                    mark_file_processed(local_path, registry)
                    registry_updated = True
                except Exception as e:
                    print(f"  Error en {file}: {e}")
            if lista_pase:
                existing_df = pd.DataFrame()
                if os.path.exists("CONSOLIDADO_CRUDO_PASE.csv"):
                    try:
                        existing_df = pd.read_csv("CONSOLIDADO_CRUDO_PASE.csv")
                    except Exception:
                        pass
                new_df = pd.concat(lista_pase, ignore_index=True)
                combined = pd.concat([existing_df, new_df], ignore_index=True).drop_duplicates().copy()
                combined.to_csv("CONSOLIDADO_CRUDO_PASE.csv", index=False, encoding='utf-8-sig')
                print(f"Actualizado: CONSOLIDADO_CRUDO_PASE.csv (Total: {len(combined)} registros)")
        else:
            print("Pase: Sin nuevos archivos por procesar.")
    else:
        print("No existe la carpeta Pase en la ruta de respaldos.")

    # 3. EDENRED
    edenred_dir = os.path.join(respaldos_dir, 'Edenred')
    if os.path.exists(edenred_dir):
        all_edenred_files = []
        for root, dirs, files in os.walk(edenred_dir):
            for file in files:
                if file.endswith('.csv') or file.endswith('.xlsx'):
                    full_path = os.path.join(root, file)
                    if not is_file_processed(full_path, registry):
                        all_edenred_files.append(full_path)
                    
        if all_edenred_files:
            print(f"Procesando {len(all_edenred_files)} nuevos/modificados archivos de Edenred...")
            lista_eden = []
            lista_eden_limpio = []
            for local_path in all_edenred_files:
                file = os.path.basename(local_path)
                try:
                    if file.endswith('.csv'):
                        df = pd.read_csv(local_path, encoding='latin1')
                    else:
                        df = pd.read_excel(local_path, header=5)
                    df['Archivo_Origen'] = file
                    lista_eden.append(df)
                    lista_eden_limpio.append(_limpiar_edenred(df))
                    mark_file_processed(local_path, registry)
                    registry_updated = True
                except Exception as e:
                    print(f"  Error en {file}: {e}")
            if lista_eden:
                # Crudo
                existing_crudo = pd.DataFrame()
                if os.path.exists("CONSOLIDADO_CRUDO_EDENRED.csv"):
                    try:
                        existing_crudo = pd.read_csv("CONSOLIDADO_CRUDO_EDENRED.csv")
                    except Exception:
                        pass
                new_crudo = pd.concat(lista_eden, ignore_index=True)
                combined_crudo = pd.concat([existing_crudo, new_crudo], ignore_index=True).drop_duplicates().copy()
                combined_crudo.to_csv("CONSOLIDADO_CRUDO_EDENRED.csv", index=False, encoding='utf-8-sig')
                
                # Limpio
                existing_limpio = pd.DataFrame()
                if os.path.exists("CONSOLIDADO_LIMPIO_EDENRED.csv"):
                    try:
                        existing_limpio = pd.read_csv("CONSOLIDADO_LIMPIO_EDENRED.csv")
                    except Exception:
                        pass
                new_limpio = pd.concat(lista_eden_limpio, ignore_index=True)
                combined_limpio = pd.concat([existing_limpio, new_limpio], ignore_index=True).drop_duplicates().copy()
                combined_limpio.to_csv("CONSOLIDADO_LIMPIO_EDENRED.csv", index=False, encoding='utf-8-sig')
                print(f"Actualizado: Edenred consolidado (Crudo: {len(combined_crudo)}, Limpio: {len(combined_limpio)})")
        else:
            print("Edenred: Sin nuevos archivos por procesar.")
    else:
        print("No existe la carpeta Edenred en la ruta de respaldos.")
        
    if registry_updated:
        save_registry(registry)
        
    print("=== PROCESO FINALIZADO ===")

if __name__ == "__main__":
    unificar_respaldos_desde_onedrive()
