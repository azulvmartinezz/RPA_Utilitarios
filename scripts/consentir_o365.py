"""Consentimiento de Microsoft 365, una sola vez, sin necesitar la terminal.

El flujo original vive en `extractors/edenred_extractor.py` y pide la
confirmación con `input()`. Eso obliga a tener una terminal interactiva abierta:
corriendo desatendido —o desde un agente, o desde un planificador— muere con
`EOF when reading a line`, que es justamente lo que dejó a Edenred sin ingerir.

Aquí se cambia una sola cosa: en vez de esperar una tecla, el script **espera a
que aparezca el archivo `url.txt`**. El proceso se queda vivo, así que el estado
del intercambio OAuth se conserva y el token se guarda igual.

El token queda en `o365_token.txt` y se renueva solo; esto se corre una vez.

Uso:
    python scripts/consentir_o365.py
    python scripts/consentir_o365.py --espera 900   # más tiempo para hacerlo
"""

import argparse
import os
import sys
import time

from dotenv import load_dotenv
from O365 import Account, FileSystemTokenBackend

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

load_dotenv(os.path.join(RAIZ, ".env"))

ARCHIVO_URL = os.path.join(RAIZ, "url.txt")
ARCHIVO_TOKEN = "o365_token.txt"
SEGUNDOS_ESPERA = 600
INTERVALO = 3


def _esperar_url(segundos: int) -> str:
    """Espera a que aparezca `url.txt` y devuelve su contenido."""
    limite = time.time() + segundos
    avisado = 0

    while time.time() < limite:
        if os.path.exists(ARCHIVO_URL):
            with open(ARCHIVO_URL, "r", encoding="utf-8") as archivo:
                contenido = archivo.read().strip()

            if contenido:
                print(f"\n✅ Recibido url.txt ({len(contenido)} caracteres).")
                # Se borra en cuanto se usa: trae el código de autorización.
                try:
                    os.remove(ARCHIVO_URL)
                except OSError:
                    print("⚠️  No se pudo borrar url.txt; bórralo a mano.")

                return contenido

        restante = int(limite - time.time())

        if restante // 30 != avisado:
            avisado = restante // 30
            print(f"   …esperando url.txt ({restante}s restantes)")

        time.sleep(INTERVALO)

    print("\n❌ Se agotó la espera y no apareció url.txt.")

    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Consentimiento de Microsoft 365.")
    parser.add_argument("--espera", type=int, default=SEGUNDOS_ESPERA)
    args = parser.parse_args()

    client_id = os.getenv("GRAPH_CLIENT_ID")
    tenant_id = os.getenv("GRAPH_TENANT_ID")
    correo = os.getenv("DESTINATARIO_EMAIL", "(sin definir)")

    if not client_id or not tenant_id:
        print("❌ Faltan GRAPH_CLIENT_ID o GRAPH_TENANT_ID en el .env")

        return 1

    # La app está registrada como Desktop App, o sea cliente público: Microsoft
    # prohíbe mandar el secret, así que va vacío.
    cuenta = Account(
        (client_id, ""),
        auth_flow="authorization",
        tenant_id=tenant_id,
        token_backend=FileSystemTokenBackend(
            token_path=RAIZ, token_filename=ARCHIVO_TOKEN
        ),
    )

    if cuenta.is_authenticated:
        print("Ya existe un token. Verificando que sirva…")
        try:
            list(cuenta.mailbox().get_messages(limit=1))
            print("✅ El token actual es válido. No hace falta consentir de nuevo.")

            return 0
        except Exception as error:  # noqa: BLE001
            print(f"⚠️  El token no sirve ({error}). Se pedirá consentimiento.")

    def consentir(url_consentimiento: str) -> str:
        print("\n" + "=" * 70)
        print(f"Inicia sesión con la cuenta: {correo}")
        print("\n1) Abre este enlace en tu navegador:\n")
        print(url_consentimiento)
        print("\n2) Al terminar llegarás a una página en blanco. Copia de la")
        print("   barra de direcciones la URL completa, la larga.")
        print(f"\n3) Guárdala en este archivo:\n   {ARCHIVO_URL}")
        print("\n   Desde la terminal:")
        print(f'     echo "URL_QUE_COPIASTE" > {ARCHIVO_URL}')
        print("=" * 70)
        print("\nNo hay que presionar nada: el script detecta el archivo solo.")

        return _esperar_url(args.espera)

    autenticado = cuenta.authenticate(
        scopes=["basic", "message_all"], handle_consent=consentir
    )

    if not autenticado:
        print("❌ La autenticación no se completó.")

        return 1

    print(f"\n✅ Token guardado en {ARCHIVO_TOKEN}. Ya se puede ingerir Edenred:")
    print("   python scripts/backfill_historico.py --edenred --mes 2026-06")

    return 0


if __name__ == "__main__":
    sys.exit(main())
