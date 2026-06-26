# 📦 Instrucciones para el Ejecutable de Jocelinne

Este directorio contiene la interfaz gráfica (`app.py`) diseñada para que Jocelinne pueda operar los flujos de RPA de forma selectiva y generar un reporte consolidado local en Excel.

---

## 🛠️ Cómo Compilar el Ejecutable (`.exe`) para Windows

Para generar el archivo ejecutable `.exe` único que le enviarás a Jocelinne, sigue estos pasos desde la terminal de tu computadora:

### Paso 1: Activar tu Entorno Virtual e Instalar PyInstaller
Primero, asegúrate de que tu entorno esté activo y de tener instalada la herramienta `pyinstaller`:

```powershell
.venv\Scripts\Activate.ps1
pip install pyinstaller
```

### Paso 2: Ejecutar el Comando de Compilación
Sitúate en la carpeta raíz del proyecto y corre el comando `pyinstaller` incluyendo los módulos del RPA para que vayan empaquetados dentro del `.exe`:

```powershell
pyinstaller --noconsole --onefile --add-data "scrapers;scrapers" --add-data "extractors;extractors" --add-data "bigquery;bigquery" --add-data "gcs_uploader.py;." ejecutable/app.py
```

*Nota: Esto creará una carpeta llamada `dist/` en tu raíz con el archivo `app.exe` dentro.*

---

## 📂 Cómo Estructurar la Carpeta para Jocelinne

Una vez compilado, crea una carpeta limpia en tu computadora llamada **RPA_Utilitarios_Ejecutable** y colócale únicamente los siguientes archivos para enviársela:

```text
RPA_Utilitarios_Ejecutable/
│
├── app.exe                      # El archivo ejecutable que compilaste (de la carpeta dist/)
├── .env                         # Copia de tu archivo .env local (con las contraseñas y llaves de Google Cloud)
├── o365_token.txt               # Tu token de Office 365 (¡así ella no tendrá que hacer login!)
└── service_account.json         # Tu archivo de credenciales de GCP (si utilizas llaves de Google Cloud)
```

> [!IMPORTANT]
> **Sobre el Token de Office 365 (`o365_token.txt`)**: 
> Como el token se creó para la cuenta institucional de **automatizacionespetroil**, al incluir el archivo `o365_token.txt` que generaste en esta carpeta, el ejecutable de Jocelinne lo leerá directamente y se conectará de inmediato a la cuenta sin pedirle a ella que inicie sesión.

---

## 🖥️ Cómo lo Usará Jocelinne

1. Jocelinne recibirá la carpeta comprimida, la extraerá en su máquina Windows.
2. Hará doble clic en **`app.exe`** para abrir la interfaz gráfica.
3. Marcará las casillas de los sistemas que desea correr (ej. solo Supramax, o todos).
4. Hará clic en **`🚀 Iniciar Flujos Seleccionados`**.
5. Verá la consola en tiempo real procesando.
6. Al finalizar, la aplicación le mostrará un mensaje flotante y generará un archivo de Excel consolidado dentro de una nueva subcarpeta llamada `Reportes_Ejecutable/` al lado del programa.

*Si por cualquier motivo el token de automatizaciones llegara a caducar en el futuro, ella sólo tendrá que hacer clic en el botón morado `Conectar Office 365 (Token)` para volver a vincular la cuenta.*

---

## 🍎 Nota para Mac

Para compilar `app.app` en macOS, usa una `.venv` creada con **Python 3.11+** que tenga **Tk 8.6 o superior**.

No uses el Python 3.9 del sistema con Tk 8.5, porque `customtkinter` puede abrir la ventana vacía o dejar la app inestable.

La validación ya quedó automatizada en [`compilar_mac.sh`](/Users/azulvioleta/Downloads/RPA_Utilitarios/compilar_mac.sh).
