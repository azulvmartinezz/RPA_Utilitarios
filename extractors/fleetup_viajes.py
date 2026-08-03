"""Ingesta de viajes de FleetUp a BigQuery, por API.

Reemplaza el camino que traía `scrapers/fleetup_rpa.py`, que abre la interfaz
web con Selenium y solo respalda el reporte mensual en GCS —la tabla destino
`rpa_utilitarios.tbl_reportes_viajes` se creó y quedó en cero filas—. Tenemos
acceso al API de FleetUp, así que aquí se piden los viajes directo: no depende
del HTML del portal ni de resolver captchas.

Unidades del API, verificadas contra el swagger y midiendo desplazamiento real:
  - coordenadas en milisegundos de arco (÷ 3,600,000)
  - distancia en metros, combustible en centilitros
  - fechas en UTC, formato compacto YYYYMMDDHHMMSS
  - `/trips/history` exige startDate y endDate de 8 caracteres (YYYYMMDD) y
    rechaza rangos mayores a 30 días: con 31 devuelve 400.

Uso:
    python -m extractors.fleetup_viajes --dias 7
    python -m extractors.fleetup_viajes --desde 2026-06-01 --hasta 2026-06-30
    python -m extractors.fleetup_viajes --dias 7 --limite 3   # prueba corta
"""

import argparse
import datetime as dt
import json
import os
import sys
import time

import pandas as pd
import psycopg2
import requests
from google.cloud import bigquery
from google.cloud import secretmanager

PROYECTO_BQ = os.getenv("GCP_PROJECT_ID", "innovacion-futuro")
DATASET = os.getenv("BQ_DATASET_FLOTA", "flota")
TABLA_VIAJES = f"{PROYECTO_BQ}.{DATASET}.viajes"
TABLA_DISPOSITIVOS = f"{PROYECTO_BQ}.{DATASET}.dispositivos"

# Las credenciales de FleetUp y de la base ya están centralizadas en el secreto
# de Control Vehicular. Se leen de ahí en vez de copiarlas a otro .env.
SECRETO = os.getenv(
    "SECRETO_CONTROL_VEHICULAR",
    "projects/herramientas-desarrollo/secrets/"
    "control-vehicular-localhost-secrets/versions/latest",
)

FACTOR_COORDENADAS = 3_600_000
METROS_POR_KM = 1000
CENTILITROS_POR_LITRO = 100

# El API no documenta límite de llamadas. Se espacia por precaución: 280
# dispositivos con esta pausa son unos dos minutos y medio.
PAUSA_SEGUNDOS = 0.5
DIAS_MAXIMOS = 30
ZONA = "America/Mazatlan"


def cargar_secretos() -> dict:
    cliente = secretmanager.SecretManagerServiceClient()
    respuesta = cliente.access_secret_version(request={"name": SECRETO})
    return json.loads(respuesta.payload.data.decode())


def token_fleetup(cfg: dict) -> str:
    url = (
        f"https://{cfg['FLEETUP_URL']}/token"
        f"?acctId={cfg['FLEETUP_ACCID']}&secret={cfg['FLEETUP_SECRET_KEY']}"
    )
    respuesta = requests.get(
        url, headers={"x-api-key": cfg["FLEETUP_API_KEY"]}, timeout=60
    )
    respuesta.raise_for_status()
    return respuesta.json()["token"]


def pedir(cfg: dict, token: str, ruta: str, cuerpo: dict):
    respuesta = requests.post(
        f"https://{cfg['FLEETUP_URL']}{ruta}",
        headers={
            "content-type": "application/json",
            "token": token,
            "x-api-key": cfg["FLEETUP_API_KEY"],
        },
        json=cuerpo,
        timeout=120,
    )
    respuesta.raise_for_status()
    return respuesta.json()


def dispositivos_de_fleetup(cfg: dict, token: str) -> list:
    """Los dispositivos que reportan posición hoy."""
    datos = pedir(
        cfg, token, "/gpsdata/device-last-location", {"acctId": cfg["FLEETUP_ACCID"]}
    )
    registros = datos if isinstance(datos, list) else datos.get("data", [])
    return sorted({str(r["devId"]) for r in registros if r.get("devId")})


def refrescar_dispositivos(cfg: dict, cliente_bq: bigquery.Client) -> int:
    """Reescribe el mapeo dispositivo→ECO que administra Control Vehicular.

    Es una proyección, no un catálogo: solo el vínculo. Los atributos de la
    unidad (empresa, dirección, colaborador) se quedan en Postgres, que es
    donde se editan.
    """
    conexion = psycopg2.connect(
        host=cfg["DB_HOST"],
        port=int(cfg["DB_PORT"]),
        user=cfg["DB_USERNAME"],
        password=cfg["DB_PASSWORD"],
        dbname=cfg["DB_DATABASE"],
    )

    try:
        with conexion.cursor() as cursor:
            cursor.execute(
                """
                SELECT "de_devId", nu_economico, id_unidad, sn_activo
                FROM unidades
                WHERE "de_devId" IS NOT NULL AND "de_devId" <> ''
                """
            )
            filas = cursor.fetchall()
    finally:
        conexion.close()

    marca = dt.datetime.now(dt.timezone.utc)
    tabla = pd.DataFrame(
        [
            {
                "dev_id": str(dev_id).strip(),
                "eco": (eco or "").strip() or None,
                "id_unidad": id_unidad,
                "sn_activo": activo,
                "actualizado_en": marca,
            }
            for dev_id, eco, id_unidad, activo in filas
        ]
    )

    if tabla.empty:
        print("⚠️  Ninguna unidad tiene dispositivo vinculado; no se toca el mapeo.")
        return 0

    cliente_bq.load_table_from_dataframe(
        tabla,
        TABLA_DISPOSITIVOS,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"),
    ).result()

    print(f"🔗 Mapeo dispositivo→ECO actualizado: {len(tabla)} vínculos.")
    return len(tabla)


def _fecha(compacta) -> dt.datetime | None:
    texto = str(compacta or "")

    if len(texto) != 14 or not texto.isdigit():
        return None

    return dt.datetime.strptime(texto, "%Y%m%d%H%M%S").replace(
        tzinfo=dt.timezone.utc
    )


def _coordenadas(valor):
    partes = str(valor or "").split(",")

    if len(partes) != 2:
        return None, None

    try:
        return (
            float(partes[0]) / FACTOR_COORDENADAS,
            float(partes[1]) / FACTOR_COORDENADAS,
        )
    except ValueError:
        return None, None


def a_fila(crudo: dict, dev_id: str) -> dict | None:
    inicio = _fecha(crudo.get("startTime"))

    if inicio is None:
        return None

    fin = _fecha(crudo.get("endTime"))
    lat_inicio, lng_inicio = _coordenadas(crudo.get("startLocation"))
    lat_fin, lng_fin = _coordenadas(crudo.get("endLocation"))

    return {
        "dev_id": dev_id,
        "fh_inicio": inicio,
        "fh_fin": fin,
        # La fecha de partición es la local de la operación, no la UTC: si no,
        # los viajes de la tarde se contarían al día siguiente.
        "fecha": inicio.astimezone(dt.timezone(dt.timedelta(hours=-7))).date(),
        "minutos": int((fin - inicio).total_seconds() // 60) if fin else None,
        "km": float(crudo.get("tripMileage") or 0) / METROS_POR_KM,
        "litros": float(crudo.get("fuelWear") or 0) / CENTILITROS_POR_LITRO,
        "puntaje": (
            float(crudo["tripScore"]) if crudo.get("tripScore") is not None else None
        ),
        "lat_inicio": lat_inicio,
        "lng_inicio": lng_inicio,
        "lat_fin": lat_fin,
        "lng_fin": lng_fin,
        "direccion_inicio": crudo.get("startAddress") or None,
        "direccion_fin": crudo.get("endAddress") or None,
        "vehiculo": crudo.get("vehicle") or None,
        "ingestado_en": dt.datetime.now(dt.timezone.utc),
    }


def traer_viajes(cfg, token, dispositivos, desde, hasta, pausa):
    filas = []
    fallos = []

    for indice, dev_id in enumerate(dispositivos, start=1):
        try:
            respuesta = pedir(
                cfg,
                token,
                "/trips/history",
                {
                    "acctId": cfg["FLEETUP_ACCID"],
                    "devId": dev_id,
                    "startDate": desde.strftime("%Y%m%d"),
                    "endDate": hasta.strftime("%Y%m%d"),
                    "isDriverDetailsRequired": False,
                    "isTripScoreRequired": True,
                },
            )
            # Este endpoint envuelve el arreglo en `trips`, no en `data`.
            viajes = respuesta.get("trips") or []
            filas.extend(f for f in (a_fila(v, dev_id) for v in viajes) if f)
            print(f"  [{indice}/{len(dispositivos)}] {dev_id}: {len(viajes)} viajes")
        except Exception as error:  # noqa: BLE001
            fallos.append((dev_id, str(error)[:120]))
            print(f"  [{indice}/{len(dispositivos)}] {dev_id}: ⚠️ {error}")

        time.sleep(pausa)

    return filas, fallos


def cargar(cliente_bq: bigquery.Client, filas: list) -> int:
    """Carga con MERGE por (dev_id, fh_inicio) para que reingerir no duplique."""
    if not filas:
        print("Sin viajes que cargar.")
        return 0

    tabla = pd.DataFrame(filas)
    temporal = f"{TABLA_VIAJES}_tmp_carga"

    cliente_bq.load_table_from_dataframe(
        tabla,
        temporal,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"),
    ).result()

    columnas = [c for c in tabla.columns]
    asignaciones = ", ".join(f"D.{c} = O.{c}" for c in columnas)
    lista = ", ".join(columnas)
    valores = ", ".join(f"O.{c}" for c in columnas)

    resultado = cliente_bq.query(
        f"""
        MERGE `{TABLA_VIAJES}` D
        USING `{temporal}` O
          ON D.dev_id = O.dev_id AND D.fh_inicio = O.fh_inicio
        WHEN MATCHED THEN UPDATE SET {asignaciones}
        WHEN NOT MATCHED THEN INSERT ({lista}) VALUES ({valores})
        """
    ).result()

    # La temporal se borra siempre: en `rpa_utilitarios` quedaron decenas de
    # tablas `__tmp_` olvidadas ocupando espacio.
    cliente_bq.delete_table(temporal, not_found_ok=True)

    print(f"✅ {len(tabla)} viajes fusionados en {TABLA_VIAJES}.")
    return len(tabla)


def ejecutar(
    desde: dt.date,
    hasta: dt.date,
    limite: int | None = None,
    pausa: float = PAUSA_SEGUNDOS,
    con_mapeo: bool = True,
) -> int:
    """Corre la ingesta. Es el punto de entrada que usa el orquestador."""
    if (hasta - desde).days > DIAS_MAXIMOS:
        raise ValueError(
            f"El API rechaza rangos mayores a {DIAS_MAXIMOS} días "
            f"(pediste {(hasta - desde).days}). Parte la carga en tramos."
        )

    print(f"Rango: {desde} → {hasta}")
    cfg = cargar_secretos()
    cliente_bq = bigquery.Client(project=PROYECTO_BQ)
    token = token_fleetup(cfg)

    if con_mapeo:
        refrescar_dispositivos(cfg, cliente_bq)

    dispositivos = dispositivos_de_fleetup(cfg, token)

    if limite:
        dispositivos = dispositivos[:limite]

    print(f"Dispositivos a consultar: {len(dispositivos)}")
    filas, fallos = traer_viajes(cfg, token, dispositivos, desde, hasta, pausa)
    cargados = cargar(cliente_bq, filas)

    if fallos:
        # Se reportan en vez de morir: un dispositivo que falla no debe tirar
        # la carga de los otros 279, pero tampoco puede pasar inadvertido.
        print(f"\n⚠️  {len(fallos)} dispositivos fallaron:")
        for dev_id, error in fallos[:10]:
            print(f"   {dev_id}: {error}")

    return cargados


def main_dias(dias: int = 7) -> int:
    """Últimos `dias` días. Lo llama el orquestador maestro."""
    hasta = dt.date.today()

    return ejecutar(hasta - dt.timedelta(days=dias), hasta)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingesta viajes de FleetUp.")
    parser.add_argument("--dias", type=int, default=7, help="Días hacia atrás.")
    parser.add_argument("--desde", help="Inicio del rango (YYYY-MM-DD).")
    parser.add_argument("--hasta", help="Fin del rango (YYYY-MM-DD).")
    parser.add_argument(
        "--limite", type=int, help="Procesar solo N dispositivos (para probar)."
    )
    parser.add_argument("--pausa", type=float, default=PAUSA_SEGUNDOS)
    parser.add_argument(
        "--sin-mapeo",
        action="store_true",
        help="No refrescar el mapeo dispositivo→ECO desde Postgres.",
    )
    args = parser.parse_args()

    hasta = (
        dt.date.fromisoformat(args.hasta) if args.hasta else dt.date.today()
    )
    desde = (
        dt.date.fromisoformat(args.desde)
        if args.desde
        else hasta - dt.timedelta(days=args.dias)
    )

    try:
        ejecutar(
            desde,
            hasta,
            limite=args.limite,
            pausa=args.pausa,
            con_mapeo=not args.sin_mapeo,
        )
    except ValueError as error:
        print(error)

        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
