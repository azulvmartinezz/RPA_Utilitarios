"""Las credenciales de los portales, leídas de la base y descifradas.

POR QUÉ DEJARON DE VIVIR EN `.env`

El 2026-09-25 Supramax rotó las contraseñas de las 19 cuentas (se fue un
colaborador que las tenía). Como vivían en `RPA_Utilitarios/.env`, en una sola
Mac, actualizarlas era entrarle a ese archivo -- y mientras tanto 14 de 19
cuentas quedaron rechazadas SIN QUE NADIE SE ENTERARA durante cuatro días:
tres dispersiones atoradas y este RPA reintentando cada 90 segundos con la
contraseña muerta.

Ahora las captura un administrador desde Configuraciones > Credenciales, con
un botón que prueba el login de verdad. Este módulo es el otro extremo de ese
cambio: si el RPA siguiera leyendo el `.env`, la pantalla sería un adorno y
las dispersiones seguirían fallando con la contraseña vieja.

EL FORMATO LO DICTA EL BACKEND

`de_secreto` es AES-256-GCM en `v1:<iv>:<tag>:<texto>`, base64 cada parte --
el mismo que escribe `CifradoService` en
`control-vehicular/src/modules/credenciales/services/cifrado.service.ts`. Si
uno de los dos cambia, el otro tiene que cambiar igual: son una sola cosa
partida en dos lenguajes.

La llave sale de `CREDENCIALES_LLAVE`, del mismo secreto de Secret Manager
que ya da las credenciales de la base. No hay valor por defecto a propósito:
sin llave esto truena en vez de seguir con contraseñas que no puede leer.
"""

import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from shared.db import cargar_secretos, conectar_postgres

VERSION = "v1"

BYTES_LLAVE = 32


def _llave(cfg: dict) -> bytes:
    crudo = cfg.get("CREDENCIALES_LLAVE") or os.getenv("CREDENCIALES_LLAVE")

    if not crudo:
        raise RuntimeError(
            "Falta CREDENCIALES_LLAVE (ni en el secreto ni en el entorno): sin "
            "ella no se pueden leer las contraseñas de proveedores."
        )

    llave = base64.b64decode(crudo)

    if len(llave) != BYTES_LLAVE:
        raise RuntimeError(
            f"CREDENCIALES_LLAVE debe ser de {BYTES_LLAVE} bytes en base64 "
            f"(llegó de {len(llave)})."
        )

    return llave


def descifrar(guardado: str, llave: bytes) -> str:
    """El inverso exacto de `CifradoService.cifrar()` del backend.

    GCM verifica el mensaje además de descifrarlo: una fila alterada en la
    base lanza `InvalidTag` en vez de devolver basura que mandaríamos al
    portal creyéndola una contraseña.
    """
    partes = guardado.split(":")

    if len(partes) != 4 or partes[0] != VERSION:
        raise ValueError(
            "El secreto guardado no tiene el formato esperado. No se descifra "
            "a medias: una contraseña mal leída se mandaría al portal igual."
        )

    _, iv, tag, texto = partes

    # El backend guarda el tag aparte; `AESGCM` lo espera pegado al final.
    cifrado = base64.b64decode(texto) + base64.b64decode(tag)

    return AESGCM(llave).decrypt(base64.b64decode(iv), cifrado, None).decode()


def cuentas_de(proveedor: str, cfg: dict | None = None) -> list[dict]:
    """Las cuentas VIVAS de un proveedor, con la contraseña ya en claro.

    Devuelve la misma forma que traía `SUPRAMAX_CREDENTIALS` en el `.env`
    (`Empresa`, `Usuario`, `Contraseña`) para que los scrapers no tengan que
    cambiar cómo la consumen: lo único que cambia es de dónde sale.

    Una cuenta que no se pueda descifrar se OMITE y se avisa, en vez de
    tumbar la corrida entera: con 19 cuentas, que una traiga basura no debe
    dejar sin dispersar a las otras 18.
    """
    cfg = cfg or cargar_secretos()
    llave = _llave(cfg)

    conexion = conectar_postgres(cfg)

    try:
        with conexion.cursor() as cursor:
            cursor.execute(
                """
                SELECT nb_cuenta, nb_usuario, de_secreto
                  FROM proveedores_credenciales
                 WHERE cl_proveedor = %s AND sn_activo
                 ORDER BY nb_cuenta
                """,
                (proveedor,),
            )
            filas = cursor.fetchall()
    finally:
        conexion.close()

    cuentas = []

    for nb_cuenta, nb_usuario, de_secreto in filas:
        try:
            contrasena = descifrar(de_secreto, llave)
        except Exception as error:  # noqa: BLE001
            print(
                f"  ⚠️ No se pudo descifrar la credencial de {nb_cuenta} "
                f"({nb_usuario}): {error}. Se omite esa cuenta."
            )
            continue

        cuentas.append(
            {"Empresa": nb_cuenta, "Usuario": nb_usuario, "Contraseña": contrasena}
        )

    return cuentas


def resolver_cuentas(proveedor: str, variable_env: str) -> list[dict]:
    """Las cuentas del proveedor: primero la base, y si no hay, el `.env`.

    LA CAÍDA AL `.env` ES UN PUENTE, NO UN PLAN

    Existe para que capturar las 19 cuentas en la pantalla nueva no tenga que
    pasar en el mismo minuto en que esto se despliega: mientras la tabla esté
    vacía, el RPA sigue corriendo como siempre. En cuanto haya una sola
    credencial capturada, la base MANDA y el `.env` deja de mirarse.

    Ese orden importa: al revés, capturar una contraseña nueva en la pantalla
    no cambiaría nada mientras el `.env` tuviera la vieja -- que es
    exactamente el problema que vinimos a resolver.
    """
    try:
        cuentas = cuentas_de(proveedor)
    except Exception as error:  # noqa: BLE001
        print(
            f"  ⚠️ No se pudieron leer las credenciales de {proveedor} de la "
            f"base ({error}). Se intenta con {variable_env}."
        )
        cuentas = []

    if cuentas:
        print(f"{len(cuentas)} cuenta(s) de {proveedor} leídas de la base.")
        return cuentas

    crudo = os.getenv(variable_env)

    if not crudo:
        raise RuntimeError(
            f"No hay credenciales de {proveedor}: la tabla "
            f"proveedores_credenciales no tiene ninguna activa y tampoco está "
            f"{variable_env} en el entorno. Captúralas en Configuraciones > "
            f"Credenciales."
        )

    print(
        f"  ⚠️ Sin credenciales de {proveedor} en la base: se usan las de "
        f"{variable_env}. Captúralas en Configuraciones > Credenciales para "
        f"poder rotarlas sin tocar este archivo."
    )

    return json.loads(crudo)
