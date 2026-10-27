#!/bin/zsh
#
# Polling de la cola de dispersión de Supramax (`supramax_dispersion_jobs`),
# cada 1-2 minutos.
#
# `POST /solicitud/recargar-supramax` del backend solo encola el job y
# contesta "encolado" de inmediato -- esto es lo que de verdad entra al
# portal y aplica la recarga. Un intervalo corto importa aquí más que en
# saldos: quien pidió la recarga está esperando ver su tarjeta cargada.
#
# El script en sí (`scrapers/supramax_dispersion_rpa.py`) es una sola pasada:
# recupera huérfanos, toma un lote de jobs PENDIENTE, los dispersa, termina.
# Este cron es lo que lo repite. Migrar a Cloud Run después es cambiar quién
# dispara esta misma invocación, no el script.
#
set -u

RAIZ="/Users/anibalfuentes/Documents/VSC/Control_Vehicular/RPA_Utilitarios"
LOGS="$RAIZ/logs_orquestador"
FECHA=$(date +%Y-%m-%d)

mkdir -p "$LOGS"
cd "$RAIZ" || exit 1

"$RAIZ/.venv/bin/python" -m scrapers.supramax_dispersion_rpa \
  >> "$LOGS/dispersion_supramax_$FECHA.log" 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] salida=$?" >> "$LOGS/dispersion_supramax_$FECHA.log"
