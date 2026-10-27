"""Puebla `flota.casetas_ubicacion`, el catálogo de coordenadas para el mapa
de casetas del tablero.

    python -m bigquery.seed_casetas_ubicacion

Es un MERGE, no un INSERT: correrlo otra vez actualiza las filas existentes
en vez de duplicarlas, así que agregar una caseta nueva a `CASETAS` de abajo
y volver a correrlo es la forma normal de ampliar el catálogo -- no hace
falta borrar nada primero.

Las coordenadas de esta primera carga salen de casetas.com.mx (publica
lat/lon por caseta con enlace a Google Maps y Waze), verificadas contra la
autopista real de cada una -- no geocodificación automática por nombre. Ver
`bigquery/ddl/casetas_ubicacion.sql` para el porqué.
"""
import datetime as dt
import os

from google.cloud import bigquery

PROYECTO_BQ = os.getenv("GCP_PROJECT_ID", "innovacion-futuro")
DATASET = os.getenv("BQ_DATASET_FLOTA", "flota")
TABLA = f"{PROYECTO_BQ}.{DATASET}.casetas_ubicacion"
FUENTE = "casetas.com.mx"

# nb_caseta debe coincidir EXACTO con pase_cruces.nb_caseta. Dos nombres que
# son la misma plaza física (p. ej. las dos grafías de "Esperanza") llevan
# las mismas coordenadas, cada uno en su propia fila -- el join es por texto
# exacto, no hay normalización del lado del catálogo.
CASETAS = [
    ("VAINILLO", 23.21624940305487, -106.2603431699663, "Libramiento Mazatlán (15D)"),
    ("MARMOL", 23.471187313387013, -106.56949656965575, "Mazatlán - Culiacán"),
    ("APODACA", 25.787615670389666, -100.16856544250632, "Monterrey (NL)"),
    ("SINALOA", 25.513032464809882, -108.34758723608219, "Puente Sinaloa"),
    ("COSTA RICA I", 24.57037898982773, -107.43008111421493, "Mazatlán - Culiacán"),
    ("ROSARIO", 22.984755540917362, -105.87807457906604, "Tepic - Mazatlán"),
    ("CULIACAN", 24.7489181972846, -107.46220147091346, "Mazatlán - Culiacán"),
    ("LEON", 21.046289930678324, -101.62851203926601, "Salamanca - León"),
    ("DURANGO", 24.005056251378306, -104.73451914310864, "Libramiento Durango"),
    ("TRAPICHILLO", 21.560635203646527, -104.9712953470269, "Tepic - Mazatlán (Nayarit)"),
    ("ACAPONETA", 22.453936244016106, -105.42485053598634, "Tepic - Mazatlán (Nayarit)"),
    ("ESTACION RUIZ", 21.951880566938872, -105.11609683707309, "Tepic - Mazatlán (Rosamorada, Nay.)"),
    ("PLAN DE BARRANCAS", 21.00532869974646, -104.1440957932826, "Guadalajara - Tepic (Jalisco)"),
    ("STA. MA. DEL ORO", 21.374125999999134, -104.708925, "Guadalajara - Tepic (Nayarit)"),
    ("SAN PEDRO", 24.690371371243764, -107.52305827467087, "Mazatlán - Culiacán (Navolato, Sin.)"),
    ("ESPERANZA SON.", 27.604597171762215, -109.89870943003737, "Libramiento Cd. Obregón (Sonora)"),
    ("ESPERANZA SONORA", 27.604597171762215, -109.89870943003737, "Libramiento Cd. Obregón (Sonora)"),
    ("ROSARITO", 32.31123256215487, -117.04437785460482, "Escénica Tijuana - Ensenada"),
    ("LEON GUZMAN", 25.51948968899933, -103.63941354299999, "Durango - Gómez Palacio"),
    ("HERMOSILLO", 29.02975215200689, -110.85413437768335, "Libramiento Hermosillo"),
    ("CUENCAME", 24.98168086993506, -103.73525179574929, "Durango - Gómez Palacio"),
    ("TECATE", 32.55162674610802, -116.62114759801428, "Tijuana - Tecate (aprox.)"),
    ("SAYULA", 19.933193039947167, -103.53012935939724, "Guadalajara - Colima (Jalisco)"),
    ("YERBANIS", 24.730698106609776, -103.86174452066822, "Durango - Gómez Palacio"),
    ("MESILLAS", 23.255196995822935, -106.0536407468196, "Mazatlán - Culiacán"),
    ("PLAYAS DE TIJUANA", 32.51402732888465, -117.10942646176365, "Escénica Tijuana - Ensenada"),
    ("LLANO GRANDE", 23.870860665339517, -105.19153817807411, "Durango - Villa Unión"),
    ("COSCOMATE", 23.706304071245892, -105.57118752812619, "Durango - Villa Unión"),
    ("SALAMANCA", 20.597631115813066, -101.18101115911118, "Querétaro - Irapuato (45D)"),
    ("GUAYMAS", 28.03672254414282, -110.9245104239737, "Hermosillo - Guaymas"),
    ("CERRO GORDO", 20.591910725401483, -101.13734623274328, "Querétaro - Irapuato (45D)"),
    ("CADEREYTA-REA", 25.60103618312252, -100.00592427299064, "Puente Cadereyta (NL)"),
    ("MAGDALENA", 30.62943413206377, -110.94864871634985, "Hermosillo - Nogales"),
    ("PUERTO INTERIOR", 20.98382313424874, -101.5422150068167, "Salamanca - León"),
    # Segunda tanda (2026-09-15): Coahuila, Jalisco, Querétaro, Edomex.
    ("PLAN DE AYALA", 25.444234021531255, -101.30388597205057, "Saltillo - Torreón (Coahuila)"),
    ("LA CUCHILLA", 25.62706035094198, -102.87836953019901, "Saltillo - Torreón (San Pedro, Coah.)"),
    ("TORREON", 25.448419132010386, -101.06705392804402, "Plaza de Cobro Torreón"),
    ("OJO CALIENTE I", 25.61236380380398, -100.91354403885421, "Libramiento Saltillo (Coahuila)"),
    ("TEPATITLAN", 20.824672674798112, -102.79501525668337, "Zapotlanejo - Lagos de Moreno (Jalisco)"),
    ("SANTA CECILIA", 20.442528873978805, -103.54579345602107, "(Jalisco)"),
    ("PALMILLAS BIS", 20.29047884334927, -99.92061130589548, "Palmillas - Apaseo el Grande (Qro)"),
    ("ATLACOMULCO-LIB QRO", 20.290140198794735, -99.94967798496384, "Palmillas - Apaseo el Grande (Qro)"),
    ("QUERETARO", 20.572203669708536, -100.47286986896246, "Querétaro - Irapuato"),
    ("ZAPOTLANEJO", 20.5860286520384, -103.1007622099411, "Macrolibramiento de Guadalajara"),
    # Aproximado: es la misma autopista (Circuito Exterior Mexiquense), no la
    # plaza exacta -- el catálogo del sitio no distingue cuál de sus varias
    # casetas es "CTO EXT MEXIQUENSE" en el CSV de Pase.
    ("CTO EXT MEXIQUENSE", 19.820061446401677, -99.23989321024048, "Circuito Exterior Mexiquense (aprox., Jorobas)"),
    ("CTO. EXT. MEXIQUENSE", 19.820061446401677, -99.23989321024048, "Circuito Exterior Mexiquense (aprox., Jorobas)"),
]


def main() -> int:
    cliente = bigquery.Client(project=PROYECTO_BQ)
    ahora = dt.datetime.now(dt.timezone.utc).isoformat()

    filas = [
        {
            "nb_caseta": nombre,
            "latitud": lat,
            "longitud": lon,
            "carretera": carretera,
            "fuente": FUENTE,
            "actualizado_en": ahora,
        }
        for nombre, lat, lon, carretera in CASETAS
    ]

    tabla_temporal = f"{TABLA}_stage_{int(dt.datetime.now().timestamp())}"

    cliente.load_table_from_json(
        filas,
        tabla_temporal,
        job_config=bigquery.LoadJobConfig(
            write_disposition="WRITE_TRUNCATE",
            schema=[
                bigquery.SchemaField("nb_caseta", "STRING"),
                bigquery.SchemaField("latitud", "FLOAT64"),
                bigquery.SchemaField("longitud", "FLOAT64"),
                bigquery.SchemaField("carretera", "STRING"),
                bigquery.SchemaField("fuente", "STRING"),
                bigquery.SchemaField("actualizado_en", "TIMESTAMP"),
            ],
        ),
    ).result()

    cliente.query(
        f"""
        MERGE `{TABLA}` T
        USING `{tabla_temporal}` S
        ON T.nb_caseta = S.nb_caseta
        WHEN MATCHED THEN
          UPDATE SET latitud = S.latitud, longitud = S.longitud,
                     carretera = S.carretera, fuente = S.fuente,
                     actualizado_en = S.actualizado_en
        WHEN NOT MATCHED THEN
          INSERT (nb_caseta, latitud, longitud, carretera, fuente, actualizado_en)
          VALUES (nb_caseta, latitud, longitud, carretera, fuente, actualizado_en)
        """
    ).result()

    cliente.delete_table(tabla_temporal, not_found_ok=True)

    print(f"✅ {len(filas)} casetas cargadas/actualizadas en {TABLA}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
