"""Drena la cola `supramax_refresh_jobs` que llena el botón "Actualizar
Supramax" de la pantalla de Saldos y recargas: si hay un pedido `PENDIENTE`,
corre la MISMA rutina de scraping que usa el cron regular de 30 minutos
(`supramax_saldos_rpa.ejecutar_actualizacion_saldos()`) de inmediato, sin
esperar el próximo ciclo. Ver `db/esquema/32_supramax_refresh_jobs.sql` (en
`control-vehicular`) para el diseño completo de la cola.

DISEÑADO PARA CORRER CADA ~1 MINUTO, Y SALIR BARATO CUANDO NO HAY NADA

A diferencia de `supramax_saldos_rpa.py` (que siempre abre Chrome para las
cuentas configuradas) y de `supramax_dispersion_rpa.py` (que solo abre Chrome
si hay jobs), este script corre mucho más seguido -- cada minuto, contra cada
30 minutos o cada 1-2 minutos de los otros dos. La inmensa mayoría de esas
corridas no van a encontrar ningún pedido pendiente, así que el costo de "no
hay nada que hacer" tiene que quedarse en una consulta SQL (`_tomar_job_
pendiente`) y nunca llegar a loguear en el portal. Por eso este archivo NO
importa Selenium directamente -- solo lo hace, indirectamente, al llamar
`ejecutar_actualizacion_saldos()`, y solo cuando de verdad hay un job que
tomar.

NO REIMPLEMENTA EL SCRAPING

Este archivo es puramente el disparador: toma el job, marca `EN_PROCESO`,
llama la misma función que usa el cron de 30 minutos, y traduce el resultado
a un estatus de `supramax_refresh_jobs`. Toda la lógica de login, parseo del
listado de créditos y escritura en `supramax_saldos` sigue viviendo en
`supramax_saldos_rpa.py` -- tener dos implementaciones del mismo scraping
sería el error que esta refactorización vino a evitar.

RECUPERACIÓN DE HUÉRFANOS: FALLIDO DIRECTO, NO REINTENTO

`supramax_dispersion_jobs` regresa sus huérfanos a `PENDIENTE` (ver
`supramax_dispersion_rpa._recuperar_huerfanos`) porque cuenta con
`nu_intentos` para acotar cuántas veces se reintenta antes de rendirse.
`supramax_refresh_jobs` NO tiene esa columna (ver el `.sql`): es un pedido de
"refresca ahora", no una operación de negocio con reintentos con backoff. Sin
un contador, reintentar automáticamente un job que se cuelga por una causa
estructural (por ejemplo, un cambio en el portal que hace que el scraping
nunca termine) lo dejaría atorándose cada `SUPRAMAX_REFRESH_HUERFANO_MIN`
minutos para siempre, sin que nadie se entere hasta revisar el log. Marcarlo
`FALLIDO` de una vez dejar la cola limpia (nada `EN_PROCESO` bloqueando
`SupramaxService.solicitarActualizacion()`, que reusa el job en curso en vez
de encolar uno nuevo) y visible en la pantalla que hizo el pedido -- quien lo
necesite solo aprieta el botón otra vez.
"""

from __future__ import annotations

import os
import sys
import threading

from dotenv import load_dotenv

RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ_PROYECTO not in sys.path:
    sys.path.insert(0, RAIZ_PROYECTO)

from scrapers.supramax_saldos_rpa import ejecutar_actualizacion_saldos  # noqa: E402
from shared.db import conectar_postgres  # noqa: E402

load_dotenv()

# Mismo nombre de patrón que `SUPRAMAX_DISPERSION_HUERFANO_MIN` en
# `supramax_dispersion_rpa.py`, con su propia variable: los dos disparadores
# de Supramax pueden querer tolerancias distintas sin acoplarse entre sí.
MINUTOS_HUERFANO = int(os.getenv("SUPRAMAX_REFRESH_HUERFANO_MIN", "10"))


class Avance:
    """Deja en `supramax_refresh_avance` el paso en que va cada cuenta, para
    que la pantalla que pidio la actualizacion lo muestre en vivo.

    Tiene su propia conexion, en autocommit y con candado: lo llaman los
    hilos del pool de navegadores (hasta tres cuentas a la vez) y cada paso
    tiene que verse de inmediato, no al final cuando se hace commit del job.
    Si la conexion falla, el avance se apaga y el scraping sigue: es para
    informar, no parte del trabajo."""

    def __init__(self, id_job: int):
        self.id_job = id_job
        self._candado = threading.Lock()
        try:
            self._conexion = conectar_postgres()
            self._conexion.autocommit = True
        except Exception as error:  # noqa: BLE001
            print(f"⚠️ Sin conexion para reportar avance: {error}")
            self._conexion = None

    def _ejecutar(self, sql: str, parametros: tuple) -> None:
        if self._conexion is None:
            return
        with self._candado, self._conexion.cursor() as cursor:
            cursor.execute(sql, parametros)

    def iniciar(self, cuentas: list[str]) -> None:
        """Todas las cuentas en cola, y el job en fase CONSULTANDO."""
        self._ejecutar(
            "UPDATE supramax_refresh_jobs SET cl_fase = 'CONSULTANDO', nu_cuentas = %s WHERE id_job = %s",
            (len(cuentas), self.id_job),
        )
        for nombre in cuentas:
            self._ejecutar(
                """
                INSERT INTO supramax_refresh_avance (id_job, nb_cuenta, cl_paso)
                VALUES (%s, %s, 'EN_COLA')
                ON CONFLICT (id_job, nb_cuenta) DO NOTHING
                """,
                (self.id_job, nombre[:150]),
            )

    def paso(self, cuenta: str, paso: str, tarjetas: int | None = None, detalle: str | None = None) -> None:
        self._ejecutar(
            """
            INSERT INTO supramax_refresh_avance (id_job, nb_cuenta, cl_paso, nu_tarjetas, de_detalle)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id_job, nb_cuenta) DO UPDATE
               SET cl_paso = EXCLUDED.cl_paso,
                   nu_tarjetas = COALESCE(EXCLUDED.nu_tarjetas, supramax_refresh_avance.nu_tarjetas),
                   de_detalle = COALESCE(EXCLUDED.de_detalle, supramax_refresh_avance.de_detalle),
                   fh_actualizacion = now()
            """,
            (self.id_job, cuenta[:150], paso, tarjetas, detalle),
        )

    def fase(self, fase: str) -> None:
        self._ejecutar(
            "UPDATE supramax_refresh_jobs SET cl_fase = %s WHERE id_job = %s",
            (fase, self.id_job),
        )

    def cerrar(self) -> None:
        if self._conexion is not None:
            try:
                self._conexion.close()
            except Exception:  # noqa: BLE001
                pass


def _recuperar_huerfanos(cursor) -> None:
    cursor.execute(
        """
        UPDATE supramax_refresh_jobs
           SET cl_estatus  = 'FALLIDO',
               de_error    = 'Job abandonado: seguía EN_PROCESO después de '
                             || %s || ' minutos (el proceso que lo tomó '
                             || 'murió o se colgó a medio camino). Vuelve a '
                             || 'intentarse desde la pantalla si aún hace falta.',
               fh_resuelto = now()
         WHERE cl_estatus = 'EN_PROCESO'
           AND fh_tomado < now() - (%s || ' minutes')::interval
        """,
        (MINUTOS_HUERFANO, MINUTOS_HUERFANO),
    )

    if cursor.rowcount:
        print(
            f"♻️ {cursor.rowcount} job(s) huérfano(s) de supramax_refresh_jobs "
            f"(EN_PROCESO hace más de {MINUTOS_HUERFANO} min) marcado(s) FALLIDO."
        )


def _tomar_job_pendiente(cursor) -> int | None:
    """`FOR UPDATE SKIP LOCKED`, igual que
    `supramax_dispersion_rpa._tomar_jobs_pendientes`: si el polling se
    solapó con otra corrida de este mismo script, esta simplemente no ve el
    job que la otra ya está tomando en vez de bloquearse esperando su lock.
    `LIMIT 1` porque nunca hay más de un job vivo a la vez -- lo garantiza
    `SupramaxService.solicitarActualizacion()` del lado del backend."""
    cursor.execute(
        """
        SELECT id_job
          FROM supramax_refresh_jobs
         WHERE cl_estatus = 'PENDIENTE'
         ORDER BY fh_creacion ASC
         FOR UPDATE SKIP LOCKED
         LIMIT 1
        """
    )
    fila = cursor.fetchone()
    if not fila:
        return None

    id_job = fila[0]
    cursor.execute(
        "UPDATE supramax_refresh_jobs SET cl_estatus = 'EN_PROCESO', fh_tomado = now() WHERE id_job = %s",
        (id_job,),
    )
    return id_job


def _marcar_completado(cursor, id_job: int) -> None:
    cursor.execute(
        "UPDATE supramax_refresh_jobs SET cl_estatus = 'COMPLETADO', fh_resuelto = now() WHERE id_job = %s",
        (id_job,),
    )


def _marcar_fallido(cursor, id_job: int, mensaje: str) -> None:
    cursor.execute(
        """
        UPDATE supramax_refresh_jobs
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
            id_job = _tomar_job_pendiente(cursor)
        # Commit inmediato al marcar EN_PROCESO: que otra corrida de este
        # mismo script (si el polling de 1 minuto se solapó) no se quede
        # esperando este lock más de lo necesario, y que el backend vea de
        # inmediato que ya hay alguien atendiendo el pedido.
        conexion.commit()

        if id_job is None:
            # El caso común: nada pendiente. Sale aquí sin haber tocado
            # Selenium ni Chrome -- ver el docstring del módulo.
            return 0

        print(f"Job #{id_job} de supramax_refresh_jobs tomado; corriendo actualización de saldos.")

        avance = Avance(id_job)
        try:
            stats = ejecutar_actualizacion_saldos(avance)
        except Exception as error:  # noqa: BLE001
            # La rutina entera no pudo correr (config faltante/ inválida, o
            # cualquier excepción no anticipada que se nos haya escapado) --
            # esto sí es FALLIDO, a diferencia de que a algunas cuentas les
            # falle el login individualmente (ver más abajo).
            mensaje = f"{type(error).__name__}: {error}"
            print(f"❌ La rutina de actualización de saldos no pudo correr: {mensaje}")
            with conexion.cursor() as cursor:
                _marcar_fallido(cursor, id_job, mensaje)
            conexion.commit()
            return 1
        finally:
            avance.cerrar()

        total_cuentas = stats.get("total_cuentas", 0)
        total_login_ok = stats.get("total_login_ok", 0)

        if total_cuentas > 0 and total_login_ok == 0:
            # Ninguna cuenta se pudo ni siquiera loguear: no es el fallo
            # parcial normal (algunas sí, otras no), es que la corrida
            # completa no sirvió de nada -- el usuario pidió un refresco y
            # no se refrescó ni una sola cuenta.
            mensaje = (
                f"Login falló para las {total_cuentas} cuenta(s) configuradas; "
                "ninguna se pudo actualizar en esta corrida."
            )
            print(f"❌ Job #{id_job}: {mensaje}")
            with conexion.cursor() as cursor:
                _marcar_fallido(cursor, id_job, mensaje)
        else:
            # Fallo parcial (algunas cuentas sin login) SÍ cuenta como
            # COMPLETADO -- el usuario pidió "actualiza lo que se pueda", no
            # "todas o nada". `supramax_saldos_rpa.py` ya deja constancia por
            # cuenta (`ERROR_LOGIN` en `supramax_saldos`) para quien quiera
            # el detalle.
            print(
                f"✅ Job #{id_job} completado: {stats.get('total_saldos_ok', 0)} saldo(s) actualizados "
                f"({total_login_ok}/{total_cuentas} cuenta(s) con login exitoso)."
            )
            with conexion.cursor() as cursor:
                _marcar_completado(cursor, id_job)

        conexion.commit()
        return 0
    finally:
        conexion.close()


if __name__ == "__main__":
    sys.exit(main())
