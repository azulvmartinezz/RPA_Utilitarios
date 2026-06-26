import os
import sys
from datetime import datetime
from google.cloud import bigquery
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv()

def deduplicar_tabla():
    project_id = os.getenv('GCP_PROJECT_ID', 'innovacion-futuro')
    dataset_id = os.getenv('BQ_DATASET', 'rpa_utilitarios')
    table_name = os.getenv('BQ_TABLE', 'consumos_flota')
    
    table_id = f"{project_id}.{dataset_id}.{table_name}"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_table_id = f"{table_id}_backup_{timestamp}"
    
    print("=== SCRIPT DE DEDUPLICACIÓN DE BIGQUERY ===")
    print(f"Proyecto: {project_id}")
    print(f"Dataset: {dataset_id}")
    print(f"Tabla a depurar: {table_name}")
    print(f"Tabla de respaldo que se creará: {table_name}_backup_{timestamp}")
    print("===========================================")
    
    # Confirmar inicialización del cliente BQ
    try:
        client = bigquery.Client(project=project_id)
    except Exception as e:
        print(f"[Error] No se pudo inicializar el cliente de BigQuery: {e}")
        print("Asegúrate de haber corrido 'gcloud auth application-default login'.")
        return

    # Paso 1: Crear respaldo de seguridad
    print(f"\n1. Creando copia de seguridad en: {backup_table_id}...")
    try:
        backup_query = f"CREATE OR REPLACE TABLE `{backup_table_id}` AS SELECT * FROM `{table_id}`"
        client.query(backup_query).result()
        print("   Respaldo creado con éxito.")
    except Exception as e:
        print(f"[Error] Falló la creación del respaldo: {e}")
        return

    # Paso 2: Reemplazar la tabla activa por su versión deduplicada
    print(f"\n2. Deduplicando la tabla activa `{table_id}`...")
    try:
        # La deduplicación se realiza agrupando por las columnas clave y conservando
        # únicamente la primera ocurrencia de cada transacción de peajes y combustibles.
        dedup_query = f"""
        CREATE OR REPLACE TABLE `{table_id}` AS
        WITH unique_rows AS (
          SELECT
            ECO,
            Fecha,
            Concepto,
            Tipo,
            Cantidad,
            Importe,
            Sistema,
            Empresa,
            Id_Origen,
            Archivo_Origen,
            ROW_NUMBER() OVER(
              PARTITION BY ECO, Fecha, Concepto, Tipo, Cantidad, Importe, Sistema, Empresa
              ORDER BY Archivo_Origen, Id_Origen
            ) as row_num
          FROM `{backup_table_id}`
        )
        SELECT
          ECO,
          Fecha,
          Concepto,
          Tipo,
          Cantidad,
          Importe,
          Sistema,
          Empresa,
          Id_Origen,
          Archivo_Origen
        FROM unique_rows
        WHERE row_num = 1
        """
        
        client.query(dedup_query).result()
        print("   Deduplicación completada con éxito.")
        print(f"\n¡Proceso finalizado! Los reportes en Looker Studio ahora coincidirán con tu Excel.")
    except Exception as e:
        print(f"[Error] Falló la deduplicación de la tabla activa: {e}")
        print(f"   Por seguridad, tu tabla original no se modificó.")
        print(f"   Puedes restaurarla desde el respaldo: `{backup_table_id}`")

if __name__ == "__main__":
    deduplicar_tabla()
