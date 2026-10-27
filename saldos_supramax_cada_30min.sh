#!/bin/zsh
#
# Refresco de saldos de Supramax (`supramax_saldos`), cada 30 minutos.
#
# Va aparte del orquestador maestro, igual que `ingesta_viajes_diaria.sh`: el
# orquestador corre los scrapers de Selenium de madrugada, sin escritorio, y
# este necesita correr cada 30 min durante el día para que "Saldos y
# recargas" no muestre un número viejo mientras alguien decide una recarga.
#
# El script en sí (`scrapers/supramax_saldos_rpa.py`) es una sola pasada por
# todas las cuentas de SUPRAMAX_CREDENTIALS -- este cron es lo que lo repite
# cada 30 min. Migrar a Cloud Run después es cambiar quién dispara esta
# misma invocación (Cloud Scheduler en vez de cron/launchd), no el script.
#
set -u

RAIZ="/Users/anibalfuentes/Documents/VSC/Control_Vehicular/RPA_Utilitarios"
LOGS="$RAIZ/logs_orquestador"
FECHA=$(date +%Y-%m-%d)

mkdir -p "$LOGS"
cd "$RAIZ" || exit 1

"$RAIZ/.venv/bin/python" -m scrapers.supramax_saldos_rpa \
  >> "$LOGS/saldos_supramax_$FECHA.log" 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] salida=$?" >> "$LOGS/saldos_supramax_$FECHA.log"
