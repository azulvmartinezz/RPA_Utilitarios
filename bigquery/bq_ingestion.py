import os
import pandas as pd
from google.cloud import bigquery

def ingest_to_bigquery(df, project_id=None):
    project_id = project_id or os.getenv('GCP_PROJECT_ID')
    if not project_id:
        raise ValueError("GCP_PROJECT_ID no está definido en el archivo .env")
    client = bigquery.Client()
    dataset = os.getenv('BQ_DATASET', 'rpa_utilitarios')
    table = os.getenv('BQ_TABLE', 'consumos_flota')
    table_id = f"{project_id}.{dataset}.{table}"
    
    # Filtrar solo las columnas que importan para la BD
    columnas_esperadas = ['ECO', 'Fecha', 'Concepto', 'Tipo', 'Cantidad', 'Importe', 'Sistema']
    df = df[columnas_esperadas].copy()
    
    # Estandarización final estricta para evitar errores de tipo en BQ
    df['ECO'] = df['ECO'].astype(str)
    df['Fecha'] = pd.to_datetime(df['Fecha'], dayfirst=True, errors='coerce').dt.date
    df['Concepto'] = df['Concepto'].astype(str)
    df['Tipo'] = df['Tipo'].fillna("N/A").astype(str)
    df['Cantidad'] = pd.to_numeric(df['Cantidad'], errors='coerce').fillna(0.0)
    df['Importe'] = pd.to_numeric(df['Importe'], errors='coerce')
    df['Sistema'] = df['Sistema'].astype(str)
    
    # Borrar filas donde el importe o ECO estén vacíos por filas de totales basura en los Excels
    df = df.dropna(subset=['Importe', 'Fecha', 'ECO'])
    
    # 🚗 FILTRO DE UTILITARIOS: Solo mantener filas cuyo ECO empiece estrictamente con AU o CA 
    # (Ej. AU-123, CA-001). Esto deja fuera al equipo pesado que usa otras letras.
    # El case=False permite que también acepte au-123 o ca-123 en minúsculas por si acaso.
    df = df[df['ECO'].str.match(r'^(AU|CA)-?\d{3}$', na=False, case=False)]
    
    if len(df) == 0:
        print("⚠️ No hay registros de utilitarios (AU-XXX o CA-XXX) para subir después del filtrado.")
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
    # Pase a veces devuelve CSV con codificaciones latinoamericanas y con más columnas en los datos que en los headers
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore") # Ignorar el warning de "Length of header or names does not match"
        try:
            df = pd.read_csv(file_path, encoding='latin1', index_col=False)
        except:
            df = pd.read_csv(file_path, index_col=False)
        
    df.columns = df.columns.str.strip()
    
    df_clean = pd.DataFrame()
    df_clean['ECO'] = df['No. economico']
    df_clean['Fecha'] = pd.to_datetime(df['Fecha de cruce'], dayfirst=True, errors='coerce') 
    df_clean['Concepto'] = "PEAJES"
    df_clean['Tipo'] = "NO APLICA"
    df_clean['Cantidad'] = 0.0 # Pase no tiene litros
    df_clean['Importe'] = df['Importe al 100%']
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
    df_clean['ECO'] = df['Identificador vehículo']
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
