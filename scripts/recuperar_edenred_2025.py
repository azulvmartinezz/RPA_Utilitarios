import os
import sys
import tempfile
import re
import pandas as pd
from google.cloud import storage, bigquery
from dotenv import load_dotenv

# Añadir directorio raíz al PATH de python para importar módulos del proyecto
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bigquery import bq_ingestion

def clean_company_name(name):
    if not name:
        return None
    # Eliminar espacios extra y normalizar nombre de la empresa
    return str(name).strip().upper()

def extract_company_from_excel(file_path):
    try:
        # Intentar leer las primeras filas para buscar el campo "Cliente:"
        df_header = pd.read_excel(file_path, header=None, nrows=5)
        for idx, row in df_header.iterrows():
            row_list = list(row.dropna())
            if len(row_list) >= 2 and "Cliente:" in str(row_list[0]):
                return clean_company_name(row_list[1])
    except Exception as e:
        print(f"  ⚠️ No se pudo extraer la empresa del contenido de {os.path.basename(file_path)}: {e}")
    return None

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
    
    # Listar todos los reportes de Edenred de 2025 en el bucket
    print("🔍 Buscando archivos de Edenred 2025...")
    blobs = list(bucket.list_blobs(prefix="Edenred/"))
    blobs_2025 = [b for b in blobs if "/2025/" in b.name and (b.name.endswith(".xlsx") or b.name.endswith(".csv"))]
    
    total_archivos = len(blobs_2025)
    print(f"📂 Encontrados {total_archivos} archivo(s) de Edenred 2025 para procesar.")
    
    # Desactivar temporalmente filtros de variables de entorno para procesar todo el año sin restricciones
    os.environ.pop("BACKFILL_YEAR", None)
    os.environ.pop("BACKFILL_MONTH", None)
    
    ingestados = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        for idx, blob in enumerate(blobs_2025, 1):
            nombre_archivo = os.path.basename(blob.name)
            print(f"\n[{idx}/{total_archivos}] Procesando archivo: {nombre_archivo}")
            
            local_path = os.path.join(tmpdir, nombre_archivo)
            try:
                # 1. Descargar archivo temporalmente
                blob.download_to_filename(local_path)
                
                # 2. Extraer el nombre de la empresa real
                empresa = extract_company_from_excel(local_path)
                if not empresa:
                    # Fallback al directorio en la ruta de GCS
                    parts = blob.name.split("/")
                    if len(parts) > 2 and parts[1] != "2025":
                        empresa = parts[1].replace("_", " ").upper()
                
                print(f"  🏢 Empresa identificada: {empresa or 'Desconocida'}")
                
                # 3. Procesar y limpiar datos del reporte
                df_clean = bq_ingestion.procesar_edenred(local_path, empresa=empresa)
                if df_clean is not None and len(df_clean) > 0:
                    df_clean["Archivo_Origen"] = nombre_archivo
                    
                    # 4. Ingestar en BigQuery (el MERGE saltará los duplicados que ya existan)
                    bq_ingestion.ingest_to_bigquery(df_clean)
                    ingestados += 1
                else:
                    print("  ⚠️ El archivo no contiene filas válidas.")
            except Exception as e:
                print(f"  ❌ Error procesando {nombre_archivo}: {e}")
                
    print(f"\n🎉 ¡Proceso finalizado! Se procesaron {ingestados} de {total_archivos} archivos exitosamente.")

if __name__ == "__main__":
    main()
