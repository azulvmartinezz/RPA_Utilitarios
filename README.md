# RPA_Utilitarios

Procesos que alimentan a Control Vehicular desde fuera del backend: portales
sin API (Supramax, Libro Azul, Pase), APIs de proveedores (FleetUp, Ticket Car)
y cargas a BigQuery.

Organizado por **cómo se dispara** cada proceso, para migrarlos a Cloud Run
Jobs (plan completo en [MIGRACION_JOBS_NUBE.md](MIGRACION_JOBS_NUBE.md)).

## Estructura

| Carpeta | Qué hay | En la nube |
|---|---|---|
| [`jobs_programados/`](jobs_programados) | Corren por horario | Cloud Run Job + Cloud Scheduler |
| [`jobs_por_evento/`](jobs_por_evento) | Atienden una cola que llena el backend cuando alguien aprieta un botón | Cloud Run Job que el backend dispara al encolar |
| [`shared/`](shared) | Conexión a la base, credenciales, login de Supramax, pool de Chrome | Va dentro de la imagen |
| [`bigquery/`](bigquery) | Ingesta a BigQuery y DDL de las tablas | `bq_ingestion.py` va en la imagen; el DDL se corre a mano |
| [`scripts/`](scripts), [`scripts_onedrive/`](scripts_onedrive), [`scratch/`](scratch) | Cargas históricas, conciliaciones y arreglos de una vez | No se programan |
| [`launchd/`](launchd) | Copia de los agentes de la Mac | Lo reemplaza Cloud Scheduler |

En la raíz quedan `gcs_uploader.py` y `pase_utils.py` (soporte de Pase), el
`orquestador_maestro.py` para correr a mano los consumos a BigQuery, y los
`.ps1` del servidor Windows.

### `jobs_programados/`

| Script | Qué hace | Horario hoy |
|---|---|---|
| `supramax_saldos_rpa.py` | Saldos de las tarjetas de Supramax (Chrome) → `supramax_saldos` | cada 30 min (Mac) |
| `fleetup_viajes.py` | Viajes de FleetUp por API → BigQuery `flota.viajes` | diario 3:00 (Mac) |
| `supramax_consumos_http.py` | Consumos de Supramax por HTTP → BigQuery | nocturno (Windows) |
| `pase_rpa.py` | Cortes de peaje de Pase (Chrome) → BigQuery | por corte (Windows) |
| `ticketcar_api.py` | Consumo de Ticket Car por SOAP → BigQuery `flota.ticketcar_transacciones` | **ninguno**: solo a mano |
| `pase_cruces.py` | Detalle de casetas desde los CSV de GCS → `flota.pase_cruces` | **ninguno**: solo a mano |

### `jobs_por_evento/`

| Script | Cola | Lo usa |
|---|---|---|
| `supramax_dispersion_rpa.py` | `supramax_dispersion_jobs` | Recarga de Supramax (**dinero real**) |
| `supramax_actualizar_bajo_demanda.py` | `supramax_refresh_jobs` | Botón «Actualizar Supramax» |
| `libroazul_actualizar_bajo_demanda.py` (+ `libroazul_precio_rpa.py`) | `libroazul_valuacion_jobs` | Valuar una unidad |
| `libroazul_versiones_actualizar_bajo_demanda.py` (+ `libroazul_versiones_rpa.py`) | `libroazul_versiones_jobs` | Buscar versiones |

Cada uno toma lo pendiente de su cola, lo procesa y termina. Hoy launchd los
corre cada 5 a 90 s. En la nube, el backend los dispara al encolar.

Los jobs de Node (espejo de SIPP, odómetros, resumen de preventivos) viven en
`control-vehicular/src/database/`, porque reusan los servicios del backend.

## Correr un proceso

```bash
source .venv/bin/activate
pip install -r requirements.txt

python -m jobs_programados.fleetup_viajes --dias 7
python -m jobs_programados.ticketcar_api --dias 7
python -m jobs_por_evento.supramax_dispersion_rpa
```

Siempre con `python -m` desde la raíz del repo, para que resuelvan `shared/`.

La conexión a la base sale del secreto `SECRETO_CONTROL_VEHICULAR` (ver
`shared/db.py`). Para apuntar a otro ambiente, cambia esa variable. Las
credenciales de Supramax están cifradas en la base (`shared/credenciales.py`);
las de Pase y Libro Azul, en el `.env` (ver `.env.example`).

## Logs

- Agentes de la Mac: `logs_orquestador/launchd_*.log`.
- Servidor Windows: `logs_tareas/`.
- Capturas de pantalla y HTML cuando un portal falla: `descargas_temporales/`.

## Retirado el 2026-11-05

Están en el historial de git:
- Los scrapers de Edenred y Ticket Car por portal, y el extractor por correo (O365). Los reemplazó `ticketcar_api.py`.
- El scraper de Supramax con Chrome. Lo reemplazó `supramax_consumos_http.py`.
- El scraper de FleetUp. Lo reemplazó `fleetup_viajes.py`.
- La app de escritorio (`ui_flet/`, `ejecutable/`, `app.exe`). Solo corría esos flujos.
- Las envolturas `.sh`: launchd ya llama al `python` del `.venv` directo.
