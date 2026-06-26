import os
import glob
import sys
import pandas as pd
from datetime import datetime
from google.cloud import storage
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from pase_utils import parse_pase_fecha, read_pase_csv_lossless

def obtener_mes_año_real(archivo, sistema):
    # Intentar leer el archivo para extraer la fecha real de los datos
    try:
        if sistema == 'Pase' or archivo.endswith('.csv'):
            df = read_pase_csv_lossless(archivo)
        elif sistema == 'Supramax' and archivo.endswith('.xls'):
            try:
                raw = pd.read_excel(archivo, engine='xlrd', header=None)
                header_row = next(i for i, row in raw.iterrows() if row.astype(str).str.strip().eq('PLACAS').any())
                df = pd.read_excel(archivo, engine='xlrd', header=header_row)
            except:
                df = pd.read_html(archivo, encoding='latin1')[0]
        elif sistema == 'Edenred':
            df = pd.read_excel(archivo, header=5)
        else:
            return None, None
            
        df.columns = df.columns.str.strip()
        cols_norm = {c: c.lower().replace(' ', '').replace('ó', 'o').replace('.', '') for c in df.columns}
        
        col_fecha = next((c for c, norm in cols_norm.items() if 'fechadecruce' in norm), None)
        if not col_fecha: col_fecha = next((c for c, norm in cols_norm.items() if 'fecha' in norm), None)
        
        if col_fecha and col_fecha in df.columns:
            fechas_validas = parse_pase_fecha(df[col_fecha]).dropna()
            if not fechas_validas.empty:
                # Tomamos la moda (el valor más repetido) para que gane la mayoría democrática
                fecha_frecuente = fechas_validas.mode().iloc[0]
                return fecha_frecuente.strftime("%Y"), fecha_frecuente.strftime("%m")
    except Exception as e:
        print(f"Error leyendo contenido de {archivo}: {e}")
        
    return None, None

def migrar_a_nube():
    load_dotenv()
    project_id = os.getenv('GCP_PROJECT_ID')
    bucket_name = os.getenv('GCP_BUCKET_RESPALDOS', f"{project_id}-respaldos-rpa")
    
    print(f"☁️ Conectando a Google Cloud Storage (Bucket: {bucket_name})...")
    client = storage.Client(project=project_id)
    bucket = client.bucket(bucket_name)
    
    if not bucket.exists():
        print(f"🛠️ Creando bucket {bucket_name} por primera vez...")
        bucket = client.create_bucket(bucket_name, location="US")
        
    archivos = glob.glob("respaldo_descargas/*")
    print(f"Encontrados {len(archivos)} archivos locales. Abriendo uno por uno para ver sus fechas reales...")
    
    for archivo in archivos:
        nombre_base = os.path.basename(archivo)
        
        # 1. Determinar el sistema
        if nombre_base.startswith("pase"):
            sistema = "Pase"
        elif nombre_base.startswith("edenred"):
            sistema = "Edenred"
        elif nombre_base.startswith("supramax"):
            sistema = "Supramax"
        else:
            continue
            
        # 2. Extraer Año y Mes REAL leyendo el archivo por dentro
        anio, mes = obtener_mes_año_real(archivo, sistema)
        
        # Si falló la lectura, caemos a la fecha de descarga (timestamp del nombre)
        if not anio or not mes:
            try:
                ts_str = nombre_base.split('_')[1]
                dt = datetime.fromtimestamp(int(ts_str))
                anio, mes = dt.strftime("%Y"), dt.strftime("%m")
            except:
                dt = datetime.now()
                anio, mes = dt.strftime("%Y"), dt.strftime("%m")
            
        # 3. Armar la ruta final
        ruta_gcs = f"{sistema}/{anio}/{mes}/{nombre_base}"
        
        # 4. Subir
        blob = bucket.blob(ruta_gcs)
        print(f"Subiendo {nombre_base} -> gs://{bucket_name}/{ruta_gcs}")
        blob.upload_from_filename(archivo)

    print("\n✅ ¡Migración Inteligente completada!")

if __name__ == '__main__':
    migrar_a_nube()
