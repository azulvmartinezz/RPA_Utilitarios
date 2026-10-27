"""Detalle de cruces de caseta de Pase, desde los CSV respaldados en GCS.

El consolidado `rpa_utilitarios.consumos_flota` guarda diez columnas por
transacción, y el CSV de Pase trae catorce. Lo que se perdía al consolidar es
justo lo que hace útil un reporte de peaje: **el nombre de la caseta**, el
carril, la hora exacta del cruce y la clase de vehículo. En el tablero eso se
veía como dos columnas —«estación» y «ciudad»— vacías en los 44,796 renglones.

No se vuelve a raspar el portal: los 117 CSV originales ya están respaldados en
`gs://innovacion-futuro-respaldos-rpa/Pase/`. Se releen con el mismo lector sin
pérdida y las mismas reglas de limpieza que usó la carga original —normalizar
el económico y quedarse solo con AU/CA— para que el conjunto de filas por
archivo sea idéntico al que ya está en BigQuery. Esa igualdad es la que permite
sustituir sin duplicar ni perder: la vista toma de aquí los archivos que
existen en GCS y del consolidado los que no.

La tabla se reescribe completa en cada corrida (`WRITE_TRUNCATE`). Es una
proyección de los archivos, no un acumulado: reprocesarla dos veces da lo mismo,
y así no hace falta una llave de deduplicación que dependa del texto crudo de
cada renglón.

Uso:
    python -m extractors.pase_cruces
    python -m extractors.pase_cruces --limite 5    # prueba corta
"""

import argparse
import datetime as dt
import hashlib
import os
import re
import sys
import tempfile

import pandas as pd
from google.cloud import bigquery
from google.cloud import storage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pase_utils import parse_pase_fecha, read_pase_csv_lossless  # noqa: E402

PROYECTO_BQ = os.getenv("GCP_PROJECT_ID", "innovacion-futuro")
DATASET = os.getenv("BQ_DATASET_FLOTA", "flota")
TABLA = f"{PROYECTO_BQ}.{DATASET}.pase_cruces"

CUBETA = os.getenv("GCS_RESPALDOS", "innovacion-futuro-respaldos-rpa")
PREFIJO = "Pase/"


def _normalize_eco(val) -> str:
    """El mismo criterio que usó la carga original; copiarlo es a propósito.

    Importarlo desde `bigquery/bq_ingestion.py` arrastraría todo ese módulo
    —cliente de BigQuery incluido— por una función de tres líneas. Si el
    criterio cambia, tiene que cambiar en los dos lados, y para eso está esta
    nota.
    """
    s = str(val).strip().upper().replace(" ", "").replace(".", "")
    m = re.match(r"^(AU|CA)-?(\d{1,3})(?!\d)", s)

    return f"{m.group(1)}-{m.group(2).zfill(3)}" if m else s


def _columna(df: pd.DataFrame, *claves: str):
    """Encuentra una columna ignorando mayúsculas, espacios, puntos y acentos.

    Los encabezados de Pase vienen con espacios de relleno y acentos
    inconsistentes entre fideicomisos.
    """
    normal = {
        c: c.lower().replace(" ", "").replace("ó", "o").replace(".", "")
        for c in df.columns
    }

    for clave in claves:
        for columna, norm in normal.items():
            if clave in norm:
                return columna

    return None


def _texto(serie: pd.Series) -> pd.Series:
    return serie.astype(str).str.strip().replace("", None)


def _empresa_de_ruta(ruta: str) -> str:
    """`Pase/PETROPLAZAS/2025/03/archivo.csv` → `PETROPLAZAS`."""
    partes = ruta.split("/")

    return partes[1].replace("_", " ") if len(partes) > 2 else None


def leer(ruta_local: str, nombre: str, empresa: str) -> pd.DataFrame:
    df = read_pase_csv_lossless(ruta_local)

    if df.empty:
        return pd.DataFrame()

    col_eco = _columna(df, "noeconomico", "eco")
    col_fecha = _columna(df, "fechadecruce", "fecha")
    col_hora = _columna(df, "horadecruce", "hora")
    col_caseta = _columna(df, "nombredecaseta", "caseta")
    col_carril = _columna(df, "nombredecarril", "carril")
    col_clase = _columna(df, "clase")
    col_tarjeta = _columna(df, "tarjetaidmx", "tarjeta")
    col_importe = _columna(df, "importeal100", "importe")

    if not col_eco or not col_fecha or not col_importe:
        print(f"  ⚠️ {nombre}: faltan columnas básicas; se omite.")

        return pd.DataFrame()

    salida = pd.DataFrame()
    salida["eco"] = df[col_eco].apply(_normalize_eco)
    salida["fecha"] = parse_pase_fecha(df[col_fecha]).dt.date
    salida["hr_cruce"] = _texto(df[col_hora]) if col_hora else None
    salida["nb_caseta"] = _texto(df[col_caseta]) if col_caseta else None
    salida["nb_carril"] = _texto(df[col_carril]) if col_carril else None
    salida["cl_clase"] = _texto(df[col_clase]) if col_clase else None
    salida["nu_tarjeta"] = _texto(df[col_tarjeta]) if col_tarjeta else None
    salida["importe"] = pd.to_numeric(
        df[col_importe].astype(str).str.replace(r"[$,]", "", regex=True),
        errors="coerce",
    ).abs()
    salida["nb_empresa"] = empresa
    salida["archivo_origen"] = nombre

    # Mismas exclusiones que la carga original, en el mismo orden.
    salida = salida.dropna(subset=["importe", "fecha", "eco"])
    salida = salida[salida["eco"].str.match(r"^(AU|CA)-\d{3}$", na=False)]

    return salida


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingesta cruces de Pase.")
    parser.add_argument("--limite", type=int, help="Procesar solo N archivos.")
    args = parser.parse_args()

    cliente_gcs = storage.Client(project=PROYECTO_BQ)

    # Un archivo por nombre, no una ruta por nombre.
    #
    # El respaldo guarda el mismo CSV bajo la carpeta de cada mes que toca, así
    # que hay 117 rutas para 74 archivos: algunos aparecen dos y tres veces.
    # Leerlos todos multiplicaba los cruces —60,442 contra los 44,796 que de
    # verdad hay— sin que nada fallara, que es como se cuela un total inflado a
    # un reporte.
    por_nombre = {}

    for blob in cliente_gcs.list_blobs(CUBETA, prefix=PREFIJO):
        if not blob.name.lower().endswith(".csv"):
            continue

        por_nombre.setdefault(blob.name.split("/")[-1], blob)

    blobs = list(por_nombre.values())

    if args.limite:
        blobs = blobs[: args.limite]

    print(f"Archivos a leer: {len(blobs)}")

    marcos = []

    with tempfile.TemporaryDirectory() as carpeta:
        for indice, blob in enumerate(blobs, start=1):
            nombre = blob.name.split("/")[-1]
            destino = os.path.join(carpeta, f"{indice}_{nombre}")
            blob.download_to_filename(destino)

            try:
                parcial = leer(destino, nombre, _empresa_de_ruta(blob.name))
            except Exception as error:  # noqa: BLE001
                print(f"  ⚠️ {nombre}: {error}")
                continue

            if not parcial.empty:
                marcos.append(parcial)

            print(f"  [{indice}/{len(blobs)}] {nombre}: {len(parcial)} cruces")

    if not marcos:
        print("Sin cruces que cargar.")

        return 1

    tabla = pd.concat(marcos, ignore_index=True)

    # Un id estable por renglón, para poder señalar un cruce concreto desde
    # fuera. Se arma con el archivo y la posición, no con el contenido: dos
    # cruces de la misma unidad, misma caseta y mismo importe en el mismo minuto
    # son posibles y no deben colapsarse en uno.
    tabla["id_cruce"] = [
        hashlib.sha1(f"{a}|{i}".encode("utf-8")).hexdigest()
        for i, a in enumerate(tabla["archivo_origen"].tolist())
    ]
    tabla["ingestado_en"] = dt.datetime.now(dt.timezone.utc)

    cliente_bq = bigquery.Client(project=PROYECTO_BQ)
    cliente_bq.load_table_from_dataframe(
        tabla,
        TABLA,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"),
    ).result()

    print(f"✅ {len(tabla)} cruces cargados en {TABLA}.")
    print(f"   Casetas distintas: {tabla['nb_caseta'].nunique()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
