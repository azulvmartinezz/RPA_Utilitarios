import os
import sys
import tempfile
import pandas as pd
from google.cloud import storage
from dotenv import load_dotenv

# Añadir directorio raíz al PATH de python para importar módulos del proyecto
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bigquery import bq_ingestion

def main():
    load_dotenv()
    project_id = os.getenv("GCP_PROJECT_ID")
    bucket_name = os.getenv("GCP_BUCKET_RESPALDOS", f"{project_id}-respaldos-rpa")
    
    if not project_id:
        print("❌ Error: GCP_PROJECT_ID no está definido en el archivo .env")
        sys.exit(1)
        
    print(f"☁️ Conectando a gs://{bucket_name} (proyecto: {project_id})...")
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    
    # Listar todos los reportes de Pase de 2025 en el bucket
    print("🔍 Buscando archivos de Pase 2025...")
    blobs = list(bucket.list_blobs(prefix="Pase/"))
    blobs_2025 = [b for b in blobs if "/2025/" in b.name and b.name.endswith(".csv")]
    
    total_archivos = len(blobs_2025)
    print(f"📂 Encontrados {total_archivos} archivo(s) de Pase 2025 para procesar.")
    
    # Desactivar temporalmente filtros de variables de entorno para procesar todo el año sin restricciones
    os.environ.pop("BACKFILL_YEAR", None)
    os.environ.pop("BACKFILL_MONTH", None)
    
    ingestados = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        for idx, blob in enumerate(blobs_2025, 1):
            nombre_archivo = os.path.basename(blob.name)
            print(f"\n[{idx}/{total_archivos}] Procesando archivo: {nombre_archivo}")
            
            # Obtener empresa desde la ruta del bucket (Pase/<EMPRESA>/2025/...)
            parts = blob.name.split("/")
            empresa = "UNKNOWN"
            if len(parts) >= 3:
                empresa = parts[1].replace("_", " ").upper()
            
            print(f"  🏢 Empresa identificada: {empresa}")
            
            local_path = os.path.join(tmpdir, nombre_archivo)
            try:
                # 1. Descargar archivo temporalmente
                blob.download_to_filename(local_path)
                
                # 2. Procesar y limpiar datos del reporte de Pase usando el parser correcto
                df_clean = bq_ingestion.procesar_pase(local_path, empresa=empresa)
                
                if df_clean is not None and len(df_clean) > 0:
                    # 3. Ingestar en BigQuery (el MERGE saltará los duplicados que ya existan)
                    bq_ingestion.ingest_to_bigquery(df_clean)
                    ingestados += 1
                else:
                    print("  ⚠️ El archivo no contiene filas válidas (o todos los ECOs fueron filtrados).")
            except Exception as e:
                print(f"  ❌ Error procesando {nombre_archivo}: {e}")
                
    print(f"\n🎉 ¡Proceso finalizado! Se procesaron {ingestados} de {total_archivos} archivos de Pase 2025 exitosamente.")

if __name__ == "__main__":
    main()
