"""Login del portal de Supramax, reutilizado por los tres scripts que le
hablan: `scrapers/supramax_rpa.py` (reporte mensual de consumos),
`scrapers/supramax_saldos_rpa.py` (caché de saldos) y
`scrapers/supramax_dispersion_rpa.py` (dispersión de crédito).

Se saca aquí en vez de dejarlo repetido en cada script nuevo: la secuencia de
llenar usuario/contraseña, someter el formulario con los reintentos de
`_intentar_submit_login` (click normal, Enter, submit por JS -- ya probados
contra los distintos comportamientos del portal) y esperar el menú
"Reportes" es exactamente la misma para las tres necesidades. Lo único que
cambia entre ellas es qué se hace DESPUÉS de loguear, que se queda en cada
script.
"""

import time

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from scrapers.supramax_rpa import (
    _esta_en_login,
    _intentar_submit_login,
    _stop_loading,
)
from shared.diagnostico import guardar_diagnostico

REPORTES_XPATH = (
    "//a[@title='Reportes'] | //a[normalize-space(text())='Reportes'] "
    "| //input[@value='Reportes']"
)


def iniciar_sesion(driver, cuenta: dict, base_url: str, descargas_dir: str, etapa: str) -> bool:
    """`True` si el login quedó dentro del portal (menú "Reportes" visible).

    Nunca lanza: si algo falla a media autenticación, se atrapa aquí mismo,
    se deja diagnóstico (`etapa` identifica qué script llamó, para poder
    distinguir capturas de saldos vs. dispersión) y se regresa `False` -- así
    el llamador decide qué hacer con la cuenta caída sin tener que envolver
    esta función en su propio try/except.
    """
    username = cuenta.get("Usuario")
    password = cuenta.get("Contraseña")
    wait = WebDriverWait(driver, 15)

    try:
        try:
            driver.get(base_url)
        except TimeoutException:
            _stop_loading(
                driver,
                "  ⚠️ La carga inicial de Supramax tardó demasiado. Forzando window.stop()...",
            )

        user_input = wait.until(EC.element_to_be_clickable((By.ID, "username")))
        user_input.click()
        user_input.clear()
        time.sleep(0.3)
        user_input.send_keys(username)

        pwd_input = wait.until(EC.element_to_be_clickable((By.ID, "password")))
        pwd_input.click()
        pwd_input.clear()
        time.sleep(0.3)
        pwd_input.send_keys(password)
        time.sleep(0.3)

        login_exitoso = _intentar_submit_login(driver, wait, pwd_input)

        try:
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.XPATH, REPORTES_XPATH))
            )
        except TimeoutException:
            login_exitoso = False
    except Exception:  # noqa: BLE001
        login_exitoso = False

    if not login_exitoso or _esta_en_login(driver):
        guardar_diagnostico(driver, descargas_dir, username, etapa)
        return False

    return True
