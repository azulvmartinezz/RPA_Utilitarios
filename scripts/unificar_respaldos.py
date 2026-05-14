import os
import re
import sys
import tempfile
import pandas as pd
from google.cloud import storage
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _normalize_eco(val):
    s = str(val).strip().upper().replace(' ', '').replace('.', '')
    m = re.match(r'^(AU|CA)-?(\d+)$', s)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(3)}"
    return s


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

def unificar_respaldos():
    import argparse
    parser = argparse.ArgumentParser(description="Unificar respaldos desde GCS")
    parser.add_argument("--supramax", action="store_true", help="Solo unificar Supramax")
    parser.add_argument("--edenred", action="store_true", help="Solo unificar Edenred")
    parser.add_argument("--pase", action="store_true", help="Solo unificar Pase")
    args = parser.parse_args()
    
    run_all = not (args.supramax or args.edenred or args.pase)

    load_dotenv()
    project_id = os.getenv('GCP_PROJECT_ID')
    bucket_name = os.getenv('GCP_BUCKET_RESPALDOS', f'{project_id}-respaldos-rpa')
    
    if not project_id:
        print("❌ Error: No se encontró GCP_PROJECT_ID en el .env")
        return

    print(f"=== UNIFICANDO RESPALDOS DESDE LA NUBE (gs://{bucket_name}) ===")
    client = storage.Client(project=project_id)
    gcs_bucket = client.bucket(bucket_name)

    # --- 1. SUPRAMAX ---
    if args.supramax or run_all:
        blobs = list(gcs_bucket.list_blobs(prefix='Supramax/'))
        blobs_xls = [b for b in blobs if b.name.endswith('.xls')]
        if blobs_xls:
            print(f"☁️  Descargando y unificando {len(blobs_xls)} archivos de Supramax...")
            lista_supra = []
            with tempfile.TemporaryDirectory() as tmpdir:
                for blob in blobs_xls:
                    nombre = os.path.basename(blob.name)
                    local_path = os.path.join(tmpdir, nombre)
                    blob.download_to_filename(local_path)
                    try:
                        raw = pd.read_excel(local_path, engine='xlrd', header=None)
                        header_row = next(i for i, row in raw.iterrows() if row.astype(str).str.strip().eq('PLACAS').any())
                        df = pd.read_excel(local_path, engine='xlrd', header=header_row)
                        df['Archivo_Origen'] = nombre
                        lista_supra.append(df)
                    except Exception as e:
                        print(f"  ⚠️ Error en {nombre}: {e}")
            if lista_supra:
                pd.concat(lista_supra, ignore_index=True).to_csv("CONSOLIDADO_CRUDO_SUPRAMAX.csv", index=False, encoding='utf-8-sig')
                print(f"✅ Creado: CONSOLIDADO_CRUDO_SUPRAMAX.csv")
        else:
            print("⚠️ No hay archivos de Supramax.")

    # --- 2. PASE ---
    if args.pase or run_all:
        blobs = list(gcs_bucket.list_blobs(prefix='Pase/'))
        blobs_csv = [b for b in blobs if b.name.endswith('.csv')]
        if blobs_csv:
            print(f"☁️  Descargando y unificando {len(blobs_csv)} archivos de Pase...")
            lista_pase = []
            with tempfile.TemporaryDirectory() as tmpdir:
                for blob in blobs_csv:
                    nombre = os.path.basename(blob.name)
                    local_path = os.path.join(tmpdir, nombre)
                    blob.download_to_filename(local_path)
                    try:
                        try:
                            df = pd.read_csv(local_path, encoding='latin1', index_col=False)
                        except:
                            df = pd.read_csv(local_path, index_col=False)
                        
                        df.columns = df.columns.str.strip()
                        cols = {c: c.lower().replace(' ', '').replace('ó','o').replace('.','') for c in df.columns}
                        col_eco = next((c for c, n in cols.items() if 'noeconomico' in n or 'economico' in n), None)
                        col_fecha = next((c for c, n in cols.items() if 'fechadecruce' in n or n == 'fecha'), None)
                        col_importe = next((c for c, n in cols.items() if 'importeal100' in n or n == 'importe'), None)

                        df_std = pd.DataFrame()
                        df_std['ECO'] = df[col_eco].astype(str).str.strip() if col_eco else None
                        df_std['Fecha'] = df[col_fecha] if col_fecha else None
                        if col_importe:
                            df_std['Importe'] = pd.to_numeric(df[col_importe].astype(str).str.replace(r'[$,]','',regex=True), errors='coerce').abs()
                        df_std['Archivo_Origen'] = nombre
                        lista_pase.append(df_std)
                    except Exception as e:
                        print(f"  ⚠️ Error en {nombre}: {e}")
            if lista_pase:
                pd.concat(lista_pase, ignore_index=True).to_csv("CONSOLIDADO_CRUDO_PASE.csv", index=False, encoding='utf-8-sig')
                print(f"✅ Creado: CONSOLIDADO_CRUDO_PASE.csv")
        else:
            print("⚠️ No hay archivos de Pase.")

    # --- 3. EDENRED ---
    if args.edenred or run_all:
        blobs = list(gcs_bucket.list_blobs(prefix='Edenred/'))
        blobs_validos = [b for b in blobs if b.name.endswith('.csv') or b.name.endswith('.xlsx')]
        if blobs_validos:
            print(f"☁️  Descargando y unificando {len(blobs_validos)} archivos de Edenred...")
            lista_eden = []
            lista_eden_limpio = []
            with tempfile.TemporaryDirectory() as tmpdir:
                for blob in blobs_validos:
                    nombre = os.path.basename(blob.name)
                    local_path = os.path.join(tmpdir, nombre)
                    blob.download_to_filename(local_path)
                    try:
                        if nombre.endswith('.csv'):
                            df = pd.read_csv(local_path, encoding='latin1')
                        else:
                            df = pd.read_excel(local_path, header=5)
                        df['Archivo_Origen'] = nombre
                        lista_eden.append(df)
                        lista_eden_limpio.append(_limpiar_edenred(df))
                    except Exception as e:
                        print(f"  ⚠️ Error en {nombre}: {e}")
            if lista_eden:
                pd.concat(lista_eden, ignore_index=True).to_csv("CONSOLIDADO_CRUDO_EDENRED.csv", index=False, encoding='utf-8-sig')
                print(f"✅ Creado: CONSOLIDADO_CRUDO_EDENRED.csv")
            if lista_eden_limpio:
                pd.concat(lista_eden_limpio, ignore_index=True).to_csv("CONSOLIDADO_LIMPIO_EDENRED.csv", index=False, encoding='utf-8-sig')
                print(f"✅ Creado: CONSOLIDADO_LIMPIO_EDENRED.csv")
        else:
            print("⚠️ No hay archivos de Edenred.")

    print("=== PROCESO FINALIZADO ===")

if __name__ == "__main__":
    unificar_respaldos()
