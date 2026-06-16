# 🧭 Guía de Scripts de RPA Utilitarios

> [!NOTE]
> Esta carpeta contiene los scripts complementarios de base de datos, backfill de datos históricos y conciliación. Para la ejecución mensual automática de producción, utiliza el [orquestador_maestro.py](file:///Users/azulvioleta/Downloads/RPA_Utilitarios/orquestador_maestro.py) en la raíz.

---

## 🏃‍♂️ 1. `backfill_historico.py`
**El orquestador de históricos.**
* **¿Qué hace?:** Realiza la descarga y carga histórica automatizada de meses pasados del 2026.
  * Lanza navegadores independientes en paralelo para **Supramax** y **Edenred**.
  * Ejecuta la descarga de **Pase** secuencialmente.
  * Limpia automáticamente el periodo en BigQuery antes de cargar para evitar duplicados.
* **Cómo se ejecuta:**
  ```bash
  # Correr todo el backfill por defecto de 2026 (Ene - Abr)
  .venv/bin/python scripts/backfill_historico.py

  # Cargar un mes y sistema específico en modo recuperación
  .venv/bin/python scripts/backfill_historico.py --pase --mes 2026-04
  ```

---

## 📊 2. `conciliacion_ecos_2025_vs_2026.py`
**El conciliador analítico.**
* **¿Qué hace?:** Genera el reporte comparativo estético de Excel (`comparativa_ecos_2025_vs_2026.xlsx`) cruzando las transacciones de BigQuery contra la tabla catálogo maestro `tbl_utilitarios_maestra`.
  * Genera el desglose del mes seleccionado en 7 pestañas: Resumen General, Comparativa del Mes, Pase, Supramax, Edenred, Mantenimientos y Validación del Catálogo.
  * Mapea de forma automática los identificadores de vehículos históricos (ej. recupera los nombres originales con `LZC` antes de normalizar).
* **Cómo se ejecuta:**
  ```bash
  # Generar el reporte para el mes de Enero (1) o Mayo (5)
  .venv/bin/python scripts/conciliacion_ecos_2025_vs_2026.py --mes 5
  ```

---

## 🗄️ 3. `unificar_respaldos.py`
**El consolidador local.**
* **¿Qué hace?:** Descarga los archivos CSV y Excel de respaldos crudos que **ya están guardados en tu nube de Google Cloud Storage (GCS)** y los unifica en un solo archivo CSV local súper limpio.
  * Realiza una deduplicación avanzada en la nube para asegurar que si el mismo archivo pospago se respaldó dos veces por error, solo se procese una vez.
  * Normaliza y limpia de forma inteligente los identificadores de vehículos (ej. convierte `AU-213(JW)` en `AU-213`).
* **Cómo se ejecuta:**
  ```bash
  # Descargar y consolidar los respaldos locales de Pase de 2026
  .venv/bin/python scripts/unificar_respaldos.py --pase --year 2026
  ```
* **Qué genera:** Crea o actualiza archivos en la raíz del proyecto como `CONSOLIDADO_CRUDO_PASE.csv`.

---

## 📊 4. `conciliar_contra_manual.py`
**El juez de las cifras.**
* **¿Qué hace?:** Cruza la base de datos de BigQuery contra el archivo de Excel cargado de forma manual (`MES POR MES - GASTOS 2026.xlsx`) que lleva el control administrativo.
  * Genera el desglose del reporte de gastos del mes para su presentación y análisis.
* **Cómo se ejecuta:**
  ```bash
  .venv/bin/python scripts/conciliar_contra_manual.py
  ```

---

## 🪪 5. `reporte_tarjetas_por_eco.py`
**El cruzador de tarjetas por unidad.**
* **¿Qué hace?:** Genera un reporte con una fila por `ECO` y las tarjetas detectadas por sistema en cuatro columnas: `ECO`, `Supramax`, `Ticket Card` y `Pase`.
  * Toma los datos desde los consolidados crudos locales.
  * Si un mismo ECO trae más de una tarjeta en un sistema, las concatena con `|` para no perder evidencia.
  * En Pase solo podrá poblar la columna si el consolidado de entrada conserva `Tarjeta IDMX`.
* **Cómo se ejecuta:**
  ```bash
  .venv/bin/python scripts/reporte_tarjetas_por_eco.py
  ```

---

## ☁️ 6. `migrar_respaldos_a_gcs.py`
**El guardián de la nube.**
* **¿Qué hace?:** Toma tus archivos descargados localmente y los sube de manera estructurada a Google Cloud Storage para que queden respaldados de forma segura en el bucket (`Pase/EMPRESA/YYYY/MM/archivo.csv`).
* **Cómo se ejecuta:**
  ```bash
  .venv/bin/python scripts/migrar_respaldos_a_gcs.py
  ```

---

## 💾 7. `recuperar_pase_2025.py` & `recuperar_edenred_2025.py`
**Los recuperadores de históricos 2025.**
* **¿Qué hacen?:** Descargan todos los archivos históricos de 2025 correspondientes a Pase o Edenred desde GCS, aplican las reglas de limpieza y formateo necesarias (como el `index_col=False` y limpieza de espacios en Pase), y los cargan de forma masiva en BigQuery sin duplicados.
* **Cómo se ejecutan:**
  ```bash
  # Recuperar todo Pase 2025
  .venv/bin/python scripts/recuperar_pase_2025.py

  # Recuperar todo Edenred 2025
  .venv/bin/python scripts/recuperar_edenred_2025.py
  ```

---

### 💡 Tips para Azul y Gemini del Futuro:

> [!TIP]
> **¿Las cifras de Pase no cuadran en BigQuery?** Corre `unificar_respaldos.py --pase --year 2026` para actualizar el consolidado local y luego audítalo contra BigQuery.
> **¿El entorno virtual?** Recuerda siempre anteponer `.venv/bin/python` al ejecutar los scripts para asegurar que usas las dependencias y credenciales de Google correctas del proyecto.
