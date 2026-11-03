# Migración de los jobs de Control Vehicular a la nube

Inventario de los procesos programados que hoy corren fuera del backend, y qué
hace falta para moverlos a GCP como un conjunto de jobs.

> Estado al 2026-11-03. Hoy 9 procesos corren por **launchd en la Mac de
> Anibal** y 2 en el **servidor Windows** (Programador de tareas). Los de la
> Mac se conectan a la base de **dev** (`controlvehicular_dev`), así que test y
> producción no tienen quién atienda sus colas.

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

**BigQuery es compartido por todos los ambientes** (jobs 6, 10 y 11). Deben
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
2. **Jobs 9, 7 y 8** (Node, sin navegador), con la imagen del backend.
3. **Job 5 (saldos Supramax)**, para validar Chrome headless y el egreso por Cloud NAT contra el portal.
4. **Jobs 10 y 11** (los del servidor Windows), para apagar las tareas programadas de allá.
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
