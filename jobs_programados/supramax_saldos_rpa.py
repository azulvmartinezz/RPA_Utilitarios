"""Refresca la caché de saldos de Supramax (`supramax_saldos`) para que la
pantalla "Saldos y recargas" de Control Vehicular pueda mostrarlos junto con
Ticket Car sin abrir Chrome por cada quien la visite.

DISEÑADO PARA CORRER COMO PROCESO CORTO

Este script hace UNA pasada por todas las cuentas y termina -- no trae un
loop interno de "cada 30 minutos". Ese intervalo lo pone quien lo invoca: el
scheduler (`POST /jobs/supramax-saldos`) o launchd en la Mac. Lo usa también
el botón «Actualizar Supramax» (`supramax_actualizar_bajo_demanda.py`).

SIN NAVEGADOR (DESDE EL 2026-11-09)

Antes abría un Chrome por cuenta, tres a la vez: 2,753 MB medidos, y el pod
de test (1 GB) se reiniciaba cada vez que alguien pedía «Actualizar
Supramax». Ahora es HTTP puro (`shared/supramax_http.py`): login por POST de
formulario y un GET de `intmain.php?nd=1&ad=crd`, que trae TODAS las tarjetas
de la cuenta en un solo HTML -- el DataTables pagina del lado del navegador,
no del servidor. 155 MB el proceso entero, mismas tarjetas que con Chrome.

Las cuentas se leen en paralelo con hilos (`SUPRAMAX_HTTP_WORKERS`, 4 por
omisión): cada una tarda ~6 s, casi todo esperando al portal, y un hilo con
una sesión de `requests` no pesa nada. Cada hilo tiene su propia sesión, así
que no comparten cookies.

TODA LA CONFIGURACIÓN SALE DE VARIABLES DE ENTORNO / SECRET MANAGER

Credenciales (`proveedores_credenciales`, o `SUPRAMAX_CREDENTIALS` de
respaldo), `SUPRAMAX_URL` (portal), `SUPRAMAX_HTTP_WORKERS` y
`SECRETO_CONTROL_VEHICULAR` (Postgres) -- nada de esto está fijo en el código.

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

import concurrent.futures
import os
import re
import sys
import traceback
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

import psycopg2
from bs4 import BeautifulSoup
from dotenv import load_dotenv

RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ_PROYECTO not in sys.path:
    sys.path.insert(0, RAIZ_PROYECTO)

from shared.db import conectar_postgres  # noqa: E402
from shared.numeros import a_decimal  # noqa: E402
from shared.credenciales import resolver_cuentas  # noqa: E402
from shared.supramax_http import LoginRechazado, entrar, nueva_sesion, salir, url_base  # noqa: E402

load_dotenv()

# El listado trae todo en una página: 120 KB la cuenta más grande. El tope es
# para un portal colgado, no para una página lenta.
ESPERA_LISTADO = 90

# Lo que pone el portal en lugar de la tabla cuando la cuenta no tiene
# vehículos (Petroil Marine) y cuando la sesión ya no sirve.
SIN_VEHICULOS = "No existen registros de veh"
SESION_CADUCADA = "Su sesion de usuario ha caducado"


@dataclass
class ResultadoCuenta:
    """Lo mismo que regresaba el pool de Chrome, para que `_guardar_resultados`
    no cambie: `ok` es «se pudo procesar», `resultado` trae `login_ok` y
    `filas`, y `error` la traza si algo reventó."""

    cuenta: dict
    resultado: dict | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

# `id_tipotarjeta` de Supramax (5) y Petroil Pay (1) -- ambas se dispersan en
# Supramax, ver `db/esquema/28_solicitudes_combustible.sql`.
TIPOS_TARJETA_SUPRAMAX = (5, 1)

# El tipo con el que se da de alta una tarjeta "Prestación" NUEVA -- siempre
# el 5 (Supramax) vigente, nunca el 1 (Petroil Pay) legado. Debe coincidir
# con `TIPO_TARJETA_SUPRAMAX` en
# `control-vehicular/src/modules/solicitud/services/prestaciones.service.ts`.
TIPO_TARJETA_SUPRAMAX_NUEVA = 5

IDV_RE = re.compile(r"idv=(\d+)")
# El tag de Supramax: hexadecimal de 12 a 16 caracteres («53A16E4A500001»).
TAG_RE = re.compile(r"[0-9A-Fa-f]{12,16}")

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
    """Cada `<tr>` del listado trae: link con `idv`, Placas, nombre del
    conductor, número interno, Tag y Crédito. Las placas van fijas en la
    segunda celda; el tag y el crédito se buscan por su forma porque el
    portal ya cambió las columnas una vez (octubre de 2026)."""
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

        textos = [c.get_text(strip=True) for c in celdas]

        # El tag y el crédito se reconocen por su FORMA, no por su posición:
        # a principios de octubre de 2026 el portal metió una columna vacía
        # antes del tag y todo se recorrió un lugar. El robot leyó el tag
        # vacío en todas las filas -todas cruzaban con la misma prestación de
        # folio vacío y se pisaban entre sí- y el tag como crédito
        # («5.358E+368700004»). 213 tarjetas se quedaron con su saldo del
        # 5 de octubre sin que nada fallara.
        tag = next((t for t in textos[2:] if TAG_RE.fullmatch(t)), "")
        credito = next((t for t in textos[2:] if t.startswith("$")), None)

        filas.append(
            {
                "idv": m.group(1),
                "placas": textos[1],
                "tag": tag,
                "credito": a_decimal(credito) if credito is not None else None,
            }
        )

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


# Lo que lanza la red, dicho como lo entiende quien mira la pantalla.
_ERRORES_LEGIBLES = {
    "ReadTimeout": "Se agotó el tiempo esperando al portal.",
    "ConnectTimeout": "El portal no contestó.",
    "ConnectionError": "No se pudo conectar con el portal.",
    "SSLError": "Falló la conexión segura con el portal.",
}


def _error_legible(error: str | None) -> str:
    """Un renglón que explique el error de una cuenta.

    Se busca, de abajo hacia arriba en la traza, el renglón con el tipo de
    excepción: el primero suele ser «Traceback (most recent call last):»."""
    renglones = [r.strip() for r in (error or "").splitlines() if r.strip()]
    for renglon in reversed(renglones):
        for tipo, texto in _ERRORES_LEGIBLES.items():
            if tipo in renglon:
                return texto
        if re.search(r"\w+(Exception|Error)\b", renglon):
            return renglon[:200]
    return renglones[0][:200] if renglones else "Error inesperado."


def _leer_listado(html: str) -> list[dict]:
    """Las filas del listado de créditos, distinguiendo «la cuenta no tiene
    vehículos» de «el portal no nos mostró el listado»: las dos dejan cero
    filas, pero la segunda no es un saldo y no debe guardarse como tal."""
    if 'id="reporte"' in html:
        return _parsear_filas(html)
    if SIN_VEHICULOS in html:
        return []
    if SESION_CADUCADA in html:
        raise RuntimeError("el portal cerró la sesión antes de mostrar los créditos")
    raise RuntimeError("el portal no mostró el listado de créditos")


def _procesar_cuenta(cuenta: dict, avance=None) -> dict:
    """Login + listado de créditos + parseo, por HTTP. Corre en un hilo del
    pool; lo que no sea un login rechazado lo atrapa `_correr_cuenta`.

    El login rechazado no es una excepción aquí: es un resultado esperado
    (`login_ok` en falso) que `_guardar_resultados` convierte en ERROR_LOGIN
    sobre los saldos de esa empresa."""
    _reportar(avance, cuenta, "SESION")
    sesion = nueva_sesion()

    try:
        try:
            entrar(sesion, cuenta)
        except LoginRechazado:
            _reportar(avance, cuenta, "ERROR_LOGIN", detalle="El portal rechazó usuario o contraseña.")
            return {"login_ok": False, "filas": []}

        _reportar(avance, cuenta, "LEYENDO")
        respuesta = sesion.get(
            f"{url_base()}intmain.php", params={"nd": 1, "ad": "crd"}, timeout=ESPERA_LISTADO
        )
        respuesta.raise_for_status()
        filas = _leer_listado(respuesta.content.decode("latin1"))
    finally:
        salir(sesion)
        sesion.close()

    _reportar(avance, cuenta, "LEIDA", tarjetas=len(filas))
    return {"login_ok": True, "filas": filas}


def _correr_cuenta(cuenta: dict, avance=None) -> ResultadoCuenta:
    try:
        return ResultadoCuenta(cuenta=cuenta, resultado=_procesar_cuenta(cuenta, avance))
    except Exception:  # noqa: BLE001 -- una cuenta caída no tumba a las demás
        return ResultadoCuenta(cuenta=cuenta, error=traceback.format_exc())


def _leer_cuentas(cuentas: list[dict], avance=None) -> list[ResultadoCuenta]:
    hilos = max(1, int(os.getenv("SUPRAMAX_HTTP_WORKERS", "4")))

    with concurrent.futures.ThreadPoolExecutor(max_workers=hilos) as executor:
        return list(executor.map(lambda cuenta: _correr_cuenta(cuenta, avance), cuentas))


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

                # Sin tag no hay con qué cruzar. Antes se buscaba igual: la
                # primera prestación sin tag se catalogaba con folio vacío y
                # desde ahí TODAS las filas sin tag caían en esa tarjeta.
                if not tag_norm:
                    total_sin_cruce += 1
                    continue

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
    "Actualizar Supramax" (`jobs_por_evento/supramax_actualizar_bajo_demanda.py`,
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

    print(f"Iniciando refresco de saldos de Supramax por HTTP: {len(cuentas)} cuenta(s).")

    if avance is not None:
        avance.iniciar([c.get("Empresa", "?") for c in cuentas])

    resultados = _leer_cuentas(cuentas, avance)

    # Las que reventaron (red, portal que no mostró el listado) no
    # alcanzaron a reportar nada propio.
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
