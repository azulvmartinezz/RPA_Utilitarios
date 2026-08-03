"""
Descarga el reporte de consumo de Ticket Car Edenred.

Reemplaza a `edenred_rpa.py`, que recorria el portal viejo de Ticket Car ®
(ASP.NET, con ids `ctl00_contenido_...`) y no descargaba nada: pedia el
reporte por correo y despues `edenred_extractor.py` entraba al buzon con O365
a recogerlo.

El portal nuevo vive en ticketcaredenred.mx y tiene descarga directa, asi que
todo ese rodeo desaparece: no hace falta buzon, ni correos sin leer, ni el
consentimiento de Microsoft, ni el manifiesto de reportes pendientes. Y se
sabe en el momento si funciono, en vez de enterarse horas despues de que el
correo nunca llego.

Otra ventaja: el periodo dejo de ser "mes de facturacion" elegido de un
combo. Ahora es un rango de fechas libre, asi que el backfill puede pedir
cualquier ventana.

La pagina es jQuery con Bootstrap y jqWidgets, no una SPA moderna: los ids son
estables y se pueden usar directo.
"""

import datetime
import glob
import os
import time

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

from scrapers.supramax_rpa import (
    _build_chrome_options,
    _create_driver,
)

# --------------------------------------------------------------------------
# Identificadores del portal
#
# Se listan juntos porque son lo unico que se rompe cuando el proveedor
# rediseña: si algo deja de funcionar, se revisa esta seccion primero.
# --------------------------------------------------------------------------
ID_USUARIO = "UserName"
ID_CONTINUAR = "ButtonLogin"
ID_PASSWORD = "TallyHawk"

# Panel de filtros del reporte dinamico
ID_TIPO_REPORTE = "cmbReport"
ID_EMPRESA = "cmbKabus"
ID_PERIODO = "txtPeriod"
ID_EVALUAR_POR = "cmbDateEvaluated"
ID_ESTATUS = "cmbStatus"
ID_FILTRAR = "btnRefresh"

# Modal de exportacion
ID_ABRIR_DESCARGA = "btnDownLoad"
ID_FORMATO = "cmbDownloadFormat"
ID_MOSTRAR_ENCABEZADOS = "ckShowReportHeaderDownload"
ID_DESCARGAR = "btnRealDownload"

URL_REPORTE = "https://ticketcaredenred.mx/reportedinamico"

ESPERA_LARGA = 40
ESPERA_DESCARGA = 180


def _esperar_descarga(descargas_dir, previos, timeout=ESPERA_DESCARGA):
    """
    Espera a que aparezca un archivo nuevo y termine de bajar.

    Se compara contra la lista previa en vez de tomar "el mas reciente":
    la carpeta la comparten los tres scrapers y el mas reciente podria ser
    de Supramax corriendo en paralelo.
    """
    limite = time.time() + timeout

    while time.time() < limite:
        actuales = set(os.listdir(descargas_dir))
        nuevos = [
            f for f in actuales - previos
            if not f.endswith((".crdownload", ".tmp"))
        ]

        if nuevos:
            ruta = os.path.join(descargas_dir, nuevos[0])
            # Un archivo puede aparecer con su nombre final antes de estar
            # completo; se espera a que deje de crecer.
            tam = -1
            while tam != os.path.getsize(ruta):
                tam = os.path.getsize(ruta)
                time.sleep(1)

            return ruta

        time.sleep(1)

    return None


def _iniciar_sesion(driver, wait, usuario, password):
    """
    El acceso va en dos pasos: primero el usuario, luego la contraseña.

    Devuelve False en lugar de reventar para que el orquestador distinga
    "no pude entrar" de "entre y no habia datos". Confundir esas dos cosas
    fue lo que hizo que el scraper anterior reportara exito con cero filas.
    """
    driver.get(URL_REPORTE)

    try:
        cookies = WebDriverWait(driver, 8).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(text(), 'Aceptar todas las cookies')]")
            )
        )
        cookies.click()
    except TimeoutException:
        pass

    try:
        campo_usuario = wait.until(
            EC.presence_of_element_located((By.ID, ID_USUARIO))
        )
    except TimeoutException:
        # Sin formulario de acceso es que la sesion del perfil sigue viva.
        print("No aparecio el formulario de acceso; la sesion parece activa.")
        return True

    campo_usuario.clear()
    campo_usuario.send_keys(usuario)
    driver.find_element(By.ID, ID_CONTINUAR).click()

    campo_password = wait.until(
        EC.element_to_be_clickable((By.ID, ID_PASSWORD))
    )
    campo_password.clear()
    campo_password.send_keys(password)
    campo_password.submit()

    time.sleep(5)

    # Si el formulario sigue en pantalla, las credenciales no sirvieron.
    if driver.find_elements(By.ID, ID_PASSWORD) or driver.find_elements(By.ID, ID_USUARIO):
        print("❌ El portal rechazo las credenciales de Ticket Car.")
        return False

    return True


def _fijar_periodo(driver, desde, hasta):
    """
    El campo de periodo usa bootstrap-daterangepicker.

    Se maneja por JavaScript y no tecleando: el plugin ignora lo que se
    escriba a mano hasta que se dispara su propio evento, asi que teclear
    dejaba el rango anterior y el reporte salia del periodo equivocado.
    """
    driver.execute_script(
        """
        const campo = $('#%s');
        const picker = campo.data('daterangepicker');
        const desde = moment(arguments[0], 'YYYY-MM-DD');
        const hasta = moment(arguments[1], 'YYYY-MM-DD');

        if (picker) {
            picker.setStartDate(desde);
            picker.setEndDate(hasta);
            campo.trigger('apply.daterangepicker', picker);
        } else {
            campo.val(desde.format('DD/MM/YYYY') + ' - ' + hasta.format('DD/MM/YYYY'));
        }
        campo.trigger('change');
        """ % ID_PERIODO,
        desde.isoformat(),
        hasta.isoformat(),
    )
    time.sleep(1)


def _seleccionar(driver, id_control, valor=None, texto_contiene=None):
    """
    Elige una opcion y avisa a jQuery.

    Los desplegables los pinta bootstrap-select sobre el <select> real; sin
    disparar `change` la interfaz se queda con la opcion anterior aunque el
    <select> ya haya cambiado.
    """
    elemento = driver.find_element(By.ID, id_control)
    select = Select(elemento)

    if valor is not None:
        select.select_by_value(str(valor))
    else:
        for opcion in select.options:
            if texto_contiene.lower() in opcion.text.lower():
                select.select_by_visible_text(opcion.text)
                break
        else:
            return False

    driver.execute_script(
        "$('#%s').trigger('change').selectpicker && $('#%s').selectpicker('refresh');"
        % (id_control, id_control)
    )
    time.sleep(1)

    return True


def _empresas_disponibles(driver):
    """Las once empresas del contrato, con su id interno."""
    select = Select(driver.find_element(By.ID, ID_EMPRESA))

    return [
        (opcion.get_attribute("value"), opcion.text.strip())
        for opcion in select.options
        if opcion.get_attribute("value")
    ]


def _descargar(driver, wait, descargas_dir):
    """
    Abre el modal de exportacion y baja el archivo.

    «Mostrar encabezados» se apaga a proposito: con esa opcion el Excel trae
    una fila de subtotales por empresa antes de los movimientos, con las sumas
    de litros e importe. Si se ingesta tal cual, esa fila se cuenta como una
    transaccion mas y duplica el gasto del periodo.
    """
    previos = set(os.listdir(descargas_dir))

    driver.execute_script("document.getElementById('%s').click();" % ID_ABRIR_DESCARGA)
    wait.until(EC.visibility_of_element_located((By.ID, ID_DESCARGAR)))

    _seleccionar(driver, ID_FORMATO, valor="xlsx")

    driver.execute_script(
        """
        const ck = document.getElementById(arguments[0]);
        if (ck && ck.checked) { ck.click(); }
        """,
        ID_MOSTRAR_ENCABEZADOS,
    )

    driver.find_element(By.ID, ID_DESCARGAR).click()

    return _esperar_descarga(descargas_dir, previos)


def main(desde=None, hasta=None, headless=False, empresas_filtro=None):
    """
    Descarga el detalle de consumo de cada empresa para el rango indicado.

    Sin fechas toma los ultimos siete dias, que es lo que cubre una corrida
    diaria con margen para reintentos.

    Devuelve la lista de rutas descargadas.
    """
    usuario = os.getenv("EDENRED_USER")
    password = os.getenv("EDENRED_PASSWORD")

    if not usuario or not password:
        print("❌ Faltan EDENRED_USER o EDENRED_PASSWORD.")
        return []

    if hasta is None:
        hasta = datetime.date.today()

    if desde is None:
        desde = hasta - datetime.timedelta(days=7)

    descargas_dir = os.path.join(os.getcwd(), "descargas_temporales")
    os.makedirs(descargas_dir, exist_ok=True)

    driver = _create_driver(_build_chrome_options(headless, descargas_dir))
    wait = WebDriverWait(driver, ESPERA_LARGA)
    descargados = []

    try:
        if not _iniciar_sesion(driver, wait, usuario, password):
            return []

        wait.until(EC.presence_of_element_located((By.ID, ID_EMPRESA)))

        _seleccionar(driver, ID_TIPO_REPORTE, texto_contiene="Detalle de consumo")
        _seleccionar(driver, ID_ESTATUS, texto_contiene="procesadas")
        _seleccionar(driver, ID_EVALUAR_POR, texto_contiene="Transacci")

        empresas = _empresas_disponibles(driver)

        if empresas_filtro:
            empresas = [e for e in empresas if e[0] in empresas_filtro]

        print(f"{len(empresas)} empresa(s) | periodo {desde} a {hasta}")

        for indice, (id_empresa, nombre) in enumerate(empresas, 1):
            print(f"\n[{indice}/{len(empresas)}] {nombre}")

            try:
                _seleccionar(driver, ID_EMPRESA, valor=id_empresa)
                _fijar_periodo(driver, desde, hasta)

                driver.find_element(By.ID, ID_FILTRAR).click()
                time.sleep(6)

                ruta = _descargar(driver, wait, descargas_dir)

                if ruta:
                    print(f"   ✅ {os.path.basename(ruta)}")
                    descargados.append((ruta, nombre))
                else:
                    print("   ⚠️ No bajo ningun archivo.")

            except Exception as error:
                print(f"   ⚠️ Error: {error}")

    finally:
        driver.quit()

    print(f"\n{len(descargados)} archivo(s) descargado(s).")

    return descargados


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    main()
