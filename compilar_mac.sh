#!/bin/bash
echo "==================================================="
echo "COMPILANDO RPA UTILITARIOS PARA MAC (.APP)"
echo "==================================================="
.venv/bin/pyinstaller --noconsole --onefile \
  --add-data "scrapers:scrapers" \
  --add-data "extractors:extractors" \
  --add-data "bigquery:bigquery" \
  --add-data "gcs_uploader.py:." \
  --add-data "scripts:scripts" \
  --add-data "scripts_onedrive:scripts_onedrive" \
  --collect-all google.cloud.bigquery \
  --collect-all google.cloud.storage \
  --collect-all google.api_core \
  --collect-all google.auth \
  --collect-all google.cloud \
  --distpath RPA_Utilitarios_Ejecutable_Mac \
  --clean ejecutable/app.py
echo "==================================================="
echo "COMPILACION COMPLETADA!"
