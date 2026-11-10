"""Ingesta de consumo de Supramax a BigQuery, por HTTP y sin navegador.

Reemplaza la parte de consumos de `scrapers/supramax_rpa.py`, que abre Chrome
por cada una de las 20 cuentas, llena el formulario de periodo, descarga un
Excel y lo aplana. Ese camino depende de que la Mac del RPA tenga Chrome
sano y por omisión solo baja el MES ANTERIOR completo: el mes en curso no
entraba hasta el mes siguiente, y cuando el orquestador dejó de correr se
quedó todo en el 22 de septiembre de 2026 sin que nada avisara.

EL MISMO REPORTE, LEÍDO POR DEBAJO

Es la técnica que ya usa Conciliaciones en el backend
(`control-vehicular/src/modules/conciliaciones/services/supramax-consumos.*`):

  - El login es un POST de formulario normal, no hace falta JavaScript.
  - `intmain.php?nd=2&re=ccl&dvh=1&dini=<epoch>&dfin=<epoch>&grupo=0` SIN
    `idv` trae el detalle de todos los vehículos de una vez (~3 MB, ~40 s por
    cuenta y mes).
  - No se lee la tabla: cada remisión trae un `<input name="datos">` con el
    arreglo de PHP en `serialize()` + base64. Cada uno repite TODAS las cargas
    de su vehículo, así que se deduplica por estación + ticket.
  - Las longitudes `s:N` de PHP son en bytes: se decodifica como latin1.
  - Los epoch son medianoche de Mazatlán (UTC-7 fijo).

MISMA FORMA QUE EL RPA, MISMA LLAVE

Las filas salen con las columnas exactas de `bq_ingestion.procesar_supramax`
—económico, fecha, MAGNA como REGULAR, litros, importe— y entran por el mismo
`ingest_to_bigquery`, que fusiona por esos valores. Reingerir un periodo que
ya cargó el RPA no duplica: se comprobó cargando días que ya estaban.

Uso:
    python -m jobs_programados.supramax_consumos_http --dias 7
    python -m jobs_programados.supramax_consumos_http --desde 2026-09-01 --hasta 2026-09-30
    python -m jobs_programados.supramax_consumos_http --dias 3 --empresa OCEÁNICA --en-seco
"""

import argparse
import base64
import datetime as dt
import os
import re
import sys

import pandas as pd
from dotenv import load_dotenv

RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ_PROYECTO not in sys.path:
    sys.path.insert(0, RAIZ_PROYECTO)

from bigquery.bq_ingestion import ingest_to_bigquery  # noqa: E402
from shared.credenciales import resolver_cuentas  # noqa: E402
from shared.supramax_http import entrar, nueva_sesion, salir, url_base  # noqa: E402

load_dotenv()

# Generoso a propósito: un mes completo de una cuenta grande tarda ~40 s.
ESPERA_REPORTE = 180

# El portal no corta los periodos, pero un mes por petición mantiene cada
# página en los ~3 MB que ya se midieron.
DIAS_POR_PETICION = 31

UTC_MENOS_7 = dt.timezone(dt.timedelta(hours=-7))


# --------------------------------------------------------------------------
# unserialize() de PHP, lo justo para estos arreglos
# --------------------------------------------------------------------------


def deserializar_php(texto: str):
    """`texto` ya decodificado como latin1: un byte, un carácter. Así las
    longitudes de `s:N:"..."`, que PHP cuenta en bytes, cuadran aunque el
    nombre del conductor traiga una Ñ."""
    pos = 0

    def leer_hasta(fin: str) -> str:
        nonlocal pos
        j = texto.index(fin, pos)
        pedazo = texto[pos:j]
        pos = j + len(fin)
        return pedazo

    def valor():
        nonlocal pos
        tipo = texto[pos]
        pos += 2  # la letra y los dos puntos (o el punto y coma de `N;`)

        if tipo == "N":
            return None
        if tipo == "b":
            return leer_hasta(";") == "1"
        if tipo in ("i", "d"):
            return leer_hasta(";")
        if tipo == "s":
            largo = int(leer_hasta(":"))
            cadena = texto[pos + 1 : pos + 1 + largo]
            pos += largo + 3  # comilla, contenido, comilla y punto y coma
            return cadena
        if tipo == "a":
            cuantos = int(leer_hasta(":"))
            pos += 1  # la llave que abre
            arreglo = {}
            for _ in range(cuantos):
                llave = valor()
                arreglo[str(llave)] = valor()
            pos += 1  # la llave que cierra
            return arreglo

        raise ValueError(f"Tipo de PHP no esperado {tipo!r} en la posición {pos - 2}.")

    return valor()


def _numero(valor) -> float:
    try:
        return float(str(valor if valor is not None else "").replace(",", ""))
    except ValueError:
        return 0.0


def leer_consumos(html: str) -> list[dict]:
    """Las cargas de la página, sin repetir (estación + ticket). Un `datos`
    dañado se salta: con 250 remisiones por página, uno no debe dejar fuera
    al resto."""
    por_ticket: dict[str, dict] = {}

    for crudo in re.findall(r'name="datos"\s+value="([^"]+)"', html):
        try:
            datos = deserializar_php(base64.b64decode(crudo).decode("latin1"))
        except Exception:  # noqa: BLE001
            continue

        consumos = (datos or {}).get("consumos") or {}
        for c in consumos.values():
            ticket = str((c or {}).get("ticket") or "").strip()
            estacion = str(c.get("estacion") or "").strip()
            if not ticket:
                continue

            llave = f"{estacion}|{ticket}"
            if llave in por_ticket:
                continue

            por_ticket[llave] = {
                "economico": str(datos.get("placas") or "").strip().upper(),
                "fh_carga": str(c.get("dcfchvta") or "").strip(),
                "producto": str(c.get("producto") or "").strip().upper(),
                "litros": _numero(c.get("cantidad")),
                "importe": _numero(c.get("importe")),
                "estacion": estacion,
                "ticket": ticket,
            }

    return list(por_ticket.values())


# --------------------------------------------------------------------------
# Portal
# --------------------------------------------------------------------------


def _epoch_mazatlan(dia: dt.date, fin_de_dia: bool) -> int:
    hora = dt.time(23, 59, 59) if fin_de_dia else dt.time(0, 0, 0)
    return int(dt.datetime.combine(dia, hora, tzinfo=UTC_MENOS_7).timestamp())


def _tramos(desde: dt.date, hasta: dt.date):
    inicio = desde
    while inicio <= hasta:
        fin = min(inicio + dt.timedelta(days=DIAS_POR_PETICION - 1), hasta)
        yield inicio, fin
        inicio = fin + dt.timedelta(days=1)


def consumos_de_cuenta(cuenta: dict, desde: dt.date, hasta: dt.date) -> list[dict]:
    sesion = nueva_sesion()
    entrar(sesion, cuenta)

    try:
        cargas = []
        for inicio, fin in _tramos(desde, hasta):
            respuesta = sesion.get(
                f"{url_base()}intmain.php",
                params={
                    "nd": 2,
                    "re": "ccl",
                    "dvh": 1,
                    "dini": _epoch_mazatlan(inicio, False),
                    "dfin": _epoch_mazatlan(fin, True),
                    "grupo": 0,
                },
                timeout=ESPERA_REPORTE,
            )
            html = respuesta.content.decode("latin1")

            # Cero remisiones es legítimo, pero solo si la página ES el
            # reporte: si el portal nos regresó al login, no es «no hubo cargas».
            if "CONSUMOS DEL PERIODO" not in html and 'name="datos"' not in html:
                raise RuntimeError(f"no devolvió el reporte de {inicio} a {fin} (¿se cerró la sesión?)")

            cargas.extend(leer_consumos(html))
        return cargas
    finally:
        salir(sesion)


# --------------------------------------------------------------------------
# Forma de `consumos_flota`, igual que `bq_ingestion.procesar_supramax`
# --------------------------------------------------------------------------


def _a_tabla(cargas: list[dict], empresa: str, desde: dt.date, hasta: dt.date) -> pd.DataFrame:
    df = pd.DataFrame(cargas)
    if df.empty:
        return df

    limpio = pd.DataFrame()
    limpio["ECO"] = df["economico"]
    limpio["Fecha"] = pd.to_datetime(df["fh_carga"], errors="coerce")
    limpio["Concepto"] = "COMBUSTIBLE"
    limpio["Tipo"] = (
        df["producto"]
        .str.upper()
        .str.strip()
        .str.replace(r"^ARCO\s+", "", regex=True)
        .replace("MAGNA", "REGULAR")
    )
    limpio["Cantidad"] = df["litros"]
    limpio["Importe"] = df["importe"]
    limpio["Sistema"] = "Supramax"
    limpio["Empresa"] = empresa
    # Estación + ticket: el identificador real de la carga, para rastrearla.
    limpio["Id_Origen"] = df["estacion"] + "-" + df["ticket"]
    limpio["Archivo_Origen"] = f"http:{desde}..{hasta}"

    # La página puede traer cargas en los bordes del periodo por zona horaria;
    # solo entra lo que cae dentro de lo pedido.
    fechas = limpio["Fecha"].dt.date
    return limpio[(fechas >= desde) & (fechas <= hasta)].copy()


def ejecutar(desde: dt.date, hasta: dt.date, empresa: str | None = None, en_seco: bool = False) -> int:
    print(f"Rango: {desde} → {hasta}")
    cuentas = resolver_cuentas("SUPRAMAX", "SUPRAMAX_CREDENTIALS")
    if empresa:
        cuentas = [c for c in cuentas if c.get("Empresa", "").upper() == empresa.upper()]

    tablas = []
    fallidas = []
    for n, cuenta in enumerate(cuentas, start=1):
        nombre = cuenta.get("Empresa", "?")
        try:
            cargas = consumos_de_cuenta(cuenta, desde, hasta)
        except Exception as error:  # noqa: BLE001
            print(f"  [{n}/{len(cuentas)}] ❌ {nombre}: {error}")
            fallidas.append(nombre)
            continue

        tabla = _a_tabla(cargas, nombre, desde, hasta)
        print(f"  [{n}/{len(cuentas)}] {nombre}: {len(tabla)} carga(s)")
        if not tabla.empty:
            tablas.append(tabla)

    # Que fallen todas no es «no hubo cargas»: casi siempre son contraseñas
    # rotadas. Así se quedó parado del 2026-10-10 al 28 con salida 0.
    if cuentas and len(fallidas) == len(cuentas):
        raise RuntimeError(f"Fallaron las {len(cuentas)} cuentas de Supramax; no se leyó nada.")

    if not tablas:
        print("⚠️ Ninguna cuenta trajo cargas en el periodo.")
        return 0

    todo = pd.concat(tablas, ignore_index=True)
    print(f"Total: {len(todo)} carga(s) de {len(tablas)} cuenta(s). Fallidas: {fallidas or 'ninguna'}")

    if en_seco:
        print("--en-seco: no se escribe en BigQuery.")
        return len(todo)

    ingest_to_bigquery(todo)
    return len(todo)


def main_dias(dias: int = 7) -> int:
    """Lo que llama el orquestador: los últimos `dias`, hasta hoy."""
    hoy = dt.datetime.now(UTC_MENOS_7).date()
    return ejecutar(hoy - dt.timedelta(days=dias), hoy)


def main() -> int:
    lector = argparse.ArgumentParser(description="Consumos de Supramax a BigQuery por HTTP")
    lector.add_argument("--dias", type=int, default=7)
    lector.add_argument("--desde")
    lector.add_argument("--hasta")
    lector.add_argument("--empresa", help="Solo esta cuenta (nombre de empresa)")
    lector.add_argument("--en-seco", action="store_true", help="Leer y contar sin escribir en BigQuery")
    args = lector.parse_args()

    hoy = dt.datetime.now(UTC_MENOS_7).date()
    hasta = dt.date.fromisoformat(args.hasta) if args.hasta else hoy
    desde = dt.date.fromisoformat(args.desde) if args.desde else hasta - dt.timedelta(days=args.dias)

    ejecutar(desde, hasta, empresa=args.empresa, en_seco=args.en_seco)
    return 0


if __name__ == "__main__":
    sys.exit(main())
