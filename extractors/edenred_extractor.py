import os
import sys
import time
import re
import json

# Añadir el directorio raíz al path para que encuentre 'bigquery' y otros módulos
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
from O365 import Account, FileSystemTokenBackend
from bigquery import bq_ingestion

load_dotenv()

CLIENT_ID = os.getenv('GRAPH_CLIENT_ID')
TENANT_ID = os.getenv('GRAPH_TENANT_ID')
EMAIL_CUENTA = os.getenv('DESTINATARIO_EMAIL')


def _manifest_path():
    return os.path.join(os.getcwd(), "descargas_temporales", "edenred_report_manifest.json")


def _load_manifest():
    path = _manifest_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_manifest(data):
    path = _manifest_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=True, indent=2)


def _pop_pending_empresa(manifest):
    pending = manifest.get("pending_reports", [])
    if not pending:
        return None
    item = pending.pop(0)
    manifest["pending_reports"] = pending
    _save_manifest(manifest)
    return item.get("empresa")


def _empresa_para_adjunto(mensaje, adjunto_name, manifest):
    key = f"attachment::{adjunto_name}"
    if key in manifest:
        return manifest[key].get("empresa")
    return _pop_pending_empresa(manifest)

def main(n_expected=1):
    if not CLIENT_ID or not TENANT_ID:
        print("❌ Faltan credenciales de Graph API en el archivo .env")
        return

    # Usaremos un archivo local (o365_token.txt) para guardar la sesión.
    # Así no tienes que iniciar sesión cada vez que corras el bot.
    # Como Azure lo configuramos como "Desktop App", es un cliente público y Microsoft PROHÍBE enviar el secret.
    # Así que solo le mandamos el CLIENT_ID y un texto vacío en lugar del secret.
    credentials = (CLIENT_ID, "")

    token_backend = FileSystemTokenBackend(token_path='.', token_filename='o365_token.txt')

    # Inicializar cuenta con los permisos delegados
    account = Account(credentials, auth_flow='authorization', tenant_id=TENANT_ID, token_backend=token_backend)
    
    # --- PRIMERA VEZ (Autenticación Interactiva) ---
    if not account.is_authenticated:
        print("\n" + "="*50)
        print("🔐 PRIMERA VEZ: AUTENTICACIÓN REQUERIDA")
        print("="*50)
        print(f"Se te pedirá que inicies sesión en Microsoft con la cuenta: {EMAIL_CUENTA}")
        print("Solo tendrás que hacerlo una vez. El bot guardará un token permanente.")
        def my_consent(consent_url):
            print("\n" + "="*50)
            print("1. Abre este link en tu navegador:")
            print(consent_url)
            print("\n2. Inicia sesión y llegarás a una página en blanco.")
            print("3. Copia toda la URL larguísima de arriba.")
            print("4. Crea un archivo llamado 'url.txt' en esta misma carpeta, pega ahí la URL y GUÁRDALO.")
            print("="*50)
            input("Una vez guardado el archivo url.txt, presiona ENTER aquí...")
            try:
                with open("url.txt", "r") as f:
                    return f.read().strip()
            except Exception as e:
                print("❌ No se pudo leer url.txt. Asegúrate de crearlo en la carpeta correcta.")
                return ""

        if not account.authenticate(scopes=['basic', 'message_all'], handle_consent=my_consent):
            print("❌ La autenticación falló o fue cancelada.")
            return
        print("✅ Autenticación exitosa. Token guardado en o365_token.txt")

    mailbox = account.mailbox()
    query = mailbox.new_query().on_attribute('isRead').equals(False)
    query.chain('and').on_attribute('subject').contains('Reportes Edenred')

    # Polling: reintenta cada 30s hasta 15 minutos esperando que lleguen los correos
    timeout = 15 * 60
    intervalo = 30
    transcurrido = 0
    mensajes = []

    while transcurrido <= timeout:
        print(f"\nBuscando reportes nuevos (Sin Leer) en el buzón de {EMAIL_CUENTA}...")
        candidatos = list(mailbox.get_messages(limit=100, query=query))
        mensajes = sorted(
            [m for m in candidatos if m.has_attachments],
            key=lambda m: m.received,
        )
        if n_expected and len(mensajes) > n_expected:
            print(f"ℹ️ Hay {len(mensajes)} correos sin leer; se procesarán solo los {n_expected} más recientes.")
            mensajes = mensajes[:n_expected]
        
        if len(mensajes) >= n_expected:
            print(f"✅ Se encontraron {len(mensajes)} correo(s) con adjuntos (esperábamos {n_expected}).")
            break
            
        print(f"⏳ Se encontraron {len(mensajes)}/{n_expected} correos. Reintentando en {intervalo}s... ({transcurrido//60}m transcurridos)")
        time.sleep(intervalo)
        transcurrido += intervalo
    else:
        if not mensajes:
            print("⏰ Tiempo de espera agotado. No llegaron correos de Edenred.")
            return
        else:
            print(f"⚠️ Tiempo de espera agotado, pero se procesarán los {len(mensajes)} correos encontrados.")

    manifest = _load_manifest()
    encontrados = 0
    total_importe = 0.0
    for mensaje in mensajes:
        if mensaje.has_attachments:
            print(f"\n✉️  Correo detectado: '{mensaje.subject}' (Recibido: {mensaje.received})")
            
            # Asegurar que exista la carpeta de descargas temporales
            descargas_dir = os.path.join(os.getcwd(), "descargas_temporales")
            if not os.path.exists(descargas_dir):
                os.makedirs(descargas_dir)
                
            mensaje.attachments.download_attachments()
            for adjunto in mensaje.attachments:
                print(f"   📎 Adjunto encontrado: {adjunto.name}")
                # Filtrar solo archivos de Excel/CSV
                if adjunto.name.lower().endswith(('.xls', '.xlsx', '.csv')):
                    ruta_guardado = os.path.join(descargas_dir, adjunto.name)
                    print(f"📥 Descargando adjunto: {adjunto.name} ...")
                    adjunto.save(location=descargas_dir)
                    empresa = _empresa_para_adjunto(mensaje, adjunto.name, manifest)
                    if empresa:
                        manifest[f"attachment::{adjunto.name}"] = {"empresa": empresa}
                        _save_manifest(manifest)
                    
                    print("🚀 Mandando a la aduana de BigQuery...")
                    try:
                        df_limpio = bq_ingestion.procesar_edenred(ruta_guardado, empresa=empresa)
                        if df_limpio is not None:
                            suma_archivo = df_limpio['Importe'].sum()
                            total_importe += suma_archivo
                            bq_ingestion.ingest_to_bigquery(df_limpio)
                            print(f"✅ Subtotal de este archivo: {suma_archivo:,.2f}")
                            # Solo marcar como leído si se procesó bien
                            mensaje.mark_as_read()
                        print("✅ Correo procesado.")
                    except Exception as e:
                        print(f"❌ Error al procesar a BigQuery: {e}")
                    finally:
                        # Respaldar el archivo en lugar de borrarlo
                        if os.path.exists(ruta_guardado):
                            respaldo_dir = os.path.join(os.getcwd(), "respaldo_descargas")
                            respaldo_dir = os.path.join(os.getcwd(), "respaldo_descargas")
                            import sys
                            raiz_proyecto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                            if raiz_proyecto not in sys.path:
                                sys.path.append(raiz_proyecto)
                            import gcs_uploader
                            gcs_uploader.subir_y_borrar_local(ruta_guardado, 'Edenred', empresa=empresa)
            
            encontrados += 1

    if encontrados == 0:
        print("No se encontraron correos nuevos con reportes.")
    else:
        print(f"\n{'='*50}")
        print(f"💰 RESUMEN EDENRED: {total_importe:,.2f} procesados.")
        print(f"{'='*50}")

if __name__ == "__main__":
    main()
