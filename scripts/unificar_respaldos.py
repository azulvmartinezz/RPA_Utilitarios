import os
import sys


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def unificar_respaldos():
    print("=== UNIFICACION EN MODO LOCAL ===")
    print("La version de nube/GCS fue deshabilitada para este proyecto.")
    print("Se ejecutara la unificacion local de respaldos desde la carpeta configurada.")

    from scripts_onedrive import unificar_respaldos_local

    unificar_respaldos_local.unificar_respaldos_desde_onedrive()


if __name__ == "__main__":
    unificar_respaldos()
