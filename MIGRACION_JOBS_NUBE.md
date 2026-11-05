# Migración de los jobs de Control Vehicular a la nube

Inventario de los procesos programados que hoy corren fuera del backend, y qué
hace falta para moverlos a GCP como un conjunto de jobs.

> Estado al 2026-11-05. Hoy 9 procesos corren por **launchd en la Mac de
> Anibal** y 2 en el **servidor Windows** (Programador de tareas). Los de la
> Mac se conectan a la base de **dev** (`controlvehicular_dev`), así que test y
> producción no tienen quién atienda sus colas.
>
> **Hueco detectado el 2026-11-05:** el consumo de **Ticket Car** a BigQuery
> (`extractors/ticketcar_api.py`) y el detalle de **casetas de Pase**
> (`extractors/pase_cruces.py`) no están programados en ningún lado. Solo
> corren cuando alguien lanza `orquestador_maestro.py` a mano. Se agregan abajo
> como jobs 13 y 14.
>
> El inventario de **todos los archivos** (cuáles son jobs, cuáles soporte,
> cuáles retirados) está en la [sección 7](#7-inventario-de-archivos).

---

## 1. Cómo está armado hoy

```
 Backend (Cloud Run)                 Base de datos del ambiente            Procesos externos
 ───────────────────                 ───────────────────────────           ──────────────────
 «Dispersar Supramax»   ──INSERT──►  supramax_dispersion_jobs    ◄──poll── supramax_dispersion_rpa   (cada 90 s)
 «Actualizar Supramax»  ──INSERT──►  supramax_refresh_jobs       ◄──poll── supramax_actualizar_bajo_demanda (60 s)
 «Valuar» Libro Azul    ──INSERT──►  libroazul_valuacion_jobs    ◄──poll── libroazul_actualizar_bajo_demanda (60 s)
 «Versiones» Libro Azul ──INSERT──►  libroazul_versiones_jobs    ◄──poll── libroazul_versiones_..._bajo_demanda (5 s)
                                     supramax_saldos             ◄──────── supramax_saldos_rpa        (cada 30 min)
                                     sipp_requisiciones / _ordenes ◄────── sincronizar-requisiciones-sipp (hora / domingo)
                                     unidades.nu_odometro        ◄──────── sincronizar-odometros-fleetup (cada hora)
 BigQuery flota.viajes                                           ◄──────── fleetup_viajes             (3:00 a. m.)
 BigQuery rpa_utilitarios.*                                      ◄──────── supramax_consumos_http / pase_rpa (Windows)
```

**El backend nunca abre los portales.** Solo escribe un renglón en una tabla
`*_jobs`. Quien lo procesa es un proceso externo que **revisa la tabla cada
pocos segundos (polling)** y escribe ahí el avance y el resultado. La pantalla
lee ese avance.

**Cada proceso usa la base del secreto que lee:**
- Los de Python leen `SECRETO_CONTROL_VEHICULAR` (por omisión
  `projects/herramientas-desarrollo/secrets/control-vehicular-localhost-secrets/versions/latest`)
  en `RPA_Utilitarios/shared/db.py`.
- Los de Node usan el mismo mecanismo del backend: `GOOGLE_CLOUD_SECRET_NAME`,
  en `control-vehicular/src/shared/services/secrets.manager.ts`.

**Para apuntar un job a otro ambiente basta con cambiar esa variable.** No hay
que tocar código.

---

## 2. Inventario

### 2.1 Procesos de cola (bajo demanda)

Los dispara una persona desde la pantalla, y alguien está esperando el
resultado. Hoy se resuelven por polling.

| # | Job | Código | Tecnología | Cola | Frecuencia hoy | Duración típica |
|---|---|---|---|---|---|---|
| 1 | **Dispersión Supramax** | `RPA_Utilitarios/scrapers/supramax_dispersion_rpa.py` | Python + **Selenium/Chrome** | `supramax_dispersion_jobs` | cada 90 s | ~1 min por job (login + captura + verificación) |
| 2 | **Actualizar saldos Supramax** (botón) | `RPA_Utilitarios/scrapers/supramax_actualizar_bajo_demanda.py` → `supramax_saldos_rpa.ejecutar_actualizacion_saldos` | Python + **Selenium/Chrome** (pool en paralelo) | `supramax_refresh_jobs`, avance en `supramax_refresh_avance` | cada 60 s | ~6 min (20 cuentas) |
| 3 | **Valuación Libro Azul** | `RPA_Utilitarios/scrapers/libroazul_actualizar_bajo_demanda.py` → `libroazul_precio_rpa.py` | Python + **undetected_chromedriver** | `libroazul_valuacion_jobs` | cada 60 s | 1–2 min |
| 4 | **Versiones Libro Azul** | `RPA_Utilitarios/scrapers/libroazul_versiones_actualizar_bajo_demanda.py` → `libroazul_versiones_rpa.py` | Python + **undetected_chromedriver** | `libroazul_versiones_jobs` | **cada 5 s** | < 1 min |

**Dónde se encolan, en el backend (`control-vehicular`):**
- Supramax: `src/modules/solicitud/services/supramax.service.ts`
  (`encolarDispersion`, `encolarDispersionDeSolicitud`, `cerrarJobsResueltos`).
- Refresco de saldos: el mismo módulo (`supramax_refresh_jobs`).
- Libro Azul: `src/modules/libro-azul/services/libroazul-refresh.service.ts`
  (`libroazul_valuacion_jobs`, esquema `db/esquema/45_*`) y
  `libroazul-versiones.service.ts` (`libroazul_versiones_jobs`, esquema `46_*`).

### 2.2 Procesos programados (cron)

| # | Job | Código | Tecnología | Escribe en | Frecuencia hoy | Duración típica |
|---|---|---|---|---|---|---|
| 5 | **Saldos Supramax** | `RPA_Utilitarios/scrapers/supramax_saldos_rpa.py` | Python + **Selenium/Chrome** (pool) | `supramax_saldos`, `tarjetas`, `colaboradores_tarjetas` | cada 30 min | ~6 min |
| 6 | **Ingesta de viajes FleetUp** | `RPA_Utilitarios/extractors/fleetup_viajes.py --dias 7` | Python + `requests` (API) | BigQuery `innovacion-futuro.flota.viajes` (MERGE) | diario 3:00 | 10–15 min (294 dispositivos, pausa de 0.5 s) |
| 7 | **Espejo de requisiciones SIPP** | `control-vehicular/src/database/sincronizar-requisiciones-sipp.ts --ambiente=produccion` | Node/Nest (API de SIPP) + desglose con Vertex AI | `sipp_requisiciones`, `sipp_ordenes_insumos`, `requisiciones_desglose*` | cada hora | variable (bloques de 40 min) |
| 8 | **Espejo SIPP semanal** | el mismo script con `--desde=2025-01-01 --sin-ordenes` | Node/Nest | `sipp_requisiciones` | domingo 3:00 | ~11 min |
| 9 | **Odómetros FleetUp** | `control-vehicular/src/database/sincronizar-odometros-fleetup.ts` | Node/Nest (API) | `unidades.nu_odometro` | cada hora | < 1 min |
| 10 | **Consumos Supramax** *(servidor Windows)* | `RPA_Utilitarios/extractors/supramax_consumos_http.py --dias 10`, lanzado por `ejecutar_supramax_incremental.ps1` | Python + `requests` (sin Chrome) | BigQuery `rpa_utilitarios.consumos_flota` (MERGE) | nocturno | minutos |
| 11 | **Cortes de peaje (Pase)** *(servidor Windows)* | `RPA_Utilitarios/scrapers/pase_rpa.py --clientes 16072 --posiciones 1 --hasta-nuevo-corte`, lanzado por `ejecutar_pase_cortes.ps1` | Python + **Selenium/Chrome** | BigQuery `flota.pase_cruces` | por corte | minutos |
| 12 | **Resumen de preventivos** *(nuevo, aún sin instalar)* | `control-vehicular/src/database/resumen-preventivos.ts` (`pnpm preventivos:resumen`) | Node/Nest (MailGun + Web Push) | correo y push, solo lectura en la base | lunes 8:00 | < 1 min |
| 13 | **Consumo Ticket Car** *(hoy solo a mano)* | `RPA_Utilitarios/extractors/ticketcar_api.py` (`main_dias(7)`, desde el orquestador) | Python + SOAP de Edenred (sin Chrome) | BigQuery `flota.ticketcar_transacciones` | **ninguna** | minutos |
| 14 | **Detalle de casetas Pase** *(hoy solo a mano)* | `RPA_Utilitarios/extractors/pase_cruces.py` (después de `pase_rpa`, en el orquestador) | Python: lee los CSV de GCS | BigQuery `flota.pase_cruces` (WRITE_TRUNCATE) | **ninguna** | minutos |

Los plists de los agentes de la Mac están en `~/Library/LaunchAgents/mx.petroil.controlvehicular.*.plist`. Los versionados:
- `control-vehicular/db/utilidades/`: odómetros, SIPP horario y semanal, resumen de preventivos;
- `RPA_Utilitarios/launchd/`: ingesta de viajes.

Logs actuales: `RPA_Utilitarios/logs_orquestador/launchd_*.log` (Mac) y
`RPA_Utilitarios/logs_tareas/` (Windows).

---

## 3. Por qué Cloud Run Jobs y no Cloud Functions

| | Cloud Functions | **Cloud Run Jobs** (recomendado) |
|---|---|---|
| Chrome/Selenium (jobs 1–5 y 11) | No: no hay forma práctica de meter Chrome en el runtime | Sí: imagen propia con Chrome |
| Duración | Hasta 9 min (gen1) o 60 min por HTTP (gen2) | Hasta 24 h por tarea |
| Reintentos y paralelismo | Por invocación | `--max-retries`, `--tasks`, `--parallelism` |
| Disparo por cron | Cloud Scheduler → HTTP | Cloud Scheduler → `jobs.run` |
| Disparo bajo demanda | HTTP | API `jobs.run` desde el backend, o Pub/Sub/Eventarc → Workflows |
| Mismo código para todo | Se partiría en dos plataformas | Una imagen por repo, un job por comando |

Cloud Functions solo serviría para los jobs sin navegador (6, 9, 10, 12), y
partiría el conjunto en dos plataformas. **Recomendación: todo como Cloud Run
Jobs**, con dos imágenes:

1. **`rpa-utilitarios`**: Python 3.12 + Chrome estable + `requirements.txt`. Un job por comando (`python -m scrapers.…` o `python -m extractors.…`).
2. **`control-vehicular`**: la misma imagen del backend, con otro comando: `node dist/database/<script>.js` (compilado) en lugar del servidor.

---

## 4. Plan de migración por job

### 4.1 Lo que aplica a todos

- **Un job por ambiente.** El ambiente se elige con variables:
  - Python: `SECRETO_CONTROL_VEHICULAR=projects/<proyecto>/secrets/<secreto-del-ambiente>/versions/latest`
  - Node: `GOOGLE_CLOUD_SECRET_PROJECT_ID` y `GOOGLE_CLOUD_SECRET_NAME` del ambiente.
- **Cuenta de servicio del job**, con:
  - `Secret Manager Secret Accessor` sobre el secreto del ambiente;
  - acceso de red a la base (`db.petroil.dev:5434` en dev; la del ambiente en los demás);
  - `BigQuery Job User` en `innovacion-futuro` y `BigQuery Data Editor` en `flota` y `rpa_utilitarios` (jobs 6, 10 y 11);
  - `Vertex AI User` en `innovacion-futuro` (job 7, por el desglose).
- **Credenciales de los portales:**
  - **Supramax:** cifradas en la tabla `proveedores_credenciales`. Para leerlas hace falta la llave `CREDENCIALES_LLAVE` del ambiente (`RPA_Utilitarios/shared/credenciales.py`). Hoy hay un respaldo en `SUPRAMAX_CREDENTIALS` del `.env` de la Mac, **que no debe viajar a la nube**: hay que capturarlas en *Configuración › Credenciales* de cada ambiente.
  - **Libro Azul y Pase:** hoy en el `.env` local (`LIBROAZUL_*`, `PASE_*`). Pasarlas al secreto del ambiente.
- **IP de salida fija (Cloud NAT con IP estática).** Ticket Car ya exige una IP registrada (`TICKETCAR_IP`, en el backend). Conviene que los jobs de portales salgan por una IP fija y conocida, por si Supramax o Libro Azul la piden o bloquean rangos de nube (ver 4.3).
- **Logs:** cada job ya escribe en la salida estándar. En Cloud Run caen en Cloud Logging sin cambios. Conviene una alerta de log-based metric por ejecuciones fallidas: las fallas silenciosas de launchd son justo lo que esto quiere evitar.
- **No correr dos instancias a la vez** de los jobs que escriben (los `.ps1` de Windows ya lo cuidan). En Cloud Run Jobs: `--parallelism=1` y un Scheduler que no se encime con la duración.

### 4.2 Jobs programados (cron) — migración directa

| Job | Comando del contenedor | Cloud Scheduler | Notas |
|---|---|---|---|
| 5 Saldos Supramax | `python -m scrapers.supramax_saldos_rpa` | `*/30 * * * *` | Chrome headless (`shared/pool_navegadores.py`). 2 GiB de memoria por el pool |
| 6 Viajes FleetUp | `python -m extractors.fleetup_viajes --dias 7` | `0 3 * * *` (America/Mazatlan) | FleetUp responde 429 si dos corridas coinciden: un solo job para todos los ambientes, porque BigQuery es compartido |
| 7 SIPP horario | `node dist/database/sincronizar-requisiciones-sipp.js --ambiente=produccion` | `0 * * * *` | Necesita `SIPP_PRODUCCION_*` en el secreto |
| 8 SIPP semanal | mismo, con `--desde=2025-01-01 --sin-ordenes` | `0 3 * * 0` | No encimarlo con el horario |
| 9 Odómetros | `node dist/database/sincronizar-odometros-fleetup.js` | `0 * * * *` | |
| 10 Consumos Supramax | `python -m extractors.supramax_consumos_http --dias 10` | nocturno | Sin Chrome. Sale con 1 si fallan todas las cuentas |
| 11 Pase | `python -m scrapers.pase_rpa --clientes 16072 --posiciones 1 --hasta-nuevo-corte` | según cortes | Chrome |
| 12 Resumen preventivos | `node dist/database/resumen-preventivos.js` | `0 8 * * 1` | Solo producción: manda correos y push reales |
| 13 Consumo Ticket Car | `python -m extractors.ticketcar_api --dias 7` | `30 3 * * *` | Edenred exige la IP de salida registrada: debe salir por la misma IP fija que el backend (`TICKETCAR_IP`) |
| 14 Casetas Pase | `python -m extractors.pase_cruces` | después del 11 | Idempotente (relee todo GCS). Encadenarlo al 11, o programarlo una hora después |

**BigQuery es compartido por todos los ambientes** (jobs 6, 10, 11, 13 y 14). Deben
correr **una sola vez**, no uno por ambiente.

### 4.3 Jobs de cola (bajo demanda) — cambiar polling por disparo

Hoy cada uno revisa su tabla cada 5 a 90 s. En la nube eso serían miles de
ejecuciones vacías al día. Hay dos caminos:

**A. Disparo desde el backend (recomendado).** Al encolar (`INSERT` en la tabla
`*_jobs`), el backend llama a la API de Cloud Run Jobs (`jobs.run`) del job
correspondiente. El job toma lo pendiente de su tabla (la lógica de
`_tomar_jobs_pendientes` con `FOR UPDATE SKIP LOCKED` ya existe), lo procesa y
termina.
- Cambios en el backend: una llamada después de cada encolado.
  - En `supramax.service.ts`: `encolarDispersion`, `encolarDispersionDeSolicitud` y el refresco.
  - En el módulo de Libro Azul: valuación y versiones.
- Cambios en los scripts: ninguno; ya procesan la cola y salen.
- Latencia: lo que tarde en arrancar el contenedor (~10–30 s). Hoy son hasta 90 s.

**B. Scheduler cada minuto (mínimo de Cloud Scheduler).** Sin tocar el backend,
pero con ~1,440 ejecuciones al día por job, casi todas vacías. Sirve como
transición o como red de seguridad junto con A. Los jobs ya recuperan los
trabajos huérfanos: `_recuperar_huerfanos` en `supramax_dispersion_rpa.py`.

**Riesgos específicos:**
- **Supramax mueve dinero real** (job 1). Solo debe existir en **producción** (y en dev mientras se prueba). En test, la cola debe quedarse sin procesar a propósito, o hay que borrar las credenciales de Supramax en el ambiente de test.
- **Libro Azul tiene muro anti-bot** (jobs 3 y 4). Por eso usa `undetected_chromedriver` (ver comentario en `libroazul_precio_rpa.py`). En IPs de nube el muro puede reaparecer: hay que probarlo primero con la IP fija de Cloud NAT. Si falla, ese job tendría que quedarse en una máquina con IP de oficina (por ejemplo, el servidor Windows).
- **Chrome headless.** Supramax ya corre headless en el pool. Libro Azul abre Chrome visible con perfil nuevo: hay que validar que en headless pase el muro.

### 4.4 Lo que **no** se migra

- **Dispersión Ticket Car:** ya corre dentro del backend (API de Edenred, IP registrada `TICKETCAR_IP`). En test también funciona **con dinero real** si el secreto de test tiene las cuentas: conviene vaciar las cuentas de Ticket Car en el secreto de test.
- **`orquestador_maestro.py` y la UI de Flet:** herramientas de escritorio para correr a mano; no son jobs.

---

## 5. Orden sugerido

1. **Imagen `rpa-utilitarios` con Chrome**, y el **job 6 (viajes)** como prueba: sin navegador y sin dinero, y ya tiene MERGE idempotente.
   En el mismo paso, el **13 (consumo Ticket Car)**: hoy no lo corre nadie, así que es lo que más gana. Antes hay que confirmar que la IP de Cloud NAT sea la registrada con Edenred.
2. **Jobs 9, 7 y 8** (Node, sin navegador), con la imagen del backend.
3. **Job 5 (saldos Supramax)**, para validar Chrome headless y el egreso por Cloud NAT contra el portal.
4. **Jobs 10, 11 y 14** (los del servidor Windows, más el detalle de casetas que va después del 11), para apagar las tareas programadas de allá.
5. **Jobs de cola** con disparo desde el backend: primero el 2 (refresco, solo lectura), luego el 1 (dispersión, dinero real) y al final el 3 y 4 (Libro Azul, por el riesgo del anti-bot).
6. **Job 12 (resumen)** al pasar a producción.
7. **Apagar los agentes de la Mac** uno por uno conforme su job en la nube corra bien una semana: `launchctl bootout gui/$(id -u)/mx.petroil.controlvehicular.<nombre>`.

---

## 6. Referencias en el código

| Qué | Dónde |
|---|---|
| Conexión a la base y secreto (Python) | `RPA_Utilitarios/shared/db.py` |
| Credenciales cifradas de portales | `RPA_Utilitarios/shared/credenciales.py`, tabla `proveedores_credenciales` |
| Login de Supramax | `RPA_Utilitarios/shared/supramax_login.py` |
| Pool de Chrome (headless) | `RPA_Utilitarios/shared/pool_navegadores.py` |
| Secreto del backend y scripts Node | `control-vehicular/src/shared/services/secrets.manager.ts` |
| Encolado de Supramax | `control-vehicular/src/modules/solicitud/services/supramax.service.ts` |
| Esquemas de las colas | `control-vehicular/db/esquema/31_*`, `32_*`, `43_*`, `45_*`, `46_*`, `50_*` |
| Plists actuales | `~/Library/LaunchAgents/mx.petroil.controlvehicular.*.plist`; copias en `control-vehicular/db/utilidades/` y `RPA_Utilitarios/launchd/` |
| Tareas de Windows | `RPA_Utilitarios/ejecutar_supramax_incremental.ps1`, `ejecutar_pase_cortes.ps1` |
| Documentación general del RPA | `RPA_Utilitarios/DOCUMENTACION_RPA.md` |

---

## 7. Inventario de archivos

**No todo vive en `RPA_Utilitarios`.** Los jobs de Node (7, 8, 9 y 12) están en
`control-vehicular/src/database/`, porque reusan la conexión, el secreto y los
servicios de Nest. El backend **no tiene ningún `@Cron` ni `setInterval`**:
todo lo programado está en los dos lugares de abajo.

Destinos:
- **Job programado**: Cloud Run Job + Cloud Scheduler.
- **Job por evento**: Cloud Run Job que el backend dispara con `jobs.run` al encolar (sección 4.3-A).
- **Soporte**: librería que va dentro de la imagen; no es un job.
- **Manual**: se corre a mano cuando hace falta; no se programa.
- **Retirado**: nadie lo llama; se puede borrar.
- **Sobra en la nube**: lo reemplazan Cloud Scheduler y Cloud Run.

### 7.1 Por qué los de evento no van *dentro* del backend

Los cuatro jobs por evento (1–4) abren **Chrome**. Meter Chrome en la imagen
del backend la infla (~400 MB), y cada recarga ocuparía una instancia del API
durante minutos con riesgo de que Cloud Run la corte. Por eso la propuesta es
**disparo desde el backend, ejecución en un Cloud Run Job**. El backend ya hace
la mitad: encola. Solo falta la llamada a `jobs.run` después del `INSERT`.

La excepción posible es **Supramax**. El login y el reporte de consumos ya
funcionan por HTTP sin navegador (`supramax_consumos_http.py`, y el login se
probó con `requests`). Si la lectura de saldos (`intmain.php?nd=1&ad=crd`) y la
captura de la recarga también salen por HTTP, los jobs 1, 2 y 5 podrían
reescribirse en TypeScript **dentro del backend**, como ya pasa con la
dispersión de Ticket Car. Es un proyecto aparte: la dispersión mueve dinero
real y cada formulario del portal habría que probarlo a mano primero.
**Libro Azul (3 y 4) no tiene esa salida:** el muro anti-bot obliga a usar
Chrome.

### 7.2 `RPA_Utilitarios/scrapers/`

| Archivo | Destino | Job | Notas |
|---|---|---|---|
| `supramax_dispersion_rpa.py` | **Job por evento** | 1 | Dinero real. Solo prod (y dev mientras se prueba) |
| `supramax_actualizar_bajo_demanda.py` | **Job por evento** | 2 | Llama a `supramax_saldos_rpa.ejecutar_actualizacion_saldos` con avance |
| `libroazul_actualizar_bajo_demanda.py` | **Job por evento** | 3 | Usa `libroazul_precio_rpa.py` |
| `libroazul_versiones_actualizar_bajo_demanda.py` | **Job por evento** | 4 | Usa `libroazul_versiones_rpa.py` |
| `supramax_saldos_rpa.py` | **Job programado** | 5 | También es librería del job 2 |
| `pase_rpa.py` | **Job programado** | 11 | Hoy en Windows |
| `libroazul_precio_rpa.py` | Soporte | 3 | Lo invoca el job 3; también se puede correr solo |
| `libroazul_versiones_rpa.py` | Soporte | 4 | Lo invoca el job 4 |
| `supramax_reconocer_tags.py` | Manual | — | Homologa tags contra el portal. Primero en lectura, después `--aplicar` |
| `edenred_rpa.py` | Retirado | — | Portal viejo de Edenred (2026-08-20) |
| `ticketcar_rpa.py` | Retirado | — | Lo reemplazó `extractors/ticketcar_api.py` (2026-08-20) |
| `supramax_rpa.py` | Retirado | — | Lo reemplazó `extractors/supramax_consumos_http.py` |
| `fleetup_rpa.py` | Retirado | — | Lo reemplazó `extractors/fleetup_viajes.py` |

### 7.3 `RPA_Utilitarios/extractors/`

| Archivo | Destino | Job | Notas |
|---|---|---|---|
| `fleetup_viajes.py` | **Job programado** | 6 | Un solo job para todos los ambientes |
| `supramax_consumos_http.py` | **Job programado** | 10 | Hoy en Windows |
| `ticketcar_api.py` | **Job programado** | 13 | **Hoy no corre solo.** Necesita la IP registrada con Edenred |
| `pase_cruces.py` | **Job programado** | 14 | **Hoy no corre solo.** Va después del 11 |
| `edenred_extractor.py` | Retirado | — | Bajaba el reporte por correo (O365) y pedía ENTER en la terminal |

### 7.4 `RPA_Utilitarios/shared/`, `bigquery/` y raíz

| Archivo | Destino | Lo usan |
|---|---|---|
| `shared/db.py` | Soporte | Todos los jobs que tocan Postgres |
| `shared/credenciales.py` | Soporte | 1, 5, 10 (credenciales cifradas de Supramax) |
| `shared/supramax_login.py` | Soporte | 1, 2, 5 |
| `shared/pool_navegadores.py`, `shared/navegador.py` | Soporte | 1, 2, 3, 5, 11 |
| `shared/diagnostico.py`, `shared/numeros.py` | Soporte | 1, 3, 5 |
| `bigquery/bq_ingestion.py`, `pase_utils.py`, `gcs_uploader.py` | Soporte | 10, 11, 14 |
| `bigquery/ddl/*.sql`, `bigquery/setup_bigquery.py`, `bigquery/seed_casetas_ubicacion.py` | Manual | Crear tablas y catálogos de BigQuery una vez |
| `shared/auth_service.py`, `config_service.py`, `consolidation_service.py`, `date_ranges.py`, `ingest_capture.py`, `pipeline_runner.py` | Solo escritorio | Los usan `ui_flet/` y `ejecutable/`; no van en la imagen de jobs |
| `shared/logging_redirect.py` | Retirado | Nadie lo importa |
| `orquestador_maestro.py` | Manual | Corre en serie 11 + 14, 10, 6 y 13. En la nube lo reemplazan los jobs sueltos |
| `ui_flet/`, `ejecutable/app.py`, `RPA_Utilitarios_Ejecutable/app.exe`, `compilar_mac.sh`, `compilar_windows.bat` | Escritorio | Herramienta para correr a mano; no se migra |
| `html_Selector_Informes_Fleetup.html` | Retirado | Apoyo del scraper de FleetUp |

### 7.5 Envolturas de programación (sobran en la nube)

| Archivo | Qué hace hoy | Reemplazo |
|---|---|---|
| `dispersion_supramax_polling.sh`, `supramax_actualizar_bajo_demanda.sh`, `saldos_supramax_cada_30min.sh`, `ingesta_viajes_diaria.sh` | Envolturas viejas; launchd ya llama al `python` del `.venv` directo (con `/bin/bash` daba exit 126) | Ninguno: borrar al migrar |
| `ejecutar_supramax_incremental.ps1`, `ejecutar_pase_cortes.ps1` | Programador de tareas de Windows, con candado contra corridas dobles | Cloud Scheduler + `--parallelism=1` |
| `launchd/*.plist`, `~/Library/LaunchAgents/mx.petroil.controlvehicular.*.plist`, `control-vehicular/db/utilidades/*.plist` | Agentes de la Mac | Cloud Scheduler; `launchctl bootout` al apagar cada uno |

### 7.6 `RPA_Utilitarios/scripts/`, `scripts_onedrive/` y `scratch/`: todos manuales

Cargas históricas y arreglos de una vez. Ninguno se programa ni va en la imagen:
`backfill_historico.py`, `recuperar_edenred_2025.py`, `recuperar_pase_2025.py`,
`deduplicar_bigquery.py`, `conciliacion_ecos_2025_vs_2026.py`,
`conciliar_contra_manual.py`, `reporte_tarjetas_por_eco.py`,
`consolidar_utilitarios.py`, `unificar_respaldos.py`, `migrar_respaldos_a_gcs.py`,
`auth_o365.py` y `consentir_o365.py` (del flujo retirado de correo de Edenred),
`scripts_onedrive/*` y `scratch/*`.

### 7.7 `control-vehicular/src/database/` (Node)

| Archivo | Destino | Job | Notas |
|---|---|---|---|
| `sincronizar-requisiciones-sipp.ts` | **Job programado** | 7 y 8 | Mismo script, distintos argumentos |
| `sincronizar-odometros-fleetup.ts` | **Job programado** | 9 | |
| `resumen-preventivos.ts` | **Job programado** | 12 | Solo producción |
| `desglose-requisiciones-sipp.ts` | Manual | — | El job 7 ya hace el desglose. Este es para reprocesar |
| `carga-sipp.ts` | Manual | — | Carga inicial del espejo de SIPP |
| `sincronizar-folios-ticketcar.ts` | Manual | — | Homologa folios de tarjetas con Edenred |
| `probar-dispersion-ticketcar.ts` | Manual | — | Prueba de la dispersión; **mueve dinero real** |
| `reindexar-rostros.ts` | Manual | — | Reconstruye el índice de Face ID |
| `backfill-polizas-locales.ts` | Manual | — | Carga de una vez |
| `analisis-tablas.ts`, `analisis-precios.ts`, `atipicos-mantenimientos.ts` | Manual | — | Reportes de análisis |
| `db/utilidades/sincronizar_mantenimientos_excel.py` | Manual | — | Carga desde Excel |

### 7.8 Fuera de los dos repos

- `Control_Vehicular/monitor_red/` (`monitor_red.sh`, `resumen.sh`): mide el internet de la oficina. Tiene que correr **en la oficina**; no se migra.
- `Control_Vehicular/policy-ocr-hybrid/`: servidor Flask de OCR de pólizas. Es un servicio, no un job. Si se sigue usando, iría a Cloud Run como servicio, aparte de esta migración.

### 7.9 Resumen

| Destino | Cuántos | Cuáles |
|---|---|---|
| Cloud Run Job + Scheduler | 10 | 5, 6, 7, 8, 9, 10, 11, 12, 13, 14 (el 7 y el 8 son el mismo script) |
| Cloud Run Job disparado por el backend | 4 | 1, 2, 3, 4 (después, opcional: 1, 2 y 5 reescritos por HTTP dentro del backend) |
| Retirados, para borrar | 9 | 4 scrapers, `edenred_extractor.py`, `logging_redirect.py`, `html_Selector_Informes_Fleetup.html`, `auth_o365.py`, `consentir_o365.py` |
| Envolturas que sobran | 6 + plists | 4 `.sh`, 2 `.ps1` |
