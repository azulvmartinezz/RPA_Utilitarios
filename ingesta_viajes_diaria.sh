#!/bin/zsh
#
# Ingesta diaria de viajes de FleetUp a BigQuery.
#
# Va aparte del orquestador maestro a propósito. Ese corre además los scrapers
# de Selenium, que abren Chrome y no sobreviven a una sesión sin escritorio;
# esto es solo API y sí aguanta correr solo de madrugada.
#
# Se piden 7 días y no 1: la carga hace MERGE por (dev_id, fh_inicio), así que
# reingerir no duplica, y una corrida perdida se recupera sola a la siguiente
# en vez de dejar un hueco que nadie nota. Los huecos de este año —julio se
# quedó en el día 29 y agosto entero faltaba— son exactamente lo que esto viene
# a evitar.
#
set -u

RAIZ="/Users/anibalfuentes/Documents/VSC/Control_Vehicular/RPA_Utilitarios"
LOGS="$RAIZ/logs_orquestador"
FECHA=$(date +%Y-%m-%d)

mkdir -p "$LOGS"
cd "$RAIZ" || exit 1

"$RAIZ/.venv/bin/python" -m extractors.fleetup_viajes --dias 7 \
  >> "$LOGS/viajes_$FECHA.log" 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] salida=$?" >> "$LOGS/viajes_$FECHA.log"
