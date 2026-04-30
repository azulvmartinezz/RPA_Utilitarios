import os
from google.cloud import bigquery
from google.api_core.exceptions import Conflict

def setup_bigquery():
    # El Project ID se asume que estará configurado en tu entorno o gcloud
    # Si quieres forzar un proyecto específico, puedes pasar: client = bigquery.Client(project='mi-proyecto')
    print("Iniciando conexión con BigQuery usando credenciales de Terminal (ADC)...")
    try:
        client = bigquery.Client()
        project_id = client.project
        print(f"Conectado exitosamente al proyecto: {project_id}")
    except Exception as e:
        print(f"Error de conexión. Asegúrate de haber ejecutado 'gcloud auth application-default login'. Detalle: {e}")
        return

    dataset_id = f"{project_id}.{os.getenv('BQ_DATASET', 'rpa_utilitarios')}"
    table_id = f"{dataset_id}.{os.getenv('BQ_TABLE', 'consumos_flota')}"

    # 1. Crear el Dataset
    dataset = bigquery.Dataset(dataset_id)
    dataset.location = "US"  # Cambia esto si prefieres otra región, ej. "us-central1"
    
    try:
        client.create_dataset(dataset, timeout=30)
        print(f"✅ Dataset {dataset_id} creado exitosamente.")
    except Conflict:
        print(f"ℹ️ El Dataset {dataset_id} ya existe.")

    # 2. Definir el Schema de la Tabla
    schema = [
        bigquery.SchemaField("ECO", "STRING", mode="NULLABLE", description="Identificador del vehículo o tarjeta"),
        bigquery.SchemaField("Fecha", "DATE", mode="NULLABLE", description="Fecha de la transacción"),
        bigquery.SchemaField("Concepto", "STRING", mode="NULLABLE", description="Producto o servicio adquirido"),
        bigquery.SchemaField("Tipo", "STRING", mode="NULLABLE", description="Combustible, Peaje, etc."),
        bigquery.SchemaField("Cantidad", "FLOAT", mode="NULLABLE", description="Litros cargados (Solo aplica para combustible)"),
        bigquery.SchemaField("Importe", "FLOAT", mode="NULLABLE", description="Monto gastado en MXN"),
        bigquery.SchemaField("Sistema", "STRING", mode="REQUIRED", description="Fuente: Pase, Supramax o Edenred"),
    ]

    table = bigquery.Table(table_id, schema=schema)
    
    # Opcional: Particionar por Fecha para optimizar costos de consultas a futuro
    table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field="Fecha",  # Particiona la tabla usando esta columna
    )

    try:
        client.create_table(table)
        print(f"✅ Tabla {table_id} creada exitosamente con la estructura solicitada.")
    except Conflict:
        print(f"ℹ️ La Tabla {table_id} ya existe.")
        
    print("\n🚀 ¡Setup de BigQuery finalizado! Estructura lista para recibir datos.")

if __name__ == "__main__":
    setup_bigquery()
