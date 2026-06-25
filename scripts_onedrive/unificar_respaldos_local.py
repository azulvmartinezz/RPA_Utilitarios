import os
import re
import sys
import pandas as pd
from dotenv import load_dotenv

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

def _parse_pase_fecha(series):
    texto = series.astype(str).str.strip()
    serie = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    
    mask_ymd = texto.str.match(r"^\d{4}/\d{2}/\d{2}$", na=False)
    if mask_ymd.any():
        serie.loc[mask_ymd] = pd.to_datetime(texto.loc[mask_ymd], format="%Y/%m/%d", errors="coerce")
        
    restantes = serie.isna()
    if restantes.any():
        serie.loc[restantes] = pd.to_datetime(texto.loc[restantes], dayfirst=True, errors="coerce")
        
    restantes = serie.isna()
    if restantes.any():
        serie.loc[restantes] = pd.to_datetime(texto.loc[restantes], errors="coerce")
        
    return serie

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
    limpio = limpio[limpio['ECO'].str.match(r'^(AU|CA)-\d{3}$', na=False)]
    return limpio

def unificar_respaldos_desde_onedrive():
    respaldos_dir = os.getenv('ONEDRIVE_RESPALDOS_DIR')
    if not respaldos_dir or not os.path.exists(respaldos_dir):
        print(f"❌ Error: La ruta local de respaldos en OneDrive no existe: {respaldos_dir}")
        return
        
    print(f"=== UNIFICANDO RESPALDOS LOCALES DESDE ONEDRIVE ({respaldos_dir}) ===")
    
    # 1. SUPRAMAX
    supramax_dir = os.path.join(respaldos_dir, 'Supramax')
    if os.path.exists(supramax_dir):
        files = [f for f in os.listdir(supramax_dir) if f.endswith('.xls')]
        print(f"📂 Encontrados {len(files)} archivos de Supramax en OneDrive...")
        lista_supra = []
        for file in files:
            local_path = os.path.join(supramax_dir, file)
            try:
                raw = pd.read_excel(local_path, engine='xlrd', header=None)
                header_row = next(i for i, row in raw.iterrows() if row.astype(str).str.strip().eq('PLACAS').any())
                df = pd.read_excel(local_path, engine='xlrd', header=header_row)
                df['Archivo_Origen'] = file
                lista_supra.append(df)
            except Exception as e:
                print(f"  ⚠️ Error en {file}: {e}")
        if lista_supra:
            pd.concat(lista_supra, ignore_index=True).to_csv("CONSOLIDADO_CRUDO_SUPRAMAX.csv", index=False, encoding='utf-8-sig')
            print(f"✅ Creado: CONSOLIDADO_CRUDO_SUPRAMAX.csv")
    else:
        print("⚠️ No existe la carpeta Supramax en la ruta de respaldos.")

    # 2. PASE
    pase_dir = os.path.join(respaldos_dir, 'Pase')
    if os.path.exists(pase_dir):
        # Escanear recursivamente en carpetas de Pase si están ordenadas por año/mes
        all_csv_files = []
        for root, dirs, files in os.walk(pase_dir):
            for file in files:
                if file.endswith('.csv'):
                    all_csv_files.append(os.path.join(root, file))
        
        print(f"📂 Encontrados {len(all_csv_files)} archivos de Pase en OneDrive...")
        lista_pase = []
        for local_path in all_csv_files:
            file = os.path.basename(local_path)
            try:
                try:
                    df = pd.read_csv(local_path, encoding='latin1', index_col=False)
                except:
                    df = pd.read_csv(local_path, index_col=False)
                
                df.columns = df.columns.str.strip()
                cols = {c: c.lower().replace(' ', '').replace('ó', 'o').replace('.', '') for c in df.columns}
                col_eco = next((c for c, n in cols.items() if 'noeconomico' in n or 'economico' in n), None)
                col_fecha = next((c for c, n in cols.items() if 'fechadecruce' in n or n == 'fecha'), None)
                col_importe = next((c for c, n in cols.items() if 'importeal100' in n or n == 'importe'), None)
                col_tarjeta = next((c for c, n in cols.items() if 'tarjetaidmx' in n), None)
                if not col_tarjeta:
                    col_tarjeta = next((c for c, n in cols.items() if n == 'tarjeta' or 'tarjeta' in n or n == 'tag'), None)
                
                df_std = pd.DataFrame()
                df_std['ECO'] = df[col_eco].astype(str).str.strip() if col_eco else None
                df_std['Fecha'] = _parse_pase_fecha(df[col_fecha]) if col_fecha else None
                if col_importe:
                    df_std['Importe'] = pd.to_numeric(df[col_importe].astype(str).str.replace(r'[$,]','',regex=True), errors='coerce').abs()
                if col_tarjeta:
                    df_std['Tarjeta IDMX'] = df[col_tarjeta].astype(str).str.strip().str.rstrip('.')
                df_std['Archivo_Origen'] = file
                df_std = df_std.dropna(subset=['Importe', 'Fecha', 'ECO'])
                df_std['ECO'] = df_std['ECO'].apply(_normalize_eco)
                lista_pase.append(df_std)
            except Exception as e:
                print(f"  ⚠️ Error en {file}: {e}")
        if lista_pase:
            pd.concat(lista_pase, ignore_index=True).to_csv("CONSOLIDADO_CRUDO_PASE.csv", index=False, encoding='utf-8-sig')
            print(f"✅ Creado: CONSOLIDADO_CRUDO_PASE.csv")
    else:
        print("⚠️ No existe la carpeta Pase en la ruta de respaldos.")

    # 3. EDENRED
    edenred_dir = os.path.join(respaldos_dir, 'Edenred')
    if os.path.exists(edenred_dir):
        all_edenred_files = []
        for root, dirs, files in os.walk(edenred_dir):
            for file in files:
                if file.endswith('.csv') or file.endswith('.xlsx'):
                    all_edenred_files.append(os.path.join(root, file))
                    
        print(f"📂 Encontrados {len(all_edenred_files)} archivos de Edenred en OneDrive...")
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
            except Exception as e:
                print(f"  ⚠️ Error en {file}: {e}")
        if lista_eden:
            pd.concat(lista_eden, ignore_index=True).to_csv("CONSOLIDADO_CRUDO_EDENRED.csv", index=False, encoding='utf-8-sig')
            print(f"✅ Creado: CONSOLIDADO_CRUDO_EDENRED.csv")
        if lista_eden_limpio:
            pd.concat(lista_eden_limpio, ignore_index=True).to_csv("CONSOLIDADO_LIMPIO_EDENRED.csv", index=False, encoding='utf-8-sig')
            print(f"✅ Creado: CONSOLIDADO_LIMPIO_EDENRED.csv")
    else:
        print("⚠️ No existe la carpeta Edenred en la ruta de respaldos.")
        
    print("=== PROCESO FINALIZADO ===")

if __name__ == "__main__":
    unificar_respaldos_desde_onedrive()
