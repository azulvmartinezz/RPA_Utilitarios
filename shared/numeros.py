"""Conversión de los textos con formato de moneda del portal de Supramax
(`$ 1,798.50`, `$ 0.00`) a `Decimal`.

Se saca a un módulo propio porque tanto `scrapers/supramax_saldos_rpa.py`
(para el crédito del listado) como `scrapers/supramax_dispersion_rpa.py`
(para `#vehsaldo` antes/después de dispersar) necesitan la misma conversión,
y comparar saldos con `float` arrastraría error de redondeo justo donde la
verificación de éxito de la dispersión necesita precisión de centavos.
"""

from decimal import Decimal, InvalidOperation


def a_decimal(texto) -> Decimal | None:
    """`None` si no se puede parsear (celda vacía, guión, etc.) en vez de
    lanzar: una fila con un crédito no parseable no debe tronar el scraping
    de las demás filas de la cuenta."""
    limpio = str(texto or "").replace("$", "").replace(",", "").strip()

    if not limpio:
        return None

    try:
        return Decimal(limpio)
    except InvalidOperation:
        return None
