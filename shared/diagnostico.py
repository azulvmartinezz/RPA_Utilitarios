"""Captura de screenshot + HTML cuando un RPA se atora en una etapa.

Factorizado de `scrapers/supramax_rpa.py` (donde vivía como
`_guardar_diagnostico`, sin depender de estado global del módulo -- solo del
`driver` y strings que recibe como parámetro) para que los scripts nuevos de
saldos y dispersión de Supramax (`scrapers/supramax_saldos_rpa.py`,
`scrapers/supramax_dispersion_rpa.py`) no reimplementen la misma captura a
mano. `supramax_rpa.py` importa esta misma función en vez de duplicarla.
"""

import datetime
import os


def guardar_diagnostico(driver, descargas_dir, identificador, etapa):
    """Guarda `<etapa>_<identificador>_<timestamp>.png` y `.html` en
    `descargas_dir`. Nunca lanza: si Chrome ya perdió la sesión (por eso se
    está diagnosticando), el propio intento de captura puede fallar, y eso no
    debe tumbar al llamador."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    seguro = "".join(
        c for c in str(identificador) if c.isalnum() or c in ("-", "_")
    )[:30] or "cuenta"
    base = os.path.join(descargas_dir, f"supramax_{etapa}_{seguro}_{timestamp}")

    try:
        os.makedirs(descargas_dir, exist_ok=True)
        driver.save_screenshot(f"{base}.png")

        with open(f"{base}.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)

        print(f"📸 Diagnóstico guardado: {os.path.basename(base)}")
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ No se pudo guardar diagnóstico ({e})")
