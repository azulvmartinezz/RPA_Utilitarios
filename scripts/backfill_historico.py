"""
Script de backfill único para cargar el historial Jan-Abr 2026.
- Supramax y Edenred corren en paralelo (navegadores independientes).
- Pase corre después (puede requerir captcha manual).
- Borra BQ al inicio para que sea idempotente.
"""

import os
import json
import sys
import datetime
import calendar
import threading
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bigquery import bq_ingestion

load_dotenv()

# ---------------------------------------------------------------------------
# Logging a archivo (todo lo que sale en consola también se guarda en .txt)
# ---------------------------------------------------------------------------
class _Tee:
    """Redirige stdout a consola Y a un archivo simultáneamente."""
    def __init__(self, *streams):
        self.streams = streams
    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()
    def flush(self):
        for s in self.streams: s.flush()

os.makedirs("logs_backfill", exist_ok=True)
_log_file = open(f"logs_backfill/backfill_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt", "w", encoding="utf-8")
sys.stdout = _Tee(sys.__stdout__, _log_file)

# Lista global para rastrear fallos
FALLOS = []  # [(sistema, cuenta, mes_str)]

# ---------------------------------------------------------------------------
# Meses a backfill por sistema  (año, mes)
# ---------------------------------------------------------------------------
MESES_SUPRAMAX = [(2026, 1), (2026, 2), (2026, 3), (2026, 4)]
MESES_EDENRED  = [(2026, 1), (2026, 2), (2026, 3), (2026, 4)]
MESES_PASE     = [(2026, 1), (2026, 2), (2026, 3), (2026, 4)]


def _fini_ffin(year, month):
    last_day = calendar.monthrange(year, month)[1]
    return (datetime.date(year, month, 1).strftime("%d/%m/%Y"),
            datetime.date(year, month, last_day).strftime("%d/%m/%Y"))


def _mes_str(year, month):
    return f"{month:02d}/{year}"


# ---------------------------------------------------------------------------
# SUPRAMAX
# ---------------------------------------------------------------------------
def backfill_supramax(cuenta_filter=None, meses_filter=None):
    print("\n" + "="*60)
    print("🚗 BACKFILL SUPRAMAX")
    print("="*60)

    credenciales_str = os.getenv('SUPRAMAX_CREDENTIALS')
    if not credenciales_str:
        print("❌ No se encontró SUPRAMAX_CREDENTIALS en .env")
        return
    credenciales = json.loads(credenciales_str)

    # Filtrar cuentas si se especificó --cuenta
    if cuenta_filter:
        credenciales = [c for c in credenciales if cuenta_filter.upper() in c['Usuario'].upper()]
        if not credenciales:
            print(f"❌ No se encontró ninguna cuenta que coincida con '{cuenta_filter}'")
            return
        print(f"🎯 Filtrando solo cuenta(s): {[c['Usuario'] for c in credenciales]}")

    # Determinar meses a procesar
    meses_a_usar = meses_filter if meses_filter else MESES_SUPRAMAX

    # Borrar solo los meses que se van a cargar.
    # IMPORTANTE: si se filtra por cuenta, NO borramos — la cuenta nunca subió datos
    # y borrar el mes completo eliminaría registros de otras cuentas que sí procesaron bien.
    if not cuenta_filter:
        for year, month in meses_a_usar:
            bq_ingestion.delete_month("Supramax", year, month)
    else:
        print("⚠️  Modo recuperación: omitiendo borrado previo para no afectar otras cuentas.")

    meses = [_fini_ffin(y, m) for y, m in meses_a_usar]
    from scrapers.supramax_rpa import process_account
    for acc in credenciales:
        fallos_cuenta = process_account(
            acc['Usuario'], acc['Contraseña'],
            meses_override=meses, meses_meta=meses_a_usar
        )
        if fallos_cuenta:
            for (y, m) in fallos_cuenta:
                FALLOS.append(("Supramax", acc['Usuario'], f"{y}-{m:02d}"))

    print("\n✅ Backfill Supramax completado.")


# ---------------------------------------------------------------------------
# EDENRED
# ---------------------------------------------------------------------------
def backfill_edenred(meses_filter=None):
    print("\n" + "="*60)
    print("💳 BACKFILL EDENRED")
    print("="*60)

    meses_a_usar = meses_filter if meses_filter else MESES_EDENRED

    try:
        for year, month in meses_a_usar:
            bq_ingestion.delete_month("Edenred", year, month)

        meses_str = [_mes_str(y, m) for y, m in meses_a_usar]
        from scrapers.edenred_rpa import main as edenred_main
        from extractors import edenred_extractor

        n_reportes = edenred_main(meses_override=meses_str)
        edenred_extractor.main(n_expected=n_reportes)
        print("\n✅ Backfill Edenred completado.")
    except Exception as e:
        print(f"❌ Error en Edenred: {e}")
        for year, month in meses_a_usar:
            FALLOS.append(("Edenred", "N/A", f"{year}-{month:02d}"))


# ---------------------------------------------------------------------------
# PASE
# ---------------------------------------------------------------------------
def backfill_pase():
    print("\n" + "="*60)
    print("🛣️  BACKFILL PASE")
    print("="*60)

    try:
        for year, month in MESES_PASE:
            bq_ingestion.delete_month("Pase", year, month)

        from scrapers.pase_rpa import main as pase_main
        pase_main(backfill_mode=True, meses_objetivo=MESES_PASE)
        print("\n✅ Backfill Pase completado.")
    except Exception as e:
        print(f"❌ Error en Pase: {e}")
        for year, month in MESES_PASE:
            FALLOS.append(("Pase", "N/A", f"{year}-{month:02d}"))


# ---------------------------------------------------------------------------
# MAIN — Ejecución Secuencial para evitar bloqueos en Mac
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Backfill Histórico 2026")
    parser.add_argument("--supramax", action="store_true", help="Correr solo Supramax")
    parser.add_argument("--edenred", action="store_true", help="Correr solo Edenred")
    parser.add_argument("--pase", action="store_true", help="Correr solo Pase")
    parser.add_argument("--cuenta", type=str, default=None, help="Filtrar por nombre de cuenta (ej: AEROSERVICIOS)")
    parser.add_argument("--mes", type=str, default=None, help="Filtrar por mes específico (ej: 2026-01)")
    args = parser.parse_args()

    # Si no se especifica ningún sistema, se corren todos
    run_all = not (args.supramax or args.edenred or args.pase)

    # Convertir --mes a lista de tuplas [(year, month)]
    meses_filter = None
    if args.mes:
        try:
            y, m = args.mes.split("-")
            meses_filter = [(int(y), int(m))]
            print(f"🗓️  Filtrando solo mes: {args.mes}")
        except:
            print(f"❌ Formato de --mes inválido. Usa YYYY-MM, ej: 2026-01")
            exit(1)

    print("🔄 INICIANDO BACKFILL HISTÓRICO 2026")
    print("Periodo:  Enero – Abril 2026\n")

    if args.supramax or run_all:
        backfill_supramax(cuenta_filter=args.cuenta, meses_filter=meses_filter)
    
    if args.edenred or run_all:
        backfill_edenred(meses_filter=meses_filter)
        
    if args.pase or run_all:
        backfill_pase()

    # ---------------------------------------------------------------------------
    # RESUMEN FINAL
    # ---------------------------------------------------------------------------
    print("\n" + "="*60)
    print("📋 RESUMEN FINAL DEL BACKFILL")
    print("="*60)
    if not FALLOS:
        print("🎉 ¡Todo procesado sin errores!")
    else:
        print(f"⚠️  {len(FALLOS)} mes(es) con error. Comandos para recuperarlos:\n")
        for sistema, cuenta, mes in FALLOS:
            flag = f"--{sistema.lower()}"
            print(f"  → {sistema} | {cuenta} | {mes}")
            print(f"    ./.venv/bin/python scripts/backfill_historico.py {flag} --cuenta \"{cuenta}\" --mes {mes}\n")
    print("="*60)
    print(f"📄 Log completo guardado en: logs_backfill/")
    print("="*60)
