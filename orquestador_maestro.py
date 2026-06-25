import threading
import time
import sys
import os
import datetime
import atexit
from scrapers import edenred_rpa, supramax_rpa, pase_rpa, fleetup_rpa
from extractors import edenred_extractor

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
    print("\n💎 [EDENRED] Iniciando flujo (Solicitud + Extracción)...")
    try:
        # Primero solicita los reportes
        n_edenred = edenred_rpa.main()
        # Luego procesa los correos que lleguen
        edenred_extractor.main(n_expected=n_edenred)
    except Exception as e:
        print(f"❌ Error crítico en flujo Edenred: {e}")

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
    print("\n🚛 [FLEETUP] Iniciando flujo (Descarga + Ingesta)...")
    try:
        fleetup_rpa.main()
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
