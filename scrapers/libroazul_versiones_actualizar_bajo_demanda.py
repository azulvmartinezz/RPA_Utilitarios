"""Drena la cola `libroazul_versiones_jobs` que llena el autocomplete del
campo "Versión" del modal "Consultar Libro Azul": si hay un pedido
`PENDIENTE`, busca las versiones reales de esa marca/modelo/año
(`libroazul_versiones_rpa.buscar_versiones()`) y las deja en
`libroazul_versiones_cache`. Ver
`control-vehicular/db/esquema/46_libroazul_versiones_cache.sql`.

POR QUÉ UN ROBOT APARTE, MÁS IMPACIENTE QUE EL DE COTIZACIONES

`libroazul_actualizar_bajo_demanda.py` (la cola de cotizaciones de verdad)
corre cada minuto a propósito: abre Chrome y cotiza, así que revisar seguido
sin que haya nada pendiente desperdiciaría recursos sin necesidad -- nadie
espera esa respuesta en el acto, el modal de seguimiento ya avisa "En cola".
Esto es distinto: la persona está parada frente al campo de texto del modal,
a la espera de ver opciones mientras escribe. Por eso este poller corre cada
pocos segundos (`StartInterval` del plist, no cada minuto) -- el costo de
"no hay nada pendiente" sigue siendo una sola consulta SQL barata, igual que
en el otro, así que revisar más seguido no es más caro en el caso común.

MISMO PATRÓN DE HUÉRFANOS Y SIN REINTENTO

Ver `libroazul_actualizar_bajo_demanda._recuperar_huerfanos`: mismo
razonamiento, aplicado aquí con un umbral más corto (el scraping de una lista
de versiones no debería tardar lo mismo que cotizar con condición y precio).
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ_PROYECTO not in sys.path:
    sys.path.insert(0, RAIZ_PROYECTO)

from scrapers.libroazul_versiones_rpa import PedidoBusquedaVersiones, buscar_versiones  # noqa: E402
from shared.db import conectar_postgres  # noqa: E402

load_dotenv()

MINUTOS_HUERFANO = int(os.getenv("LIBROAZUL_VERSIONES_HUERFANO_MIN", "3"))


def _recuperar_huerfanos(cursor) -> None:
    cursor.execute(
        """
        UPDATE libroazul_versiones_jobs
           SET cl_estatus  = 'FALLIDO',
               de_error    = 'Job abandonado: seguía EN_PROCESO después de '
                             || %s || ' minutos.',
               fh_resuelto = now()
         WHERE cl_estatus = 'EN_PROCESO'
           AND fh_tomado < now() - (%s || ' minutes')::interval
        """,
        (MINUTOS_HUERFANO, MINUTOS_HUERFANO),
    )

    if cursor.rowcount:
        print(
            f"♻️ {cursor.rowcount} job(s) huérfano(s) de libroazul_versiones_jobs "
            f"(EN_PROCESO hace más de {MINUTOS_HUERFANO} min) marcado(s) FALLIDO."
        )


def _tomar_job_pendiente(cursor) -> dict | None:
    cursor.execute(
        """
        SELECT id_job, nb_marca, nb_modelo, nu_claveanio
          FROM libroazul_versiones_jobs
         WHERE cl_estatus = 'PENDIENTE'
         ORDER BY fh_creacion ASC
         FOR UPDATE SKIP LOCKED
         LIMIT 1
        """
    )
    fila = cursor.fetchone()
    if not fila:
        return None

    job = dict(zip(("id_job", "nb_marca", "nb_modelo", "nu_claveanio"), fila))

    cursor.execute(
        "UPDATE libroazul_versiones_jobs SET cl_estatus = 'EN_PROCESO', fh_tomado = now() WHERE id_job = %s",
        (job["id_job"],),
    )
    return job


def _marcar_completado(cursor, id_job: int) -> None:
    cursor.execute(
        "UPDATE libroazul_versiones_jobs SET cl_estatus = 'COMPLETADO', fh_resuelto = now() WHERE id_job = %s",
        (id_job,),
    )


def _marcar_fallido(cursor, id_job: int, mensaje: str) -> None:
    cursor.execute(
        """
        UPDATE libroazul_versiones_jobs
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
        conexion.commit()

        if job is None:
            return 0

        id_job = job["id_job"]
        print(
            f"Job #{id_job} de libroazul_versiones_jobs tomado; "
            f"buscando versiones de {job['nb_marca']} {job['nb_modelo']} {job['nu_claveanio']}."
        )

        try:
            pedido = PedidoBusquedaVersiones(
                nb_marca=job["nb_marca"], nb_modelo=job["nb_modelo"], nu_claveanio=job["nu_claveanio"]
            )
            versiones = buscar_versiones(pedido)
        except Exception as error:  # noqa: BLE001
            mensaje = f"{type(error).__name__}: {error}"
            print(f"❌ Job #{id_job} falló: {mensaje}")
            with conexion.cursor() as cursor:
                _marcar_fallido(cursor, id_job, mensaje)
            conexion.commit()
            return 1

        print(f"✅ Job #{id_job} completado: {len(versiones)} versión(es) guardada(s) en caché.")
        with conexion.cursor() as cursor:
            _marcar_completado(cursor, id_job)
        conexion.commit()
        return 0
    finally:
        conexion.close()


if __name__ == "__main__":
    sys.exit(main())
