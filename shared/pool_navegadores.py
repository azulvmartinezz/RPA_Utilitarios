"""Pool de sesiones Chrome para correr varias cuentas de Supramax en paralelo.

POR QUÉ EXISTE

`scrapers/supramax_rpa.py` procesa las cuentas de `SUPRAMAX_CREDENTIALS` una
por una, con un solo `webdriver` reutilizado entre ellas -- eso está bien
para el reporte mensual de consumos, que ya corre de madrugada sin prisa. Los
dos scripts nuevos (`scrapers/supramax_saldos_rpa.py`,
`scrapers/supramax_dispersion_rpa.py`) sí necesitan varias cuentas al mismo
tiempo: los saldos deben refrescarse cada 30 min sin que la cuenta 9 espere a
que terminen las 8 anteriores, y la dispersión debe drenar la cola rápido.
Selenium no permite compartir un `webdriver` entre threads, así que cada
worker abre el suyo.

PERFILES DE CHROME DEDICADOS

Cada worker recibe su propio directorio de perfil, derivado de
`perfil_base` (p. ej. `chrome_profile/supramax/saldos`) más un identificador
de cuenta (normalmente `id_empresa`). Así, ingesta de saldos, dispersión y el
scraper histórico (`chrome_profile/supramax` a secas) nunca comparten sesión
entre sí ni entre cuentas concurrentes -- si compartieran perfil, dos Chrome
abriendo el mismo `user-data-dir` a la vez se pisan la sesión o Chrome
rechaza abrir el segundo.

UNA CUENTA CAÍDA NO TUMBA A LAS DEMÁS

Mismo principio que `TicketCarService.saldos()` del lado NestJS ("una cuenta
caída no tumba la pantalla"): cada cuenta corre en su propio try/except: un
login que falla, un timeout de Supramax o una excepción de Selenium se
capturan por cuenta y se reportan en el resultado, sin cancelar los futures
de las demás cuentas que sí van bien.

PENSADO PARA CLOUD RUN

`max_workers` sale de quien llama (que a su vez lo lee de una variable de
entorno, ej. `SUPRAMAX_MAX_WORKERS`) -- este módulo no fija un default de
concurrencia por su cuenta. El mismo código sirve para 3-4 workers en la Mac
de Anibal hoy y para más paralelismo en un Cloud Run Job después, sin tocar
esta lógica.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import glob
import os
import traceback
from typing import Any, Callable

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

from shared import navegador


@dataclasses.dataclass
class ResultadoCuenta:
    """Lo que produjo (o no) una cuenta al correr en el pool."""

    cuenta: dict
    resultado: Any = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _id_carpeta(valor: Any) -> str:
    """Nombre de carpeta seguro para un identificador de cuenta/empresa."""
    texto = str(valor)
    limpio = "".join(c for c in texto if c.isalnum() or c in ("-", "_"))
    return limpio or "cuenta"


def _identificador_cuenta(cuenta: dict) -> Any:
    """De qué carpeta usa cada worker.

    Se prefiere `id_empresa` (el identificador estable de nuestro catálogo)
    sobre `Empresa`/`Usuario` porque es el que no cambia si renombran la
    cuenta en Supramax; se cae a esos otros campos cuando `id_empresa` no
    viene resuelto todavía (por ejemplo, en `supramax_saldos_rpa.py`, que
    solo conoce las cuentas de `SUPRAMAX_CREDENTIALS` antes de cruzarlas
    contra Postgres).
    """
    return (
        cuenta.get("id_empresa")
        or cuenta.get("Empresa")
        or cuenta.get("Usuario")
        or "cuenta"
    )


def _find_cached_chromedriver() -> str | None:
    """Copiado de `scrapers/supramax_rpa.py::_find_cached_chromedriver`.

    No se importa de ahí porque ese módulo trae efectos de import (carga
    `.env`, importa `gcs_uploader`) que no hacen falta aquí y que atarían el
    pool de navegadores al reporte mensual de consumos.
    """
    patron = os.path.expanduser("~/.wdm/drivers/chromedriver/**/chromedriver")
    candidatos = [p for p in glob.glob(patron, recursive=True) if os.path.isfile(p)]
    if not candidatos:
        return None
    return max(candidatos, key=os.path.getmtime)


def _build_chrome_options(perfil_dir: str, headless: bool, descargas_dir: str | None) -> Options:
    opciones = Options()
    opciones.page_load_strategy = "eager"

    if headless:
        opciones.add_argument("--headless=new")

    opciones.add_argument("--window-size=1920,1080")
    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")
    opciones.add_argument("--disable-gpu")
    opciones.add_argument("--disable-software-rasterizer")
    # Puerto 0 = que el propio Chrome elija uno libre: con varios workers
    # abriendo Chrome al mismo tiempo, un puerto fijo chocaría entre ellos.
    opciones.add_argument("--remote-debugging-port=0")

    # Que Chrome no baje nada que el RPA no pidió. Sin esto, cada perfil
    # —uno por empresa— descargaba por su cuenta componentes y modelos de IA
    # local de ~200 MB: 1.9 GB repetidos en `component_crx_cache` y otros
    # 580 MB en `optimization_guide_model_store`, y todo por el mismo enlace
    # de internet de la oficina que usa el túnel de la app. Son las banderas
    # que Puppeteer y Playwright ponen por omisión; no tocan la navegación.
    opciones.add_argument("--disable-component-update")
    opciones.add_argument("--disable-background-networking")
    opciones.add_argument(
        "--disable-features=OptimizationGuideModelDownloading,"
        "OptimizationHintsFetching,OptimizationTargetPrediction,OptimizationHints"
    )

    os.makedirs(perfil_dir, exist_ok=True)
    opciones.add_argument(f"--user-data-dir={perfil_dir}")

    binario = navegador.resolver_binario()
    if binario:
        opciones.binary_location = binario

    if descargas_dir:
        os.makedirs(descargas_dir, exist_ok=True)
        opciones.add_experimental_option(
            "prefs",
            {
                "download.default_directory": descargas_dir,
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "safebrowsing.enabled": True,
            },
        )

    return opciones


def crear_driver(perfil_dir: str, headless: bool = True, descargas_dir: str | None = None):
    """Arranca un Chrome con perfil dedicado, con la misma resiliencia de
    arranque que `supramax_rpa.py::_create_driver` (Selenium Manager primero,
    luego el chromedriver ya cacheado, luego `ChromeDriverManager`) -- se
    repite aquí en vez de importarla porque esa función no toma el directorio
    de perfil como parámetro y está atada al perfil único
    `chrome_profile/supramax`."""
    opciones = _build_chrome_options(perfil_dir, headless, descargas_dir)
    errores = []

    try:
        return webdriver.Chrome(options=opciones)
    except Exception as exc:  # noqa: BLE001
        errores.append(f"Selenium Manager: {exc}")

    driver_path = _find_cached_chromedriver()
    if driver_path:
        try:
            os.chmod(driver_path, os.stat(driver_path).st_mode | 0o111)
        except OSError:
            pass
        try:
            return webdriver.Chrome(service=Service(driver_path), options=opciones)
        except Exception as exc:  # noqa: BLE001
            errores.append(f"ChromeDriver cacheado ({driver_path}): {exc}")

    try:
        driver_path = ChromeDriverManager().install()
        try:
            os.chmod(driver_path, os.stat(driver_path).st_mode | 0o111)
        except OSError:
            pass
        return webdriver.Chrome(service=Service(driver_path), options=opciones)
    except Exception as exc:  # noqa: BLE001
        errores.append(f"ChromeDriverManager: {exc}")

    raise RuntimeError(
        "No se pudo iniciar Chrome para el pool de Supramax.\n"
        + "\n".join(f"  - {item}" for item in errores)
    )


def _correr_cuenta(
    cuenta: dict,
    perfil_base: str,
    headless: bool,
    descargas_dir: str | None,
    funcion_por_cuenta: Callable[[Any, dict], Any],
) -> ResultadoCuenta:
    perfil_dir = os.path.join(perfil_base, _id_carpeta(_identificador_cuenta(cuenta)))
    driver = None

    try:
        driver = crear_driver(perfil_dir, headless=headless, descargas_dir=descargas_dir)
        resultado = funcion_por_cuenta(driver, cuenta)
        return ResultadoCuenta(cuenta=cuenta, resultado=resultado)
    except Exception as error:  # noqa: BLE001
        # Una cuenta caída no tumba a las demás: se captura aquí, no se deja
        # escapar hacia el ThreadPoolExecutor.
        return ResultadoCuenta(
            cuenta=cuenta,
            error=f"{error}\n{traceback.format_exc(limit=5)}",
        )
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:  # noqa: BLE001
                pass


def ejecutar_en_paralelo(
    cuentas: list[dict],
    max_workers: int,
    funcion_por_cuenta: Callable[[Any, dict], Any],
    perfil_base: str,
    headless: bool = True,
    descargas_dir: str | None = None,
) -> list[ResultadoCuenta]:
    """Corre `funcion_por_cuenta(driver, cuenta)` para cada cuenta, hasta
    `max_workers` a la vez, cada una con su propio `webdriver` y su propio
    perfil de Chrome bajo `perfil_base/<id_cuenta>`.

    Devuelve un `ResultadoCuenta` por cuenta, en el orden en que van
    terminando (no en el orden de `cuentas`) -- quien llama no debe asumir
    orden si le importa, y debe revisar `.ok` de cada uno antes de asumir que
    `.resultado` es válido.
    """
    if max_workers < 1:
        raise ValueError(f"max_workers debe ser >= 1 (llegó {max_workers})")

    if not cuentas:
        return []

    resultados: list[ResultadoCuenta] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futuros = [
            executor.submit(
                _correr_cuenta, cuenta, perfil_base, headless, descargas_dir, funcion_por_cuenta
            )
            for cuenta in cuentas
        ]

        for futuro in concurrent.futures.as_completed(futuros):
            # `_correr_cuenta` ya atrapa cualquier excepción de la cuenta;
            # `futuro.result()` no debería lanzar, pero si algo interno del
            # propio executor fallara, tampoco queremos tumbar al resto.
            try:
                resultados.append(futuro.result())
            except Exception as error:  # noqa: BLE001
                resultados.append(ResultadoCuenta(cuenta={}, error=str(error)))

    return resultados
