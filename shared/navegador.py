"""Localiza el navegador que usan los scrapers.

Existe porque `undetected_chromedriver` —el que usa Pase para pasar el WAF de
Radware— no trae el descubrimiento automático de Selenium Manager: si no hay un
Chrome en la ruta estándar de macOS, falla con «Binary Location Must be a
String» sin decir qué le falta.

Selenium sí descarga un «Chrome for Testing» por su cuenta y lo deja en su
caché, así que se busca también ahí. Con eso los scrapers corren en máquinas
donde no hay Chrome instalado.
"""

import os
import re
import subprocess

CACHE_SELENIUM = os.path.expanduser("~/.cache/selenium/chrome")

RUTAS_CONOCIDAS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]


def _desde_cache_selenium():
    """El binario más reciente que haya bajado Selenium Manager."""
    if not os.path.isdir(CACHE_SELENIUM):
        return None

    encontrados = []

    for plataforma in os.listdir(CACHE_SELENIUM):
        raiz = os.path.join(CACHE_SELENIUM, plataforma)

        if not os.path.isdir(raiz):
            continue

        for version in os.listdir(raiz):
            binario = os.path.join(
                raiz,
                version,
                "Google Chrome for Testing.app",
                "Contents",
                "MacOS",
                "Google Chrome for Testing",
            )

            if os.path.exists(binario):
                encontrados.append((version, binario))

    if not encontrados:
        return None

    # Se ordena por versión numérica para no quedarse con una vieja.
    encontrados.sort(key=lambda par: [int(n) for n in par[0].split(".") if n.isdigit()])

    return encontrados[-1][1]


def resolver_binario():
    """Ruta del navegador, o None si no hay ninguno."""
    for candidato in [os.getenv("CHROME_BINARY"), *RUTAS_CONOCIDAS]:
        if candidato and os.path.exists(candidato):
            return candidato

    return _desde_cache_selenium()


def version_mayor(binario=None):
    """Versión mayor del navegador, que es lo que pide `undetected_chromedriver`.

    Se consulta en vez de fijarla en el código: estaba clavada en 149 y cada
    actualización de Chrome habría roto el scraper.
    """
    binario = binario or resolver_binario()

    if not binario:
        return None

    try:
        salida = subprocess.run(
            [binario, "--version"], capture_output=True, text=True, timeout=30
        ).stdout
        encontrado = re.search(r"(\d+)\.", salida)

        return int(encontrado.group(1)) if encontrado else None
    except Exception:  # noqa: BLE001
        return None
