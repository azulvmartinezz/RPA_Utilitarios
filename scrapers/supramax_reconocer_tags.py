"""Reconoce los TAG reales de Supramax cruzando por económico, y homologa
`tarjetas.nu_foliotarjeta` cuando difiere de lo que el portal reporta hoy.

POR QUÉ HACE FALTA ESTO

La primera corrida real de `supramax_saldos_rpa.py` (2026-09-16) confirmó que
`tarjetas.nu_foliotarjeta` (para `id_tipotarjeta IN (5, 1)`) no cruza contra
el TAG que el portal reporta hoy para 0 de ~500 vehículos: 19/19 cuentas
loguearon bien, pero el cruce por tag/idv no encontró nada. El tag que
tenemos capturado (de una carga manual, ver `db/datos/007_tarjetas.sql`) está
desactualizado, no el criterio de cruce en sí.

CÓMO SE RECONOCE SIN CONFIAR EN EL TAG

El portal rotula "Placas" a la columna que, mirando los valores reales
(`AU-057`, `CA-044`, `25ENE`...), en realidad trae el ECONÓMICO -- el mismo
formato que `unidades.nu_economico`, no una placa vehicular. Cruzando por ahí
en vez de por tag se puede comparar, unidad por unidad, el tag que Supramax
reporta HOY contra el que tenemos guardado, sin depender de que el tag viejo
siga siendo válido.

REPORTE PRIMERO, ESCRITURA SOLO CON --aplicar

Por default este script NO escribe nada: imprime un reporte de qué unidades
cruzaron limpio, cuáles necesitan que se actualice el tag, cuáles no tienen
ninguna tarjeta Supramax/Petroil Pay asignada todavía, cuáles son ambiguas
(more de una tarjeta candidata) y cuáles trae el portal pero no se reconocen
en nuestra base. Homologar `tarjetas.nu_foliotarjeta` es una escritura sobre
datos de producción compartidos con Ticket Car y el resto del sistema -- se
corre primero en modo lectura, se revisa el reporte a ojo, y solo se aplica
con la bandera explícita.

Reusa el scraping de `supramax_saldos_rpa.py` (`_procesar_cuenta`, el mismo
pool de `shared/pool_navegadores.py`) -- no hay una segunda implementación
del login ni del parseo de la tabla, solo un cruce distinto sobre las mismas
filas.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from dotenv import load_dotenv

from scrapers.supramax_saldos_rpa import (
    TIPOS_TARJETA_SUPRAMAX,
    _asegurar_tarjeta_prestacion,
    _es_prestacion,
    _procesar_cuenta,
    _resolver_id_empresa_cuenta,
)
from shared.db import conectar_postgres
from shared.pool_navegadores import ejecutar_en_paralelo

load_dotenv()


def _mapa_unidades_por_economico(cursor) -> dict[str, int]:
    cursor.execute("SELECT id_unidad, nu_economico FROM unidades")
    mapa: dict[str, int] = {}
    for id_unidad, economico in cursor.fetchall():
        clave = (economico or "").strip().upper()
        if clave:
            mapa[clave] = id_unidad
    return mapa


def _mapa_tarjetas_por_unidad(cursor) -> dict[int, list[tuple[int, int, str]]]:
    cursor.execute(
        """
        SELECT ut.id_unidad, t.id_empresa, t.id_tarjeta, t.nu_foliotarjeta
          FROM unidades_tarjetas ut
          JOIN tarjetas t ON t.id_empresa = ut.id_empresa AND t.id_tarjeta = ut.id_tarjeta
         WHERE t.id_tipotarjeta = ANY(%s)
        """,
        (list(TIPOS_TARJETA_SUPRAMAX),),
    )
    mapa: dict[int, list[tuple[int, int, str]]] = {}
    for id_unidad, id_empresa, id_tarjeta, folio in cursor.fetchall():
        mapa.setdefault(id_unidad, []).append((id_empresa, id_tarjeta, (folio or "").strip()))
    return mapa


def _scrapear_todas_las_cuentas() -> list[tuple[str, dict]]:
    """Mismo montaje de `ejecutar_actualizacion_saldos()`, pero regresa las
    filas crudas por cuenta en vez de escribirlas -- este script decide el
    cruce, no escribe `supramax_saldos`."""
    credenciales_str = os.getenv("SUPRAMAX_CREDENTIALS")
    if not credenciales_str:
        raise RuntimeError("falta SUPRAMAX_CREDENTIALS en el entorno.")

    cuentas = json.loads(credenciales_str)
    if not cuentas:
        return []

    max_workers = int(os.getenv("SUPRAMAX_MAX_WORKERS", "3"))
    headless = os.getenv("SUPRAMAX_HEADLESS", "true").strip().lower() != "false"
    base_url = os.getenv("SUPRAMAX_URL", "https://es09326.migasolinera.net/emaxclie/")
    if not base_url.endswith("/"):
        base_url += "/"

    descargas_dir = os.path.join(os.getcwd(), "descargas_temporales")
    os.makedirs(descargas_dir, exist_ok=True)

    # Perfil propio, distinto al de saldos/dispersión/histórico -- este
    # script puede correr al mismo tiempo que cualquiera de los otros sin
    # pisarles la sesión.
    perfil_base = os.path.join(os.getcwd(), "chrome_profile", "supramax", "reconocimiento")

    print(f"Reconociendo tags de Supramax: {len(cuentas)} cuenta(s), hasta {max_workers} en paralelo.")

    resultados = ejecutar_en_paralelo(
        cuentas=cuentas,
        max_workers=max_workers,
        funcion_por_cuenta=lambda driver, cuenta: _procesar_cuenta(driver, cuenta, base_url, descargas_dir),
        perfil_base=perfil_base,
        headless=headless,
        descargas_dir=descargas_dir,
    )

    salida = []
    for resultado in resultados:
        empresa_nombre = resultado.cuenta.get("Empresa", "?")
        if not resultado.ok:
            print(f"❌ Cuenta {empresa_nombre} falló al procesarse en el pool: {resultado.error}")
            continue
        datos = resultado.resultado or {}
        if not datos.get("login_ok"):
            print(f"⚠️ Login fallido para {empresa_nombre}: se omite del reconocimiento.")
            continue
        salida.append((empresa_nombre, datos))

    return salida


def reconocer(aplicar: bool) -> None:
    por_cuenta = _scrapear_todas_las_cuentas()

    conexion = conectar_postgres()
    try:
        with conexion.cursor() as cursor:
            unidades_por_economico = _mapa_unidades_por_economico(cursor)
            tarjetas_por_unidad = _mapa_tarjetas_por_unidad(cursor)

        coinciden: list[tuple[str, str]] = []
        para_actualizar: list[tuple[int, int, str, str, str, str]] = []
        sin_tarjeta: list[tuple[str, str, str]] = []
        ambiguas: list[tuple[str, str, list]] = []
        no_reconocidas: list[tuple[str, str]] = []
        # (nombre de cuenta, tag) -- se resuelve el id_empresa y se cataloga
        # solo con --aplicar, ver más abajo.
        prestaciones_detectadas: list[tuple[str, str]] = []

        for empresa_nombre, datos in por_cuenta:
            for fila in datos.get("filas", []):
                economico = fila["placas"].strip().upper()
                tag_portal = fila["tag"].strip().upper()

                id_unidad = unidades_por_economico.get(economico)
                if id_unidad is None:
                    # "PRE-1234": prestación de un colaborador con auto
                    # propio, no un económico ajeno de verdad -- se separa
                    # del balde de "no reconocidas" porque SÍ se entiende
                    # qué es, ver `PrestacionesService` en el backend.
                    if _es_prestacion(economico):
                        prestaciones_detectadas.append((empresa_nombre, tag_portal))
                    else:
                        no_reconocidas.append((empresa_nombre, economico))
                    continue

                candidatas = tarjetas_por_unidad.get(id_unidad, [])

                if not candidatas:
                    sin_tarjeta.append((empresa_nombre, economico, fila["idv"]))
                elif len(candidatas) > 1:
                    ambiguas.append((empresa_nombre, economico, candidatas))
                else:
                    id_empresa, id_tarjeta, folio_actual = candidatas[0]
                    if folio_actual.upper() == tag_portal:
                        coinciden.append((empresa_nombre, economico))
                    else:
                        para_actualizar.append(
                            (id_empresa, id_tarjeta, economico, folio_actual, tag_portal, fila["idv"])
                        )

        print("\n=== RECONOCIMIENTO DE TAGS DE SUPRAMAX ===\n")
        print(f"Ya coinciden (sin cambio):        {len(coinciden)}")
        print(f"Para homologar (tag distinto):    {len(para_actualizar)}")
        print(f"Sin tarjeta Supramax asignada:     {len(sin_tarjeta)}")
        print(f"Ambiguas (más de una tarjeta):     {len(ambiguas)}")
        print(f"Prestaciones detectadas (PRE-):    {len(prestaciones_detectadas)}")
        print(f"No reconocidas (económico ajeno):  {len(no_reconocidas)}")

        if para_actualizar:
            print("\n--- Para homologar ---")
            print(f"{'Económico':<12} {'Tag en BD':<20} {'Tag en portal':<20} idv")
            for _, _, economico, folio_actual, tag_portal, idv in para_actualizar:
                print(f"{economico:<12} {folio_actual or '(vacío)':<20} {tag_portal:<20} {idv}")

        if sin_tarjeta:
            print("\n--- Sin tarjeta Supramax/Petroil Pay asignada ---")
            for empresa_nombre, economico, idv in sin_tarjeta[:30]:
                print(f"{economico:<12} (idv {idv}, cuenta {empresa_nombre})")
            if len(sin_tarjeta) > 30:
                print(f"... y {len(sin_tarjeta) - 30} más.")

        if ambiguas:
            print("\n--- Ambiguas: requieren revisión manual ---")
            for empresa_nombre, economico, candidatas in ambiguas:
                print(f"{economico:<12} candidatas: {candidatas}")

        if prestaciones_detectadas:
            print("\n--- Prestaciones detectadas (se catalogan con --aplicar) ---")
            for empresa_nombre, tag_portal in prestaciones_detectadas[:30]:
                print(f"tag {tag_portal:<20} (cuenta {empresa_nombre})")
            if len(prestaciones_detectadas) > 30:
                print(f"... y {len(prestaciones_detectadas) - 30} más.")

        if not aplicar:
            print(
                "\nModo reconocimiento (sin --aplicar): no se escribió nada. "
                "Vuelve a correr con --aplicar para homologar los tags y catalogar las prestaciones listadas arriba."
            )
            return

        if para_actualizar:
            with conexion.cursor() as cursor:
                for id_empresa, id_tarjeta, _, _, tag_portal, _ in para_actualizar:
                    cursor.execute(
                        "UPDATE tarjetas SET nu_foliotarjeta = %s WHERE id_empresa = %s AND id_tarjeta = %s",
                        (tag_portal, id_empresa, id_tarjeta),
                    )
                conexion.commit()

            print(f"\n✅ {len(para_actualizar)} tarjeta(s) homologada(s) con el tag real de Supramax.")
        else:
            print("\nNada que homologar.")

        if prestaciones_detectadas:
            with conexion.cursor() as cursor:
                total_catalogadas = 0
                for empresa_nombre, tag_portal in prestaciones_detectadas:
                    id_empresa = _resolver_id_empresa_cuenta(cursor, empresa_nombre)
                    if id_empresa is None:
                        print(f"⚠️ No se pudo resolver el id_empresa de la cuenta «{empresa_nombre}» -- se omite tag {tag_portal} (revisar a mano).")
                        continue
                    _asegurar_tarjeta_prestacion(cursor, id_empresa, tag_portal)
                    total_catalogadas += 1
                conexion.commit()

            print(f"✅ {total_catalogadas} tarjeta(s) «Prestación» catalogada(s) (sin colaborador asignado todavía).")
    finally:
        conexion.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="Escribe los tags homologados en tarjetas.nu_foliotarjeta. Sin esta bandera, solo reporta.",
    )
    args = parser.parse_args()

    try:
        reconocer(aplicar=args.aplicar)
    except RuntimeError as error:
        print(f"ERROR: {error}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
