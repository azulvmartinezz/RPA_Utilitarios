import os
import re
import uuid
import json
import hashlib
import pandas as pd
from google.cloud import bigquery
from pase_utils import parse_pase_fecha, read_pase_csv_lossless


_VALID_IDENTIFIER_RE = re.compile(r'^[A-Za-z0-9_-]+$')
_VALID_SYSTEMS = {"Pase", "Supramax", "Edenred", "Google Sheets"}


def _safe_identifier(value, name):
    if not value or not _VALID_IDENTIFIER_RE.match(str(value)):
        raise ValueError(f"{name} contiene caracteres no permitidos")
    return value


def _normalize_eco(val):
    s = str(val).strip().upper().replace(' ', '').replace('.', '')
    # Solo aceptar ECOs canónicos AU/CA con número. Permite sufijos o anotaciones como (JW) usando (?!\d)
    m = re.match(r'^(AU|CA)-?(\d{1,3})(?!\d)', s)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(3)}"
    return s


def _table_id(project_id=None):
    project_id = project_id or os.getenv('GCP_PROJECT_ID')
    dataset = _safe_identifier(os.getenv('BQ_DATASET', 'rpa_utilitarios'), "BQ_DATASET")
    table = _safe_identifier(os.getenv('BQ_TABLE', 'consumos_flota'), "BQ_TABLE")
    return f"{project_id}.{dataset}.{table}"


def _apply_backfill_period_filter(df):
    sistemas = {s for s in df['Sistema'].dropna().unique()}
    if sistemas == {"Pase"}:
        # En Pase no filtramos por mes para no perder cruces tardíos facturados en meses posteriores.
        # La deduplicación por Id_Origen en BigQuery MERGE evita duplicidades de forma segura.
        backfill_year = os.getenv('BACKFILL_YEAR')
        if backfill_year:
            try:
                bf_y = int(backfill_year)
                df = df[pd.to_datetime(df['Fecha']).dt.year == bf_y].copy()
                print(f"  -> [Pase BACKFILL_YEAR={bf_y}] Filas después de filtrar por año: {len(df)}")
            except Exception as e:
                print(f"  ⚠️ Error al filtrar Pase por BACKFILL_YEAR: {e}")
        return df

    backfill_month = os.getenv('BACKFILL_MONTH')
    if backfill_month:
        try:
            period_start = pd.to_datetime(f"{backfill_month}-01").date()
            next_start = (pd.Timestamp(period_start) + pd.offsets.MonthBegin(1)).date()
            fechas = pd.to_datetime(df['Fecha'], errors='coerce').dt.date
            df = df[(fechas >= period_start) & (fechas < next_start)].copy()
            print(f"  -> [BACKFILL_MONTH={backfill_month}] Filas después de filtrar mes exacto {backfill_month}: {len(df)}")
            return df
        except Exception as e:
            print(f"  ⚠️ Error al filtrar por BACKFILL_MONTH: {e}")

    backfill_year = os.getenv('BACKFILL_YEAR')
    if backfill_year:
        try:
            bf_y = int(backfill_year)
            df = df[pd.to_datetime(df['Fecha']).dt.year == bf_y].copy()
            print(f"  -> [BACKFILL_YEAR={bf_y}] Filas después de filtrar por año {bf_y}: {len(df)}")
        except Exception as e:
            print(f"  ⚠️ Error al filtrar por BACKFILL_YEAR: {e}")
    return df


def ensure_aux_columns(project_id=None):
    client = bigquery.Client()
    table_id = _table_id(project_id)
    for column_name in ("Empresa", "Id_Origen", "Archivo_Origen"):
        sql = f"ALTER TABLE `{table_id}` ADD COLUMN IF NOT EXISTS {column_name} STRING"
        client.query(sql).result()


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
    table_id = _table_id(project_id)
    ensure_aux_columns(project_id)
    
    # Filtrar solo las columnas que importan para la BD
    columnas_esperadas = ['ECO', 'Fecha', 'Concepto', 'Tipo', 'Cantidad', 'Importe', 'Sistema', 'Empresa', 'Id_Origen', 'Archivo_Origen']
    if 'Empresa' not in df.columns:
        df = df.copy()
        df['Empresa'] = None
    if 'Id_Origen' not in df.columns:
        df = df.copy()
        df['Id_Origen'] = None
    if 'Archivo_Origen' not in df.columns:
        df = df.copy()
        df['Archivo_Origen'] = None
    df = df[columnas_esperadas].copy()
    
    # Estandarización final estricta para evitar errores de tipo en BQ
    df['ECO'] = df['ECO'].astype(str)
    df['Fecha'] = pd.to_datetime(df['Fecha'], dayfirst=True, errors='coerce').dt.date
    df['Concepto'] = df['Concepto'].astype(str)
    df['Tipo'] = df['Tipo'].where(df['Tipo'].notna(), other=None)
    df['Cantidad'] = pd.to_numeric(df['Cantidad'], errors='coerce')
    df['Importe'] = pd.to_numeric(df['Importe'], errors='coerce')
    df['Sistema'] = df['Sistema'].astype(str)
    df['Empresa'] = df['Empresa'].where(df['Empresa'].notna(), other=None)
    df['Id_Origen'] = df['Id_Origen'].where(df['Id_Origen'].notna(), other=None)
    df['Archivo_Origen'] = df['Archivo_Origen'].where(df['Archivo_Origen'].notna(), other=None)
    
    print(f"  -> Filas antes de limpieza: {len(df)}")
    
    # Borrar filas donde el importe o ECO estén vacíos por filas de totales basura en los Excels
    df = df.dropna(subset=['Importe', 'Fecha', 'ECO'])
    print(f"  -> Filas después de dropna(Importe, Fecha, ECO): {len(df)}")
    
    # Filtrado por periodo de backfill para evitar traslapes entre cortes
    df = _apply_backfill_period_filter(df)

    if len(df) == 0:
        print("  ⚠️ Todas las filas fueron borradas (por valores nulos o filtro de año). Revisa si hay nulos en Importe, Fecha o ECO, o si el año coincide.")
        return
        
    # Normalizar ECO a formato canónico AU-XXX / CA-XXX antes de filtrar
    df['ECO'] = df['ECO'].apply(_normalize_eco)
    df = df[df['ECO'].str.match(r'^(AU|CA)-\d{3}$', na=False)]
    
    print(f"  -> Filas después de regex de ECO (solo AU/CA permitidos): {len(df)}")
    
    if len(df) == 0:
        print("  ⚠️ No hay registros de utilitarios (AU-XXX o CA-XXX) para subir después del filtrado.")
        return
        
    sistemas = {s for s in df['Sistema'].dropna().unique()}
    is_pase = sistemas == {"Pase"}

    # Para Pase usamos un identificador técnico por fila de origen.
    # Para los demás sistemas mantenemos la llave natural previa.
    if is_pase and df['Id_Origen'].notna().any():
        df = df.drop_duplicates(subset=['Id_Origen']).copy()
    else:
        df = df.drop_duplicates(subset=columnas_esperadas).copy()
    temp_table = f"{table_id}__tmp_{uuid.uuid4().hex[:12]}"

    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")

    print(f"Subiendo {len(df)} registros limpios a BigQuery ({table_id})...")
    load_job = client.load_table_from_dataframe(df, temp_table, job_config=job_config)
    load_job.result()

    if is_pase and df['Id_Origen'].notna().any():
        merge_sql = f"""
            MERGE `{table_id}` T
            USING `{temp_table}` S
            ON T.Sistema = S.Sistema
               AND T.Id_Origen = S.Id_Origen
            WHEN NOT MATCHED THEN
              INSERT (ECO, Fecha, Concepto, Tipo, Cantidad, Importe, Sistema, Empresa, Id_Origen, Archivo_Origen)
              VALUES (S.ECO, CAST(S.Fecha AS DATE), S.Concepto, S.Tipo, S.Cantidad, S.Importe, S.Sistema, S.Empresa, S.Id_Origen, S.Archivo_Origen)
        """
    else:
        merge_sql = f"""
            MERGE `{table_id}` T
            USING `{temp_table}` S
            ON T.ECO = S.ECO
               AND T.Fecha = CAST(S.Fecha AS DATE)
               AND T.Concepto = S.Concepto
               AND IFNULL(T.Tipo, '') = IFNULL(S.Tipo, '')
               AND IFNULL(T.Cantidad, -1) = IFNULL(S.Cantidad, -1)
               AND T.Importe = S.Importe
               AND T.Sistema = S.Sistema
            WHEN NOT MATCHED THEN
              INSERT (ECO, Fecha, Concepto, Tipo, Cantidad, Importe, Sistema, Empresa, Id_Origen, Archivo_Origen)
              VALUES (S.ECO, CAST(S.Fecha AS DATE), S.Concepto, S.Tipo, S.Cantidad, S.Importe, S.Sistema, S.Empresa, S.Id_Origen, S.Archivo_Origen)
        """
    client.query(merge_sql).result()
    client.delete_table(temp_table, not_found_ok=True)
    print("✅ Ingesta completada con éxito.\n")

def procesar_supramax(file_path, empresa=None):
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
    df_clean['Empresa'] = empresa
    
    return df_clean

def procesar_pase(file_path, empresa=None):
    print(f"Procesando Pase: {file_path}")
    df = read_pase_csv_lossless(file_path)
    archivo_origen = os.path.basename(file_path)

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
        df_clean['Fecha'] = parse_pase_fecha(df[col_fecha])
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
    df_clean['Empresa'] = empresa
    df_clean['Archivo_Origen'] = archivo_origen

    # Id técnico por fila para evitar duplicados entre recargas sin colapsar
    # cruces legítimos del mismo ECO/Fecha/Importe. Mezcla archivo + número de fila
    # + contenido bruto de la fila para ser estable entre reruns del mismo CSV.
    df_firma = df.fillna('').astype(str).apply(lambda col: col.str.strip())
    firma_filas = df_firma.agg('||'.join, axis=1)
    id_origen = []
    for raw_idx, firma in zip(df.index.tolist(), firma_filas.tolist()):
        raw = f"Pase|{archivo_origen}|{int(raw_idx)}|{firma}"
        id_origen.append(hashlib.sha1(raw.encode('utf-8')).hexdigest())
    df_clean['Id_Origen'] = id_origen

    return df_clean

def procesar_edenred(file_path, empresa=None):
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
    df_clean['Empresa'] = empresa
    
    return df_clean

if __name__ == "__main__":
    # Puedes probar el script manualmente aquí:
    # df = procesar_supramax("ruta/a/tu/descarga/supramax.xls")
    # ingest_to_bigquery(df)
    pass
