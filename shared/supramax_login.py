"""Login del portal de Supramax, reutilizado por los tres scripts que le
hablan: `jobs_programados/supramax_saldos_rpa.py` (caché de saldos) y
`jobs_por_evento/supramax_dispersion_rpa.py` (dispersión de crédito).

Se saca aquí en vez de dejarlo repetido en cada script nuevo: la secuencia de
llenar usuario/contraseña, someter el formulario con los reintentos de
`_intentar_submit_login` (click normal, Enter, submit por JS -- ya probados
contra los distintos comportamientos del portal) y esperar el menú
"Reportes" es exactamente la misma para las tres necesidades. Lo único que
cambia entre ellas es qué se hace DESPUÉS de loguear, que se queda en cada
script.
"""

import time

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from shared.diagnostico import guardar_diagnostico


# Las tres funciones de abajo vivían en `scrapers/supramax_rpa.py` (el reporte
# mensual con Chrome, retirado el 2026-11-05) y se importaban de ahí. Se
# trajeron tal cual para poder borrar ese script.


def _renderer_timeout(err):
    return "timed out receiving message from renderer" in str(err).lower()


def _stop_loading(driver, mensaje):
    print(mensaje)
    try:
        driver.execute_script("window.stop();")
        time.sleep(1)
    except Exception as stop_err:
        print(f"  ⚠️ No se pudo detener la carga: {stop_err}")


def _esta_en_login(driver):
    try:
        return len(driver.find_elements(By.ID, "loginform")) > 0
    except Exception:
        return False


def _intentar_submit_login(driver, wait, pwd_input):
    intentos = [
        ("click normal", lambda: wait.until(EC.element_to_be_clickable((By.ID, "login"))).click()),
        ("enter en password", lambda: pwd_input.send_keys(Keys.RETURN)),
        ("submit del formulario", lambda: driver.execute_script("arguments[0].submit();", wait.until(EC.presence_of_element_located((By.ID, "loginform"))))),
    ]

    for descripcion, accion in intentos:
        try:
            print(f"  -> Intentando login por {descripcion}...")
            accion()
            time.sleep(2)

            if not _esta_en_login(driver):
                return True

            REPORTES_XPATH = "//a[@title='Reportes'] | //a[normalize-space(text())='Reportes'] | //input[@value='Reportes']"
            try:
                WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, REPORTES_XPATH)))
                return True
            except TimeoutException:
                pass
        except WebDriverException as e:
            if _renderer_timeout(e):
                _stop_loading(driver, f"  ⚠️ La navegación después del login por {descripcion} se tardó demasiado. Forzando window.stop()...")
            else:
                print(f"  ⚠️ Falló el intento de login por {descripcion}: {e}")
        except Exception as e:
            print(f"  ⚠️ Falló el intento de login por {descripcion}: {e}")

    return False

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
