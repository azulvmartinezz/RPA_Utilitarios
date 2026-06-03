import os
import time
import re
import datetime
import json
from dotenv import load_dotenv
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from twocaptcha import TwoCaptcha
import undetected_chromedriver as uc

# Cargar variables de entorno
load_dotenv()

PASE_USER = os.getenv('PASE_USER')
PASE_PASSWORD = os.getenv('PASE_PASSWORD')
TWOCAPTCHA_API_KEY = os.getenv('TWOCAPTCHA_API_KEY')

_MESES_ES = {
    'ENERO': 1, 'FEBRERO': 2, 'MARZO': 3, 'ABRIL': 4,
    'MAYO': 5, 'JUNIO': 6, 'JULIO': 7, 'AGOSTO': 8,
    'SEPTIEMBRE': 9, 'OCTUBRE': 10, 'NOVIEMBRE': 11, 'DICIEMBRE': 12
}

_CLIENT_MAP_PATH = os.path.join(os.getcwd(), "descargas_temporales", "pase_client_map.json")


def _extraer_numero_cliente(texto):
    match = re.search(r'(?:NÚMERO|NUMERO)\s+DE\s+CLIENTE\s*:\s*(\d+)', str(texto or ""), re.IGNORECASE)
    return match.group(1) if match else None


def _extraer_numero_cliente_archivo(path_archivo):
    nombre = os.path.basename(str(path_archivo or ""))
    match = re.match(r'g\d+[A-Z](\d+)\.\d+\.csv$', nombre, re.IGNORECASE)
    return match.group(1) if match else None


def _cargar_mapa_clientes():
    try:
        with open(_CLIENT_MAP_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "by_number" in data:
            by_number = {
                str(k): str(v)
                for k, v in (data.get("by_number") or {}).items()
                if k and v
            }
            by_slot = {
                str(k): str(v)
                for k, v in (data.get("by_slot") or {}).items()
                if k and v
            }
            return {"by_number": by_number, "by_slot": by_slot}

        # Compatibilidad con el formato anterior: {"53089": "PETRO SMART ..."}
        by_number = {str(k): str(v) for k, v in data.items() if k and v}
        return {"by_number": by_number, "by_slot": {}}
    except Exception:
        return {"by_number": {}, "by_slot": {}}


def _guardar_mapa_clientes(mapa):
    try:
        os.makedirs(os.path.dirname(_CLIENT_MAP_PATH), exist_ok=True)
        with open(_CLIENT_MAP_PATH, "w", encoding="utf-8") as fh:
            json.dump(mapa, fh, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception as e:
        print(f"⚠️ No se pudo guardar el mapa local de clientes Pase: {e}")


def _nombre_empresa_pase(texto):
    lineas = [ln.strip() for ln in str(texto or "").splitlines() if ln.strip()]
    if not lineas:
        return "sin_empresa"
    if len(lineas) >= 2 and (
        lineas[0].upper().startswith("NÚMERO DE CLIENTE")
        or lineas[0].upper().startswith("NUMERO DE CLIENTE")
    ):
        return lineas[1]
    return lineas[0]


def _mes_objetivo_desde_periodo(texto, meses_objetivo):
    """Devuelve el primer mes objetivo cubierto por el periodo mostrado en Pase."""
    t = texto.upper()
    years = re.findall(r'\b(\d{4})\b', t)
    if not years:
        return None
    end_year = int(years[-1])

    match_end = re.search(r'(\w+)\s+DEL\s+' + str(end_year), t)
    if not match_end:
        return None
    end_month = _MESES_ES.get(match_end.group(1))
    if not end_month:
        return None

    match_start = re.search(r'DEL\s+\d+\s+(?:DE\s+)?(\w+)', t)
    start_month = _MESES_ES.get(match_start.group(1)) if match_start else None
    if not start_month:
        start_month = end_month

    start_year = end_year if start_month <= end_month else end_year - 1

    for ty, tm in sorted(meses_objetivo):
        if (start_year, start_month) <= (ty, tm) <= (end_year, end_month):
            return ty, tm
    return None

def _periodo_en_rango(texto, meses_objetivo):
    """True si el periodo de facturación cubre al menos uno de los meses objetivo."""
    return _mes_objetivo_desde_periodo(texto, meses_objetivo) is not None


def solve_recaptcha(sitekey, url):
    print(f"Resolviendo captcha (sitekey: {sitekey})... esto puede tardar un poco.")
    solver = TwoCaptcha(TWOCAPTCHA_API_KEY)
    
    try:
        result = solver.recaptcha(
            sitekey=sitekey,
            url=url
        )
        print("Captcha resuelto exitosamente!")
        return result['code']
    except Exception as e:
        print(f"Error resolviendo captcha: {e}")
        return None

def _descargar_prepago(driver, wait, backfill_mode=False, meses_objetivo=None):
    """Flujo de descarga para cuentas PREPAGO (pestaña CRUCES con filtro por mes)."""
    print("Modalidad PREPAGO: usando flujo de pestaña CRUCES...")

    # 1. Navegar a CRUCES si hay tab con ese texto (opcional: PREPAGO puede ya estar ahí)
    try:
        # Hacerlo case-insensitive y más tolerante (puede ser 'Cruces' o 'CRUCES')
        cruces_tab = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(translate(., 'cruces', 'CRUCES'), 'CRUCES')]")))
        driver.execute_script("arguments[0].click();", cruces_tab)
        print("  ✅ Cambiado a pestaña CRUCES exitosamente.")
        time.sleep(2)
    except Exception as e:
        print(f"  ⚠️ No se pudo dar clic explícito en la pestaña CRUCES: {e}")
        pass

    # 2. Limpiar filtros activos (botón "X + embudo")
    try:
        limpiar_btn = wait.until(EC.element_to_be_clickable((By.XPATH,
            "//button["
            ".//*[contains(@d,'M14.76,20.83')]"           # SVG FilterAltOff
            " or @aria-label='limpiar filtros'"
            " or @title='Limpiar filtros'"
            "]")))
        limpiar_btn.click()
        time.sleep(1)
    except Exception:
        pass  # Sin filtros activos, continuar

    # Determinar qué meses descargar
    if backfill_mode and meses_objetivo:
        # Calcular data-values exactos para los meses objetivo
        today = datetime.date.today()
        data_values = []
        for ty, tm in meses_objetivo:
            months_ago = (today.year - ty) * 12 + (today.month - tm)
            if months_ago > 0:
                data_values.append(str(months_ago))
        print(f"Modo backfill filtrado: data-values {data_values}")
    elif backfill_mode:
        # Sin filtro: todos los meses disponibles
        mes_dropdown = wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//div[@role='button'][@aria-haspopup='true']")))
        mes_dropdown.click()
        time.sleep(1)
        opciones = wait.until(EC.presence_of_all_elements_located(
            (By.XPATH, "//li[@role='option'][@data-value]")))
        data_values = [op.get_attribute('data-value')
                       for op in opciones if int(op.get_attribute('data-value')) > 0]
        driver.find_element(By.TAG_NAME, 'body').click()
        time.sleep(1)
    else:
        data_values = ["1"]  # Solo mes anterior

    for dv in data_values:
        # 3. Abrir dropdown de mes y seleccionar
        try:
            # Hacer clic en el body para cerrar cualquier menú previo (ej. exportación)
            driver.find_element(By.TAG_NAME, 'body').click()
            time.sleep(1)
        except:
            pass

        # === LIMPIAR FILTROS DEL CICLO ANTERIOR ===
        try:
            limpiar_btn = driver.find_element(By.XPATH,
                "//button["
                ".//*[contains(@d,'M14.76,20.83')]"
                " or @aria-label='limpiar filtros'"
                " or @title='Limpiar filtros'"
                " or @title='Remover filtro'"
                " or ancestor::span[@title='Remover filtro']"
                "]")
            if limpiar_btn.is_displayed() and limpiar_btn.is_enabled():
                print("  Limpiando filtro anterior...")
                driver.execute_script("arguments[0].click();", limpiar_btn)
                time.sleep(2)
        except:
            pass
        # ==========================================

        # Buscar de forma infalible el dropdown correcto:
        # Hacemos clic en los posibles dropdowns hasta que aparezcan las opciones de mes (li con role=option)
        opciones_encontradas = False
        dropdowns = driver.find_elements(By.XPATH, "//div[@role='button'][@aria-haspopup='listbox' or @aria-haspopup='true']")
        for dd in dropdowns:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", dd)
                time.sleep(0.5)
                driver.execute_script("arguments[0].click();", dd)
                time.sleep(1.5)
                
                # Verificar si aparecieron las opciones y están VISIBLES
                # (MUI a veces deja menús ocultos en el DOM, ej. el de clientes)
                opciones = driver.find_elements(By.XPATH, "//li[@role='option']")
                opciones_visibles = [op for op in opciones if op.is_displayed()]
                
                if opciones_visibles:
                    # Confirmar que es el menú de meses (las opciones deberían tener años, ej. '2026')
                    texto_opciones = " ".join([op.text for op in opciones_visibles])
                    if "202" in texto_opciones or "Personalizado" in texto_opciones:
                        opciones_encontradas = True
                        break
                
                # Si no aparecieron o no es el menú correcto, cerramos este menú dándole clic al body
                driver.find_element(By.TAG_NAME, 'body').click()
                time.sleep(0.5)
            except:
                pass

        if not opciones_encontradas:
            print(f"❌ No se pudo abrir el menú desplegable para buscar data-value={dv}.")
            raise Exception(f"No dropdown opened for dv={dv}")

        try:
            opcion = wait.until(EC.element_to_be_clickable(
                (By.XPATH, f"//li[@role='option'][@data-value='{dv}']")))
        except Exception as e:
            # Intentar buscar por JavaScript
            opciones = driver.find_elements(By.XPATH, "//li[@role='option']")
            opcion = None
            for op in opciones:
                if op.get_attribute('data-value') == str(dv):
                    print("Se encontró por JS, forzando click...")
                    driver.execute_script("arguments[0].click();", op)
                    opcion = op
                    break
            if not opcion:
                print(f"⚠️ El periodo (data-value={dv}) NO ESTÁ DISPONIBLE en la plataforma. Saltando...")
                # Cerrar el menú antes de continuar
                try:
                    driver.find_element(By.TAG_NAME, 'body').click()
                    time.sleep(1)
                except: pass
                continue

        print(f"  Seleccionando periodo: {opcion.text if opcion else 'N/A'}")
        try:
            opcion.click()
        except:
            if opcion: driver.execute_script("arguments[0].click();", opcion)
        time.sleep(2)

        # 4. Aplicar filtro (botón "embudo")
        aplicar_btn = wait.until(EC.element_to_be_clickable((By.XPATH,
            "//button["
            ".//*[contains(@d,'M14,12V19.88')]"           # SVG FilterAlt
            " or @aria-label='aplicar filtros'"
            " or @title='Aplicar filtros'"
            "]")))
        aplicar_btn.click()
        
        # Esperar a que la tabla termine de cargar los datos desde el servidor (puede ser lento)
        time.sleep(8)

        # Verificar si hay registros antes de exportar
        try:
            # Buscar el texto EXACTO "sin registros" para no confundirlo con "Sin registros seleccionados" del footer
            sin_registros = driver.find_elements(By.XPATH, "//*[normalize-space(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')) = 'sin registros' or normalize-space(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')) = 'no rows']")
            if any(el.is_displayed() for el in sin_registros):
                print(f"  ⚠️ No hay registros para este mes (aparece 'Sin registros'). Saltando descarga...")
                continue
        except:
            pass

        # 5. Expandir "Más opciones de filtros" SOLO si el botón de exportar está oculto
        try:
            exportar_btns = driver.find_elements(By.XPATH, "//button[contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'export') or contains(translate(@title, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'export') or contains(translate(@title, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'descargar')]")
            if not any(b.is_displayed() for b in exportar_btns):
                mas_opciones_btn = driver.find_element(By.XPATH, "//button[@title='Más opciones de filtros' or @aria-label='Más opciones de filtros']")
                mas_opciones_btn.click()
                time.sleep(1)
        except: pass

        # 6. Exportar CSV: abrir menú → seleccionar CSV
        try:
            # Esperamos hasta 20 segundos para que la API responda y la tabla renderice el botón de exportar
            wait_largo = WebDriverWait(driver, 20)
            def get_export_btn(d):
                btns = d.find_elements(By.XPATH, "//button[contains(translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'export') or contains(translate(@title, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'export') or contains(translate(@title, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'descargar')]")
                visibles = [b for b in btns if b.is_displayed()]
                return visibles[-1] if visibles else False
                
            exportar_btn = wait_largo.until(get_export_btn)
            driver.execute_script("arguments[0].click();", exportar_btn)
        except:
            print(f"  ⚠️ El botón de exportar no apareció tras 20s (quizá la tabla sigue vacía). Saltando descarga...")
            continue

        # Esperar a que el menú sea visible (buscar por el div role="presentation")
        wait.until(EC.presence_of_element_located(
            (By.XPATH, "//div[@role='presentation']")))
        time.sleep(1)

        # Buscar y hacer clic en CSV
        try:
            # Buscar opción de CSV ya sea por el atributo title, o por el texto que contenga 'coma' o 'csv'
            xpath_csv = "//li[@role='menuitem'][contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'coma') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'csv') or contains(translate(@title, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'coma') or contains(translate(@title, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'csv')]"
            csv_option = wait.until(EC.element_to_be_clickable((By.XPATH, xpath_csv)))
            driver.execute_script("arguments[0].click();", csv_option)
            print(f"  ✅ Exportación CSV iniciada para periodo data-value={dv}")
            time.sleep(8)
        except Exception as e:
            print("❌ No se encontró la opción CSV. Imprimiendo opciones de menú disponibles:")
            try:
                opciones = driver.find_elements(By.XPATH, "//li[@role='menuitem']")
                for idx, op in enumerate(opciones):
                    print(f"Opción {idx}: text='{op.text}', title='{op.get_attribute('title')}', innerHTML='{op.get_attribute('innerHTML')}'")
            except Exception as e2:
                print(f"Tampoco se pudieron extraer las opciones: {e2}")
            raise e


def main(backfill_mode=False, meses_objetivo=None, start_from=0):
    print("Iniciando RPA para Pase...")
    mapa_clientes = _cargar_mapa_clientes()
    
    if not TWOCAPTCHA_API_KEY:
        print("ERROR: Por favor agrega tu TWOCAPTCHA_API_KEY al archivo .env")
        return
        
    if not PASE_USER or not PASE_PASSWORD:
        print("ERROR: Faltan credenciales PASE_USER o PASE_PASSWORD en el archivo .env")
        print("Asegúrate de tener PASE_USER=... y PASE_PASSWORD=... configurados.")
        return

    # Configuración Anti-Detección usando undetected_chromedriver
    chrome_options = uc.ChromeOptions()
    
    # Forzar descargas a una carpeta controlada para poder leer los archivos
    descargas_dir = os.path.join(os.getcwd(), "descargas_temporales")
    if not os.path.exists(descargas_dir):
        os.makedirs(descargas_dir)
        
    prefs = {
        "download.default_directory": descargas_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    chrome_options.add_experimental_option("prefs", prefs)
    
    # Crear una carpeta local para guardar las cookies y el historial (Perfil Persistente)
    profile_path = os.path.join(os.getcwd(), "chrome_profile")
    
    # Ejecutando con undetected-chromedriver para saltar Radware WAF
    # Fijamos la versión 147 para que coincida con tu navegador instalado
    driver = uc.Chrome(options=chrome_options, user_data_dir=profile_path, version_main=147)
    
    wait = WebDriverWait(driver, 30)
    
    url = os.getenv('PASE_URL')
    
    try:
        indice_actual = start_from
        num_clientes = start_from + 1  # Se actualizará en la primera pasada
        
        while indice_actual < num_clientes:
            print(f"\n{'='*50}")
            print(f"🔄 PROCESANDO CLIENTE {indice_actual + 1} DE {num_clientes if num_clientes > 1 else '?'}")
            print(f"{'='*50}")
            
            print(f"Navegando a: {url}")
            driver.get(url)
            time.sleep(8)
            
            # --- Lógica Anti-WAF (Radware) ---
            if "validate.perfdrive.com" in driver.current_url:
                print("\n" + "="*50)
                print("🚨 WAF de Radware detectado (hCaptcha).")
                print("⚠️ 2Captcha actualmente no soporta hCaptcha.")
                print("👉 POR FAVOR, RESUELVE EL CAPTCHA MANUALMENTE EN LA VENTANA DE CHROME.")
                print("El bot te esperará hasta 90 segundos...")
                print("="*50 + "\n")
                
                try:
                    # Esperar hasta que la URL cambie de validate.perfdrive.com a apps.pase.com.mx
                    wait_waf = WebDriverWait(driver, 90)
                    wait_waf.until(EC.url_contains("apps.pase.com.mx"))
                    print("✅ hCaptcha resuelto manualmente. Continuando con el proceso automático...")
                    time.sleep(3)
                except:
                    print("❌ Se agotó el tiempo esperando a que resolvieras el captcha.")
                    return
            # --- Fin Lógica Anti-WAF ---
            
            # 1. Llenar usuario y contraseña
            print("Esperando los campos de usuario y contraseña...")
            time.sleep(3) # Pausa extra para que termine de cargar el framework (React/Vue)
            user_input = wait.until(EC.presence_of_element_located((By.ID, "username")))
            pwd_input = wait.until(EC.presence_of_element_located((By.ID, "password")))
            
            print("Ingresando credenciales...")
            user_input.send_keys(PASE_USER)
            pwd_input.send_keys(PASE_PASSWORD)
            
            # 2. ReCaptcha
            print("Buscando widget de ReCaptcha para obtener el sitekey...")
            try:
                recaptcha_div = driver.find_element(By.CLASS_NAME, "g-recaptcha")
                sitekey = recaptcha_div.get_attribute("data-sitekey")
                
                if sitekey:
                    print(f"Sitekey encontrado: {sitekey}")
                    
                    # Resolver captcha
                    token = solve_recaptcha(sitekey, driver.current_url)
                    
                    if token:
                        print("Inyectando token en la página...")
                        # Esperar e inyectar de forma robusta por ID o por Name, reintentando si no aparece de inmediato
                        token_inyectado = False
                        for _ in range(10):
                            el_exists = driver.execute_script(
                                """
                                var el = document.getElementById('g-recaptcha-response');
                                if (!el) {
                                    var els = document.getElementsByName('g-recaptcha-response');
                                    if (els && els.length > 0) el = els[0];
                                }
                                if (el) {
                                    el.innerHTML = arguments[0];
                                    el.value = arguments[0];
                                    return true;
                                }
                                return false;
                                """,
                                token,
                            )
                            if el_exists:
                                token_inyectado = True
                                break
                            time.sleep(1)
                        
                        if not token_inyectado:
                            print("⚠️ Advertencia: No se encontró el campo g-recaptcha-response para inyectar el token.")
                        time.sleep(1)
                        
                        # Dato vital: Pase requiere que ejecutemos el callback del captcha
                        print("Ejecutando el callback onRecaptchaValid...")
                        driver.execute_script(
                            "if(typeof onRecaptchaValid !== 'undefined') { onRecaptchaValid(arguments[0]); }",
                            token,
                        )
                        time.sleep(2)
                else:
                    print("No se encontró el data-sitekey en el widget.")
            except Exception as e:
                print(f"Ocurrió un error con ReCaptcha: {e}")
                
            # 3. Clic en Entrar
            print("Haciendo clic en Entrar...")
            # Ubicar el botón "Entrar" usando XPATHs alternativos para mayor tolerancia a variaciones
            try:
                continue_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[span[text()='Entrar']]")))
            except Exception:
                print("⚠️ No se encontró por span[text()='Entrar'], intentando XPATH alternativo con contains...")
                continue_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Entrar')]")))
            continue_btn.click()
            
            # Esperar a que cargue la pantalla de selección de cliente
            print("\nEsperando la pantalla de selección de cliente...")
            
            # El desplegable es un div personalizado de Material-UI. Lo buscamos a través de su input oculto.
            dropdown_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//input[@name='cliente']/preceding-sibling::div[@role='button']")))
            print("Abriendo el menú desplegable...")
            driver.execute_script("arguments[0].click();", dropdown_btn)
            
            # Esperar a que se despliegue la lista flotante
            time.sleep(1.5)
            
            # Localizar todas las opciones (li con role='option')
            opciones = wait.until(EC.presence_of_all_elements_located((By.XPATH, "//li[@role='option']")))
            num_clientes = len(opciones)
            
            if num_clientes > 0 and indice_actual < num_clientes:
                print(f"Seleccionando cliente {indice_actual + 1} de {num_clientes}...")
                empresa_actual = opciones[indice_actual].text.strip()
                numero_cliente_actual = _extraer_numero_cliente(empresa_actual)
                empresa_limpia = _nombre_empresa_pase(empresa_actual)
                if numero_cliente_actual and empresa_limpia != "sin_empresa":
                    mapa_clientes["by_number"][numero_cliente_actual] = empresa_limpia
                    mapa_clientes["by_slot"][str(indice_actual + 1)] = empresa_limpia
                    _guardar_mapa_clientes(mapa_clientes)
                elif empresa_limpia == "sin_empresa":
                    empresa_limpia = mapa_clientes["by_slot"].get(str(indice_actual + 1), "sin_empresa")
                    if empresa_limpia != "sin_empresa":
                        print(
                            f"🔁 Empresa inferida desde posición local del selector "
                            f"({indice_actual + 1}): {empresa_limpia}"
                        )
                print(f"Cliente seleccionado: {empresa_actual}")
                print(f"Empresa usada para BQ/GCS: {empresa_limpia}")
                driver.execute_script("arguments[0].click();", opciones[indice_actual])
                time.sleep(1)

                print("Haciendo clic en 'Continuar'...")
                btn_continuar = driver.find_element(By.XPATH, "//button[span[contains(text(), 'Continuar')]]")
                btn_continuar.click()

                print(f"\nPanel principal cargado.")

                # --- 5. Descarga de Reportes ---
                print("\nIniciando descarga de reportes de consumo...")

                def modalidad_cargada(driver):
                    try:
                        # Intentar XPath original (cliente 1-4)
                        elem = driver.find_element(By.XPATH, "//span[normalize-space(text())='Modalidad']/following-sibling::h6")
                        text = elem.text.strip()
                        if text:
                            return elem
                    except:
                        pass

                    try:
                        # Fallback: buscar h6 que contiene PREPAGO/POSPAGO/DECENAL directamente
                        elem = driver.find_element(By.XPATH, "//h6[contains(., 'PREPAGO') or contains(., 'POSPAGO') or contains(., 'DECENAL')]")
                        text = elem.text.strip()
                        if text:
                            return elem
                    except:
                        pass

                    return False

                wait_largo = WebDriverWait(driver, 15)
                modalidad_elem = wait_largo.until(modalidad_cargada)
                modalidad = modalidad_elem.text.strip().upper()
                print(f"Modalidad detectada: {modalidad}")

                try:
                    periodo_por_archivo = {}

                    if modalidad == "PREPAGO":
                        _descargar_prepago(driver, wait, backfill_mode, meses_objetivo)
                    elif modalidad not in ("POSPAGO",):
                        print(f"⚠️ Modalidad '{modalidad}' no soportada, omitiendo descargas.")
                    else:
                        xpath_csv_btn = "//h6[contains(text(), 'DET. CRUCES (NUEVO)')]/ancestor::div[3]//a[@title='Descargar archivo separado por comas']"
                        wait_corto = WebDriverWait(driver, 5)

                        if backfill_mode:
                            periodo_dropdown = wait.until(EC.element_to_be_clickable((By.XPATH, "//input[@placeholder='Periodo']/preceding-sibling::div[@role='button']")))
                            periodo_dropdown.click()
                            time.sleep(1.5)
                            opciones_all = wait.until(EC.presence_of_all_elements_located((By.XPATH, "//li[@role='option']")))
                            n_periodos = len(opciones_all)
                            print(f"Modo backfill: {n_periodos} periodos encontrados. Descargando todos...")
                            driver.find_element(By.TAG_NAME, 'body').click()
                            time.sleep(1)

                            for i in range(n_periodos):
                                periodo_dropdown = wait.until(EC.element_to_be_clickable((By.XPATH, "//input[@placeholder='Periodo']/preceding-sibling::div[@role='button']")))
                                periodo_dropdown.click()
                                time.sleep(1.5)
                                opciones_iter = wait.until(EC.presence_of_all_elements_located((By.XPATH, "//li[@role='option']")))
                                if i >= len(opciones_iter):
                                    break
                                texto = opciones_iter[i].text
                                if meses_objetivo and not _periodo_en_rango(texto, meses_objetivo):
                                    print(f"  Saltando periodo fuera del rango: {texto[:50]}...")
                                    driver.find_element(By.TAG_NAME, 'body').click()
                                    time.sleep(0.5)
                                    continue
                                print(f"  Periodo {i+1}/{n_periodos}: {texto}")
                                codigo_match = re.match(r"\s*(\d+)-", texto)
                                mes_bucket = _mes_objetivo_desde_periodo(texto, meses_objetivo or [])
                                if codigo_match and mes_bucket:
                                    periodo_por_archivo[codigo_match.group(1)] = mes_bucket
                                opciones_iter[i].click()
                                time.sleep(6)
                                btn_csv_i = wait_corto.until(EC.element_to_be_clickable((By.XPATH, xpath_csv_btn)))
                                btn_csv_i.click()
                                time.sleep(10)

                            print("✅ Descarga de todos los periodos completada.")
                        else:
                            btn_csv_actual = wait_corto.until(EC.element_to_be_clickable((By.XPATH, xpath_csv_btn)))
                            print("Descargando archivo CSV del corte actual...")
                            btn_csv_actual.click()
                            time.sleep(5)

                            periodo_dropdown = wait.until(EC.element_to_be_clickable((By.XPATH, "//input[@placeholder='Periodo']/preceding-sibling::div[@role='button']")))
                            texto_periodo = periodo_dropdown.text
                            print(f"Periodo detectado: {texto_periodo}")

                            match_mes_completo = re.search(r"DEL\s+01\b", texto_periodo, re.IGNORECASE)

                            if match_mes_completo:
                                print("✅ Es un mes calendario completo. No se necesita descargar el corte anterior.")
                                time.sleep(5)
                            else:
                                print("\nCiclo desfasado detectado. Abriendo el menú desplegable para el corte anterior...")
                                periodo_dropdown.click()
                                time.sleep(1.5)
                                opciones_periodo = wait.until(EC.presence_of_all_elements_located((By.XPATH, "//li[@role='option']")))
                                if len(opciones_periodo) >= 2:
                                    print("Seleccionando el corte del mes anterior cerrado...")
                                    opciones_periodo[1].click()
                                    time.sleep(6)
                                    btn_csv_anterior = wait_corto.until(EC.element_to_be_clickable((By.XPATH, xpath_csv_btn)))
                                    print("Descargando archivo CSV del corte anterior...")
                                    btn_csv_anterior.click()
                                    print("\nEsperando 15 segundos para asegurar que ambos archivos se terminen de descargar...")
                                    time.sleep(15)
                                else:
                                    print("No hay suficientes periodos en el historial para descargar uno anterior.")

                    print("✅ Descargas completadas para esta empresa.")

                    # === INGESTA A BIGQUERY ===
                    from bigquery import bq_ingestion
                    time.sleep(3)
                    archivos = os.listdir(descargas_dir)
                    archivos_csv = [os.path.join(descargas_dir, f) for f in archivos if f.endswith('.csv')]

                    if archivos_csv:
                        print(f"🚀 Se encontraron {len(archivos_csv)} archivo(s) CSV. Mandando a la aduana de BigQuery...")
                        for archivo in archivos_csv:
                            empresa_archivo = empresa_limpia
                            numero_cliente_archivo = _extraer_numero_cliente_archivo(archivo)
                            if empresa_archivo == "sin_empresa" and numero_cliente_archivo:
                                empresa_archivo = mapa_clientes["by_number"].get(numero_cliente_archivo, "sin_empresa")
                                if empresa_archivo != "sin_empresa":
                                    print(
                                        f"🔁 Empresa inferida desde mapa local para cliente "
                                        f"{numero_cliente_archivo}: {empresa_archivo}"
                                    )
                            try:
                                df_limpio = bq_ingestion.procesar_pase(archivo, empresa=empresa_archivo)
                                if df_limpio is not None:
                                    bq_ingestion.ingest_to_bigquery(df_limpio)
                            except Exception as e:
                                print(f"❌ Error durante la ingesta a BQ de {archivo}: {e}")
                            finally:
                                respaldo_dir = os.path.join(os.getcwd(), "respaldo_descargas")
                                import sys
                                # Add project root to path so we can import gcs_uploader
                                raiz_proyecto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                                if raiz_proyecto not in sys.path:
                                    sys.path.append(raiz_proyecto)
                                import gcs_uploader
                                year_override = None
                                month_override = None
                                archivo_base = os.path.basename(archivo)
                                codigo_archivo = re.search(r"\.(\d+)\.csv$", archivo_base, re.IGNORECASE)
                                if codigo_archivo and codigo_archivo.group(1) in periodo_por_archivo:
                                    year_override, month_override = periodo_por_archivo[codigo_archivo.group(1)]
                                elif backfill_mode and meses_objetivo and len(set(meses_objetivo)) == 1:
                                    year_override, month_override = meses_objetivo[0]

                                gcs_uploader.subir_y_borrar_local(
                                    archivo,
                                    'Pase',
                                    empresa=empresa_archivo,
                                    year=year_override,
                                    month=month_override,
                                )
                    else:
                        print("⚠️ No se encontraron archivos CSV en la carpeta temporal.")

                except Exception as e:
                    import traceback
                    print(f"⚠️ Error en descargas/ingesta: {e}")
                    traceback.print_exc()
                
                # --- 6. Cerrar Sesión ---
                print("\nCerrando sesión para continuar con la siguiente empresa...")
                time.sleep(1.5)  # Esperar que desaparezcan overlays/backdrops de MUI
                menu_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@aria-label='More']")))
                driver.execute_script("arguments[0].click();", menu_btn)
                time.sleep(1)
                
                # Clic en Cerrar Sesión
                logout_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'Cerrar Sesión')]")))
                logout_btn.click()
                time.sleep(1)
                
                print("Confirmando el cierre de sesión en la ventana emergente...")
                confirmar_logout_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[span[translate(text(), 'ACEPTAR', 'aceptar')='aceptar']]")))
                confirmar_logout_btn.click()
                
                print("Esperando volver a la pantalla de inicio de sesión...")
                wait.until(EC.url_contains("apps.pase.com.mx/uc"))
                time.sleep(3)
                
            else:
                print("No se encontraron clientes o se alcanzó el límite.")
            
            indice_actual += 1
            
    except Exception as e:
        print(f"Ocurrió un error en el flujo: {type(e).__name__} - {e}")
    finally:
        print("Cerrando el navegador...")
        driver.quit()

if __name__ == "__main__":
    main()
