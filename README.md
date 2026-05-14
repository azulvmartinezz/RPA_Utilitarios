# RPA Utilitarios

Automatización para descargar, respaldar, normalizar y conciliar consumos de utilitarios desde portales de combustible/peajes y BigQuery.

## Estructura

- `orquestador_maestro.py`: entrada principal mensual para ejecutar los flujos operativos.
- `scrapers/`: automatizaciones Selenium de Pase, Supramax y Edenred.
- `extractors/`: procesamiento posterior a descargas/correos.
- `bigquery/`: limpieza, carga y setup de tabla en BigQuery.
- `gcs_uploader.py`: respaldo de archivos fuente en Google Cloud Storage.
- `scripts/`: tareas manuales de soporte:
  - `backfill_historico.py`: recarga histórica por sistema/mes.
  - `conciliar_contra_manual.py`: conciliación contra archivo manual del departamento.
  - `unificar_respaldos.py`: genera consolidados desde el bucket.
  - `migrar_respaldos_a_gcs.py`: migración puntual de respaldos locales antiguos.
- `DOCUMENTACION_RPA.md`: documentación funcional más detallada.

## Configuración

1. Copiar `.env.example` a `.env`.
2. Llenar las variables locales. No subir `.env`.
3. Autenticarse con Google Cloud mediante Application Default Credentials o el método configurado localmente.

## Comandos Útiles

Ejecutar flujo mensual completo:

```bash
python orquestador_maestro.py
```

Backfill de un sistema/mes:

```bash
python scripts/backfill_historico.py --edenred --mes 2026-01
python scripts/backfill_historico.py --supramax --mes 2026-01
python scripts/backfill_historico.py --pase --mes 2026-01
```

Generar consolidados desde GCS:

```bash
python scripts/unificar_respaldos.py
```

Conciliar contra archivo manual:

```bash
python scripts/conciliar_contra_manual.py
```

## Seguridad

No versionar datos operativos, credenciales, tokens, perfiles de navegador, logs ni reportes generados. `.gitignore` bloquea esos artefactos por defecto.
