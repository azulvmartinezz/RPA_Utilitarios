# 🤖 RPA Utilitarios: Ingestión, Conciliación y Reportes Financieros

Este repositorio contiene las herramientas de automatización RPA (Selenium con evasión de WAF), ETL y conciliación de datos de consumos de vehículos utilitarios (Combustible, Peajes y Mantenimientos) integrados con **Google BigQuery** y **Google Cloud Storage (GCS)**.

---

## 📂 Clasificación de Scripts: Funcionales vs. Temporales

Para facilitar el mantenimiento del repositorio, aquí se detallan cuáles scripts son **operativos (de uso recurrente)** y cuáles son **utilidades de backfill o diagnóstico único**:

### 1. 🚀 Scripts Operativos y Recurrentes (Producción)
Estos scripts forman parte del ciclo mensual o análisis regular y están diseñados para ser ejecutados de forma recurrente.

| Script | Ubicación | Descripción | Comando de Ejecución |
| :--- | :--- | :--- | :--- |
| **Orquestador Maestro** | `orquestador_maestro.py` | Corre secuencialmente la extracción RPA de Pase, Supramax y Edenred para el **mes anterior** al actual, realiza la ingesta en BigQuery y genera un log de ejecución local. | `python orquestador_maestro.py` |
| **Conciliador de ECOs 2025 vs 2026** | `scripts/conciliacion_ecos_2025_vs_2026.py` | Genera el reporte comparativo estético de Excel (`comparativa_ecos_2025_vs_2026.xlsx`) con 7 hojas incluyendo desglose de sistemas, resumen YTD y validación completa de catálogo. | `python scripts/conciliacion_ecos_2025_vs_2026.py --mes 1` |

### 2. 🗄️ Scripts de Backfill y Carga de Históricos (De una Sola Vez / Utilidades)
Scripts desarrollados para poblar la base de datos con históricos de 2025/2026 o realizar migraciones. Solo se corren si se necesita reconstruir los datos desde cero.

| Script | Ubicación | Descripción | Comando de Ejecución |
| :--- | :--- | :--- | :--- |
| **Recuperar Pase 2025** | `scripts/recuperar_pase_2025.py` | Descarga todos los CSV históricos del 2025 de Pase desde el bucket de respaldos en GCS, corrige el column-shifting y los sube limpios a BigQuery. | `python scripts/recuperar_pase_2025.py` |
| **Recuperar Edenred 2025** | `scripts/recuperar_edenred_2025.py` | Descarga e ingesta los reportes históricos del 2025 de Edenred desde GCS, forzando tipos y resolviendo traslapes de fechas. | `python scripts/recuperar_edenred_2025.py` |
| **Backfill Histórico 2026** | `scripts/backfill_historico.py` | Permite recargar meses específicos del 2026 para cualquiera de los tres sistemas operativos (`--pase`, `--supramax`, `--edenred`). | `python scripts/backfill_historico.py --pase --mes 2026-01` |
| **Unificar Respaldos** | `scripts/unificar_respaldos.py` | Descarga todos los archivos del bucket de GCS y genera archivos consolidados locales de Edenred, Supramax y Pase. | `python scripts/unificar_respaldos.py` |
| **Migrar a GCS** | `scripts/migrar_respaldos_a_gcs.py` | Sube archivos de respaldo locales de forma masiva a la estructura de carpetas en Google Cloud Storage. | `python scripts/migrar_respaldos_a_gcs.py` |


---

## 🛠️ Guía de Ejecución Rápida

### 1. Entorno y Configuración
Asegúrate de que tu entorno virtual esté activo y las dependencias instaladas:
```bash
source .venv/bin/activate
pip install -r requirements.txt
```

Tu archivo `.env` en la raíz del proyecto debe tener las credenciales correctas:
```ini
GCP_PROJECT_ID="nombre-de-proyecto"
GCP_BUCKET_RESPALDOS="nombre-de-bucket-respaldos"
BQ_DATASET="rpa_utilitarios"
BQ_TABLE="consumos_flota"

EDENRED_USER="usuario"
EDENRED_PASSWORD="password"
EDENRED_URL="https://..."

PASE_USER="usuario"
PASE_PASSWORD="password"
PASE_URL="https://..."

SUPRAMAX_URL="https://..."
SUPRAMAX_CREDENTIALS='[{"Usuario": "user1", "Contraseña": "pass1", "Empresa": "EMPRESA1"}, ...]'

TWOCAPTCHA_API_KEY="tu_api_key_de_2captcha"
DESTINATARIO_EMAIL="correo@empresa.com"
```

---

### 2. Cómo correr los Procesos Operativos

#### A. Ejecución Mensual Automática
Para correr el RPA completo (Pase ➡️ Supramax ➡️ Edenred) para el mes anterior completo:
```bash
python orquestador_maestro.py
```
* **Salida de Logs**: El orquestador redirecciona automáticamente toda la salida de pantalla y errores a un archivo dentro de la carpeta `logs_orquestador/` (ej. `logs_orquestador/orquestador_20260602_171500.txt`).
* **Capturas en caso de error**: Si alguna página da error o timeout, se guardará un `.png` y un `.html` en `descargas_temporales/`.

#### B. Generar el Reporte de Conciliación
Para generar el libro de Excel comparativo con 2025 para un mes en particular (ej. Enero = `1`, Mayo = `5`):
```bash
python scripts/conciliacion_ecos_2025_vs_2026.py --mes 1
```
* **Salida**: Crea o actualiza el archivo `comparativa_ecos_2025_vs_2026.xlsx` en la raíz del proyecto.

---

### 3. Cómo correr Scripts de Soporte y Backfill (Ejemplos)

* **Cargar todo el histórico de Pase de 2025:**
  ```bash
  python scripts/recuperar_pase_2025.py
  ```
* **Cargar todo el histórico de Edenred de 2025:**
  ```bash
  python scripts/recuperar_edenred_2025.py
  ```
* **Hacer backfill de Febrero 2026 en Supramax:**
  ```bash
  python scripts/backfill_historico.py --supramax --mes 2026-02
  ```
* **Unificar respaldos en archivos locales consolidados:**
  ```bash
  python scripts/unificar_respaldos.py
  ```

---

## 🔒 Seguridad e Ignorados
El archivo `.gitignore` está configurado para no subir datos sensibles ni basura al repositorio de código:
* **No versionar**: `.env`, perfiles de Chrome (`chrome_profile/`, `edenred_profile/`), archivos temporales descargados (`descargas_temporales/`), ni los archivos Excel generados (`*.xlsx`, `*.csv`).
