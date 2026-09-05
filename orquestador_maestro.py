import threading
import time
import sys
import os
import datetime
import atexit
from scrapers import supramax_rpa, pase_rpa
from extractors import fleetup_viajes, ticketcar_api

class _Tee:
    def __init__(self, *streams):
        self.streams = streams
    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()
    def flush(self):
        for s in self.streams: s.flush()

# Crear directorio de logs y redirigir salida estándar y de errores
os.makedirs("logs_orquestador", exist_ok=True)
log_path = f"logs_orquestador/orquestador_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
_log_file = open(log_path, "w", encoding="utf-8")
sys.stdout = _Tee(sys.__stdout__, _log_file)
sys.stderr = _Tee(sys.__stderr__, _log_file)

atexit.register(lambda: _log_file.close())


def flujo_edenred():
    """
    Ticket Car: consumo por API, directo a BigQuery.

    Tercera versión de este flujo. La primera pedía el reporte por correo y lo
    recogía del buzón con O365; la segunda lo descargaba del portal con
    Selenium. Las dos dependían de que el proveedor no moviera el HTML y las
    dos entregaban un Excel de 22 columnas que se aplanaba a diez.

    La API entrega la transacción completa —estación, autorización, impuestos
    desglosados, odómetro, centro de costos— y aterriza en
    `flota.ticketcar_transacciones`.

    Los dos scrapers quedaron retirados —ver el encabezado de cada uno—. El
    portal viejo ya no permite descargar reportes, así que el hueco del 1 al 20
    de junio de 2026 se le pidió al proveedor; llegará como archivo y se carga
    con `bigquery.bq_ingestion.procesar_ticketcar`, que sigue en pie.
    """
    print("\n💎 [TICKET CAR] Consumo por API...")
    try:
        total = ticketcar_api.main_dias(7)
        print(f"💰 RESUMEN TICKET CAR: {total} transacciones ingeridas.")
    except Exception as e:
        print(f"❌ Error crítico en flujo Ticket Car: {e}")


def flujo_supramax():
    print("\n📈 [SUPRAMAX] Iniciando descarga e ingesta directa...")
    try:
        supramax_rpa.main()
    except Exception as e:
        print(f"❌ Error crítico en flujo Supramax: {e}")

def flujo_pase():
    print("\n🎫 [PASE] Iniciando descarga e ingesta directa...")
    try:
        pase_rpa.main()
    except Exception as e:
        print(f"❌ Error crítico en flujo Pase: {e}")

def flujo_fleetup():
    """Viajes de FleetUp por API.

    Sustituye a `scrapers/fleetup_rpa.py`, que abría el portal con Selenium
    para bajar un Excel mensual y solo lo respaldaba en GCS —su tabla destino,
    `rpa_utilitarios.tbl_reportes_viajes`, quedó en cero filas—. Tenemos
    credenciales del API, así que ya no hace falta el navegador ni resolver
    captchas. El scraper sigue en el repo por si se necesita comparar contra
    el reporte del portal, pero no forma parte del flujo.
    """
    print("\n🚛 [FLEETUP] Ingestando viajes por API...")
    try:
        # Se traen 30 días, el máximo que acepta el API en una llamada, para
        # que una corrida perdida se recupere sola en la siguiente.
        fleetup_viajes.main_dias(30)
    except Exception as e:
        print(f"❌ Error crítico en flujo FleetUp: {e}")

def main():
    start_time = time.time()
    print("="*60)
    print("🚀 INICIANDO ORQUESTADOR MAESTRO (SECUENCIAL) 🚀")
    print(f"📄 Guardando log en: {log_path}")
    print("="*60)

    # Ejecutamos secuencialmente para evitar que Chrome/Selenium
    # choquen al intentar abrir múltiples navegadores en Mac.
    
    flujo_pase()
    flujo_supramax()
    flujo_fleetup()
    flujo_edenred()

    total_minutos = (time.time() - start_time) / 60
    print("\n" + "="*60)
    print(f"✅ PROCESO GLOBAL FINALIZADO EN {total_minutos:.2f} MINUTOS")
    print(f"📄 Log completo guardado en: {log_path}")
    print("="*60)

if __name__ == "__main__":
    main()
