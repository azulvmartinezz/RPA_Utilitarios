#!/bin/zsh
#
# Disparador bajo demanda del botón "Actualizar Supramax" (`supramax_refresh_jobs`),
# cada ~1 minuto.
#
# A diferencia de `saldos_supramax_cada_30min.sh`, este cron corre mucho más
# seguido porque hay alguien esperando frente a la pantalla -- pero la
# inmensa mayoría de esas corridas no van a encontrar ningún pedido
# pendiente, así que el script (`scrapers/supramax_actualizar_bajo_demanda.py`)
# sale con solo una consulta SQL cuando no hay nada que hacer, sin abrir
# Chrome. Cuando SÍ hay un pedido, corre la misma rutina de scraping que usa
# el cron de 30 minutos (`supramax_saldos_rpa.ejecutar_actualizacion_saldos`)
# -- no hay una segunda implementación del scraping, solo un segundo
# disparador.
#
set -u

RAIZ="/Users/anibalfuentes/Documents/VSC/Control_Vehicular/RPA_Utilitarios"
LOGS="$RAIZ/logs_orquestador"
FECHA=$(date +%Y-%m-%d)

mkdir -p "$LOGS"
cd "$RAIZ" || exit 1

"$RAIZ/.venv/bin/python" -m scrapers.supramax_actualizar_bajo_demanda \
  >> "$LOGS/supramax_bajo_demanda_$FECHA.log" 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] salida=$?" >> "$LOGS/supramax_bajo_demanda_$FECHA.log"
