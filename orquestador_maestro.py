import threading
import time
from scrapers import edenred_rpa, supramax_rpa, pase_rpa
from extractors import edenred_extractor

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

def main():
    start_time = time.time()
    print("="*60)
    print("🚀 INICIANDO ORQUESTADOR MAESTRO TURBO (PARALELO) 🚀")
    print("="*60)

    # Definimos los hilos
    hilos = [
        threading.Thread(target=flujo_edenred, name="Hilo-Edenred"),
        threading.Thread(target=flujo_supramax, name="Hilo-Supramax"),
        threading.Thread(target=flujo_pase, name="Hilo-Pase")
    ]

    # Arrancamos todos los motores
    for hilo in hilos:
        hilo.start()

    # Esperamos a que todos terminen
    for hilo in hilos:
        hilo.join()

    total_minutos = (time.time() - start_time) / 60
    print("\n" + "="*60)
    print(f"✅ PROCESO GLOBAL FINALIZADO EN {total_minutos:.2f} MINUTOS")
    print("="*60)

if __name__ == "__main__":
    main()
