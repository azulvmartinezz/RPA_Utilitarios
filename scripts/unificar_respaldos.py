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
    # Solo aceptar ECOs canónicos AU/CA con número. Permite sufijos o anotaciones como (JW) usando (?!\d)
    m = re.match(r'^(AU|CA)-?(\d{1,3})(?!\d)', s)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(3)}"
    return s


def _parse_pase_fecha(series):
    texto = series.astype(str).str.strip()
    serie = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")

    # 1) Formato ISO mexicano visto en Pase: YYYY/MM/DD
    mask_ymd = texto.str.match(r"^\d{4}/\d{2}/\d{2}$", na=False)
    if mask_ymd.any():
        serie.loc[mask_ymd] = pd.to_datetime(texto.loc[mask_ymd], format="%Y/%m/%d", errors="coerce")

    # 2) Para el resto, usar el mismo criterio operativo que BQ: dayfirst=True
    restantes = serie.isna()
    if restantes.any():
        serie.loc[restantes] = pd.to_datetime(texto.loc[restantes], dayfirst=True, errors="coerce")

    # 3) Último intento genérico por si aparece alguna variante rara
    restantes = serie.isna()
    if restantes.any():
        serie.loc[restantes] = pd.to_datetime(texto.loc[restantes], errors="coerce")

    return serie


def _extraer_periodo_desde_ruta_pase(blob_name):
    match = re.search(r'/(\d{4})/(\d{2})/', blob_name)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _extraer_empresa_desde_ruta_pase(blob_name):
    match = re.search(r'/(\d{4})/(\d{2})/', blob_name)
    if not match:
        return "Legacy"
    idx = match.start()
    return blob_name[:idx]


def _dedupe_name_pase(nombre_blob):
    nombre = os.path.basename(nombre_blob)
    match = re.match(r'^pase_[0-9a-f]{12}_(.+)$', nombre, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return nombre


def _is_new_pase_layout(blob_name):
    # Nuevo layout: Pase/<EMPRESA>/YYYY/MM/archivo
    return re.match(r"^Pase/[^/]+/\d{4}/\d{2}/", blob_name) is not None


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
    parser.add_argument("--year", type=int, help="Filtrar respaldos por año (ej. 2026)")
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
        if args.year:
            year_fragment = f"/{args.year}/"
            blobs_csv = [b for b in blobs_csv if year_fragment in b.name]
        if blobs_csv:
            print(f"☁️  Descargando y unificando {len(blobs_csv)} archivos de Pase...")
            lista_pase = []
            blobs_elegidos = {}
            with tempfile.TemporaryDirectory() as tmpdir:
                for blob in blobs_csv:
                    bucket_year, bucket_month = _extraer_periodo_desde_ruta_pase(blob.name)
                    empresa_prefix = _extraer_empresa_desde_ruta_pase(blob.name)
                    dedupe_nombre = _dedupe_name_pase(blob.name)
                    
                    # Si el archivo es genérico (como cruces.csv de prepago), incluimos la empresa y el periodo en la clave
                    # para evitar que colisionen las descargas de distintas empresas o distintos meses.
                    # Si es único (pospago con número de cliente y periodo), la clave es solo el nombre del archivo
                    # para evitar descargar el mismo periodo duplicado que esté respaldado en carpetas de meses distintos
                    # o en layouts distintos (legacy vs nuevo).
                    is_generic = "cruces.csv" in dedupe_nombre.lower()
                    if is_generic:
                        dedupe_key = (empresa_prefix, bucket_year, bucket_month, dedupe_nombre)
                    else:
                        dedupe_key = (dedupe_nombre,)

                    existente = blobs_elegidos.get(dedupe_key)
                    if existente is None:
                        blobs_elegidos[dedupe_key] = blob
                    else:
                        existente_new = _is_new_pase_layout(existente.name)
                        actual_new = _is_new_pase_layout(blob.name)
                        # Preferir la ruta nueva con empresa sobre el layout legacy Pase/YYYY/MM
                        if actual_new and not existente_new:
                            print(
                                f"  ↪️ Archivo Pase duplicado: se prefiere layout nuevo "
                                f"{blob.name} sobre {existente.name}"
                            )
                            blobs_elegidos[dedupe_key] = blob
                        else:
                            print(
                                f"  ↪️ Archivo Pase duplicado por respaldo mensual, se omite: "
                                f"{blob.name} (ya considerado desde {existente.name})"
                            )

                for dedupe_key, blob in blobs_elegidos.items():
                    # Re-extraer el periodo correcto desde la ruta del blob elegido
                    blob_year, blob_month = _extraer_periodo_desde_ruta_pase(blob.name)
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
                        col_tarjeta = next((c for c, n in cols.items() if 'tarjetaidmx' in n), None)
                        if not col_tarjeta:
                            col_tarjeta = next((c for c, n in cols.items() if n == 'tarjeta' or 'tarjeta' in n), None)

                        df_std = pd.DataFrame()
                        df_std['ECO'] = df[col_eco].astype(str).str.strip() if col_eco else None
                        df_std['Fecha'] = _parse_pase_fecha(df[col_fecha]) if col_fecha else None
                        if col_importe:
                            df_std['Importe'] = pd.to_numeric(df[col_importe].astype(str).str.replace(r'[$,]','',regex=True), errors='coerce').abs()
                        if col_tarjeta:
                            df_std['Tarjeta IDMX'] = df[col_tarjeta].astype(str).str.strip()
                        df_std['Archivo_Origen'] = nombre
                        df_std = df_std.dropna(subset=['Importe', 'Fecha', 'ECO'])
                        df_std['ECO'] = df_std['ECO'].apply(_normalize_eco)
                        # En Pase no filtramos por mes de la carpeta para no descartar cruces tardíos
                        if blob_year:
                            df_std = df_std[df_std['Fecha'].dt.year == blob_year]
                        if args.year:
                            df_std = df_std[df_std['Fecha'].dt.year == args.year]
                        lista_pase.append(df_std)
                    except Exception as e:
                        print(f"  ⚠️ Error en {nombre}: {e}")
            if lista_pase:
                df_pase_final = pd.concat(lista_pase, ignore_index=True)
                df_pase_final.to_csv("CONSOLIDADO_CRUDO_PASE.csv", index=False, encoding='utf-8-sig')
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
