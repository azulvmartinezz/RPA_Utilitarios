import time
from scrapers import edenred_rpa, supramax_rpa, pase_rpa
from extractors import edenred_extractor

def main():
    print("="*50)
    print("🚀 INICIANDO ORQUESTADOR MAESTRO DE RPA 🚀")
    print("="*50)

    # PASO 1: Edenred — solicita los reportes por correo
    print("\n[1/4] Solicitando reportes de Edenred...")
    try:
        edenred_rpa.main()
    except Exception as e:
        print(f"❌ Error crítico en Edenred RPA: {e}")

    # PASO 2: Edenred extractor — polling hasta que lleguen los correos e ingesta a BQ
    print("\n[2/4] Esperando y extrayendo correos de Edenred hacia BigQuery...")
    try:
        edenred_extractor.main()
    except Exception as e:
        print(f"❌ Error extrayendo correos de Edenred: {e}")

    # PASO 3: Supramax
    print("\n[3/4] Descargando e ingiriendo Supramax a BigQuery...")
    try:
        supramax_rpa.main()
    except Exception as e:
        print(f"❌ Error crítico en Supramax RPA: {e}")

    # PASO 4: Pase
    print("\n[4/4] Descargando e ingiriendo Pase a BigQuery...")
    try:
        pase_rpa.main()
    except Exception as e:
        print(f"❌ Error crítico en Pase RPA: {e}")

    print("\n" + "="*50)
    print("✅ FLUJO MAESTRO COMPLETADO EXITOSAMENTE ✅")
    print("="*50)

if __name__ == "__main__":
    main()
