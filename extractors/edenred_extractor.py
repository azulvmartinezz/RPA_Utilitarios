import os
import time
from dotenv import load_dotenv
from O365 import Account, FileSystemTokenBackend
from bigquery import bq_ingestion

load_dotenv()

CLIENT_ID = os.getenv('GRAPH_CLIENT_ID')
CLIENT_SECRET = os.getenv('GRAPH_CLIENT_SECRET')
TENANT_ID = os.getenv('GRAPH_TENANT_ID')
EMAIL_CUENTA = os.getenv('DESTINATARIO_EMAIL')

def main():
    if not CLIENT_ID or not CLIENT_SECRET or not TENANT_ID:
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

    # Polling: reintenta cada 30s hasta 15 minutos esperando que lleguen los correos
    timeout = 15 * 60
    intervalo = 30
    transcurrido = 0
    mensajes = []

    while transcurrido <= timeout:
        print(f"\nBuscando reportes nuevos (Sin Leer) en el buzón de {EMAIL_CUENTA}...")
        candidatos = list(mailbox.get_messages(limit=25, query=query))
        mensajes = [m for m in candidatos if m.has_attachments]
        if mensajes:
            print(f"✅ Se encontraron {len(mensajes)} correo(s) con adjuntos.")
            break
        print(f"⏳ Sin correos nuevos. Reintentando en {intervalo}s... ({transcurrido//60}m transcurridos)")
        time.sleep(intervalo)
        transcurrido += intervalo
    else:
        print("⏰ Tiempo de espera agotado. No llegaron correos de Edenred.")
        return

    encontrados = 0
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
                    mensaje.mark_as_read()

                    print("🚀 Mandando a la aduana de BigQuery...")
                    try:
                        df_limpio = bq_ingestion.procesar_edenred(ruta_guardado)
                        if df_limpio is not None:
                            bq_ingestion.ingest_to_bigquery(df_limpio)
                        print("✅ Correo procesado y marcado como leído.")
                    except Exception as e:
                        print(f"❌ Error al procesar a BigQuery: {e}")
                    finally:
                        # Limpiar el archivo descargado
                        if os.path.exists(ruta_guardado):
                            os.remove(ruta_guardado)
            
            encontrados += 1

    if encontrados == 0:
        print("No se encontraron correos nuevos con reportes.")

if __name__ == "__main__":
    main()
