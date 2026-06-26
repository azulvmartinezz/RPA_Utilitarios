# Limpieza y Deduplicación de BigQuery (`consumos_flota`)

Este documento explica por qué se creó el script de deduplicación y cómo utilizarlo de manera segura para que Looker Studio muestre montos correctos y alineados con tu Excel local.

---

### ¿Por qué se generaron duplicados en BigQuery?

1. **El caso de Pase (Peajes)**:
   * Para evitar colapsar cruces legítimos que ocurren el mismo día con el mismo importe en una misma caseta (por ejemplo, viajes de ida y vuelta), el cargador original de BigQuery decide la unicidad usando el nombre del archivo origen (`Archivo_Origen`).
   * Como los reportes del portal de Pase se descargaron con fechas solapadas y nombres distintos entre ejecuciones del RPA, BigQuery no reconoció los cruces repetidos y los insertó múltiples veces. Esto generó casi **$1.5 millones de pesos de duplicados** en 2025/2026.

2. **El caso de Supramax y Edenred**:
   * Ocurrieron pequeñas duplicaciones por reintentos de cargas manuales o interrupciones en el flujo automático del orquestador.

---

### ¿Cómo funciona el script de limpieza?

El script [deduplicar_bigquery.py](file:///c:/Users/Violeta/Documents/Proyectos%20VSC/RPA_Utilitarios/scripts/deduplicar_bigquery.py) está diseñado para ejecutarse **100% bajo demanda** y de forma segura:

1. **Copia de seguridad automática**: Antes de tocar la tabla activa, crea una réplica exacta llamada `consumos_flota_backup_YYYYMMDD_HHMMSS`. Si algo sale mal, tu información histórica original siempre estará a salvo allí.
2. **Filtro de unicidad**: Reemplaza la tabla activa aplicando una regla analítica (`ROW_NUMBER() OVER(PARTITION BY ... ORDER BY ...)`). Agrupa todas las transacciones idénticas (ECO, Fecha, Importe, Concepto, Sistema) y se queda únicamente con la primera ocurrencia, purgando el resto.

---

### Instrucciones para ejecutarlo

Cuando estés lista para alinear Looker Studio con tu Excel:

1. Asegúrate de tener iniciada sesión en Google Cloud SDK en tu terminal:
   ```bash
   gcloud auth application-default login
   ```
2. Ejecuta el script con Python desde la carpeta raíz del proyecto:
   ```bash
   .venv\Scripts\python scripts\deduplicar_bigquery.py
   ```
3. Una vez terminado, Looker Studio se actualizará automáticamente con las cifras correctas.
