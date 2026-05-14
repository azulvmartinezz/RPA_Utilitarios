import os
import hashlib
import pandas as pd
from datetime import datetime
from google.cloud import storage
from dotenv import load_dotenv

def obtener_mes_año_real(archivo, sistema):
    try:
        if sistema == 'Pase' or archivo.endswith('.csv'):
            try:
                df = pd.read_csv(archivo, encoding='latin1', index_col=False)
            except:
                df = pd.read_csv(archivo, index_col=False)
        elif sistema == 'Supramax' and archivo.endswith('.xls'):
            try:
                raw = pd.read_excel(archivo, engine='xlrd', header=None)
                header_row = next(i for i, row in raw.iterrows() if row.astype(str).str.strip().eq('PLACAS').any())
                df = pd.read_excel(archivo, engine='xlrd', header=header_row)
            except:
                df = pd.read_html(archivo, encoding='latin1')[0]
        elif sistema == 'Edenred':
            try:
                df = pd.read_excel(archivo, header=5)
            except:
                df = pd.read_csv(archivo, encoding='latin1')
        else:
            return None, None
            
        df.columns = df.columns.str.strip()
        cols_norm = {c: c.lower().replace(' ', '').replace('ó', 'o').replace('.', '') for c in df.columns}
        
        col_fecha = next((c for c, norm in cols_norm.items() if 'fechadecruce' in norm), None)
        if not col_fecha: col_fecha = next((c for c, norm in cols_norm.items() if 'fecha' in norm), None)
        
        if col_fecha and col_fecha in df.columns:
            try:
                # Forzar dayfirst=True para reportes mexicanos (DD/MM/YYYY)
                fechas_validas = pd.to_datetime(df[col_fecha], errors='coerce', dayfirst=True).dropna()
            except Exception:
                fechas_validas = pd.to_datetime(df[col_fecha], errors='coerce', infer_datetime_format=True, dayfirst=True).dropna()
                
            if not fechas_validas.empty:
                fecha_frecuente = fechas_validas.mode().iloc[0]
                return fecha_frecuente.strftime("%Y"), fecha_frecuente.strftime("%m")
    except Exception as e:
        print(f"Error extrayendo fecha de {archivo}: {e}")
        
    return None, None

def subir_y_borrar_local(archivo_local, sistema):
    load_dotenv()
    project_id = os.getenv('GCP_PROJECT_ID')
    bucket_name = os.getenv('GCP_BUCKET_RESPALDOS', f"{project_id}-respaldos-rpa")
    
    if not project_id:
        print("⚠️ No se puede subir a GCS: Falta GCP_PROJECT_ID en el .env")
        return
        
    try:
        client = storage.Client(project=project_id)
        bucket = client.bucket(bucket_name)
        
        # Obtenemos el nombre ORIGINAL del archivo (ej. g00L16072.234.csv) SIN timestamps!
        nombre_original = os.path.basename(archivo_local)
        
        # Extraer mes y año real leyendo el archivo
        anio, mes = obtener_mes_año_real(archivo_local, sistema)
        if not anio or not mes:
            # Fallback a la fecha actual si el archivo está vacío o roto
            dt = datetime.now()
            anio, mes = dt.strftime("%Y"), dt.strftime("%m")
            
        # Clave deterministica por contenido:
        # - Archivos distintos con el mismo nombre original no se pisan.
        # - El mismo archivo, si se reprocesa, cae en la misma ruta y no duplica respaldos.
        with open(archivo_local, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()[:12]
        nombre_limpio = f"{sistema.lower()}_{digest}_{nombre_original}"
        ruta_gcs = f"{sistema}/{anio}/{mes}/{nombre_limpio}"
        
        print(f"☁️ Subiendo a la nube: gs://{bucket_name}/{ruta_gcs}")
        blob = bucket.blob(ruta_gcs)
        blob.upload_from_filename(archivo_local)
        print(f"✅ Subida exitosa. Si el contenido ya existía, se actualizó el mismo respaldo.")
        
        # Limpieza final: Eliminar el archivo local para no ensuciar la Mac
        os.remove(archivo_local)
        print(f"🗑️ Archivo temporal '{nombre_original}' borrado de la Mac.")
        
    except Exception as e:
        print(f"❌ Error al interactuar con Google Cloud: {e}")
