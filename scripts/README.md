# 🧭 Guía de Scripts de RPA Utilitarios

Esta guía está diseñada para ayudar a la **Azul y al Gemini del futuro** a entender rápidamente qué hace cada script en esta carpeta, cómo ejecutarlo y qué resultados esperar. ¡Mantengamos el orden y la conciliación impecable!

---

## 🏃‍♂️ 1. `backfill_historico.py`
**El orquestador del pasado.**
* **¿Qué hace?:** Realiza la descarga y carga histórica automatizada de datos antiguos (ej. 2025 o primeros meses de 2026).
  * Lanza navegadores independientes en paralelo para **Supramax** y **Edenred**.
  * Ejecuta la descarga de **Pase** secuencialmente (resolviendo captchas de forma manual o con 2Captcha si es necesario).
  * Limpia automáticamente el periodo en BigQuery antes de cargar para evitar duplicados (idempotente).
* **Cómo se ejecuta:**
  ```bash
  # Correr todo el backfill por defecto de 2026 (Ene - Abr)
  .venv/bin/python scripts/backfill_historico.py

  # Cargar un mes y sistema específico en modo recuperación
  .venv/bin/python scripts/backfill_historico.py --pase --mes 2026-04
  ```

---

## 🗄️ 2. `unificar_respaldos.py`
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

## 🔎 3. `comparar_pase_consolidado_vs_bq.py`
**El auditor de confianza.**
* **¿Qué hace?:** Compara línea por línea el archivo consolidado local CSV (`CONSOLIDADO_CRUDO_PASE.csv`) contra los registros que están cargados en **BigQuery** para un mes específico.
  * Suma importes y cuenta transacciones agrupando por vehículo (`ECO`).
  * Te muestra en pantalla el total del CSV, el total de BigQuery y **la diferencia exacta** al centavo, listando los vehículos donde existan discrepancias.
* **Cómo se ejecuta:**
  ```bash
  # Auditar y comparar las cifras de Abril 2026 (solo vehículos canónicos)
  .venv/bin/python scripts/comparar_pase_consolidado_vs_bq.py --year 2026 --month 4 --only-utilitarios
  ```

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

## ☁️ 5. `migrar_respaldos_a_gcs.py`
**El guardián de la nube.**
* **¿Qué hace?:** Toma tus archivos descargados localmente y los sube de manera estructurada a Google Cloud Storage para que queden respaldados de forma segura en el bucket (`Pase/EMPRESA/YYYY/MM/archivo.csv`).
* **Cómo se ejecuta:**
  ```bash
  .venv/bin/python scripts/migrar_respaldos_a_gcs.py
  ```

---

## ☁️ 6. `descargar_archivos_gcs.py`
**El explorador del bucket.**
* **¿Qué hace?:** Descarga archivos de respaldo desde Google Cloud Storage a una carpeta local, con filtros opcionales por sistema, año, mes y empresa. Respeta la estructura de rutas del bucket (`Sistema/Empresa/YYYY/MM/archivo`).
* **Cómo se ejecuta:**
  ```bash
  # Ver qué hay en GCS para Pase enero 2025 (sin descargar)
  .venv/bin/python scripts/descargar_archivos_gcs.py --sistema Pase --year 2025 --mes 01 --solo-listar

  # Descargar Pase de todo 2025
  .venv/bin/python scripts/descargar_archivos_gcs.py --sistema Pase --year 2025

  # Descargar solo una empresa y un mes
  .venv/bin/python scripts/descargar_archivos_gcs.py --sistema Pase --empresa PETROIL --year 2025 --mes 01
  ```
* **Qué genera:** Crea una carpeta `descargas_gcs/` (configurable con `--destino`) replicando la estructura del bucket.

---

## 🔎 7. `diagnostico_ecos_enero.py`
**El detective de unidades.**
* **¿Qué hace?:** Compara los ECOs únicos de un mes entre dos años directamente en BigQuery. Identifica las unidades que aparecen en un año pero no en el otro — exactamente las ~80 de diferencia que brinca en el dashboard.
* **Cómo se ejecuta:**
  ```bash
  # Comparar enero 2025 vs enero 2026 (valores default)
  .venv/bin/python scripts/diagnostico_ecos_enero.py

  # Con detalle de registros crudos de las unidades nuevas
  .venv/bin/python scripts/diagnostico_ecos_enero.py --export-detalle

  # Otro mes, ej. febrero
  .venv/bin/python scripts/diagnostico_ecos_enero.py --mes 02
  ```
* **Qué genera:**
  * `ecos_solo_2026_mes01.csv` — las ~80 unidades nuevas que aparecen en 2026 pero no en 2025
  * `ecos_solo_2025_mes01.csv` — unidades que existían en 2025 pero no en 2026
  * `comparativa_ecos_mes01_2025_vs_2026.csv` — todos los ECOs lado a lado con columna `presencia`
  * `detalle_ecos_nuevos_2026_mes01.csv` — registros crudos de las unidades nuevas (solo con `--export-detalle`)

---

## 🧷 8. `rastrear_ecos.py`
**El trazador puntual.**
* **¿Qué hace?:** Toma uno o varios ECOs concretos y te dice en qué meses aparecen, en qué sistema, con qué empresa y desde qué archivo origen. Puede consultar tanto `BigQuery` como un archivo local (`CONSOLIDADO_CRUDO_EDENRED.csv`, `Untitled-1.tsv`, etc.).
* **Cómo se ejecuta:**
  ```bash
  # Consultar BigQuery y consolidado local para ECOs sospechosos
  .venv/bin/python scripts/rastrear_ecos.py --ecos AU-004 AU-006 AU-009 AU-011

  # Solo archivo local TSV sin encabezados
  .venv/bin/python scripts/rastrear_ecos.py \
    --solo-local \
    --local-file Untitled-1.tsv \
    --ecos AU-004 AU-006 AU-009 AU-011

  # Filtrar a Edenred enero-abril 2026 y exportar resultados
  .venv/bin/python scripts/rastrear_ecos.py \
    --ecos AU-004 AU-006 AU-009 AU-011 \
    --sistema Edenred \
    --year-from 2026 \
    --year-to 2026 \
    --meses 1 2 3 4 \
    --export-prefix rastreo_inactivos
  ```
* **Qué genera:** Si usas `--export-prefix`, crea `<prefijo>_bq.csv` y/o `<prefijo>_local.csv` en la raíz del proyecto.

---

### 💡 Tips para Azul y Gemini del Futuro:
1. **¿Las cifras de Pase no cuadran?** Corre `unificar_respaldos.py --pase --year 2026` para actualizar el consolidado local y luego audítalo con `comparar_pase_consolidado_vs_bq.py` para ver exactamente qué vehículos tienen diferencia.
2. **¿Brinca la cantidad de unidades entre años?** Corre `diagnostico_ecos_enero.py` para ver exactamente cuáles ECOs son nuevos o cuáles desaparecieron.
3. **El entorno virtual:** Recuerda siempre anteponer `.venv/bin/python` al ejecutar los scripts para asegurar que usas las dependencias y credenciales de Google correctas del proyecto.
