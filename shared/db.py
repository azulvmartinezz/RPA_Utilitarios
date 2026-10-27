"""Conexión a Postgres para los scripts RPA que hablan con Control Vehicular.

Extraído de `extractors/fleetup_viajes.py`: ese módulo ya resolvía las
credenciales de la base desde el mismo secreto de Secret Manager que usa el
backend NestJS (`SECRETO_CONTROL_VEHICULAR`), pero la conexión vivía inline
dentro de `refrescar_dispositivos()`. Los scripts de Supramax
(`scrapers/supramax_saldos_rpa.py`, `scrapers/supramax_dispersion_rpa.py`)
necesitan la misma conexión sin duplicar la lectura del secreto, así que se
saca aquí para que ambos (y cualquier extractor futuro) la compartan.

Nunca se cachea el secreto a nivel de módulo: cada llamada a
`cargar_secretos()` vuelve a pedirlo a Secret Manager. Son procesos cortos de
cron/polling, no un servidor de larga vida -- el costo de una llamada extra
por corrida es insignificante comparado con el riesgo de quedarse en memoria
con una contraseña que ya rotó.
"""

import json
import os

import psycopg2
from google.cloud import secretmanager

# Mismo default que usa `extractors/fleetup_viajes.py`: si algún día se corre
# desde Cloud Run con otro proyecto, esto se sobreescribe por variable de
# entorno sin tocar código.
SECRETO_CONTROL_VEHICULAR = os.getenv(
    "SECRETO_CONTROL_VEHICULAR",
    "projects/herramientas-desarrollo/secrets/"
    "control-vehicular-localhost-secrets/versions/latest",
)


def cargar_secretos() -> dict:
    """El secreto compartido con `control-vehicular`: credenciales de FleetUp,
    de la base, etc. Se filtra aquí solo lo que hace falta para conectar."""
    cliente = secretmanager.SecretManagerServiceClient()
    respuesta = cliente.access_secret_version(
        request={"name": SECRETO_CONTROL_VEHICULAR}
    )
    return json.loads(respuesta.payload.data.decode())


def conectar_postgres(cfg: dict | None = None):
    """Conexión `psycopg2` lista para usar, a la misma base que usa el backend.

    `cfg` es opcional para quien ya cargó el secreto por otro motivo (por
    ejemplo, para leer también `SUPRAMAX_URL` del mismo secreto) y no quiere
    pedirlo dos veces a Secret Manager.
    """
    cfg = cfg or cargar_secretos()
    return psycopg2.connect(
        host=cfg["DB_HOST"],
        port=int(cfg["DB_PORT"]),
        user=cfg["DB_USERNAME"],
        password=cfg["DB_PASSWORD"],
        dbname=cfg["DB_DATABASE"],
    )
