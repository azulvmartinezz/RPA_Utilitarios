"""Drena la cola `supramax_dispersion_jobs` que llena
`POST /solicitud/recargar-supramax` (backend NestJS) y aplica cada dispersión
real en el portal de Supramax con un navegador.

DISEÑADO PARA CORRER COMO PROCESO CORTO, DISPARADO POR POLLING

Igual que `scrapers/supramax_saldos_rpa.py`: este script hace UNA pasada
-- recupera huérfanos, toma un lote de jobs `PENDIENTE`, los dispersa, deja
constancia -- y termina. El intervalo corto (1-2 min) lo pone quien lo
invoca (`dispersion_supramax_polling.sh` hoy; un Cloud Run Job con Cloud
Scheduler después). No hay estado propio en disco entre corridas más que el
perfil de Chrome.

EL RPA NUNCA TOCA `solicitudes`

Solo se escribe en `supramax_dispersion_jobs`. Cerrar el ciclo de negocio
-- pasar la `solicitud` de Autorizada (402) a Completada (404), con el aviso
push que dispara `SolicitudService.depositar()` -- lo hace el backend
NestJS por su cuenta, leyendo esta misma tabla (`SupramaxService.
cerrarJobsResueltos()`). Duplicar esa lógica de negocio en Python sería
reinventar reglas que ya viven en un solo lugar.

CONCURRENCIA: UN WORKER POR EMPRESA, NO POR JOB

Varios jobs de la misma empresa comparten una sola sesión ya logueada -- no
tiene sentido loguear la misma cuenta N veces para N recargas. Empresas
distintas sí corren en paralelo, cada una en su propio thread/`webdriver`
(`shared/pool_navegadores.py`), con su propio perfil de Chrome dedicado
(`chrome_profile/supramax/dispersion/<id_empresa>`).

RECUPERACIÓN DE HUÉRFANOS

Si Chrome se cae a medio camino (o el proceso completo muere) con un job en
`EN_PROCESO`, esa fila se queda así -- a propósito, ver `_procesar_grupo` y
el comentario de "invalid session id" -- hasta que una corrida posterior,
antes de tomar jobs nuevos, la encuentra con `fh_tomado` viejo y la regresa a
`PENDIENTE` incrementando `nu_intentos`.

CON TOPE, Y DICIENDO POR QUÉ

Ese reciclado tiene límite (`SUPRAMAX_DISPERSION_INTENTOS_MAX`, 2 por
defecto): al agotarlo el job pasa a `FALLIDO` con el motivo en `de_error`.
Sin el tope --como estuvo hasta el 2026-09-29-- tres jobs se reintentaron
36, 147 y 157 veces durante cuatro días contra una contraseña rotada, con
`de_error` en nulo. Ninguna rama de fallo puede quedarse muda: un job que se
rinde y lo explica vale más que uno que insiste en silencio.

EN QUÉ PASO VA

Cada job publica su avance en `cl_paso`/`de_paso` conforme ocurre (ver la
clase `Progreso`), para que quien pidió la recarga vea el recorrido real en
la pantalla en vez de un «se aplicará en los próximos minutos» y nada más.
"""

from __future__ import annotations

import json
import os
import sys
import time
from decimal import Decimal

from dotenv import load_dotenv
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ_PROYECTO not in sys.path:
    sys.path.insert(0, RAIZ_PROYECTO)

from shared.db import conectar_postgres  # noqa: E402
from shared.diagnostico import guardar_diagnostico  # noqa: E402
from shared.numeros import a_decimal  # noqa: E402
from shared.credenciales import resolver_cuentas  # noqa: E402
from shared.pool_navegadores import ejecutar_en_paralelo  # noqa: E402
from shared.supramax_login import iniciar_sesion  # noqa: E402

load_dotenv()

# Tolerancia de redondeo al comparar el saldo esperado contra el leído del
# portal después de dispersar (ver `_verificar_dispersion`).
TOLERANCIA_CENTAVOS = Decimal("0.01")

# Cuántas veces se relee el saldo esperando que la dispersión ya haya
# aplicado, antes de darla por no confirmada en esta corrida.
INTENTOS_VERIFICACION = 6
ESPERA_VERIFICACION_SEG = 2


# =============================================================================
# 0. Constancia de en qué paso va cada job
# =============================================================================


class Progreso:
    """Escribe en qué paso va un job, para que la pantalla lo pueda mostrar.

    CONEXIÓN PROPIA, A PROPÓSITO

    Esto se llama desde los threads del pool, mientras la conexión principal
    de `main()` está a media transacción. Compartirla haría que un `commit`
    de progreso publicara a destiempo lo que la transacción principal todavía
    estaba armando. Cada worker abre la suya, en `autocommit` -- el progreso
    tiene que verse YA, no al final, que es justo para lo que sirve.

    NUNCA LANZA

    Si no se puede escribir el paso, se avisa y se sigue. El progreso es para
    mirar; que falle no debe costar una dispersión.
    """

    def __init__(self) -> None:
        self._conexion = None

    def _conn(self):
        if self._conexion is None:
            self._conexion = conectar_postgres()
            self._conexion.autocommit = True

        return self._conexion

    def paso(self, id_job: int, clave: str, texto: str) -> None:
        try:
            with self._conn().cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE supramax_dispersion_jobs
                       SET cl_paso = %s, de_paso = %s, fh_paso = now()
                     WHERE id_job = %s
                    """,
                    (clave, texto[:200], id_job),
                )
        except Exception as error:  # noqa: BLE001
            print(f"  ⚠️ No se pudo registrar el paso «{clave}» del job {id_job}: {error}")

    def cerrar(self) -> None:
        if self._conexion is not None:
            try:
                self._conexion.close()
            except Exception:  # noqa: BLE001
                pass
            self._conexion = None


class CreditoInsuficiente(Exception):
    """La cuenta no tiene crédito disponible para asignar.

    Es distinto de «falló la dispersión»: el portal de Supramax separa el
    SALDO de la cuenta del CRÉDITO DISPONIBLE POR ASIGNAR, y solo del segundo
    se puede repartir a los vehículos. Una cuenta puede tener $121,641 de
    saldo y $0 por asignar -- pasó el 2026-09-29 con TRASLADOS ROEH -- y el
    formulario se deja llenar igual, acepta el clic y no aplica nada.

    Se distingue porque NO tiene caso reintentarlo: mientras alguien no mueva
    dinero al crédito asignable en el portal, el segundo intento va a fallar
    igual que el primero. Reintentar solo retrasa el aviso.
    """


def _credito_por_asignar(driver) -> Decimal | None:
    """Cuánto crédito puede repartir esta cuenta ahora mismo.

    Se lee del renglón «Crédito disponible por asignar» de la propia pantalla
    de asignación. `None` si no se encontró: ahí se sigue adelante en vez de
    frenar, porque no vale la pena tumbar una dispersión buena por no haber
    podido leer un rótulo.
    """
    try:
        celda = driver.find_element(
            By.XPATH,
            "//td[contains(normalize-space(.), 'disponible por asignar')]"
            "/following-sibling::td[2]",
        )
        return a_decimal(celda.text)
    except Exception:  # noqa: BLE001
        return None


def _motivo_legible(error: Exception, job: dict) -> str:
    """Traduce la excepción de Selenium a algo que le sirva a quien lo lee.

    Un `TimeoutException` crudo trae cien líneas de stack de chromedriver y
    ni una palabra sobre qué se estaba buscando. Lo que hace falta saber es
    QUÉ no apareció -- casi siempre significa que esa unidad ya no existe en
    el portal, o que la cuenta no la tiene asignada.
    """
    idv = job["id_vehiculosupramax"]
    texto = str(error)

    if isinstance(error, TimeoutException):
        return (
            f"El portal no mostró el formulario de crédito de la unidad {idv}. "
            "Puede que ya no exista en Supramax, que la cuenta no la tenga "
            "asignada, o que el portal esté tardando de más."
        )

    if "no such element" in texto.lower():
        return (
            f"No se encontró un campo esperado en la pantalla de la unidad {idv}. "
            "Es probable que el portal haya cambiado de forma."
        )

    # Sin la primera línea el mensaje de Selenium es ilegible; con el stack
    # completo tampoco se lee. Se corta.
    return f"Error al dispersar la unidad {idv}: {texto.splitlines()[0][:300]}"


# =============================================================================
# 1. Recuperación de huérfanos + toma de un lote de jobs pendientes
# =============================================================================


def _recuperar_huerfanos(cursor, minutos: int, intentos_maximos: int) -> None:
    """Rescata los jobs que quedaron colgados, PERO NO PARA SIEMPRE.

    AQUÍ VIVÍA EL BUCLE INFINITO
    ----------------------------
    Esta función devolvía a `PENDIENTE` todo lo que llevara rato en
    `EN_PROCESO`, subiendo `nu_intentos` sin mirarlo nunca. El tope
    (`intentos_maximos`) solo se consultaba en `_marcar_fallo_o_reintento`,
    que únicamente se alcanza cuando la dispersión misma truena -- jamás
    cuando el login falla.

    Resultado real, septiembre de 2026: tres jobs con 36, 147 y 157 intentos,
    reintentándose cada 90 segundos durante cuatro días contra una contraseña
    que ya no servía, con `de_error` en nulo. Nadie podía saberlo mirando la
    base.

    Ahora lo que se pasa del tope se cierra como `FALLIDO` y DICE POR QUÉ.
    Un job que se rinde y lo explica vale más que uno que insiste callado.
    """
    cursor.execute(
        """
        UPDATE supramax_dispersion_jobs
           SET cl_estatus = 'FALLIDO',
               nu_intentos = nu_intentos + 1,
               cl_paso = 'FALLIDO',
               de_paso = 'Se detuvo tras agotar los intentos',
               fh_paso = now(),
               de_error = coalesce(de_error, '') ||
                 case when de_error is null or de_error = '' then '' else ' | ' end ||
                 'El robot lo intentó ' || (nu_intentos + 1) ||
                 ' vez/veces y la sesión quedó colgada sin terminar. Se detiene '
                 'para no reintentar indefinidamente: revisa que la cuenta de '
                 'Supramax de esta empresa entre en Configuraciones > Credenciales.',
               fh_resuelto = now()
         WHERE cl_estatus = 'EN_PROCESO'
           AND fh_tomado < now() - (%s || ' minutes')::interval
           AND nu_intentos + 1 >= %s
        """,
        (minutos, intentos_maximos),
    )

    if cursor.rowcount:
        print(
            f"🛑 {cursor.rowcount} job(s) marcado(s) FALLIDO por agotar los "
            f"{intentos_maximos} intentos."
        )

    cursor.execute(
        """
        UPDATE supramax_dispersion_jobs
           SET cl_estatus = 'PENDIENTE',
               nu_intentos = nu_intentos + 1,
               cl_paso = 'EN_COLA',
               de_paso = 'Reintentando: el intento anterior quedó a medias',
               fh_paso = now()
         WHERE cl_estatus = 'EN_PROCESO'
           AND fh_tomado < now() - (%s || ' minutes')::interval
        """,
        (minutos,),
    )

    if cursor.rowcount:
        print(
            f"♻️ {cursor.rowcount} job(s) huérfano(s) "
            f"(EN_PROCESO hace más de {minutos} min) devuelto(s) a PENDIENTE."
        )


def _tomar_jobs_pendientes(cursor, limite: int) -> list[dict]:
    """`FOR UPDATE SKIP LOCKED`: si otra corrida de este mismo script ya está
    tomando jobs (el polling se solapó), esta simplemente toma los que
    queden libres en vez de bloquearse esperando el lock de la otra."""
    cursor.execute(
        """
        SELECT id_job, id_solicitud, id_empresa, id_tarjeta,
               id_vehiculosupramax, im_importe, nu_intentos
          FROM supramax_dispersion_jobs
         WHERE cl_estatus = 'PENDIENTE'
         ORDER BY fh_creacion
         FOR UPDATE SKIP LOCKED
         LIMIT %s
        """,
        (limite,),
    )
    filas = cursor.fetchall()

    if not filas:
        return []

    columnas = [
        "id_job",
        "id_solicitud",
        "id_empresa",
        "id_tarjeta",
        "id_vehiculosupramax",
        "im_importe",
        "nu_intentos",
    ]
    jobs = [dict(zip(columnas, fila)) for fila in filas]

    cursor.execute(
        """
        UPDATE supramax_dispersion_jobs
           SET cl_estatus = 'EN_PROCESO', fh_tomado = now()
         WHERE id_job = ANY(%s)
        """,
        ([job["id_job"] for job in jobs],),
    )

    return jobs


def _resolver_cuentas_por_job(
    cursor, jobs: list[dict], cuentas_credenciales: list[dict]
) -> dict[int, dict | None]:
    """Resuelve, POR TARJETA, la cuenta real de Supramax donde hay que
    dispersar -- nunca por empresa sola.

    La primera prueba real de dispersión (2026-09-16, CA-146 de "Aske")
    falló resolviendo por `empresas.nb_empresa`. La segunda prueba (AU-224,
    misma empresa nuestra) reveló por qué eso no alcanza: CA-146 vive hoy en
    la cuenta de Supramax "ROEH" (se vendió) y AU-224 en "ABASTECEDORA" --
    dos tarjetas de la MISMA empresa nuestra, en DOS cuentas distintas. Un
    mapeo a nivel empresa no puede representar eso.

    La fuente de verdad es `supramax_saldos.nb_cuenta`: la cuenta con la que
    `supramax_saldos_rpa.py` leyó esa tarjeta la última vez (ver
    `34_supramax_saldos_cuenta.sql`). Si la tarjeta nunca se ha leído
    todavía (recién dada de alta, sin fila en `supramax_saldos`), se cae a
    `empresas.nb_empresa` como mejor esfuerzo -- puede fallar igual que
    falló para CA-146, pero es preferible a no intentar nada.
    """
    ids_empresa = list({job["id_empresa"] for job in jobs})

    cursor.execute(
        "SELECT id_empresa, id_tarjeta, nb_cuenta FROM supramax_saldos WHERE id_empresa = ANY(%s)",
        (ids_empresa,),
    )
    cuenta_por_tarjeta = {
        (id_empresa, id_tarjeta): nb_cuenta
        for id_empresa, id_tarjeta, nb_cuenta in cursor.fetchall()
        if nb_cuenta
    }

    ids_empresa_sin_tarjeta = {
        job["id_empresa"]
        for job in jobs
        if (job["id_empresa"], job["id_tarjeta"]) not in cuenta_por_tarjeta
    }

    nombres_empresa: dict[int, str] = {}
    if ids_empresa_sin_tarjeta:
        cursor.execute(
            "SELECT id_empresa, nb_empresa FROM empresas WHERE id_empresa = ANY(%s)",
            (list(ids_empresa_sin_tarjeta),),
        )
        nombres_empresa = dict(cursor.fetchall())

    por_nombre = {
        (cuenta.get("Empresa") or "").strip().upper(): cuenta
        for cuenta in cuentas_credenciales
    }

    resueltas: dict[int, dict | None] = {}
    for job in jobs:
        clave = (job["id_empresa"], job["id_tarjeta"])
        nombre_cuenta = cuenta_por_tarjeta.get(clave) or nombres_empresa.get(job["id_empresa"])
        resueltas[job["id_job"]] = por_nombre.get((nombre_cuenta or "").strip().upper())

    return resueltas


def _agrupar_por_cuenta(jobs: list[dict], cuenta_por_job: dict[int, dict]) -> list[dict]:
    """Agrupa los jobs que ya resolvieron a la MISMA cuenta de Supramax, para
    que compartan una sola sesión -- no tiene sentido loguear la misma
    cuenta N veces para N recargas. Se agrupa por `Usuario` (llave estable de
    `SUPRAMAX_CREDENTIALS`), no por `id_empresa`: la razón de ser de esta
    función es justo que ya no se puede asumir que "misma empresa" implica
    "misma cuenta"."""
    grupos: dict[str, dict] = {}
    for job in jobs:
        cuenta = cuenta_por_job[job["id_job"]]
        llave = cuenta.get("Usuario") or json.dumps(cuenta, sort_keys=True)
        grupo = grupos.setdefault(llave, {"cuenta": cuenta, "jobs": []})
        grupo["jobs"].append(job)
    return list(grupos.values())


# =============================================================================
# 2. Dispersión real contra el portal, por grupo (una sesión, varios jobs)
# =============================================================================


def _leer_saldo_actual(driver, wait) -> Decimal | None:
    campo = wait.until(EC.presence_of_element_located((By.ID, "vehsaldo")))
    return a_decimal(campo.get_attribute("value"))


def _dispersar_job(driver, base_url: str, job: dict, progreso: Progreso) -> Decimal | None:
    """Navega al vehículo, lee el saldo de ANTES, escribe el importe y hace
    click en "Sumar crédito" -- nunca en `#btn_subcrdveh` ("Restar crédito"),
    que existe en el mismo formulario y resta en vez de sumar.

    Cada `progreso.paso()` se escribe DESPUÉS de que el paso ocurrió, no
    antes: si el portal se traba buscando el formulario, la pantalla se queda
    en «Buscando la unidad» -- que es la verdad -- en vez de avanzar sola
    hasta el final como haría una barra decorativa.
    """
    wait = WebDriverWait(driver, 20)
    url = f"{base_url}intmain.php?nd=1&ad=crd&idv={job['id_vehiculosupramax']}"

    progreso.paso(
        job["id_job"],
        "NAVEGANDO",
        f"Buscando la unidad {job['id_vehiculosupramax']} en la asignación de créditos",
    )
    driver.get(url)

    # Se espera el formulario Y su token dinámico (`submissionId`, que
    # Zebra_Form genera por JS) antes de tocar nada: si se llena el importe
    # antes de que el token exista, el submit puede rechazarse por la
    # validación anti-bot sin un mensaje claro de por qué.
    wait.until(EC.presence_of_element_located((By.ID, "reg_crdveh")))
    wait.until(EC.presence_of_element_located((By.ID, "submissionId")))

    saldo_antes = _leer_saldo_actual(driver, wait)

    # Se mira ANTES de teclear: el portal deja llenar el formulario y acepta
    # el clic aunque no haya crédito que repartir, y entonces el fallo se
    # descubre hasta la verificación, sin decir por qué.
    por_asignar = _credito_por_asignar(driver)

    if por_asignar is not None and por_asignar < job["im_importe"]:
        raise CreditoInsuficiente(
            f"La cuenta tiene ${por_asignar} de crédito disponible por asignar "
            f"y esta recarga es de ${job['im_importe']}. El saldo de la cuenta "
            "no es lo mismo: hay que pasarlo a crédito asignable en el portal "
            "de Supramax antes de que el robot pueda repartirlo."
        )

    campo_importe = wait.until(EC.element_to_be_clickable((By.ID, "importe")))
    campo_importe.clear()
    campo_importe.send_keys(str(job["im_importe"]))

    progreso.paso(
        job["id_job"],
        "IMPORTE",
        f"Unidad encontrada (saldo actual ${saldo_antes}). Capturando ${job['im_importe']}",
    )

    boton_sumar = wait.until(EC.element_to_be_clickable((By.ID, "btn_addcrdveh")))
    driver.execute_script("arguments[0].click();", boton_sumar)

    progreso.paso(
        job["id_job"],
        "APLICANDO",
        f"Enviando «Sumar crédito» por ${job['im_importe']}",
    )
    time.sleep(2)

    return saldo_antes


def _verificar_dispersion(
    driver, base_url: str, idv: str, saldo_antes: Decimal | None, importe: Decimal
) -> Decimal | None:
    """
    SUPUESTO YA VALIDADO CON UNA PRUEBA REAL (2026-09-16, AU-224, $1.00):
    recargar la misma URL del vehículo sí vuelve a traer `#vehsaldo`
    actualizado con `saldo_antes + importe` -- confirmado end-to-end (botón →
    cola → este script → portal → verificación). Sigue siendo la única
    fuente conocida de confirmación: `html_pages/supramax/dispersionmodal.html`
    no incluye la pantalla posterior al submit, así que este releer sigue
    siendo cómo se sabe que aplicó.

    Regresa el saldo CONFIRMADO por el portal (no `esperado`, que es un
    cálculo local) para que quien llama pueda escribirlo directo en
    `supramax_saldos` sin otra visita al portal -- ver `_marcar_completado`.
    `None` si no se pudo confirmar en los reintentos disponibles.
    """
    if saldo_antes is None:
        return None

    esperado = saldo_antes + importe
    wait = WebDriverWait(driver, 20)
    url = f"{base_url}intmain.php?nd=1&ad=crd&idv={idv}"

    for _ in range(INTENTOS_VERIFICACION):
        driver.get(url)

        try:
            wait.until(EC.presence_of_element_located((By.ID, "vehsaldo")))
        except TimeoutException:
            time.sleep(ESPERA_VERIFICACION_SEG)
            continue

        saldo_actual = _leer_saldo_actual(driver, wait)

        if saldo_actual is not None and abs(saldo_actual - esperado) <= TOLERANCIA_CENTAVOS:
            return saldo_actual

        time.sleep(ESPERA_VERIFICACION_SEG)

    return None


def _procesar_grupo(driver, grupo: dict, base_url: str, descargas_dir: str) -> dict:
    """Un worker del pool: login una vez, luego un job tras otro. Se ejecuta
    dentro de un thread propio -- cualquier excepción que no sea "invalid
    session id" la atrapa igual `pool_navegadores._correr_cuenta` y tumba
    solo este grupo, no a los demás."""
    cuenta = grupo["cuenta"]
    jobs = grupo["jobs"]
    username = cuenta.get("Usuario")
    nombre_cuenta = cuenta.get("Empresa") or username

    progreso = Progreso()

    try:
        return _procesar_grupo_con_progreso(
            driver, jobs, cuenta, nombre_cuenta, username, base_url, descargas_dir, progreso
        )
    finally:
        progreso.cerrar()


def _procesar_grupo_con_progreso(
    driver, jobs, cuenta, nombre_cuenta, username, base_url, descargas_dir, progreso
) -> dict:
    for job in jobs:
        progreso.paso(
            job["id_job"],
            "LOGIN",
            f"Entrando al portal de Supramax con la cuenta {nombre_cuenta}",
        )

    if not iniciar_sesion(driver, cuenta, base_url, descargas_dir, "dispersion"):
        return {"login_ok": False, "resultados": []}

    resultados = []

    for job in jobs:
        try:
            saldo_antes = _dispersar_job(driver, base_url, job, progreso)

            progreso.paso(
                job["id_job"],
                "VERIFICANDO",
                "Releyendo el saldo de la tarjeta para confirmar que subió",
            )

            saldo_confirmado = _verificar_dispersion(
                driver, base_url, job["id_vehiculosupramax"], saldo_antes, job["im_importe"]
            )
            resultados.append(
                {
                    "job": job,
                    "exito": saldo_confirmado is not None,
                    "saldo_confirmado": saldo_confirmado,
                    "error": None,
                }
            )

            if saldo_confirmado is None:
                guardar_diagnostico(driver, descargas_dir, username, "dispersion_fallida")
        except CreditoInsuficiente as error:
            # No se reintenta: hasta que alguien mueva dinero en el portal, el
            # segundo intento falla igual. Ver la clase.
            guardar_diagnostico(driver, descargas_dir, username, "dispersion_fallida")
            resultados.append(
                {
                    "job": job,
                    "exito": False,
                    "saldo_confirmado": None,
                    "error": str(error),
                    "sin_reintento": True,
                }
            )
        except Exception as error:  # noqa: BLE001
            mensaje = str(error)

            if "invalid session id" in mensaje.lower():
                # Chrome perdió la sesión a medio camino: se aborta el grupo
                # limpio, SIN marcar este job (ni los que faltan) como
                # COMPLETADO ni FALLIDO a ciegas -- no hay forma de saber si
                # el submit ya había aplicado del lado de Supramax antes de
                # perder la sesión. Se quedan en EN_PROCESO; la recuperación
                # de huérfanos del inicio de una corrida posterior los toma,
                # y ahora esa recuperación SÍ tiene tope.
                #
                # Lo que sí cambia: se deja dicho. Antes este caso no escribía
                # nada y el job parecía estar trabajando cuando ya nadie lo
                # estaba mirando.
                progreso.paso(
                    job["id_job"],
                    "EN_COLA",
                    "El navegador perdió la sesión a media dispersión; "
                    "se reintentará en la próxima pasada",
                )
                print(
                    f"🛑 Sesión de navegador perdida procesando {username}. "
                    f"Se detiene el grupo; job(s) restantes quedan EN_PROCESO."
                )
                break

            guardar_diagnostico(driver, descargas_dir, username, "dispersion_fallida")
            resultados.append(
                {
                    "job": job,
                    "exito": False,
                    "saldo_confirmado": None,
                    "error": _motivo_legible(error, job),
                }
            )

    return {"login_ok": True, "resultados": resultados}


# =============================================================================
# 3. Escritura de resultados en `supramax_dispersion_jobs`
# =============================================================================


def _marcar_completado(cursor, job: dict, saldo_confirmado: Decimal) -> None:
    # No hay folio propio de Supramax que se pueda leer del portal (ver
    # comentario de la columna en `31_supramax_dispersion_jobs.sql`), así que
    # se fabrica uno propio, único y verificable, con el mismo criterio que
    # ya usa Ticket Car para `nu_foliodispersion`: un valor derivado que no
    # se repite entre corridas.
    folio = f"SPX-{job['id_vehiculosupramax']}-{int(time.time())}"
    cursor.execute(
        """
        UPDATE supramax_dispersion_jobs
           SET cl_estatus = 'COMPLETADO',
               nu_foliodispersion = %s,
               cl_paso = 'LISTO',
               de_paso = 'Saldo confirmado en el portal',
               fh_paso = now(),
               fh_resuelto = now()
         WHERE id_job = %s
        """,
        (folio, job["id_job"]),
    )

    # El saldo ya se leyó del portal para CONFIRMAR que la dispersión aplicó
    # (`_verificar_dispersion`) -- se escribe aquí mismo en la caché para que
    # "Saldos y recargas" lo refleje de inmediato, sin esperar el próximo
    # ciclo de `supramax_saldos_rpa.py` (cada 30 min) ni una segunda visita
    # al portal. Si la tarjeta nunca se había leído todavía (sin fila previa
    # en `supramax_saldos`, poco probable pero no imposible si se dispersa
    # antes del primer sync), este UPDATE no encuentra fila y no hace nada:
    # no se inserta a ciegas porque faltarían nu_placas/nu_tag/nb_cuenta que
    # solo trae el scrape completo.
    cursor.execute(
        """
        UPDATE supramax_saldos
           SET im_credito = %s,
               fh_consulta = now(),
               cl_estatus = 'OK'
         WHERE id_empresa = %s AND id_tarjeta = %s
        """,
        (saldo_confirmado, job["id_empresa"], job["id_tarjeta"]),
    )


def _marcar_fallo_o_reintento(cursor, job: dict, mensaje: str, intentos_maximos: int) -> None:
    nuevos_intentos = job["nu_intentos"] + 1

    if nuevos_intentos >= intentos_maximos:
        cursor.execute(
            """
            UPDATE supramax_dispersion_jobs
               SET cl_estatus = 'FALLIDO',
                   nu_intentos = %s,
                   de_error = %s,
                   cl_paso = 'FALLIDO',
                   de_paso = 'Se detuvo tras agotar los intentos',
                   fh_paso = now(),
                   fh_resuelto = now()
             WHERE id_job = %s
            """,
            (nuevos_intentos, mensaje, job["id_job"]),
        )
    else:
        # Vuelve a PENDIENTE (no se toca `fh_tomado`): la próxima corrida de
        # este mismo script lo vuelve a tomar como cualquier job nuevo.
        cursor.execute(
            """
            UPDATE supramax_dispersion_jobs
               SET cl_estatus = 'PENDIENTE',
                   nu_intentos = %s,
                   de_error = %s,
                   cl_paso = 'EN_COLA',
                   de_paso = 'Falló el intento anterior; se volverá a intentar',
                   fh_paso = now()
             WHERE id_job = %s
            """,
            (nuevos_intentos, mensaje, job["id_job"]),
        )


def _actualizar_jobs(conexion, resultados_grupos, intentos_maximos: int) -> None:
    with conexion.cursor() as cursor:
        for resultado in resultados_grupos:
            grupo = resultado.cuenta  # el "cuenta" del pool es el grupo aquí

            nombre_cuenta = grupo.get("cuenta", {}).get("Empresa", "?")

            if not resultado.ok:
                print(
                    f"❌ Grupo de la cuenta {nombre_cuenta} "
                    f"falló en el pool: {resultado.error}"
                )
                # ANTES: `continue` a secas. Los jobs quedaban EN_PROCESO sin
                # `de_error` y sin consumir intento, y la recuperación de
                # huérfanos los reciclaba para siempre.
                primera_linea = str(resultado.error or "").splitlines()
                for job in grupo.get("jobs", []):
                    _marcar_fallo_o_reintento(
                        cursor,
                        job,
                        "El robot no pudo abrir el navegador para la cuenta "
                        f"{nombre_cuenta}: "
                        f"{primera_linea[0][:300] if primera_linea else 'sin detalle'}",
                        intentos_maximos,
                    )
                continue

            datos = resultado.resultado or {}

            if not datos.get("login_ok"):
                print(
                    f"⚠️ Login fallido para la cuenta {nombre_cuenta}: "
                    f"se anota el motivo y se consume un intento."
                )
                # EL AGUJERO QUE COSTÓ CUATRO DÍAS. Esta rama era un
                # `continue` mudo: los jobs volvían a PENDIENTE por huérfanos,
                # sin motivo escrito y sin tope, cada 90 segundos.
                for job in grupo.get("jobs", []):
                    _marcar_fallo_o_reintento(
                        cursor,
                        job,
                        f"No se pudo entrar al portal de Supramax con la cuenta "
                        f"{nombre_cuenta}. Revisa que esa credencial siga "
                        f"sirviendo en Configuraciones > Credenciales.",
                        intentos_maximos,
                    )
                continue

            for item in datos.get("resultados", []):
                job = item["job"]

                if item["exito"]:
                    _marcar_completado(cursor, job, item["saldo_confirmado"])
                else:
                    mensaje = item.get("error") or (
                        "El saldo no confirmó el importe esperado tras dispersar "
                        f"(verificado {INTENTOS_VERIFICACION} veces)."
                    )
                    # Un fallo que un reintento no puede arreglar se cierra ya,
                    # con su motivo. Insistir solo retrasa el aviso.
                    tope = 1 if item.get("sin_reintento") else intentos_maximos
                    _marcar_fallo_o_reintento(cursor, job, mensaje, tope)

        conexion.commit()


def _marcar_sin_cuenta(cursor, jobs_sin_cuenta: list[dict]) -> None:
    for job in jobs_sin_cuenta:
        cursor.execute(
            """
            UPDATE supramax_dispersion_jobs
               SET cl_estatus = 'FALLIDO',
                   de_error = %s,
                   cl_paso = 'FALLIDO',
                   de_paso = 'No hay cuenta de Supramax para esta empresa',
                   fh_paso = now(),
                   fh_resuelto = now()
             WHERE id_job = %s
            """,
            (
                f"No hay cuenta de Supramax configurada para la empresa {job['id_empresa']}.",
                job["id_job"],
            ),
        )


# =============================================================================
# 4. Orquestación
# =============================================================================


def main() -> int:
    minutos_huerfano = int(os.getenv("SUPRAMAX_DISPERSION_HUERFANO_MIN", "10"))
    # Dos: si el segundo intento tampoco entra, se detiene y lo dice. Un
    # tercero contra un portal que ya rechazó dos veces solo retrasa el aviso.
    intentos_maximos = int(os.getenv("SUPRAMAX_DISPERSION_INTENTOS_MAX", "2"))
    max_workers = int(os.getenv("SUPRAMAX_MAX_WORKERS", "3"))
    # El tamaño del lote de jobs a tomar por corrida sigue al tamaño del pool
    # por defecto: no tiene caso tomar más jobs de los que se pueden atender
    # en paralelo en esta misma pasada.
    tamano_lote = int(os.getenv("SUPRAMAX_DISPERSION_LOTE", str(max_workers)))
    headless = os.getenv("SUPRAMAX_HEADLESS", "true").strip().lower() != "false"

    # Primero la tabla `proveedores_credenciales`, y solo si está vacía el
    # `.env` de siempre -- ver `shared/credenciales.resolver_cuentas`.
    try:
        cuentas_credenciales = resolver_cuentas("SUPRAMAX", "SUPRAMAX_CREDENTIALS")
    except Exception as error:  # noqa: BLE001
        print(f"ERROR: {error}")
        return 1

    base_url = os.getenv("SUPRAMAX_URL", "https://es09326.migasolinera.net/emaxclie/")
    if not base_url.endswith("/"):
        base_url += "/"

    descargas_dir = os.path.join(os.getcwd(), "descargas_temporales")
    os.makedirs(descargas_dir, exist_ok=True)

    # Prefijo de perfil propio de este script: nunca `chrome_profile/supramax`
    # a secas (`supramax_rpa.py`) ni `.../saldos` (`supramax_saldos_rpa.py`).
    perfil_base = os.path.join(os.getcwd(), "chrome_profile", "supramax", "dispersion")

    conexion = conectar_postgres()

    try:
        with conexion.cursor() as cursor:
            _recuperar_huerfanos(cursor, minutos_huerfano, intentos_maximos)
        conexion.commit()

        with conexion.cursor() as cursor:
            jobs = _tomar_jobs_pendientes(cursor, tamano_lote)
        # Commit inmediato al marcar EN_PROCESO: que otra corrida de este
        # mismo script (si el polling se solapó) no se quede esperando este
        # lock más de lo necesario.
        conexion.commit()

        if not jobs:
            print("Sin jobs de dispersión pendientes.")
            return 0

        print(f"Tomados {len(jobs)} job(s) de dispersión pendientes.")

        with conexion.cursor() as cursor:
            cuenta_por_job = _resolver_cuentas_por_job(cursor, jobs, cuentas_credenciales)

        jobs_con_cuenta = [job for job in jobs if cuenta_por_job.get(job["id_job"])]
        jobs_sin_cuenta = [job for job in jobs if not cuenta_por_job.get(job["id_job"])]

        grupos_a_correr = _agrupar_por_cuenta(jobs_con_cuenta, cuenta_por_job)

        if jobs_sin_cuenta:
            with conexion.cursor() as cursor:
                _marcar_sin_cuenta(cursor, jobs_sin_cuenta)
            conexion.commit()
            print(
                f"❌ {len(jobs_sin_cuenta)} job(s) sin cuenta de Supramax configurada "
                f"para su empresa, marcados FALLIDO sin reintento."
            )

        if not grupos_a_correr:
            return 0

        resultados = ejecutar_en_paralelo(
            cuentas=grupos_a_correr,
            max_workers=max_workers,
            funcion_por_cuenta=lambda driver, grupo: _procesar_grupo(
                driver, grupo, base_url, descargas_dir
            ),
            perfil_base=perfil_base,
            headless=headless,
            descargas_dir=descargas_dir,
        )

        _actualizar_jobs(conexion, resultados, intentos_maximos)
    finally:
        conexion.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
