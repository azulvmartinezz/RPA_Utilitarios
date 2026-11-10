"""Sesión HTTP con el portal de clientes de Supramax, sin navegador.

El login del portal es un formulario normal (POST), sin JavaScript, así que
para LEER no hace falta Chrome: lo usan `supramax_consumos_http.py` (detalle
de cargas) y `supramax_saldos_rpa.py` (créditos de cada vehículo).

POR QUÉ NO CHROME

Medido el 2026-11-09 con las mismas cuentas y el mismo parser: el robot de
saldos con 3 Chrome en paralelo llegó a 2,753 MB (32 procesos); por HTTP, el
proceso entero de Python, 155 MB. El pod de test tiene 1 GB y se reiniciaba
por memoria cada vez que alguien pedía «Actualizar Supramax».

La dispersión sí sigue en Chrome: su formulario genera por JavaScript un
token anti-bot (`submissionId`) y mueve dinero real.
"""

from __future__ import annotations

import os
import re

import requests


def url_base() -> str:
    """Se lee en cada llamada y no al importar: quien importa este módulo
    suele cargar su `.env` (`load_dotenv()`) después de los imports."""
    url = os.getenv("SUPRAMAX_URL", "https://es09326.migasolinera.net/emaxclie/")
    return url if url.endswith("/") else f"{url}/"


ESPERA_LOGIN = 25

# Lo que dice el portal cuando la contraseña no sirve, y el texto del
# formulario de ingreso cuando nos regresa a él sin explicar.
RECHAZO_LOGIN = "Cuenta de cliente no valida"
SIGUE_EN_LOGIN = "Nombre de cliente"


class LoginRechazado(RuntimeError):
    """Usuario o contraseña que el portal no acepta: no es una falla del robot
    ni de la red, y no se arregla reintentando."""


def nueva_sesion() -> requests.Session:
    sesion = requests.Session()
    sesion.headers["User-Agent"] = "Mozilla/5.0 (ControlVehicular RPA)"
    return sesion


def entrar(sesion: requests.Session, cuenta: dict) -> None:
    """Inicia sesión con la cuenta (`Usuario` / `Contraseña`). Lanza
    `LoginRechazado` si el portal rechaza las credenciales y `RuntimeError` si
    regresa al formulario sin decir por qué."""
    base = url_base()
    pagina = sesion.get(base, timeout=ESPERA_LOGIN)
    token = re.search(r'name="timer_loginform"[^>]*value="([^"]*)"', pagina.text)

    respuesta = sesion.post(
        base,
        data={
            "name_loginform": "loginform",
            "timer_loginform": token.group(1) if token else "",
            "response_loginform": "",
            "username": cuenta.get("Usuario", ""),
            "password": cuenta.get("Contraseña", ""),
            "login": " Entrar ",
        },
        timeout=ESPERA_LOGIN,
    )
    html = respuesta.content.decode("latin1")

    if RECHAZO_LOGIN in html:
        raise LoginRechazado("el portal rechazó usuario o contraseña")
    if SIGUE_EN_LOGIN in html or len(html.strip()) < 500:
        raise RuntimeError("el portal devolvió el formulario de ingreso sin explicar")


def salir(sesion: requests.Session) -> None:
    """Cortesía: si falla, no cambia el resultado."""
    try:
        sesion.get(f"{url_base()}index.php", params={"lg": 1}, timeout=ESPERA_LOGIN)
    except requests.RequestException:
        pass
