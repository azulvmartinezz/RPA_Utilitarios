# 🤖 RPA Utilitarios: Automatización de Portales Financieros

## 📖 Resumen del Proyecto
Este proyecto consiste en un conjunto de scripts de RPA (Robotic Process Automation) construidos en Python. Su objetivo es automatizar la extracción de reportes financieros y consumos de combustible/peajes desde tres portales corporativos distintos. El flujo elimina la necesidad de intervención manual mensual, manejando múltiples empresas filiales por portal.

---

## 🛠️ Stack Tecnológico y Entorno
* **Lenguaje:** Python 3.9+
* **Automatización Web:** `selenium`
* **Evasión de WAF/Anti-Bot:** `undetected-chromedriver` (Manejo de Radware Bot Manager)
* **Resolución de Captchas:** `twocaptcha` (API Externa)
* **Gestión de Entorno:** `python-dotenv`
* **Seguridad:** `.env` local (ignorado en Git) para credenciales y tokens.

---

## 📂 Módulos y Lógica de Negocio

### 1. `pase_rpa.py` (Portal Pase - Peajes)
* **Reto:** El sitio cuenta con protección WAF estricta.
* **Solución:** Uso de `undetected-chromedriver` y preservación de cookies a través de la carpeta `chrome_profile/` (Ignorada en Git).
* **Flujo:**
  1. Resuelve Google ReCaptcha V2 inyectando el token mediante JS.
  2. Itera sobre un menú desplegable (Material-UI) con 6 empresas distintas.
  3. **Inteligencia:** Detecta dinámicamente si el ciclo de facturación es de un *mes calendario perfecto* (descarga 1 archivo) o *desfasado* (descarga 2 archivos para completar el mes).
  4. Maneja cuentas sin movimiento cerrando sesión rápidamente.

### 2. `supramax_rpa.py` (Portal Supramax - Combustible)
* **Reto:** Iterar sobre 21 cuentas distintas sin dejar sesiones colgadas ni guardar credenciales en el código fuente.
* **Solución:** Se diseñó un JSON matricial en la variable `SUPRAMAX_CREDENTIALS` dentro del `.env`.
* **Flujo:**
  1. Extrae y formatea fechas dinámicas (Ej. Del día 01 al último día del mes anterior) usando la librería `datetime`.
  2. Espera de manera explícita (hasta 2 minutos) a que la base de datos de Supramax procese la petición.
  3. **Inteligencia:** Si lee `"No se encontraron registros"`, salta inmediatamente el ciclo de descarga, hace *logout* e inicia con la siguiente empresa.

### 3. `edenred_rpa.py` (Portal Edenred - Tarjetas)
* **Reto:** El portal requiere que se seleccione un periodo desde un *dropdown* revuelto y se envíe el reporte por correo.
* **Solución:** 
  1. Navega por menús anidados de Reportes Financieros.
  2. Calcula el string del mes anterior (Ej. `03/2026`) y hace *match* contra las opciones del `<select>` de HTML.
  3. Ingresa un `DESTINATARIO_EMAIL` dinámico extraído del `.env` y confirma el envío.

---

## 🔐 Manejo de Secretos (Variables de Entorno)
Cualquier futuro desarrollo debe adherirse a esta estructura. El archivo `.env` **nunca** debe ser subido a repositorios.

Variables actuales esperadas en `.env`:
* `EDENRED_USER` / `EDENRED_PASSWORD`
* `PASE_USER` / `PASE_PASSWORD`
* `TWOCAPTCHA_API_KEY`
* `DESTINATARIO_EMAIL`
* `SUPRAMAX_CREDENTIALS` (Array JSON con `Empresa`, `Usuario`, `Contraseña`)

---

## 🚀 Siguientes Pasos (Roadmap)
1. **Data Engineering:** Crear un script de Python (con `pandas`) que actúe como "ETL". Debe leer los CSV y XLS descargados, normalizar sus nombres de columnas (Litros, Importe, Concepto, Placa) e inyectarlos a una tabla central en **Google BigQuery**.
2. **Orquestación:** Configurar un Cron Job o contenedor (Docker/Cloud Run) para ejecutar automáticamente los 3 scripts los primeros días de cada mes.
