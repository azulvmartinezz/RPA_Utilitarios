"""Cotiza el precio de venta de una unidad en la Guía EBC (Libro Azul)
scrapeando el sitio público https://www.libroazul.com -- reemplazo del
webservice SOAP de pago que no se contrató (las ventas de unidades son
esporádicas, no justifican el costo del paquete de consultas).

PENSADO PARA UNA UNIDAD A LA VEZ, DISPARADO BAJO DEMANDA

A diferencia de `supramax_saldos_rpa.py` (varias cuentas, cron cada 30 min),
esto no es un refresco masivo: cada corrida cotiza UNA unidad, disparada por
el botón "Consultar Libro Azul" de la pantalla de Ventas. Quien orquesta eso
es `libroazul_actualizar_bajo_demanda.py`, que drena la cola
`libroazul_valuacion_jobs` (ver `control-vehicular/db/esquema/
45_libroazul_valuacion_jobs.sql`); este archivo solo sabe cotizar una unidad,
no sabe nada de la cola.

MARCA/MODELO/AÑO/VERSIÓN/CONDICIÓN VIENEN DEL PEDIDO, NO DE LA BASE

La integración SOAP guardaba esos datos una vez en `unidades_libroazul` y no
los volvía a preguntar. Aquí se decidió distinto a propósito: la persona que
aprieta "Consultar Libro Azul" ve un modal con lo que el sistema propone (de
los datos de la unidad) y lo corrige ahí mismo antes de mandar el RPA a
navegar -- así el robot nunca tiene que "adivinar" qué tarjeta de versión
elegir en `resultados_busqueda.html`, ni qué condición marcar en
`seleccion_condicion.html`; ya le llegan como texto exacto en `PedidoCotizacion`.
`unidades_libroazul` sigue existiendo para el flujo SOAP viejo, pero este RPA
no depende de ella.

SOLO VENTA, NO COMPRA

El flujo público (`inicio` -> buscador -> `resultados_busqueda` -> elegir
condición -> `precio_auto`) solo muestra un número: el "precio sugerido de
venta entre particulares". El "valor comercial" que antes traía `Compra` del
SOAP vive, si acaso, en el portal de paga (`portal.libroazul.com`), no aquí.
Por eso `cotizar_unidad()` nunca llena `im_valorcompra` -- se deja como
estaba en vez de inventar un número.

PROGRESO POR PASOS

`cotizar()` recibe un callback `progreso(clave, detalle)` opcional que se
llama antes de cada etapa -- mismo mecanismo que ya usa
`supramax_dispersion_rpa.py` (`cl_paso`/`de_paso`) para que la pantalla
muestre un modal de seguimiento en vivo. Las claves deben coincidir con el
array `PASOS` del componente Vue de seguimiento (ver
`vehicular-admin/src/components/dialogs/Ventas/SeguimientoLibroAzul.vue`):
BUSCANDO, ELIGIENDO_VERSION, ELIGIENDO_CONDICION, LEYENDO_PRECIO, LISTO.

RECAPTCHA INVISIBLE EN LOS FORMS

`usados_form` (buscador de inicio) trae reCAPTCHA v3 invisible: si el score
empieza a bloquear el submit, se verá como un timeout esperando la página de
resultados -- `_ESPERA_CORTA`/`_ESPERA_LARGA` son deliberadamente generosos
para no confundir "nos bloqueó" con "la página tardó".

UNDETECTED-CHROMEDRIVER, NO SELENIUM "NORMAL" -- Y PERFIL DESECHABLE

Comprobado en vivo el 2026-10-01: con Selenium normal (el mismo `crear_driver`
de `pool_navegadores.py` que usa Supramax), `/inicio` empezó a tapar el
formulario con un muro publicitario de terceros ("Desbloquea más
contenido... ver un anuncio para acceder 24h") después de varias corridas --
y confirmado que NO era por reusar perfil de Chrome (apareció igual con un
perfil recién creado). Lo que sí cambió las cosas: la misma marca/modelo/año
cargada a mano en el Chrome normal de Anibal (no automatizado) nunca mostró
el muro. Eso apunta a que el proveedor del muro está detectando que el
navegador es automatizado (`navigator.webdriver`, el propio "Chrome for
Testing" que resuelve Selenium Manager cuando no hay Chrome real instalado,
etc.) y no a un límite por IP. Por eso aquí se usa `undetected_chromedriver`
(mismo recurso que `pase_rpa.py` ya usa para saltar el WAF de Radware, con el
mismo resolver de binario/versión de `shared/navegador.py`) en vez de
Selenium "normal". El perfil sigue siendo desechable (un directorio temporal
por corrida, borrado al salir): aquí no hay sesión que conservar, y así
ninguna corrida hereda nada de la anterior.

Si el muro vuelve a aparecer A PESAR de `undetected_chromedriver`,
`_verificar_muro` lo detecta temprano y falla con un mensaje claro (en vez de
un timeout confuso esperando selects que nunca se van a llenar) -- en ese
caso sí habría que reconsiderar si de verdad es un límite por IP y no solo
detección de automatización.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Iterator

import undetected_chromedriver as uc
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from psycopg2.extras import Json
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ_PROYECTO not in sys.path:
    sys.path.insert(0, RAIZ_PROYECTO)

from shared.db import conectar_postgres  # noqa: E402
from shared.navegador import resolver_binario, version_mayor  # noqa: E402
from shared.numeros import a_decimal  # noqa: E402

load_dotenv()

LIBROAZUL_URL = os.getenv("LIBROAZUL_URL", "https://www.libroazul.com")
LIBROAZUL_HEADLESS = os.getenv("LIBROAZUL_HEADLESS", "true").strip().lower() != "false"

_ESPERA_CORTA = 15
_ESPERA_LARGA = 30

# Las tres condiciones que pregunta seleccion_condicion.html -- mismos
# nombres que `CondicionLibroAzul` en el DTO de NestJS
# (src/modules/libro-azul/dtos/libroazul.dto.ts), para no traducir de un lado
# al otro.
CONDICIONES_VALIDAS = ("Excelente", "Bueno", "Regular")

ProgresoCallback = Callable[[str, str], None]
CacheVersionesCallback = Callable[[list[str]], None]


class LibroAzulScrapeError(RuntimeError):
    """La página no trajo lo que se esperaba en algún paso del flujo."""


@dataclass
class PedidoCotizacion:
    """Lo que la persona confirmó en el modal antes de encolar -- ver
    `libroazul_valuacion_jobs` en `45_libroazul_valuacion_jobs.sql`."""

    nb_marca: str
    nb_modelo: str
    nu_claveanio: str
    nb_version: str
    cl_condicion: str

    def __post_init__(self) -> None:
        if self.cl_condicion not in CONDICIONES_VALIDAS:
            raise ValueError(
                f"cl_condicion debe ser una de {CONDICIONES_VALIDAS}, llegó '{self.cl_condicion}'."
            )


@dataclass
class ResultadoCotizacion:
    im_valor: Decimal
    nb_version_sitio: str
    url_precio: str


@contextmanager
def abrir_driver_desechable() -> Iterator:
    """Un Chrome con `undetected_chromedriver`, perfil nuevo en un directorio
    temporal y borrado al salir -- ver "UNDETECTED-CHROMEDRIVER..." en el
    docstring del módulo. La usan tanto `cotizar_unidad()` como
    `libroazul_versiones_rpa.buscar_versiones()`.

    Mismo patrón que `pase_rpa.py` para el binario/versión (`resolver_binario`/
    `version_mayor` de `shared/navegador.py`): `undetected_chromedriver` no
    trae el descubrimiento automático de Selenium Manager, así que sin esto
    falla con «Binary Location Must be a String» en una máquina sin Chrome
    real instalado."""
    perfil_dir = tempfile.mkdtemp(prefix="libroazul_")

    opciones = uc.ChromeOptions()
    if LIBROAZUL_HEADLESS:
        opciones.add_argument("--headless=new")
    opciones.add_argument("--window-size=1920,1080")

    binario = resolver_binario()
    if binario:
        opciones.binary_location = binario

    driver = uc.Chrome(
        options=opciones,
        user_data_dir=perfil_dir,
        browser_executable_path=binario,
        version_main=version_mayor(binario),
    )
    try:
        yield driver
    finally:
        driver.quit()
        shutil.rmtree(perfil_dir, ignore_errors=True)


def _sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def _normalizar(texto: str) -> str:
    """Comparación insensible a acentos, mayúsculas y espacios repetidos --
    el sitio escribe "M G" con espacio para MG y puede diferir en caja
    respecto a lo capturado en el modal."""
    limpio = _sin_acentos(texto or "").upper().strip()
    return re.sub(r"\s+", " ", limpio)


def _sin_progreso(clave: str, detalle: str) -> None:
    """Callback por default cuando quien llama no quiere seguimiento (ej.
    `main()` de prueba manual) -- no hace nada, para no obligar a todos los
    llamadores a pasar uno."""


def _seleccionar_por_texto_similar(select: Select, objetivo: str, nombre_campo: str) -> None:
    """`Select.select_by_visible_text` exige coincidencia exacta; los combos
    de libroazul.com traen variantes de caja/espacios frente a lo capturado
    en el modal. Tres niveles, del más al menos estricto:
    1) normalizado exacto;
    2) uno contiene al otro (ej. objetivo "MG5" contra opción "MG5 HYBRID+");
    3) igual ignorando TODOS los espacios -- el catálogo de EBC separa
       algunas marcas con espacio ("M G" para MG, "General Motors" sigue
       igual) mientras que la unidad puede traerla pegada ("MG"); un simple
       "contiene" no basta porque ninguna de las dos cadenas contiene a la
       otra cuando el espacio cae a la mitad de la palabra."""
    objetivo_norm = _normalizar(objetivo)
    opciones = select.options

    for opcion in opciones:
        if _normalizar(opcion.text) == objetivo_norm:
            select.select_by_visible_text(opcion.text)
            return

    for opcion in opciones:
        texto_norm = _normalizar(opcion.text)
        if objetivo_norm in texto_norm or texto_norm in objetivo_norm:
            select.select_by_visible_text(opcion.text)
            return

    objetivo_sin_espacios = objetivo_norm.replace(" ", "")
    for opcion in opciones:
        if _normalizar(opcion.text).replace(" ", "") == objetivo_sin_espacios:
            select.select_by_visible_text(opcion.text)
            return

    disponibles = ", ".join(o.text for o in opciones if o.get_attribute("value"))
    raise LibroAzulScrapeError(
        f"No se encontró '{objetivo}' entre las opciones de {nombre_campo} "
        f"del sitio. Disponibles: {disponibles or '(ninguna)'}"
    )


def _cerrar_banner_consentimiento(driver) -> None:
    """A veces el sitio tapa el formulario con el overlay de consentimiento
    de Google Funding Choices (`.fc-dialog-overlay`, confirmado en vivo el
    2026-10-01: el click a `usados_btn` salió `ElementClickIntercepted`
    porque ese overlay lo recibía en su lugar). No siempre aparece -- parece
    depender de si el perfil de Chrome ya tiene el consentimiento guardado de
    una corrida anterior -- así que se espera poco y no truena si no está."""
    try:
        boton = WebDriverWait(driver, 4).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, ".fc-cta-consent, .fc-button-consent, .fc-primary-button"))
        )
        boton.click()
        WebDriverWait(driver, 4).until(
            EC.invisibility_of_element_located((By.CSS_SELECTOR, ".fc-dialog-overlay"))
        )
    except Exception:  # noqa: BLE001
        # No apareció a tiempo (lo normal) o ya se cerró solo -- seguir.
        pass


def _verificar_muro(driver) -> None:
    """Detecta el muro publicitario de terceros ("Desbloquea más
    contenido... ver un anuncio para acceder 24h") que puede tapar
    `/inicio` -- ver "EL MURO PUBLICITARIO..." en el docstring del módulo.
    Falla rápido y con un mensaje claro en vez de dejar que los selects
    tronen por timeout, que es indistinguible de "el sitio tardó" sin mirar
    una screenshot."""
    if driver.find_elements(By.XPATH, "//*[contains(text(), 'Desbloquea más contenido')]"):
        raise LibroAzulScrapeError(
            "libroazul.com mostró su muro publicitario de terceros "
            "('Desbloquea más contenido') en vez del buscador. No es un bug "
            "del RPA ni se puede saltar automáticamente -- probablemente "
            "hubo demasiadas visitas seguidas desde esta IP. Reintenta en "
            "un rato."
        )


def _buscar_versiones(driver, nb_marca: str, nb_modelo: str, nu_claveanio: str) -> None:
    """Llena y envía `usados_form` de `/inicio` -- año (exacto, son los
    mismos números) y luego marca/modelo por texto (los combos de marca se
    recargan con JS al elegir año; modelo se recarga al elegir marca, mismo
    patrón visto en `inicio.html`). Toma los tres campos sueltos (no un
    `PedidoCotizacion` completo) para que `libroazul_versiones_rpa.py`
    -- que solo busca versiones, no cotiza -- la pueda reusar sin tener que
    inventar una versión/condición ficticias."""
    driver.get(f"{LIBROAZUL_URL}/inicio")
    _cerrar_banner_consentimiento(driver)
    _verificar_muro(driver)
    wait = WebDriverWait(driver, _ESPERA_CORTA)

    anio_select = Select(wait.until(EC.element_to_be_clickable((By.ID, "usados_anio"))))
    anio_select.select_by_value(str(nu_claveanio))

    marca_select = Select(wait.until(EC.element_to_be_clickable((By.ID, "usados_marca"))))
    # Esperar a que el JS del sitio rellene el combo de marcas tras elegir
    # año -- de entrada solo trae la opción placeholder deshabilitada.
    wait.until(lambda d: len(Select(d.find_element(By.ID, "usados_marca")).options) > 1)
    _seleccionar_por_texto_similar(marca_select, nb_marca, "marca")

    modelo_select = Select(driver.find_element(By.ID, "usados_modelo"))
    wait.until(lambda d: len(Select(d.find_element(By.ID, "usados_modelo")).options) > 1)
    _seleccionar_por_texto_similar(modelo_select, nb_modelo, "modelo")

    driver.find_element(By.ID, "usados_btn").click()

    # Aterriza en resultados_busqueda.html -- breadcrumb "Versiones", tarjetas
    # con clase "card-hover".
    wait_larga = WebDriverWait(driver, _ESPERA_LARGA)
    wait_larga.until(EC.presence_of_element_located((By.CSS_SELECTOR, "section .card-hover")))


def listar_versiones_disponibles(driver) -> list[tuple[str, str]]:
    """Lee las tarjetas de `resultados_busqueda.html` (una por versión, título
    en `h3.mb-2 a`, link a `seleccion_condicion.html`). Se usa tanto para
    elegir la versión pedida (`_elegir_version`) como para alimentar el
    autocomplete (`libroazul_versiones_rpa.py` y el `cache_versiones` de
    `cotizar()`) -- una sola fuente de verdad para "qué versiones hay"."""
    soup = BeautifulSoup(driver.page_source, "html.parser")
    tarjetas = soup.select("section .card-hover")

    opciones = []
    for tarjeta in tarjetas:
        link = tarjeta.select_one("h3 a")
        if not link or not link.get("href"):
            continue
        opciones.append((link.get_text(strip=True), link["href"]))

    return opciones


def _elegir_version(driver, nb_version_objetivo: str) -> None:
    """Hace click en la tarjeta de `resultados_busqueda.html` que mejor
    matchea `nb_version`. No hay ID numérico confiable que reusar del SOAP
    (el ID de la URL, ej. `11325`, es del sitio web público y no se sabe si
    coincide con `NU_ClaveVersion` del webservice) -- por eso se busca por
    texto en cada corrida en vez de saltar directo con un ID guardado."""
    objetivo_norm = _normalizar(nb_version_objetivo)
    opciones = listar_versiones_disponibles(driver)

    if not opciones:
        raise LibroAzulScrapeError(
            "resultados_busqueda.html no trajo ninguna tarjeta de versión "
            "para esta marca/modelo/año."
        )

    coincidencia_exacta = next(
        (href for texto, href in opciones if _normalizar(texto) == objetivo_norm), None
    )
    href_elegido = coincidencia_exacta

    if href_elegido is None:
        # Sin coincidencia exacta: la opción cuyo texto comparte más
        # palabras con la versión pedida -- más robusto que un simple
        # "contiene" cuando el sitio reordena tokens (ej. "L4/1.5 Aut" vs
        # "Aut L4/1.5").
        objetivo_tokens = set(objetivo_norm.split())
        mejor = max(
            opciones,
            key=lambda par: len(objetivo_tokens & set(_normalizar(par[0]).split())),
        )
        if objetivo_tokens & set(_normalizar(mejor[0]).split()):
            href_elegido = mejor[1]

    if href_elegido is None:
        disponibles = ", ".join(texto for texto, _ in opciones)
        raise LibroAzulScrapeError(
            f"No se encontró una versión parecida a '{nb_version_objetivo}' "
            f"en resultados_busqueda.html. Disponibles: {disponibles}"
        )

    driver.get(href_elegido if href_elegido.startswith("http") else f"{LIBROAZUL_URL}{href_elegido}")

    wait = WebDriverWait(driver, _ESPERA_LARGA)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/autosusados/precio/']")))


def _elegir_condicion(driver, condicion_objetivo: str) -> None:
    """En `seleccion_condicion.html`, cada tarjeta trae un `<h2>` con el
    nombre de la condición (Excelente/Bueno/Regular) y un botón "Consultar
    Precio" propio -- se ubica la tarjeta por el texto del h2, no por
    posición, por si el sitio llegara a reordenarlas."""
    soup = BeautifulSoup(driver.page_source, "html.parser")
    objetivo_norm = _normalizar(condicion_objetivo)

    href_elegido = None
    for tarjeta in soup.select(".card.shadow"):
        titulo = tarjeta.select_one("h2")
        if titulo and _normalizar(titulo.get_text(strip=True)) == objetivo_norm:
            link = tarjeta.select_one("a[href*='/autosusados/precio/']")
            if link and link.get("href"):
                href_elegido = link["href"]
                break

    if href_elegido is None:
        raise LibroAzulScrapeError(
            f"No se encontró la tarjeta de condición '{condicion_objetivo}' "
            "en seleccion_condicion.html."
        )

    driver.get(href_elegido if href_elegido.startswith("http") else f"{LIBROAZUL_URL}{href_elegido}")

    wait = WebDriverWait(driver, _ESPERA_LARGA)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "h3.text-primary")))


def _leer_precio(driver) -> ResultadoCotizacion:
    """`precio_auto.html`: el único precio publicado ("Precio") vive en un
    `<h3 class="mb-1 text-primary">$217,400</h3>` dentro de la primera
    columna del form -- justo debajo de un `<h3>Precio</h3>` hermano. Se
    localiza por la clase (`text-primary` + `mb-1`) en vez de por posición
    para no depender de que ese sea el primer `h3` de toda la página."""
    soup = BeautifulSoup(driver.page_source, "html.parser")

    nodo_precio = soup.select_one("h3.mb-1.text-primary")
    if not nodo_precio:
        raise LibroAzulScrapeError(
            "precio_auto.html no trajo el <h3> del precio (¿cambió la maqueta del sitio?)."
        )

    im_valor = a_decimal(nodo_precio.get_text(strip=True))
    if im_valor is None:
        raise LibroAzulScrapeError(
            f"No se pudo convertir a número el precio leído: '{nodo_precio.get_text(strip=True)}'."
        )

    nodo_version = soup.select_one("h6.text-primary")
    nb_version_sitio = nodo_version.get_text(strip=True) if nodo_version else ""

    return ResultadoCotizacion(im_valor=im_valor, nb_version_sitio=nb_version_sitio, url_precio=driver.current_url)


def _edicion_vigente() -> str:
    """«SEPTIEMBRE 2026», igual que `LibroAzulValuacionService.edicionVigente()`
    del lado NestJS -- se arma con la fecha del sistema, no preguntándole al
    sitio."""
    meses = [
        "ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO",
        "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE",
    ]
    hoy = time.localtime()
    return f"{meses[hoy.tm_mon - 1]} {hoy.tm_year}"


def cotizar(
    driver,
    pedido: PedidoCotizacion,
    progreso: ProgresoCallback = _sin_progreso,
    cache_versiones: CacheVersionesCallback | None = None,
) -> ResultadoCotizacion:
    """El flujo completo, sobre un `driver` ya abierto: buscar -> elegir
    versión -> elegir condición -> leer precio. Separado de `cotizar_unidad`
    para poder probarlo en un notebook/REPL con un driver manual sin tocar
    Postgres.

    `cache_versiones`, si se da, recibe la lista de TODAS las versiones que
    vio en `resultados_busqueda.html` -- de pasada, sin consulta extra al
    sitio, para alimentar `libroazul_versiones_cache` (el autocomplete del
    campo "Versión") aunque la persona ya haya escrito la versión exacta que
    buscaba."""
    progreso("BUSCANDO", f"Buscando {pedido.nb_marca} {pedido.nb_modelo} {pedido.nu_claveanio} en libroazul.com")
    _buscar_versiones(driver, pedido.nb_marca, pedido.nb_modelo, pedido.nu_claveanio)

    if cache_versiones is not None:
        cache_versiones([texto for texto, _ in listar_versiones_disponibles(driver)])

    progreso("ELIGIENDO_VERSION", f"Eligiendo la versión '{pedido.nb_version}'")
    _elegir_version(driver, pedido.nb_version)

    progreso("ELIGIENDO_CONDICION", f"Condición: {pedido.cl_condicion}")
    _elegir_condicion(driver, pedido.cl_condicion)

    progreso("LEYENDO_PRECIO", "Leyendo el precio sugerido de venta")
    return _leer_precio(driver)


def guardar_cache_versiones(conexion, nb_marca: str, nb_modelo: str, nu_claveanio: str, versiones: list[str]) -> None:
    """Upsert en `libroazul_versiones_cache` -- mismo índice único por el que
    el backend busca (`UPPER(nb_marca), UPPER(nb_modelo), nu_claveanio`, ver
    `46_libroazul_versiones_cache.sql`). Hace su propio commit porque se
    llama de pasada durante una cotización normal (que todavía no ha
    terminado) y una caché desactualizada no es motivo para tronar esa
    cotización si el commit final fallara por otra causa."""
    with conexion.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO libroazul_versiones_cache (nb_marca, nb_modelo, nu_claveanio, versiones, fh_consulta)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (UPPER(nb_marca), UPPER(nb_modelo), nu_claveanio)
            DO UPDATE SET versiones = EXCLUDED.versiones, fh_consulta = now()
            """,
            (nb_marca, nb_modelo, nu_claveanio, Json(versiones)),
        )
    conexion.commit()


def cotizar_unidad(
    id_unidad: int,
    pedido: PedidoCotizacion,
    nu_odometro: float | None,
    id_usuario: int,
    progreso: ProgresoCallback = _sin_progreso,
) -> dict:
    """Cotiza una unidad y deja el resultado como una fila nueva en
    `unidades_valuaciones` (`cl_fuente = 'scraping'`). Es lo que llama
    `libroazul_actualizar_bajo_demanda.py` por cada job de la cola, y
    también sirve para probar el RPA a mano desde la línea de comandos (ver
    `if __name__ == "__main__"` más abajo).
    """
    conexion = conectar_postgres()
    try:
        if nu_odometro is None:
            with conexion.cursor() as cursor:
                cursor.execute("SELECT nu_odometro FROM unidades WHERE id_unidad = %s", (id_unidad,))
                fila = cursor.fetchone()
                nu_odometro = float(fila[0]) if fila and fila[0] is not None else 0

        with abrir_driver_desechable() as driver:
            resultado = cotizar(
                driver,
                pedido,
                progreso,
                cache_versiones=lambda versiones: guardar_cache_versiones(
                    conexion, pedido.nb_marca, pedido.nb_modelo, pedido.nu_claveanio, versiones
                ),
            )

        with conexion.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO unidades_valuaciones
                    (id_unidad, im_valor, im_valorsinkm, im_valorcompra, nu_odometro,
                     cl_moneda, nb_fuente, cl_fuente, nu_claveversion, nb_version,
                     nb_edicion, sn_activo, id_usuariocreacion, fh_creacion, fh_valuacion)
                VALUES
                    (%s, %s, %s, NULL, %s,
                     'MXN', 'Guía EBC (Libro Azul) - sitio público', 'scraping', NULL, %s,
                     %s, true, %s, now(), current_date)
                RETURNING id_valuacion
                """,
                (
                    id_unidad,
                    resultado.im_valor,
                    # El sitio público no distingue "de tabla" vs "ajustado
                    # por km" -- solo hay un número. Ver docstring del
                    # módulo: se guarda igual en ambas columnas en vez de
                    # inventar un ajuste que el sitio no da.
                    resultado.im_valor,
                    nu_odometro,
                    pedido.nb_version,
                    _edicion_vigente(),
                    id_usuario,
                ),
            )
            id_valuacion = cursor.fetchone()[0]
        conexion.commit()

        return {
            "id_valuacion": id_valuacion,
            "im_valor": resultado.im_valor,
            "nb_version": pedido.nb_version,
            "nb_version_sitio": resultado.nb_version_sitio,
            "url_precio": resultado.url_precio,
            "nu_odometro": nu_odometro,
        }
    finally:
        conexion.close()


def main() -> int:
    """Prueba manual:
    `python -m scrapers.libroazul_precio_rpa <id_unidad> <marca> <modelo> <anio> <version> <condicion> [id_usuario]`.
    No usa la cola -- cotiza directo, igual que hará
    `libroazul_actualizar_bajo_demanda.py` por cada job, pero de una sentada
    para poder revisar en vivo que el flujo contra el sitio real sigue
    funcionando antes de dejarlo corriendo por launchd."""
    if len(sys.argv) < 7:
        print(
            "Uso: python -m scrapers.libroazul_precio_rpa "
            "<id_unidad> <marca> <modelo> <anio> <version> <condicion> [id_usuario]"
        )
        return 1

    id_unidad = int(sys.argv[1])
    pedido = PedidoCotizacion(
        nb_marca=sys.argv[2],
        nb_modelo=sys.argv[3],
        nu_claveanio=sys.argv[4],
        nb_version=sys.argv[5],
        cl_condicion=sys.argv[6],
    )
    id_usuario = int(sys.argv[7]) if len(sys.argv) > 7 else 1

    resultado = cotizar_unidad(id_unidad, pedido, None, id_usuario, progreso=lambda clave, detalle: print(f"  [{clave}] {detalle}"))
    print(f"✅ Unidad {id_unidad}: ${resultado['im_valor']:,.2f} ({resultado['nb_version']}) -> {resultado['url_precio']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
