"""Refresca la caché de saldos de Supramax (`supramax_saldos`) para que la
pantalla "Saldos y recargas" de Control Vehicular pueda mostrarlos junto con
Ticket Car sin abrir Chrome por cada quien la visite.

DISEÑADO PARA CORRER COMO PROCESO CORTO, DISPARADO POR CRON

Este script hace UNA pasada por todas las cuentas de `SUPRAMAX_CREDENTIALS` y
termina -- no trae un loop interno de "cada 30 minutos". Ese intervalo lo
pone quien lo invoca: hoy `saldos_supramax_cada_30min.sh` vía cron/launchd en
la Mac de Anibal, más adelante (sin tocar este archivo) un Cloud Run Job
disparado por Cloud Scheduler con el mismo período. No guarda estado propio
en disco entre corridas -- lo único persistente es el perfil de Chrome de
Selenium, que solo sirve para no repetir el login de cero cada vez que
Supramax lo permite.

TODA LA CONFIGURACIÓN SALE DE VARIABLES DE ENTORNO / SECRET MANAGER

`SUPRAMAX_CREDENTIALS` (cuentas), `SUPRAMAX_URL` (portal), `SUPRAMAX_MAX_WORKERS`
(tamaño del pool) y `SECRETO_CONTROL_VEHICULAR` (credenciales de Postgres) --
nada de esto está fijo en el código. Es lo mismo que pide migrar esto a Cloud
Run después: el mismo contenedor sirve para el cron local de hoy y para un
job remoto mañana con solo cambiar variables de entorno.

VARIAS CUENTAS EN PARALELO, PERFILES DE CHROME SEPARADOS

Se usa `shared/pool_navegadores.py`: cada cuenta de empresa corre en su
propio thread con su propio `webdriver` y su propio directorio de perfil de
Chrome (`chrome_profile/supramax/saldos/<empresa>`), para no compartir sesión
entre cuentas concurrentes ni con `supramax_rpa.py`
(`chrome_profile/supramax`) o `supramax_dispersion_rpa.py`
(`chrome_profile/supramax/dispersion/<empresa>`).

CÓMO SE CRUZA CADA FILA DEL PORTAL CONTRA NUESTRA BASE

El riesgo 1 del plan aprobado asumía que `tarjetas.nu_foliotarjeta` seguía
siendo igual al `idv` del portal (el query param de
`intmain.php?nd=1&ad=crd&idv=<idv>`). Comprobado en vivo (2026-09-16, primera
corrida real): esa suposición era falsa -- las 19 cuentas loguearon bien pero
0 saldos cruzaron. `nu_foliotarjeta`, para `id_tipotarjeta = 5`, no es el
`idv`: es el TAG NFC (`0442BABB780000`, mismo formato hex que la columna
"Tag" del listado, ver `db/datos/007_tarjetas.sql`). El `idv` es solo el
identificador interno de Supramax para armar la URL del formulario de
dispersión -- no vive en ningún lado de nuestra base y no hace falta que
viva: se relee del portal en cada refresco.

El cruce real es por TAG, no por `idv`. `id_vehiculosupramax` se sigue
guardando (es lo que necesita `supramax_dispersion_rpa.py` para navegar al
vehículo correcto al dispersar), pero la llave de cruce contra
`tarjetas.nu_foliotarjeta` (con `id_tipotarjeta IN (5, 1)`) es el tag,
normalizado a mayúsculas por si el portal y nuestra captura difieren en caja.

Un vehículo de Supramax cuyo tag no cruza contra ninguna `nu_foliotarjeta` no
es un error: es esperable que existan vehículos de la cuenta que no están en
nuestro catálogo todavía (o, para `id_tipotarjeta = 1` / Petroil Pay, cuyo
`nu_foliotarjeta` es un PIN corto y no un tag -- esos probablemente nunca
crucen por esta vía y son un pendiente aparte, no un bug de este script). Se
registra en el log sin tronar (ver `_guardar_resultados`).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
from decimal import Decimal

import psycopg2
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ_PROYECTO not in sys.path:
    sys.path.insert(0, RAIZ_PROYECTO)

from shared.db import conectar_postgres  # noqa: E402
from shared.numeros import a_decimal  # noqa: E402
from shared.credenciales import resolver_cuentas  # noqa: E402
from shared.pool_navegadores import ejecutar_en_paralelo  # noqa: E402
from shared.supramax_login import iniciar_sesion  # noqa: E402

load_dotenv()

# `id_tipotarjeta` de Supramax (5) y Petroil Pay (1) -- ambas se dispersan en
# Supramax, ver `db/esquema/28_solicitudes_combustible.sql`.
TIPOS_TARJETA_SUPRAMAX = (5, 1)

# El tipo con el que se da de alta una tarjeta "Prestación" NUEVA -- siempre
# el 5 (Supramax) vigente, nunca el 1 (Petroil Pay) legado. Debe coincidir
# con `TIPO_TARJETA_SUPRAMAX` en
# `control-vehicular/src/modules/solicitud/services/prestaciones.service.ts`.
TIPO_TARJETA_SUPRAMAX_NUEVA = 5

IDV_RE = re.compile(r"idv=(\d+)")

# "PRE-1234", "pre 1234", "PRE1234" -- mismo criterio que
# `PrestacionesService.esPrestacion()` en el backend: letras "PRE" seguidas
# (con separador opcional) de al menos un dígito.
PRESTACION_RE = re.compile(r"^pre[\s-]?\d+", re.IGNORECASE)


def _es_prestacion(placas: str) -> bool:
    return bool(PRESTACION_RE.match(placas.strip()))


# `im_credito` es numeric(12, 2): hasta 9,999,999,999.99. Lo que no cabe no es
# un saldo, es basura del portal (AU-170 llegó como 5.358E+368).
LIMITE_CREDITO = Decimal("10000000000")


def _credito_valido(credito: Decimal) -> bool:
    """Sin aritmética a propósito: `abs()` sobre el valor de AU-170 desborda el
    contexto de `decimal` y truena antes de poder compararlo. Comparar no
    redondea, así que no puede desbordarse; `is_finite` descarta NaN e
    infinito, que `a_decimal` también deja pasar."""
    return credito.is_finite() and -LIMITE_CREDITO < credito < LIMITE_CREDITO


def _parsear_filas(html: str) -> list[dict]:
    """Cada `<tr>` del listado trae: link con `idv`, Placas, Tipo (nombre del
    conductor -- mal rotulado en el portal, se ignora), Marca (número interno,
    se ignora), Tag, Crédito. Confirmado leyendo
    `html_pages/supramax/listado_dispersion.html` real: las columnas por
    posición son estables aunque el encabezado "Tipo" no describa lo que
    trae."""
    soup = BeautifulSoup(html, "html.parser")
    tabla = soup.find("table", id="reporte")
    if not tabla:
        return []

    tbody = tabla.find("tbody")
    if not tbody:
        return []

    filas = []
    for tr in tbody.find_all("tr"):
        celdas = tr.find_all("td")
        if len(celdas) < 6:
            continue

        link = celdas[0].find("a")
        href = link.get("href", "") if link else ""
        m = IDV_RE.search(href)
        if not m:
            continue

        filas.append(
            {
                "idv": m.group(1),
                "placas": celdas[1].get_text(strip=True),
                "tag": celdas[4].get_text(strip=True),
                "credito": a_decimal(celdas[5].get_text(strip=True)),
            }
        )

    return filas


def _recolectar_todas_las_filas(driver, wait) -> list[dict]:
    """Recorre la paginación de DataTables (`#reporte_next`) hasta que el
    botón "siguiente" quede deshabilitado. Se navega por el DOM en vez de
    asumir que todas las filas ya están cargadas de entrada: no está probado
    si `dataTable({bFilter:true})` pagina del lado del cliente sobre todas
    las filas o si el portal las manda por página."""
    filas: list[dict] = []
    vistos: set[str] = set()

    while True:
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "reporte")))
        time.sleep(0.3)

        for fila in _parsear_filas(driver.page_source):
            if fila["idv"] not in vistos:
                vistos.add(fila["idv"])
                filas.append(fila)

        try:
            siguiente = driver.find_element(By.ID, "reporte_next")
        except Exception:  # noqa: BLE001
            break

        clases = siguiente.get_attribute("class") or ""
        if "paginate_button_disabled" in clases:
            break

        driver.execute_script("arguments[0].click();", siguiente)
        time.sleep(1)

    return filas


def _reportar(avance, cuenta: dict, paso: str, **extra) -> None:
    """Avisa el paso de una cuenta a quien esta mirando, si hay alguien.

    `avance` solo existe cuando la corrida la pidio el boton de la pantalla
    (ver `supramax_actualizar_bajo_demanda.Avance`); la de cada 30 minutos lo
    deja en None. Un fallo al reportar nunca tumba el scraping: el avance es
    para informar, no parte del trabajo."""
    if avance is None:
        return
    try:
        avance.paso(cuenta.get("Empresa", "?"), paso, **extra)
    except Exception as error:  # noqa: BLE001
        print(f"⚠️ No se pudo reportar el avance ({paso}): {error}")


# Lo que Selenium lanza, dicho como lo entiende quien mira la pantalla.
_ERRORES_LEGIBLES = {
    "TimeoutException": "Se agotó el tiempo esperando al portal.",
    "NoSuchElementException": "El portal no mostró la página esperada.",
    "WebDriverException": "El navegador del robot falló.",
    "SessionNotCreatedException": "No se pudo abrir el navegador del robot.",
}


def _error_legible(error: str | None) -> str:
    """Un renglón que explique el error de una cuenta.

    El primer renglón no sirve: las excepciones de Selenium sin mensaje dejan
    solo "Message: " (asi quedo Petroil Marine el 2026-10-24). Se busca, de
    abajo hacia arriba en la traza, el renglón con el tipo de excepción."""
    renglones = [r.strip() for r in (error or "").splitlines() if r.strip()]
    for renglon in reversed(renglones):
        for tipo, texto in _ERRORES_LEGIBLES.items():
            if tipo in renglon:
                return texto
        if re.search(r"\w+(Exception|Error)\b", renglon):
            return renglon[:200]
    return renglones[0][:200] if renglones else "Error inesperado."


def _procesar_cuenta(driver, cuenta: dict, base_url: str, descargas_dir: str, avance=None) -> dict:
    """Login (vía `shared/supramax_login.py`) + navegar al listado de
    créditos + parsear. Se ejecuta dentro de un worker del pool -- cualquier
    excepción la atrapa `pool_navegadores._correr_cuenta`, no hace falta un
    try/except propio aquí salvo para el caso de login fallido, que no es un
    error inesperado sino algo que hay que decidir cómo reportar."""
    _reportar(avance, cuenta, "SESION")

    if not iniciar_sesion(driver, cuenta, base_url, descargas_dir, "saldos"):
        _reportar(avance, cuenta, "ERROR_LOGIN", detalle="No se pudo iniciar sesión en el portal.")
        return {"login_ok": False, "filas": []}

    _reportar(avance, cuenta, "LEYENDO")

    wait = WebDriverWait(driver, 15)
    url_creditos = f"{base_url}intmain.php?nd=1&ad=crd"
    driver.get(url_creditos)

    filas = _recolectar_todas_las_filas(driver, wait)
    _reportar(avance, cuenta, "LEIDA", tarjetas=len(filas))
    return {"login_ok": True, "filas": filas}


def _mapa_tarjetas_supramax(cursor) -> dict[str, tuple[int, int]]:
    cursor.execute(
        """
        SELECT id_empresa, id_tarjeta, nu_foliotarjeta
          FROM tarjetas
         WHERE id_tipotarjeta = ANY(%s)
        """,
        (list(TIPOS_TARJETA_SUPRAMAX),),
    )

    # Normalizado a mayúsculas: el portal y nuestra captura manual no
    # siempre coinciden en caja para el mismo tag hex.
    mapa: dict[str, tuple[int, int]] = {}
    for id_empresa, id_tarjeta, folio in cursor.fetchall():
        folio_norm = (folio or "").strip().upper()
        if folio_norm:
            mapa[folio_norm] = (id_empresa, id_tarjeta)

    return mapa


def _sin_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )


def _id_empresa_por_nombre(cursor, nombre: str) -> int | None:
    """Cruce EXACTO contra `empresas.nb_empresa`, insensible a mayúsculas y
    acentos ("OCEÁNICA" del portal debe encontrar "OCEANICA" en la base). Se
    hace en Python, no en SQL (`empresas` es una tabla chica, ~60 filas):
    Postgres sin la extensión `unaccent` no tiene forma corta de comparar sin
    acentos."""
    cursor.execute("SELECT id_empresa, nb_empresa FROM empresas")
    objetivo = _sin_acentos(nombre).strip().upper()

    for id_empresa, nb_empresa in cursor.fetchall():
        if _sin_acentos(nb_empresa or "").strip().upper() == objetivo:
            return id_empresa

    return None


def _id_empresa_unico_por_cuenta(cursor, nombre_cuenta: str) -> int | None:
    """El `id_empresa` real de una cuenta de Supramax, derivado de lo que YA
    cruzó para ella en `supramax_saldos` -- para cuando el nombre de la
    cuenta no calza ni siquiera sin acentos con ninguna `empresas.nb_empresa`
    (apodos/abreviaturas como "MEXCALPITAL", "ASAMAZ UTILITARIOS",
    "es9326.no-ip.net"). Solo regresa un `id_empresa` cuando hay uno único
    entre las tarjetas ya catalogadas de esa cuenta -- si hay más de uno
    (confirmado en producción para "OBSERVATORIO", "ROEH" y "SHEKINAH", que sí
    reparten vehículos entre dos empresas), se regresa `None` y se omite en
    vez de adivinar a cuál de las dos pertenece la prestación nueva."""
    cursor.execute(
        "SELECT DISTINCT id_empresa FROM supramax_saldos WHERE nb_cuenta = %s",
        (nombre_cuenta,),
    )
    filas = cursor.fetchall()
    return filas[0][0] if len(filas) == 1 else None


def _resolver_id_empresa_cuenta(cursor, nombre_cuenta: str) -> int | None:
    """El resolver que de verdad se usa para catalogar una prestación nueva:
    primero el nombre EXACTO de la cuenta (la señal más confiable cuando
    existe), y solo si no hay ninguna coincidencia, lo ya cruzado para esa
    cuenta -- nunca al revés, porque un nombre exacto pesa más que una
    coincidencia estadística."""
    return _id_empresa_por_nombre(cursor, nombre_cuenta) or _id_empresa_unico_por_cuenta(
        cursor, nombre_cuenta
    )


def _asegurar_tarjeta_prestacion(cursor, id_empresa: int, tag: str) -> int:
    """Espejo, en SQL directo, de `PrestacionesService.asegurarTarjetaPrestacion()`
    en el backend Nest -- el RPA no puede llamar ese servicio, así que repite
    la misma lógica idempotente aquí: da de alta la tarjeta en `tarjetas` si
    el tag es nuevo, y su fila en `colaboradores_tarjetas` (con
    `id_usuario` NULL, a la espera de que un administrador la asigne desde
    la pantalla). Devuelve `id_tarjeta`."""
    cursor.execute(
        "SELECT id_tarjeta, id_tarjetaglobal FROM tarjetas WHERE id_empresa = %s AND nu_foliotarjeta = %s",
        (id_empresa, tag),
    )
    fila = cursor.fetchone()

    if fila:
        id_tarjeta, id_tarjetaglobal = fila
    else:
        cursor.execute(
            """
            INSERT INTO tarjetas
                (id_empresa, id_tarjeta, id_tipotarjeta, nu_foliotarjeta, fh_creacion, sn_activo)
            SELECT %s, coalesce(max(id_tarjeta), 0) + 1, %s, %s, now(), true
              FROM tarjetas WHERE id_empresa = %s
            RETURNING id_tarjeta, id_tarjetaglobal
            """,
            (id_empresa, TIPO_TARJETA_SUPRAMAX_NUEVA, tag, id_empresa),
        )
        id_tarjeta, id_tarjetaglobal = cursor.fetchone()

    cursor.execute(
        """
        INSERT INTO colaboradores_tarjetas (id_tarjetaglobal)
        VALUES (%s)
        ON CONFLICT (id_tarjetaglobal) DO NOTHING
        """,
        (id_tarjetaglobal,),
    )

    return id_tarjeta


def _guardar_resultados(conexion, resultados) -> dict:
    """Además de escribir en `supramax_saldos`, regresa un resumen numérico
    (`total_login_ok` sobre todo) -- lo necesita
    `supramax_actualizar_bajo_demanda.py` para decidir si el job bajo demanda
    se marca COMPLETADO (aunque algunas cuentas hayan fallado, que es lo
    normal) o FALLIDO (ninguna cuenta se pudo ni siquiera loguear)."""
    with conexion.cursor() as cursor:
        mapa = _mapa_tarjetas_supramax(cursor)

        total_ok = 0
        total_descartadas = 0
        total_sin_cruce = 0
        total_login_ok = 0
        total_prestaciones = 0

        for resultado in resultados:
            empresa_nombre = resultado.cuenta.get("Empresa", "?")

            if not resultado.ok:
                print(f"❌ Cuenta {empresa_nombre} falló al procesarse en el pool: {resultado.error}")
                continue

            datos = resultado.resultado or {}

            if not datos.get("login_ok"):
                print(f"⚠️ Login fallido para {empresa_nombre}: sin saldos nuevos para esta cuenta.")
                # Se marca ERROR_LOGIN sobre lo que ya teníamos de esta
                # empresa (si algo), para que la pantalla no muestre un saldo
                # viejo como si fuera fresco. No se inserta nada nuevo: sin
                # login no hay credito que leer.
                id_empresa = _id_empresa_por_nombre(cursor, empresa_nombre)
                if id_empresa is not None:
                    cursor.execute(
                        "UPDATE supramax_saldos SET cl_estatus = 'ERROR_LOGIN' WHERE id_empresa = %s",
                        (id_empresa,),
                    )
                continue

            total_login_ok += 1

            # Se resuelve una sola vez por cuenta, perezoso -- solo hace
            # falta si de verdad aparece una fila "PRE-" sin tarjeta todavía.
            id_empresa_cuenta: int | None = None
            id_empresa_cuenta_resuelto = False

            for fila in datos.get("filas", []):
                # Por TAG, no por idv: ver "CÓMO SE CRUZA..." en el
                # docstring del módulo -- nu_foliotarjeta guarda el tag, el
                # idv es solo el identificador de sesión del portal.
                tag_norm = fila["tag"].strip().upper()
                match = mapa.get(tag_norm)

                if not match and _es_prestacion(fila["placas"]):
                    if not id_empresa_cuenta_resuelto:
                        id_empresa_cuenta = _resolver_id_empresa_cuenta(cursor, empresa_nombre)
                        id_empresa_cuenta_resuelto = True

                    if id_empresa_cuenta is not None:
                        id_tarjeta_nueva = _asegurar_tarjeta_prestacion(cursor, id_empresa_cuenta, tag_norm)
                        match = (id_empresa_cuenta, id_tarjeta_nueva)
                        mapa[tag_norm] = match
                        total_prestaciones += 1

                if not match:
                    total_sin_cruce += 1
                    continue

                id_empresa, id_tarjeta = match

                # Celda de crédito vacía en el portal = 0.
                credito = fila["credito"] if fila["credito"] is not None else Decimal(0)

                # Un valor imposible no se guarda ni como 0 -- eso se leería
                # como tarjeta sin crédito --: se salta y la tarjeta conserva
                # su último saldo bueno, con su fecha vieja a la vista.
                if not _credito_valido(credito):
                    print(f"⚠️ {empresa_nombre} {fila['placas']}: crédito imposible ({credito}), se conserva el saldo anterior.")
                    total_descartadas += 1
                    continue

                # Cada fila en su propio savepoint. Todo se confirma con un solo
                # commit al final, así que un error de una sola tarjeta
                # abortaba la transacción y ninguna de las 20 cuentas guardaba
                # nada: pasó en 93 de 375 corridas entre el 5 y el 11 de
                # octubre de 2026 por créditos vacíos, y luego por uno
                # desbordado. Ahora falla esa fila y las demás siguen.
                cursor.execute("SAVEPOINT fila_saldo")
                try:
                    cursor.execute(
                        """
                        INSERT INTO supramax_saldos
                            (id_empresa, id_tarjeta, id_vehiculosupramax, nu_placas, nu_tag, im_credito, nb_cuenta, fh_consulta, cl_estatus)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, now(), 'OK')
                        ON CONFLICT (id_empresa, id_tarjeta) DO UPDATE SET
                            id_vehiculosupramax = EXCLUDED.id_vehiculosupramax,
                            nu_placas           = EXCLUDED.nu_placas,
                            nu_tag              = EXCLUDED.nu_tag,
                            im_credito          = EXCLUDED.im_credito,
                            nb_cuenta           = EXCLUDED.nb_cuenta,
                            fh_consulta         = now(),
                            cl_estatus          = 'OK'
                        """,
                        (
                            id_empresa,
                            id_tarjeta,
                            fila["idv"],
                            fila["placas"],
                            fila["tag"],
                            credito,
                            # La cuenta REAL de Supramax bajo la que vive esta
                            # tarjeta -- puede no coincidir con
                            # empresas.nb_empresa (ver 34_supramax_saldos_cuenta.sql).
                            # Es lo que usa supramax_dispersion_rpa.py para
                            # saber en qué cuenta hacer login antes de dispersar.
                            empresa_nombre,
                        ),
                    )
                except psycopg2.Error as error:
                    cursor.execute("ROLLBACK TO SAVEPOINT fila_saldo")
                    print(f"⚠️ {empresa_nombre} {fila['placas']}: no se pudo guardar el saldo ({error.pgerror or error}).")
                    total_descartadas += 1
                    continue

                cursor.execute("RELEASE SAVEPOINT fila_saldo")
                total_ok += 1

        conexion.commit()

    print(
        f"✅ {total_ok} saldos de Supramax actualizados ({total_prestaciones} prestación(es) nueva(s) catalogada(s)). "
        f"{total_sin_cruce} vehículos del portal sin tarjeta nuestra (ver riesgo 1 del plan). "
        f"{total_descartadas} descartado(s) por dato inválido."
    )

    return {
        "total_saldos_ok": total_ok,
        "total_descartadas": total_descartadas,
        "total_sin_cruce": total_sin_cruce,
        "total_login_ok": total_login_ok,
        "total_prestaciones": total_prestaciones,
    }


def ejecutar_actualizacion_saldos(avance=None) -> dict:
    """Cuerpo real del refresco de saldos -- antes vivía inline en `main()`.
    Se extrae para que dos disparadores distintos lo reusen sin duplicar la
    lógica de scraping: el cron de 30 minutos (`saldos_supramax_cada_30min.sh`,
    vía `main()` más abajo) y el disparador bajo demanda del botón
    "Actualizar Supramax" (`scrapers/supramax_actualizar_bajo_demanda.py`,
    que importa y llama esta función directo).

    Lanza `RuntimeError` únicamente cuando la configuración impide correr la
    rutina POR COMPLETO (`SUPRAMAX_CREDENTIALS` ausente o con JSON inválido):
    eso sí es "la rutina entera no pudo correr". Un login fallido para una
    cuenta puntual NO lanza excepción -- es el comportamiento esperado (ver
    `_guardar_resultados`) y se refleja en el diccionario que se regresa, para
    que quien llama decida qué hacer con un fallo parcial vs. total (el
    disparador bajo demanda, por ejemplo, solo marca su job como FALLIDO si
    `total_login_ok` sale en cero).
    """
    # Primero la tabla `proveedores_credenciales`, y solo si está vacía el
    # `.env` de siempre -- ver `shared/credenciales.resolver_cuentas`.
    cuentas = resolver_cuentas("SUPRAMAX", "SUPRAMAX_CREDENTIALS")

    if not cuentas:
        print("No hay cuentas de Supramax activas; nada que procesar.")
        return {
            "total_cuentas": 0,
            "total_login_ok": 0,
            "total_saldos_ok": 0,
            "total_sin_cruce": 0,
            "total_prestaciones": 0,
        }

    max_workers = int(os.getenv("SUPRAMAX_MAX_WORKERS", "3"))
    headless = os.getenv("SUPRAMAX_HEADLESS", "true").strip().lower() != "false"

    base_url = os.getenv("SUPRAMAX_URL", "https://es09326.migasolinera.net/emaxclie/")
    if not base_url.endswith("/"):
        base_url += "/"

    descargas_dir = os.path.join(os.getcwd(), "descargas_temporales")
    os.makedirs(descargas_dir, exist_ok=True)

    # Prefijo de perfil propio de este script: nunca `chrome_profile/supramax`
    # a secas (ese es de `supramax_rpa.py`) ni `.../dispersion` (ese es de
    # `supramax_dispersion_rpa.py`).
    perfil_base = os.path.join(os.getcwd(), "chrome_profile", "supramax", "saldos")

    print(f"Iniciando refresco de saldos de Supramax: {len(cuentas)} cuenta(s), hasta {max_workers} en paralelo.")

    if avance is not None:
        avance.iniciar([c.get("Empresa", "?") for c in cuentas])

    resultados = ejecutar_en_paralelo(
        cuentas=cuentas,
        max_workers=max_workers,
        funcion_por_cuenta=lambda driver, cuenta: _procesar_cuenta(
            driver, cuenta, base_url, descargas_dir, avance
        ),
        perfil_base=perfil_base,
        headless=headless,
        descargas_dir=descargas_dir,
    )

    # Las que reventaron dentro del pool (Chrome no abrio, excepcion no
    # prevista) no alcanzaron a reportar nada propio.
    for resultado in resultados:
        if not resultado.ok:
            _reportar(avance, resultado.cuenta, "ERROR", detalle=_error_legible(resultado.error))

    if avance is not None:
        avance.fase("GUARDANDO")

    conexion = conectar_postgres()
    try:
        stats = _guardar_resultados(conexion, resultados)
    finally:
        conexion.close()

    for resultado in resultados:
        if resultado.ok and (resultado.resultado or {}).get("login_ok"):
            _reportar(avance, resultado.cuenta, "LISTO")

    stats["total_cuentas"] = len(cuentas)
    return stats


def main() -> int:
    try:
        ejecutar_actualizacion_saldos()
    except RuntimeError as error:
        print(f"ERROR: {error}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
