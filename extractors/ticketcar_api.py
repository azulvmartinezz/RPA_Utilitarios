"""Ingesta de consumo de Ticket Car (Edenred TC+) a BigQuery, por API.

Reemplaza a `scrapers/ticketcar_rpa.py`, que abría Chrome contra el portal,
descargaba un Excel y lo aplanaba a diez columnas. Aquí se pide la transacción
completa al servicio SOAP: no depende del HTML del portal, se sabe al momento
si funcionó, y se conserva el detalle que el reporte descartaba —estación,
autorización, impuestos desglosados, odómetro, centro de costos—.

TRES LLAMADAS, NO UNA
---------------------
`TransactionGetFilteredList` identifica quién cargó únicamente con el número de
tarjeta: no trae placa, ni económico, ni referencia al vehículo. El puente se
arma con las otras dos operaciones —la tarjeta lleva un `IdentificationNumber`
que también trae el vehículo, y el vehículo carga el económico en su
`Description`—. Verificado contra Petro Smart: 88 vehículos, 91 tarjetas, y las
38 tarjetas que aparecen en transacciones cruzan todas.

LO QUE EL MANUAL DICE MAL
-------------------------
La especificación v1.9 documenta el rango en `TransactionTimeStart` /
`TransactionTimeEnd`, y el WSDL declara `InitRankDate` / `EndRankDate`. Ninguno
de los dos funciona: el servicio exige `TransactionDateTimeStart` y
`TransactionDateTimeEnd`, y además dentro del `TransactionDTO`, no del filtro.
Se descubrió leyendo el mensaje de error, que es la única fuente correcta.

Otras dos cosas que se midieron y no están documentadas:
  - `PageRecords` acepta al menos 200; la nota de «máximo 50» es para All=true.
  - El `Paging` de la respuesta viene nulo, así que NO hay total de registros:
    hay que pedir páginas hasta que una venga corta.

HASTA DÓNDE LLEGA
-----------------
La API solo conoce lo nacido en TC+: la primera transacción disponible es del
21/06/2026. Pedir enero o 2025 devuelve `Success = true` con lista vacía, que
es indistinguible de «no hubo consumo». Por eso el backfill arranca en esa
fecha y no antes.

Uso:
    python -m extractors.ticketcar_api --dias 7
    python -m extractors.ticketcar_api --desde 2026-06-21 --hasta 2026-08-20
    python -m extractors.ticketcar_api --catalogos      # solo tarjetas y vehículos
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

import warnings

import pandas as pd
from google.cloud import bigquery, secretmanager

# `google-cloud-bigquery` avisa en CADA carga que en el futuro querrá
# `pandas-gbq`. Son dos líneas por cuenta por tabla: ahogan el resumen que sí
# hay que leer en la bitácora del orquestador.
warnings.filterwarnings("ignore", message=".*pandas-gbq.*")

PROYECTO_BQ = os.getenv("GCP_PROJECT_ID", "innovacion-futuro")
DATASET = os.getenv("BQ_DATASET_FLOTA", "flota")
TABLA_TRX = f"{PROYECTO_BQ}.{DATASET}.ticketcar_transacciones"
TABLA_TARJETAS = f"{PROYECTO_BQ}.{DATASET}.ticketcar_tarjetas"
TABLA_VEHICULOS = f"{PROYECTO_BQ}.{DATASET}.ticketcar_vehiculos"

# Las credenciales viven en el mismo secreto que ya usa `fleetup_viajes`, en
# vez de repartirse en otro .env. Son nueve cuentas, una por empresa, bajo la
# llave TICKETCAR_CUENTAS.
SECRETO = os.getenv(
    "SECRETO_CONTROL_VEHICULAR",
    "projects/herramientas-desarrollo/secrets/"
    "control-vehicular-localhost-secrets/versions/latest",
)

URL = os.getenv(
    "TICKETCAR_URL",
    "https://mobilityservices.ticketcaredenred.mx/TicketCarZero.svc",
)

# La primera transacción que existe en la plataforma nueva.
INICIO_HISTORICO = dt.date(2026, 6, 21)

PAGINA = 200

NS = {
    "req_trx": "http://schemas.datacontract.org/2004/07/"
               "ERMX.SSOServices.TicketCar.Entities.Transactions.Requests",
    "trx": "http://schemas.datacontract.org/2004/07/"
           "ERMX.SSOServices.TicketCar.Entities.Transactions",
    "req_veh": "http://schemas.datacontract.org/2004/07/"
               "ERMX.SSOServices.TicketCar.Entities.Vehicles.Requests",
    "veh": "http://schemas.datacontract.org/2004/07/"
           "ERMX.SSOServices.TicketCar.Entities.Vehicles",
    "req_tar": "http://schemas.datacontract.org/2004/07/"
               "ERMX.SSOServices.TicketCar.Entities.Cards.Requests",
    "tar": "http://schemas.datacontract.org/2004/07/"
           "ERMX.SSOServices.TicketCar.Entities.Cards",
    "com": "http://schemas.datacontract.org/2004/07/"
           "ERMX.SSOServices.TicketCar.Entities.Common",
}


def cargar_secretos() -> dict:
    """
    Las credenciales salen del secreto compartido.

    `TICKETCAR_CUENTAS` en el entorno lo sobrescribe. No es un atajo para
    guardar claves en un `.env`: es lo que permite correr contra una cuenta
    suelta al depurar, y lo que salva la corrida el día que Secret Manager no
    responda. En el servidor no se define y todo sale del secreto.
    """
    del_entorno = os.getenv("TICKETCAR_CUENTAS")

    if del_entorno:
        return {"TICKETCAR_CUENTAS": del_entorno}

    cliente = secretmanager.SecretManagerServiceClient()
    respuesta = cliente.access_secret_version(request={"name": SECRETO})

    return json.loads(respuesta.payload.data.decode())


def cuentas(cfg: dict) -> list:
    crudo = cfg.get("TICKETCAR_CUENTAS")

    if not crudo:
        raise ValueError(
            "Falta TICKETCAR_CUENTAS en el secreto. Debe ser una lista JSON de "
            "objetos con empresa, id y token."
        )

    return json.loads(crudo) if isinstance(crudo, str) else crudo


def ip_saliente() -> str:
    """
    El servicio exige la IP del cliente dentro del mensaje.

    No la valida contra la de la conexión —se probó desde la oficina y acepta
    lo que se le mande— pero es un campo obligatorio del contrato. Se resuelve
    en cada corrida porque la IP del sitio cambia.
    """
    fija = os.getenv("TICKETCAR_IP")

    if fija:
        return fija

    with urllib.request.urlopen("https://api.ipify.org", timeout=20) as r:
        return r.read().decode().strip()


def _llamar(operacion: str, cuerpo: str) -> ET.Element:
    sobre = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        f"<s:Body>{cuerpo}</s:Body></s:Envelope>"
    ).encode()

    peticion = urllib.request.Request(
        URL,
        data=sobre,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"urn:ERMX.Services/ITicketCarZero/{operacion}"',
        },
    )

    with urllib.request.urlopen(peticion, timeout=120) as respuesta:
        return ET.fromstring(respuesta.read())


def _texto(nodo, etiqueta: str, espacio: str):
    """
    Busca en cualquier nivel. Sirve para etiquetas únicas en el mensaje.

    Cuidado: `Identification` aparece cinco veces en una transacción —la de la
    estación, la de la mercancía, la de la transacción— y `.//` devuelve la
    primera en orden de documento, que no siempre es la que se quiere. Para
    esas hay que acotar el nodo o usar `_directo`.
    """
    if nodo is None:
        return None

    hijo = nodo.find(f".//{{{NS[espacio]}}}{etiqueta}")

    return hijo.text if hijo is not None else None


def _directo(nodo, etiqueta: str, espacio: str):
    """Solo hijos inmediatos: evita traerse el homónimo de un nodo anidado."""
    if nodo is None:
        return None

    hijo = nodo.find(f"{{{NS[espacio]}}}{etiqueta}")

    return hijo.text if hijo is not None else None


def _seguridad(cuenta: dict, ip: str) -> str:
    return (
        f'<r:Security><c:CustomerIdentification>{cuenta["id"]}'
        f"</c:CustomerIdentification>"
        f"<c:Ip>{ip}</c:Ip><c:Token>{cuenta['token']}</c:Token></r:Security>"
    )


def _paginacion(pagina: int) -> str:
    return (
        f"<r:Paging><c:All>false</c:All><c:PageNumber>{pagina}</c:PageNumber>"
        f"<c:PageRecords>{PAGINA}</c:PageRecords></r:Paging>"
    )


def transacciones(cuenta: dict, ip: str, desde: dt.date, hasta: dt.date) -> list:
    """Todas las transacciones del rango, paginando hasta que una página venga corta."""
    salida, pagina = [], 1

    while True:
        cuerpo = (
            '<TransactionGetFilteredList xmlns="urn:ERMX.Services">'
            f'<request xmlns:r="{NS["req_trx"]}" xmlns:t="{NS["trx"]}" '
            f'xmlns:c="{NS["com"]}">'
            "<r:Item>"
            f"<t:TransactionDateTimeEnd>{hasta}T23:59:59</t:TransactionDateTimeEnd>"
            f"<t:TransactionDateTimeStart>{desde}T00:00:00</t:TransactionDateTimeStart>"
            "<t:TransactionFilter>"
            f'<t:CustomerIdentification>{cuenta["id"]}</t:CustomerIdentification>'
            f"<t:EndRankDate>{hasta}T23:59:59</t:EndRankDate>"
            f"<t:InitRankDate>{desde}T00:00:00</t:InitRankDate>"
            "<t:IsInvoiced>1</t:IsInvoiced>"
            "</t:TransactionFilter></r:Item>"
            f"{_paginacion(pagina)}{_seguridad(cuenta, ip)}"
            "</request></TransactionGetFilteredList>"
        )

        raiz = _llamar("TransactionGetFilteredList", cuerpo)
        bloques = raiz.findall(f'.//{{{NS["trx"]}}}TransactionDTO')
        salida += bloques

        if len(bloques) < PAGINA:
            return salida

        pagina += 1


def _catalogo(operacion: str, envoltura: str, elemento: str,
              espacio_req: str, espacio: str, cuenta: dict, ip: str) -> list:
    salida, pagina = [], 1

    while True:
        cuerpo = (
            f'<{operacion} xmlns="urn:ERMX.Services">'
            f'<request xmlns:r="{NS[espacio_req]}" xmlns:e="{NS[espacio]}" '
            f'xmlns:c="{NS["com"]}">'
            f'<r:Item><e:CustomerIdentification>{cuenta["id"]}'
            "</e:CustomerIdentification></r:Item>"
            f"{_paginacion(pagina)}{_seguridad(cuenta, ip)}"
            f"{envoltura}</request></{operacion}>"
        )

        raiz = _llamar(operacion, cuerpo)
        bloques = raiz.findall(f".//{{{NS[espacio]}}}{elemento}")
        salida += bloques

        if len(bloques) < PAGINA:
            return salida

        pagina += 1


def vehiculos(cuenta: dict, ip: str) -> list:
    # `SelectionType` va después de Security porque el serializador de .NET
    # ordena los elementos alfabéticamente y rechaza el mensaje si no se respeta.
    return _catalogo(
        "VehicleGetFilteredList", "<r:SelectionType>Full</r:SelectionType>",
        "VehicleDTO", "req_veh", "veh", cuenta, ip,
    )


def tarjetas(cuenta: dict, ip: str) -> list:
    return _catalogo(
        "CardGetFilteredList", "", "CardDTO", "req_tar", "tar", cuenta, ip,
    )


def normalizar_economico(valor) -> str | None:
    """
    El proveedor escribe el mismo catálogo de tres formas: `AU-107`, `AU 238`,
    `BP 26`. En Control Vehicular las unidades van siempre como `AU-000`.

    El prefijo se acepta abierto —dos a cuatro letras— y no contra una lista
    fija. La flota tiene AU y CA, pero el catálogo del proveedor trae también
    TM, BP y PRE, y una lista cerrada los tiraría en silencio; lo que sí es o
    no una unidad lo decide el cruce contra Control Vehicular, no esta función.

    Lo que no cumple el patrón se devuelve nulo en vez de forzarse: `00001` o
    `LASGR` no son económicos, y convertirlos inventaría unidades que no
    existen. Quedan visibles como transacciones sin económico.
    """
    texto = re.sub(r"\s+", "", str(valor or "").upper())
    coincidencia = re.match(r"^([A-Z]{2,4})-?(\d+)$", texto)

    if not coincidencia:
        return None

    return f"{coincidencia.group(1)}-{int(coincidencia.group(2)):03d}"


def _decimal(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _entero(valor):
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return None


def fila_transaccion(nodo, cuenta: dict, mapa: dict, sello) -> dict | None:
    autorizacion = _texto(nodo, "AuthorizationNumber", "trx")
    fecha_texto = _directo(nodo, "TransactionDate", "trx")

    identificador = _entero(_directo(nodo, "Identification", "trx"))

    """
    La llave es `identificador`, no `AuthorizationNumber`.

    El número de autorización parecía la llave natural —lo es en el papel del
    ticket— pero se repite: de 494 transacciones de Petro Smart, dos pares
    comparten número en fechas distintas. Es un consecutivo de seis dígitos que
    da la vuelta. El `Identification` interno de Edenred sí fue único en las
    494, y es el que permite reingerir un periodo sin duplicar ni chocar.

    Sin él no hay forma de fusionar sin arriesgar duplicados, así que el
    renglón se descarta en vez de colarse.
    """
    if not autorizacion or not fecha_texto or not identificador:
        return None

    momento = dt.datetime.fromisoformat(fecha_texto)
    tarjeta = _directo(nodo, "CardNumber", "trx")

    """
    El detalle se acota nodo por nodo y no con `.//` desde la raíz.

    `Identification` existe en la estación, en la mercancía y en la propia
    transacción; `Description` y `Name` existen en la mercancía y en otros
    catálogos. Buscar desde arriba devuelve el primero en orden de documento,
    que fue justo lo que dejó los litros en nulo: `Quantity` cuelga de
    `Merchandise`, no del contenedor que decía el mapeo del proveedor.
    """
    detalle = nodo.find(f'{{{NS["trx"]}}}Detail')
    estacion = detalle.find(f'{{{NS["trx"]}}}Afiliate') if detalle is not None else None
    mercancia = detalle.find(f'{{{NS["trx"]}}}Merchandise') if detalle is not None else None

    return {
        "customer_id": int(cuenta["id"]),
        "autorizacion": autorizacion,
        "identificador": identificador,
        "fh_transaccion": momento,
        "fecha": momento.date(),
        "nb_empresa": cuenta.get("empresa"),
        "eco": mapa.get(tarjeta),
        "nu_tarjeta": tarjeta,
        "cl_estatus": _directo(nodo, "Status", "trx"),
        "cl_tipo": _directo(nodo, "TypeDescription", "trx"),
        "nb_combustible": _directo(mercancia, "Name", "trx"),
        "nu_litros": _decimal(_directo(mercancia, "Quantity", "trx")),
        "im_precio_unitario": _decimal(_directo(mercancia, "UnitPrice", "trx")),
        "im_total": _decimal(_directo(nodo, "TotalAmount", "trx")),
        "im_iva": _decimal(_directo(nodo, "IVAAmount", "trx")),
        "im_ieps": _decimal(_directo(nodo, "IEPSAmount", "trx")),
        "nb_estacion": _directo(estacion, "SocialReason", "trx"),
        "nu_estacion": _directo(estacion, "Identification", "trx"),
        "nb_ciudad": _directo(estacion, "City", "trx"),
        "nb_estado": _directo(estacion, "State", "trx"),
        "nu_odometro": _entero(_texto(detalle, "TransactionCurrentKm", "trx")),
        "nu_odometro_previo": _entero(_texto(detalle, "TransactionPreviousKm", "trx")),
        "nb_centrocosto": _texto(detalle, "CostCenterDescription", "trx"),
        "nb_region": _texto(detalle, "RegionDescription", "trx"),
        "sn_facturada": _directo(nodo, "IsInvoiced", "trx") == "true",
        "nu_factura": _texto(detalle, "InvoiceReference", "trx"),
        "fh_ingesta": sello,
    }


def _fusionar(cliente_bq, tabla: str, filas: list, llaves: list) -> int:
    """MERGE por llave natural: reingerir un periodo corrige en vez de duplicar."""
    if not filas:
        return 0

    datos = pd.DataFrame(filas)

    # La temporal lleva la cuenta en el nombre. Con un nombre fijo, dos cargas
    # seguidas reutilizaban el id del trabajo y BigQuery contestaba 409
    # «Already Exists: Job» a la segunda —pasó al reanudar tras una corrida
    # interrumpida—.
    sufijo = int(datos["customer_id"].iloc[0]) if "customer_id" in datos else 0
    temporal = f"{tabla}_tmp_{sufijo}"

    # El esquema se toma de la tabla destino en vez de dejar que pandas lo
    # infiera. Una columna que sale entera de nulos —`eco` en una cuenta cuyos
    # vehiculos no traen economico— se infiere como INT64 y el MERGE revienta
    # contra la columna STRING de la tabla real.
    esquema = [c for c in cliente_bq.get_table(tabla).schema
               if c.name in datos.columns]

    cliente_bq.load_table_from_dataframe(
        datos, temporal,
        job_config=bigquery.LoadJobConfig(
            write_disposition="WRITE_TRUNCATE", schema=esquema,
        ),
    ).result()

    try:
        columnas = list(datos.columns)
        condicion = " AND ".join(f"D.{k} = O.{k}" for k in llaves)
        asignaciones = ", ".join(f"D.{c} = O.{c}" for c in columnas)
        lista = ", ".join(columnas)
        valores = ", ".join(f"O.{c}" for c in columnas)

        cliente_bq.query(
            f"MERGE `{tabla}` D USING `{temporal}` O ON {condicion} "
            f"WHEN MATCHED THEN UPDATE SET {asignaciones} "
            f"WHEN NOT MATCHED THEN INSERT ({lista}) VALUES ({valores})"
        ).result()
    finally:
        # Se borra siempre: en `rpa_utilitarios` quedaron 32 tablas `__tmp_`
        # olvidadas de corridas que fallaron a media carga.
        cliente_bq.delete_table(temporal, not_found_ok=True)

    return len(datos)


def refrescar_catalogos(cliente_bq, cuenta: dict, ip: str, sello) -> dict:
    """Sube tarjetas y vehículos y devuelve el mapa tarjeta → económico."""
    lista_veh = vehiculos(cuenta, ip)
    lista_tar = tarjetas(cuenta, ip)

    filas_veh, por_entidad = [], {}

    for v in lista_veh:
        entidad = _texto(v, "IdentificationNumber", "veh")
        descripcion = _texto(v, "Description", "veh")
        eco = normalizar_economico(descripcion)

        if entidad:
            por_entidad[entidad] = eco

        filas_veh.append({
            "customer_id": int(cuenta["id"]),
            "nu_entidad": entidad,
            "nb_descripcion": descripcion,
            "eco": eco,
            "nu_placa": _texto(v, "Plate", "veh"),
            "nu_odometro": _entero(_texto(v, "Kilometers", "veh")),
            "im_rendimiento": _decimal(_texto(v, "Performance", "veh")),
            "fh_ingesta": sello,
        })

    filas_tar, mapa = [], {}

    for t in lista_tar:
        numero = _texto(t, "Number", "tar")
        entidad = _texto(t, "IdentificationNumber", "tar")

        if numero and entidad in por_entidad:
            mapa[numero] = por_entidad[entidad]

        filas_tar.append({
            "customer_id": int(cuenta["id"]),
            "nu_tarjeta": numero,
            "nu_entidad": entidad,
            "cl_estatus": _texto(t, "StatusDescription", "tar"),
            "id_centrocosto": _texto(t, "CostCenterIdentification", "tar"),
            "fh_ingesta": sello,
        })

    _fusionar(cliente_bq, TABLA_VEHICULOS, [f for f in filas_veh if f["nu_entidad"]],
              ["customer_id", "nu_entidad"])
    _fusionar(cliente_bq, TABLA_TARJETAS, [f for f in filas_tar if f["nu_tarjeta"]],
              ["customer_id", "nu_tarjeta"])

    return mapa


def ejecutar(desde: dt.date, hasta: dt.date, solo_catalogos: bool = False) -> int:
    if desde < INICIO_HISTORICO:
        print(
            f"⚠️  La API no tiene nada antes del {INICIO_HISTORICO}; se ajusta "
            f"el inicio. Lo anterior vive en `consumos_flota` como 'Edenred'."
        )
        desde = INICIO_HISTORICO

    cfg = cargar_secretos()
    ip = ip_saliente()
    cliente_bq = bigquery.Client(project=PROYECTO_BQ)
    sello = dt.datetime.now(dt.timezone.utc)
    total = 0

    for cuenta in cuentas(cfg):
        nombre = cuenta.get("empresa", cuenta["id"])

        try:
            mapa = refrescar_catalogos(cliente_bq, cuenta, ip, sello)

            if solo_catalogos:
                print(f"   • {nombre}: catálogo refrescado ({len(mapa)} tarjetas)")
                continue

            nodos = transacciones(cuenta, ip, desde, hasta)
            filas = [f for f in (fila_transaccion(n, cuenta, mapa, sello) for n in nodos) if f]
            sin_eco = sum(1 for f in filas if not f["eco"])

            _fusionar(cliente_bq, TABLA_TRX, filas, ["customer_id", "identificador"])
            total += len(filas)

            aviso = f", {sin_eco} sin económico" if sin_eco else ""
            print(f"   ✅ {nombre}: {len(filas)} transacciones{aviso}")
        except Exception as error:
            # Una cuenta que falla no debe tumbar a las otras ocho: cada una es
            # una credencial distinta contra el mismo servicio.
            print(f"   ❌ {nombre}: {error}")

    return total


def main_dias(dias: int = 7) -> int:
    hoy = dt.date.today()

    return ejecutar(hoy - dt.timedelta(days=dias), hoy)


def main() -> int:
    lector = argparse.ArgumentParser(description=__doc__)
    lector.add_argument("--dias", type=int, default=7)
    lector.add_argument("--desde")
    lector.add_argument("--hasta")
    lector.add_argument("--catalogos", action="store_true",
                        help="Solo refresca tarjetas y vehículos.")
    args = lector.parse_args()

    hoy = dt.date.today()
    desde = dt.date.fromisoformat(args.desde) if args.desde else hoy - dt.timedelta(days=args.dias)
    hasta = dt.date.fromisoformat(args.hasta) if args.hasta else hoy

    print(f"💎 Ticket Car · {desde} a {hasta}")
    total = ejecutar(desde, hasta, solo_catalogos=args.catalogos)
    print(f"💰 {total} transacciones ingeridas.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
