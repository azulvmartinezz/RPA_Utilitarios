import os
import json
import time
import datetime
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

import sys
raiz_proyecto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if raiz_proyecto not in sys.path:
    sys.path.insert(0, raiz_proyecto)
import gcs_uploader

# Cargar variables de entorno
load_dotenv()


def _click_js(driver, wait, locator, descripcion, timeout=20):
    element = WebDriverWait(driver, timeout).until(EC.presence_of_element_located(locator))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    time.sleep(0.3)
    WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator))
    driver.execute_script("arguments[0].click();", element)
    print(f"  -> Click: {descripcion}")
    return element


def _guardar_diagnostico(driver, descargas_dir, username, etapa):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    usuario_seguro = "".join(c for c in str(username) if c.isalnum() or c in ("-", "_"))[:30] or "cuenta"
    base = os.path.join(descargas_dir, f"supramax_{etapa}_{usuario_seguro}_{timestamp}")
    try:
        driver.save_screenshot(f"{base}.png")
        with open(f"{base}.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print(f"📸 Diagnóstico guardado: {os.path.basename(base)}")
    except Exception as e:
        print(f"⚠️ No se pudo guardar diagnóstico ({e})")


def process_account(username, password, fini_override=None, ffin_override=None, meses_override=None, meses_meta=None):
    print(f"\n--- Iniciando proceso para cuenta Supramax: {username} ---")

    chrome_options = Options()
    # chrome_options.add_argument("--headless")

    descargas_dir = os.path.join(os.getcwd(), "descargas_temporales")
    if not os.path.exists(descargas_dir):
        os.makedirs(descargas_dir)
    else:
        # Limpieza inicial para evitar archivos huérfanos
        for f in os.listdir(descargas_dir):
            if f.endswith('.xls') or f.endswith('.crdownload') or f.endswith('.tmp'):
                try: os.remove(os.path.join(descargas_dir, f))
                except: pass

    prefs = {
        "download.default_directory": descargas_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    chrome_options.add_experimental_option("prefs", prefs)

    import sys
    if not hasattr(sys, '_chromedriver_path'):
        sys._chromedriver_path = ChromeDriverManager().install()
    service = Service(sys._chromedriver_path)
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_page_load_timeout(300)   # 5 min para cuentas con reportes pesados
    driver.set_script_timeout(300)
    wait = WebDriverWait(driver, 15)
    long_wait = WebDriverWait(driver, 300)

    url = os.getenv('SUPRAMAX_URL')

    # Determinar la lista de (fini, ffin) a descargar en esta sesión
    if meses_override:
        meses = meses_override
    elif fini_override and ffin_override:
        meses = [(fini_override, ffin_override)]
    else:
        today = datetime.date.today()
        first_day_this_month = today.replace(day=1)
        last_day_prev_month = first_day_this_month - datetime.timedelta(days=1)
        first_day_prev_month = last_day_prev_month.replace(day=1)
        meses = [(first_day_prev_month.strftime("%d/%m/%Y"),
                  last_day_prev_month.strftime("%d/%m/%Y"))]

    fallos = []  # meses que no se pudieron procesar

    try:
        driver.get(url)

        # Login Robusto
        print("Ingresando credenciales...")
        user_input = wait.until(EC.element_to_be_clickable((By.ID, "username")))
        user_input.click()
        user_input.clear()
        time.sleep(0.5)
        user_input.send_keys(username)
        
        pwd_input = wait.until(EC.element_to_be_clickable((By.ID, "password")))
        pwd_input.click()
        pwd_input.clear()
        time.sleep(0.5)
        pwd_input.send_keys(password)
        
        time.sleep(0.5)
        driver.find_element(By.ID, "login").click()

        # Esperar pantalla principal (busca menú Reportes por title o por texto)
        REPORTES_XPATH = "//a[@title='Reportes'] | //a[normalize-space(text())='Reportes'] | //input[@value='Reportes']"
        try:
            WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.XPATH, REPORTES_XPATH)))
        except TimeoutException:
            print(f"❌ No apareció el menú 'Reportes' después del login. Se omite esta cuenta.")
            _guardar_diagnostico(driver, descargas_dir, username, "login")
            return

        for fini, ffin in meses:
            print(f"\n[Mes {meses.index((fini, ffin))+1}/{len(meses)}] {fini} → {ffin}")
            
            try:
                # Navegación a reportes
                wait.until(EC.element_to_be_clickable((By.XPATH, REPORTES_XPATH))).click()
                wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'Reporte de consumos')]"))).click()

                # Inyectar fechas via JS (los campos tienen date-picker y son read-only)
                wait.until(EC.presence_of_element_located((By.ID, "fini")))
                driver.execute_script("""
                    var fi = document.getElementById('fini');
                    fi.removeAttribute('readonly');
                    fi.value = arguments[0];
                    fi.dispatchEvent(new Event('change', {bubbles: true}));
                    fi.dispatchEvent(new Event('blur', {bubbles: true}));
                    var ff = document.getElementById('ffin');
                    ff.removeAttribute('readonly');
                    ff.value = arguments[1];
                    ff.dispatchEvent(new Event('change', {bubbles: true}));
                    ff.dispatchEvent(new Event('blur', {bubbles: true}));
                """, fini, ffin)
                time.sleep(0.5)

                _click_js(driver, wait, (By.ID, "btn_submit"), "Procesar")

                # Verificar si hay datos
                try:
                    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, "//td[contains(text(), 'No se encontraron registros')]")))
                    print("⚠️ Sin registros para este periodo, saltando...")
                    continue
                except:
                    pass

                # Si hay datos, descargar
                _click_js(driver, wait, (By.XPATH, "//a[contains(text(), 'Detalles por Venta Unitaria')]"), "Detalles por Venta Unitaria")

                # Esperar a que cargue la página de detalles y hacer scroll al fondo
                time.sleep(2)
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(0.5)

                descargar_xls_btn = wait.until(EC.element_to_be_clickable(
                    (By.XPATH,
                     "//tr[td[contains(normalize-space(text()), 'Todos los consumos.')]]//input[@type='image']"
                     " | //a[img[contains(@src,'xls') or contains(@src,'excel')]]"
                     " | //a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'versión xls')]"
                     " | //a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'version xls')]"
                    )))
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", descargar_xls_btn)
                
                # Iniciar monitoreo de descarga
                inicio_descarga = time.time()
                descargar_xls_btn.click()
                print("  -> Click: Descargar XLS")

                # Esperar descarga con timeout extendido (180s para cuentas con reportes pesados)
                tiempo_espera = 0
                archivo_descargado = None
                while tiempo_espera < 180:
                    archivos = os.listdir(descargas_dir)
                    archivos_xls = [
                        os.path.join(descargas_dir, f) 
                        for f in archivos 
                        if f.endswith('.xls') and os.path.getmtime(os.path.join(descargas_dir, f)) >= (inicio_descarga - 2)
                    ]
                    archivos_temp = [f for f in archivos if f.endswith('.crdownload') or f.endswith('.tmp')]
                    
                    if archivos_xls and not archivos_temp:
                        archivo_descargado = max(archivos_xls, key=os.path.getmtime)
                        break
                    time.sleep(1)
                    tiempo_espera += 1

                if archivo_descargado:
                    print(f"✅ Archivo descargado. Subiendo a BigQuery...")
                    from bigquery import bq_ingestion
                    try:
                        df_limpio = bq_ingestion.procesar_supramax(archivo_descargado)
                        if df_limpio is not None:
                            bq_ingestion.ingest_to_bigquery(df_limpio)
                    except Exception as e:
                        print(f"❌ Error en ingesta a BQ: {e}")
                    finally:
                        gcs_uploader.subir_y_borrar_local(archivo_descargado, 'Supramax')
                else:
                    print("⚠️ Tiempo de espera agotado. No se detectó el archivo.")

            except Exception as e:
                if "invalid session id" in str(e).lower():
                    print(f"🛑 Sesión de navegador perdida. Abortando meses restantes de {username}.")
                    raise e
                print(f"⚠️ Error procesando mes {fini}: {e}")
                _guardar_diagnostico(driver, descargas_dir, username, "error_mes")
                # Registrar el fallo con su tupla (year, month)
                idx = meses.index((fini, ffin))
                if meses_meta and idx < len(meses_meta):
                    fallos.append(meses_meta[idx])

        # Cerrar sesión
        try:
            driver.switch_to.default_content()
            wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//a[contains(text(), 'SALIR') or contains(@href, 'index.php?lg=1')]"))).click()
            time.sleep(2)
        except:
            pass

        print(f"✅ Proceso completado para cuenta {username}.")

    except Exception as e:
        print(f"❌ Ocurrió un error crítico en la cuenta {username}: {e}")
        try: _guardar_diagnostico(driver, descargas_dir, username, "error_critico")
        except: pass
    finally:
        try: driver.quit()
        except: pass

    return fallos


def main():
    print("Iniciando RPA para Supramax...")
    
    credenciales_str = os.getenv('SUPRAMAX_CREDENTIALS')
    
    if not credenciales_str:
        print("ERROR: No se encontró la variable SUPRAMAX_CREDENTIALS en tu .env")
        return
        
    try:
        credenciales = json.loads(credenciales_str)
    except Exception as e:
        print(f"ERROR: El contenido de SUPRAMAX_CREDENTIALS no es un JSON válido. ({e})")
        return
    
    # Iterar por cada cuenta
    for idx, acc in enumerate(credenciales):
        print(f"\n{'='*50}")
        print(f"🔄 PROCESANDO CUENTA {idx + 1} DE {len(credenciales)}")
        print(f"{'='*50}")
        process_account(acc['Usuario'], acc['Contraseña'])
        
    print("\n✅ Proceso global de Supramax finalizado.")

if __name__ == "__main__":
    main()
