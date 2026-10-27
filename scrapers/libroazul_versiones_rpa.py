"""Busca (y cachea) las versiones reales que existen en libroazul.com para
una marca/modelo/año -- alimenta el autocomplete del campo "Versión" del
modal "Consultar Libro Azul".

NO COTIZA NADA

A diferencia de `libroazul_precio_rpa.py`, esto no elige condición ni lee
precio: solo navega `inicio` -> `resultados_busqueda` y lista las tarjetas.
Es deliberadamente más barato que una cotización completa porque se dispara
mucho más seguido (cada marca/modelo/año nueva que alguien escribe en el
modal, antes de que exista caché para ella) -- ver
`libroazul_versiones_actualizar_bajo_demanda.py`, que drena esta cola cada
pocos segundos en vez de cada minuto como `libroazul_actualizar_bajo_demanda.py`.

Reusa `_buscar_versiones`/`listar_versiones_disponibles`/
`guardar_cache_versiones` de `libroazul_precio_rpa.py` -- no hay una segunda
implementación del mismo scraping de resultados_busqueda.html.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from dotenv import load_dotenv

RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ_PROYECTO not in sys.path:
    sys.path.insert(0, RAIZ_PROYECTO)

from scrapers.libroazul_precio_rpa import (  # noqa: E402
    _buscar_versiones,
    abrir_driver_desechable,
    guardar_cache_versiones,
    listar_versiones_disponibles,
)
from shared.db import conectar_postgres  # noqa: E402

load_dotenv()


@dataclass
class PedidoBusquedaVersiones:
    """Lo mínimo para listar versiones -- a diferencia de `PedidoCotizacion`
    no lleva `nb_version` ni `cl_condicion`: eso es justo lo que este script
    todavía no sabe (es lo que la persona está a punto de escribir)."""

    nb_marca: str
    nb_modelo: str
    nu_claveanio: str


def buscar_versiones(pedido: PedidoBusquedaVersiones) -> list[str]:
    """Abre Chrome, navega hasta `resultados_busqueda.html` para esa
    marca/modelo/año y deja la lista en `libroazul_versiones_cache` (además
    de devolverla, por si quien llama la necesita de una vez)."""
    conexion = conectar_postgres()
    try:
        with abrir_driver_desechable() as driver:
            _buscar_versiones(driver, pedido.nb_marca, pedido.nb_modelo, pedido.nu_claveanio)
            versiones = [texto for texto, _ in listar_versiones_disponibles(driver)]

        guardar_cache_versiones(conexion, pedido.nb_marca, pedido.nb_modelo, pedido.nu_claveanio, versiones)
        return versiones
    finally:
        conexion.close()


def main() -> int:
    """Prueba manual: `python -m scrapers.libroazul_versiones_rpa <marca> <modelo> <anio>`."""
    if len(sys.argv) < 4:
        print("Uso: python -m scrapers.libroazul_versiones_rpa <marca> <modelo> <anio>")
        return 1

    pedido = PedidoBusquedaVersiones(nb_marca=sys.argv[1], nb_modelo=sys.argv[2], nu_claveanio=sys.argv[3])
    versiones = buscar_versiones(pedido)
    print(f"✅ {len(versiones)} versión(es) encontrada(s): {', '.join(versiones)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
