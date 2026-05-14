import os
import re
import pandas as pd
from google.cloud import bigquery


_VALID_IDENTIFIER_RE = re.compile(r'^[A-Za-z0-9_-]+$')
_VALID_SYSTEMS = {"Pase", "Supramax", "Edenred", "Google Sheets"}


def _safe_identifier(value, name):
    if not value or not _VALID_IDENTIFIER_RE.match(str(value)):
        raise ValueError(f"{name} contiene caracteres no permitidos")
    return value


def _normalize_eco(val):
    s = str(val).strip().upper().replace(' ', '').replace('.', '')
    # Solo aceptar ECOs canónicos AU/CA con número. Sufijos como LZC no son la misma unidad.
    m = re.match(r'^(AU|CA)-?(\d+)$', s)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(3)}"
    return s


def delete_month(sistema, year, month, project_id=None):
    if sistema not in _VALID_SYSTEMS:
        raise ValueError(f"Sistema no permitido: {sistema}")
    project_id = project_id or os.getenv('GCP_PROJECT_ID')
    client = bigquery.Client()
    dataset = _safe_identifier(os.getenv('BQ_DATASET', 'rpa_utilitarios'), "BQ_DATASET")
    table = _safe_identifier(os.getenv('BQ_TABLE', 'consumos_flota'), "BQ_TABLE")
    table_id = f"{project_id}.{dataset}.{table}"
    query = f"""
        DELETE FROM `{table_id}`
        WHERE Sistema = @sistema
        AND DATE_TRUNC(Fecha, MONTH) = DATE('{year}-{month:02d}-01')
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("sistema", "STRING", sistema),
        ]
    )
    client.query(query, job_config=job_config).result()
    print(f"🗑️  Borrados registros de {sistema} para {year}-{month:02d}")


def ingest_to_bigquery(df, project_id=None):
    project_id = project_id or os.getenv('GCP_PROJECT_ID')
    if not project_id:
        raise ValueError("GCP_PROJECT_ID no está definido en el archivo .env")
    client = bigquery.Client()
    dataset = _safe_identifier(os.getenv('BQ_DATASET', 'rpa_utilitarios'), "BQ_DATASET")
    table = _safe_identifier(os.getenv('BQ_TABLE', 'consumos_flota'), "BQ_TABLE")
    table_id = f"{project_id}.{dataset}.{table}"
    
    # Filtrar solo las columnas que importan para la BD
    columnas_esperadas = ['ECO', 'Fecha', 'Concepto', 'Tipo', 'Cantidad', 'Importe', 'Sistema']
    df = df[columnas_esperadas].copy()
    
    # Estandarización final estricta para evitar errores de tipo en BQ
    df['ECO'] = df['ECO'].astype(str)
    df['Fecha'] = pd.to_datetime(df['Fecha'], dayfirst=True, errors='coerce').dt.date
    df['Concepto'] = df['Concepto'].astype(str)
    df['Tipo'] = df['Tipo'].where(df['Tipo'].notna(), other=None)
    df['Cantidad'] = pd.to_numeric(df['Cantidad'], errors='coerce')
    df['Importe'] = pd.to_numeric(df['Importe'], errors='coerce')
    df['Sistema'] = df['Sistema'].astype(str)
    
    print(f"  -> Filas antes de limpieza: {len(df)}")
    
    # Borrar filas donde el importe o ECO estén vacíos por filas de totales basura en los Excels
    df = df.dropna(subset=['Importe', 'Fecha', 'ECO'])
    print(f"  -> Filas después de dropna(Importe, Fecha, ECO): {len(df)}")
    if len(df) == 0:
        print("  ⚠️ Todas las filas fueron borradas por valores nulos. Revisa si hay nulos en Importe, Fecha o ECO.")
        return
        
    # Normalizar ECO a formato canónico AU-XXX / CA-XXX antes de filtrar
    df['ECO'] = df['ECO'].apply(_normalize_eco)
    df = df[df['ECO'].str.match(r'^(AU|CA)-\d{3}$', na=False)]
    
    print(f"  -> Filas después de regex de ECO (solo AU/CA permitidos): {len(df)}")
    
    if len(df) == 0:
        print("  ⚠️ No hay registros de utilitarios (AU-XXX o CA-XXX) para subir después del filtrado.")
        return
        
    # Job config para hacer Append (Añadir a lo existente)
    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_APPEND",
    )
    
    print(f"Subiendo {len(df)} registros limpios a BigQuery ({table_id})...")
    job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
    job.result() # Esperar a que termine
    print("✅ Ingesta completada con éxito.\n")

def procesar_supramax(file_path):
    print(f"Procesando Supramax: {file_path}")
    # Supramax suele devolver tablas HTML con extensión .xls
    try:
        # Leer sin header para detectar la fila que contiene "PLACAS"
        raw = pd.read_excel(file_path, engine='xlrd', header=None)
        header_row = next(
            i for i, row in raw.iterrows()
            if row.astype(str).str.strip().eq('PLACAS').any()
        )
        df = pd.read_excel(file_path, engine='xlrd', header=header_row)
    except Exception:
        df = pd.read_html(file_path, encoding='latin1')[0]

    # Limpiar nombres de columnas por si tienen espacios ocultos
    df.columns = df.columns.str.strip()
    
    df_clean = pd.DataFrame()
    df_clean['ECO'] = df['PLACAS']
    df_clean['Fecha'] = pd.to_datetime(df['FECHA'], format='%Y/%m/%d %H:%M:%S', errors='coerce')
    df_clean['Concepto'] = "COMBUSTIBLE"
    df_clean['Tipo'] = (
        df['PRODUCTO']
        .str.upper()
        .str.strip()
        .str.replace(r'^ARCO\s+', '', regex=True)
        .replace('MAGNA', 'REGULAR')
    )
    df_clean['Cantidad'] = pd.to_numeric(df['CANTIDAD'], errors='coerce')
    df_clean['Importe'] = df['IMPORTE']
    df_clean['Sistema'] = "Supramax"
    
    return df_clean

def procesar_pase(file_path):
    print(f"Procesando Pase: {file_path}")
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            df = pd.read_csv(file_path, encoding='latin1', index_col=False)
        except Exception:
            df = pd.read_csv(file_path, index_col=False)

    df.columns = df.columns.str.strip()

    df_clean = pd.DataFrame()

    # Encontrar columnas dinámicamente ignorando mayúsculas, espacios, puntos y acentos
    cols_norm = {c: c.lower().replace(' ', '').replace('ó', 'o').replace('.', '') for c in df.columns}
    
    col_eco = next((c for c, norm in cols_norm.items() if 'noeconomico' in norm), None)
    if not col_eco: col_eco = next((c for c, norm in cols_norm.items() if 'eco' in norm), None)
        
    col_fecha = next((c for c, norm in cols_norm.items() if 'fechadecruce' in norm), None)
    if not col_fecha: col_fecha = next((c for c, norm in cols_norm.items() if 'fecha' in norm), None)

    col_importe = next((c for c, norm in cols_norm.items() if 'importeal100' in norm), None)
    if not col_importe: col_importe = next((c for c, norm in cols_norm.items() if 'importe' in norm), None)
    if not col_importe: col_importe = next((c for c, norm in cols_norm.items() if 'monto' in norm), None)
    if not col_importe: col_importe = next((c for c, norm in cols_norm.items() if 'cobro' in norm), None)

    if col_eco: 
        df_clean['ECO'] = df[col_eco]
    else:
        print(f"⚠️ Atención: No se encontró columna ECO. Columnas disponibles: {df.columns.tolist()}")
        df_clean['ECO'] = None
        
    if col_fecha:
        # Forzar dayfirst=True para evitar confusiones MDY vs DMY en reportes mexicanos
        try:
            df_clean['Fecha'] = pd.to_datetime(df[col_fecha], dayfirst=True, errors='coerce')
        except:
            df_clean['Fecha'] = pd.to_datetime(df[col_fecha], errors='coerce')
    else:
        df_clean['Fecha'] = None
    
    if col_importe:
        # Siempre hacemos absoluto el importe (Petro Smart viene en negativo, otros en positivo)
        # Limpiar signo de dólar y comas para que to_numeric no falle
        importe_str = df[col_importe].astype(str).str.replace(r'[$,]', '', regex=True)
        df_clean['Importe'] = pd.to_numeric(importe_str, errors='coerce').abs()
    else:
        df_clean['Importe'] = None

    df_clean['Concepto'] = "PEAJES"
    df_clean['Tipo'] = None
    df_clean['Cantidad'] = None
    df_clean['Sistema'] = "Pase"

    return df_clean

def procesar_edenred(file_path):
    print(f"Procesando Edenred: {file_path}")
    try:
        df = pd.read_excel(file_path, header=5)
    except:
        df = pd.read_csv(file_path, encoding='latin1')
        
    df.columns = df.columns.str.strip()
    
    df_clean = pd.DataFrame()
    # Usar la columna 'Vehículo' (u 8va columna si no existe por nombre exacto)
    if 'Vehículo' in df.columns:
        df_clean['ECO'] = df['Vehículo']
    else:
        df_clean['ECO'] = df.iloc[:, 7]  # Columna número 8
    df_clean['Fecha'] = pd.to_datetime(df['Fecha Transacción'], dayfirst=True, errors='coerce')
    df_clean['Concepto'] = "COMBUSTIBLE"
    df_clean['Tipo'] = df['Mercancía']
    df_clean['Cantidad'] = pd.to_numeric(df['Cantidad Mercancía'], errors='coerce')
    df_clean['Importe'] = df['Importe Transacción']
    df_clean['Sistema'] = "Edenred"
    
    return df_clean

if __name__ == "__main__":
    # Puedes probar el script manualmente aquí:
    # df = procesar_supramax("ruta/a/tu/descarga/supramax.xls")
    # ingest_to_bigquery(df)
    pass
