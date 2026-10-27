"""Drena la cola `libroazul_valuacion_jobs` que llena el botón "Consultar
Libro Azul" de la carta de venta: si hay un pedido `PENDIENTE`, cotiza esa
unidad en el sitio público de la Guía EBC
(`libroazul_precio_rpa.cotizar_unidad()`) de inmediato, actualizando
`cl_paso`/`de_paso` en cada etapa para que el modal de seguimiento del
frontend (calcado de `SeguimientoDispersion.vue` de Supramax) tenga qué
mostrar. Ver `control-vehicular/db/esquema/45_libroazul_valuacion_jobs.sql`
para el diseño completo de la cola -- es el mismo patrón que
`supramax_actualizar_bajo_demanda.py` ya resolvió para Supramax, adaptado a
que aquí cada job es una unidad puntual (con su propia marca/modelo/año/
versión/condición) y no un refresco global.

DISEÑADO PARA CORRER CADA ~1 MINUTO, Y SALIR BARATO CUANDO NO HAY NADA

Igual que su contraparte de Supramax: la inmensa mayoría de las corridas no
van a encontrar ningún pedido pendiente, así que ese caso tiene que quedarse
en una consulta SQL y nunca llegar a abrir Chrome. Por eso este archivo NO
importa Selenium directamente -- solo lo hace, indirectamente, al llamar
`cotizar_unidad()`, y solo cuando de verdad hay un job que tomar.

RECUPERACIÓN DE HUÉRFANOS: FALLIDO DIRECTO, NO REINTENTO

Mismo razonamiento que `supramax_actualizar_bajo_demanda._recuperar_huerfanos`:
sin un contador de intentos, reintentar solo automáticamente un job que se
cuelga por una causa estructural (el sitio cambió su maqueta, por ejemplo) lo
dejaría atorándose para siempre sin que nadie se entere. Se marca `FALLIDO`,
la cola queda libre para esa unidad, y quien lo necesite vuelve a apretar
"Consultar Libro Azul".
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ_PROYECTO not in sys.path:
    sys.path.insert(0, RAIZ_PROYECTO)

from scrapers.libroazul_precio_rpa import PedidoCotizacion, cotizar_unidad  # noqa: E402
from shared.db import conectar_postgres  # noqa: E402

load_dotenv()

MINUTOS_HUERFANO = int(os.getenv("LIBROAZUL_REFRESH_HUERFANO_MIN", "10"))


def _recuperar_huerfanos(cursor) -> None:
    cursor.execute(
        """
        UPDATE libroazul_valuacion_jobs
           SET cl_estatus  = 'FALLIDO',
               de_error    = 'Job abandonado: seguía EN_PROCESO después de '
                             || %s || ' minutos (el proceso que lo tomó '
                             || 'murió o se colgó a medio camino). Vuelve a '
                             || 'apretar "Consultar Libro Azul" si aún hace falta.',
               fh_resuelto = now()
         WHERE cl_estatus = 'EN_PROCESO'
           AND fh_tomado < now() - (%s || ' minutes')::interval
        """,
        (MINUTOS_HUERFANO, MINUTOS_HUERFANO),
    )

    if cursor.rowcount:
        print(
            f"♻️ {cursor.rowcount} job(s) huérfano(s) de libroazul_valuacion_jobs "
            f"(EN_PROCESO hace más de {MINUTOS_HUERFANO} min) marcado(s) FALLIDO."
        )


def _tomar_job_pendiente(cursor) -> dict | None:
    """`FOR UPDATE SKIP LOCKED`: si el polling de 1 minuto se solapó con otra
    corrida de este mismo script, esta simplemente no ve el job que la otra
    ya está tomando. A diferencia de Supramax, aquí SÍ puede haber más de un
    job vivo a la vez (unidades distintas) -- se toma solo el más viejo por
    corrida, para no abrir varios Chrome en paralelo desde un poller que se
    dispara cada minuto."""
    cursor.execute(
        """
        SELECT id_job, id_unidad, nb_marca, nb_modelo, nu_claveanio, nb_version,
               cl_condicion, nu_odometro, id_usuariocreacion
          FROM libroazul_valuacion_jobs
         WHERE cl_estatus = 'PENDIENTE'
         ORDER BY fh_creacion ASC
         FOR UPDATE SKIP LOCKED
         LIMIT 1
        """
    )
    fila = cursor.fetchone()
    if not fila:
        return None

    columnas = (
        "id_job", "id_unidad", "nb_marca", "nb_modelo", "nu_claveanio", "nb_version",
        "cl_condicion", "nu_odometro", "id_usuariocreacion",
    )
    job = dict(zip(columnas, fila))

    cursor.execute(
        "UPDATE libroazul_valuacion_jobs SET cl_estatus = 'EN_PROCESO', fh_tomado = now() WHERE id_job = %s",
        (job["id_job"],),
    )
    return job


def _marcar_paso(conexion, id_job: int, clave: str, detalle: str) -> None:
    """El callback que se le pasa a `cotizar()` -- hace su propio commit
    inmediato (no espera al final del job) para que el modal de seguimiento,
    que sondea cada pocos segundos, vea el paso en cuanto cambia y no solo al
    terminar."""
    with conexion.cursor() as cursor:
        cursor.execute(
            "UPDATE libroazul_valuacion_jobs SET cl_paso = %s, de_paso = %s, fh_paso = now() WHERE id_job = %s",
            (clave, detalle, id_job),
        )
    conexion.commit()


def _marcar_completado(cursor, id_job: int, id_valuacion: int) -> None:
    cursor.execute(
        """
        UPDATE libroazul_valuacion_jobs
           SET cl_estatus = 'COMPLETADO', cl_paso = 'LISTO', de_paso = 'Cotización guardada',
               fh_paso = now(), fh_resuelto = now(), id_valuacion_resultado = %s
         WHERE id_job = %s
        """,
        (id_valuacion, id_job),
    )


def _marcar_fallido(cursor, id_job: int, mensaje: str) -> None:
    cursor.execute(
        """
        UPDATE libroazul_valuacion_jobs
           SET cl_estatus = 'FALLIDO', de_error = %s, fh_resuelto = now()
         WHERE id_job = %s
        """,
        (mensaje, id_job),
    )


def main() -> int:
    conexion = conectar_postgres()
    try:
        with conexion.cursor() as cursor:
            _recuperar_huerfanos(cursor)
        conexion.commit()

        with conexion.cursor() as cursor:
            job = _tomar_job_pendiente(cursor)
        # Commit inmediato al marcar EN_PROCESO: igual que en Supramax, que
        # otra corrida solapada no espere este lock más de lo necesario.
        conexion.commit()

        if job is None:
            return 0

        id_job = job["id_job"]
        print(f"Job #{id_job} de libroazul_valuacion_jobs tomado; cotizando unidad {job['id_unidad']}.")

        # Entre tomar el job y el primer callback de `cotizar()` (que solo se
        # dispara una vez que Chrome ya abrió y arrancó la navegación) puede
        # pasar varios segundos arrancando el navegador headless. Sin este
        # aviso, el modal de seguimiento se queda sin ningún paso resaltado
        # durante ese hueco -- se ve como atorado aunque sí está corriendo.
        _marcar_paso(conexion, id_job, "EN_COLA", "Arrancando el navegador")

        try:
            pedido = PedidoCotizacion(
                nb_marca=job["nb_marca"],
                nb_modelo=job["nb_modelo"],
                nu_claveanio=job["nu_claveanio"],
                nb_version=job["nb_version"],
                cl_condicion=job["cl_condicion"],
            )
            resultado = cotizar_unidad(
                job["id_unidad"],
                pedido,
                float(job["nu_odometro"]) if job["nu_odometro"] is not None else None,
                job["id_usuariocreacion"],
                progreso=lambda clave, detalle: _marcar_paso(conexion, id_job, clave, detalle),
            )
        except Exception as error:  # noqa: BLE001
            mensaje = f"{type(error).__name__}: {error}"
            print(f"❌ Job #{id_job} (unidad {job['id_unidad']}) falló: {mensaje}")
            with conexion.cursor() as cursor:
                _marcar_fallido(cursor, id_job, mensaje)
            conexion.commit()
            return 1

        print(
            f"✅ Job #{id_job} completado: unidad {job['id_unidad']} valuada en "
            f"${resultado['im_valor']:,.2f} ({resultado['nb_version']})."
        )
        with conexion.cursor() as cursor:
            _marcar_completado(cursor, id_job, resultado["id_valuacion"])
        conexion.commit()
        return 0
    finally:
        conexion.close()


if __name__ == "__main__":
    sys.exit(main())
