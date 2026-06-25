import os
import sys
from google.cloud import storage
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv()

def descargar_historico():
    project_id = os.getenv('GCP_PROJECT_ID')
    bucket_name = os.getenv('GCP_BUCKET_RESPALDOS', f'{project_id}-respaldos-rpa')
    respaldos_dir = os.getenv('ONEDRIVE_RESPALDOS_DIR')
    
    if not project_id:
        print("[Error] No se encontro GCP_PROJECT_ID en el .env")
        return
        
    if not respaldos_dir or not os.path.exists(respaldos_dir):
        print(f"[Error] La ruta local de OneDrive no existe: {respaldos_dir}")
        return

    print("=== INICIANDO DESCARGA DEL HISTORICO DESDE LA NUBE ===")
    print(f"Conectando al bucket gs://{bucket_name}...")
    
    try:
        client = storage.Client(project=project_id)
        bucket = client.bucket(bucket_name)
    except Exception as e:
        print(f"[Error] Al inicializar cliente de GCP: {e}")
        print("Recuerda ejecutar 'gcloud auth application-default login' antes de correr este script.")
        return

    # Descargar para cada sistema
    for sistema in ['Supramax', 'Pase', 'Edenred']:
        target_dir = os.path.join(respaldos_dir, sistema)
        os.makedirs(target_dir, exist_ok=True)
        
        print(f"\nProcesando carpeta: {sistema}...")
        try:
            blobs = list(bucket.list_blobs(prefix=f"{sistema}/"))
            valid_blobs = [b for b in blobs if b.name.endswith('.xls') or b.name.endswith('.csv') or b.name.endswith('.xlsx')]
            
            print(f"Encontrados {len(valid_blobs)} archivos en la nube para {sistema}.")
            
            for idx, blob in enumerate(valid_blobs):
                # Mantener nombre de archivo limpio en la carpeta de destino
                nombre_archivo = os.path.basename(blob.name)
                dest_path = os.path.join(target_dir, nombre_archivo)
                
                if not os.path.exists(dest_path):
                    print(f"[{idx+1}/{len(valid_blobs)}] Descargando: {nombre_archivo}...")
                    blob.download_to_filename(dest_path)
                else:
                    print(f"  Omitido (Ya existe): {nombre_archivo}")
                    
            print(f"Descarga completada para {sistema}.")
        except Exception as e:
            print(f"[Error] Al descargar archivos de {sistema}: {e}")
            
    print("\n=== DESCARGA DE HISTORICO FINALIZADA CON EXITO ===")
    print(f"Todos los archivos respaldados estan ahora en: {respaldos_dir}")

if __name__ == "__main__":
    descargar_historico()
