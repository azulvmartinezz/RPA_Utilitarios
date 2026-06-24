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
from selenium.common.exceptions import TimeoutException, WebDriverException
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

def _scrape_active_vehicles(driver, wait):
    """Navega a la lista de vehículos activos, descarga la versión XLS y parsea ECO -> Tag."""
    print("Navegando a la pestaña de Vehículos Activos...")
    try:
        base_url = os.getenv('SUPRAMAX_URL', 'https://es09326.migasolinera.net/emaxclie/')
        if not base_url.endswith('/'):
            base_url += '/'
        target_url = f"{base_url}intmain.php?nd=1&ad=veh&m=sveh&act=1"
        driver.get(target_url)
        time.sleep(3)
    except Exception as e:
        print(f"  ❌ No se pudo navegar a la lista de vehículos activos: {e}")
        return {}

    descargas_dir = os.path.join(os.getcwd(), "descargas_temporales")
    pre_files = set(os.listdir(descargas_dir))

    try:
        xls_btn_xpath = "//input[@type='image' and contains(@src, 'xls')]"
        xls_btn = wait.until(EC.element_to_be_clickable((By.XPATH, xls_btn_xpath)))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", xls_btn)
        time.sleep(0.5)
        xls_btn.click()
        print("  ✅ Clic en botón Versión XLS exitosamente.")
        time.sleep(12)
    except Exception as e:
        print(f"  ❌ Error al intentar dar clic en exportar XLS de Supramax: {e}")
        return {}

    post_files = set(os.listdir(descargas_dir))
    new_files = post_files - pre_files
    xls_downloaded = [f for f in new_files if f.endswith('.xls')]
    
    if not xls_downloaded:
        print("  ❌ No se encontró ningún archivo XLS nuevo descargado.")
        return {}
        
    downloaded_file = os.path.join(descargas_dir, xls_downloaded[0])
    print(f"  ✅ Archivo de vehículos activos Supramax descargado: {downloaded_file}")

    tags_map = {}
    try:
        import pandas as pd
        import re
        try:
            dfs = pd.read_html(downloaded_file)
            df = dfs[0]
        except Exception:
            df = pd.read_excel(downloaded_file)
            
        df.columns = [str(c).strip() for c in df.columns]
        placas_col = next((c for c in df.columns if 'placa' in c.lower()), None)
        tag_col = next((c for c in df.columns if any(kw in c.lower() for kw in ['tag', 'identificador', 'tarjeta'])), None)
        
        if not (placas_col and tag_col):
            # Intentar encontrar una fila de encabezado en las primeras 10 filas
            for idx in range(min(10, len(df))):
                row_vals = [str(val).strip().lower() for val in df.iloc[idx]]
                has_placa = any('placa' in val for val in row_vals)
                has_tag = any(any(kw in val for kw in ['tag', 'identificador', 'tarjeta']) for val in row_vals)
                if has_placa and has_tag:
                    df.columns = [str(val).strip() for val in df.iloc[idx]]
                    df = df.iloc[idx+1:]
                    placas_col = next((c for c in df.columns if 'placa' in c.lower()), None)
                    tag_col = next((c for c in df.columns if any(kw in c.lower() for kw in ['tag', 'identificador', 'tarjeta'])), None)
                    break
        
        if not (placas_col and tag_col) and len(df.columns) >= 3:
            # En Supramax, típicamente la columna 1 (0-indexed) es Placas y el Tag/Tarjeta es la última columna
            placas_col = df.columns[1]
            tag_col = df.columns[-1]
            print(f"  ⚠️ Columnas de Placa/Tag no identificadas por nombre. Usando columnas por posición: Placa={placas_col}, Tag={tag_col}")

        if placas_col and tag_col:
            for _, row in df.iterrows():
                placas_val = str(row[placas_col]).strip().upper().replace(" ", "").replace(".", "")
                tag_val = str(row[tag_col]).strip()
                
                m = re.match(r"^(AU|CA|PRE)-?(\d{1,3})(?!\d)", placas_val)
                if m and tag_val and tag_val != "nan" and tag_val != "":
                    # Normalizar a prefijo AU o CA. Si es PRE, se mantiene o se clasifica
                    prefijo = m.group(1)
                    eco_norm = f"{prefijo}-{m.group(2).zfill(3)}"
                    tags_map[eco_norm] = tag_val
            print(f"  ✅ Se extrajeron {len(tags_map)} tags de Supramax válidos desde el archivo.")
        else:
            print(f"  ❌ No se encontraron las columnas de Placas/Tag en el archivo. Columnas: {list(df.columns)}")
    except Exception as e:
        print(f"  ❌ Error al procesar el archivo XLS de Supramax: {e}")
    finally:
        try:
            os.remove(downloaded_file)
        except:
            pass

    return tags_map


def process_account(username, password, fini_override=None, ffin_override=None, meses_override=None, meses_meta=None, empresa=None, only_tags=False, with_tags=False):
    print(f"\n--- Iniciando proceso para cuenta Supramax: {username} ---")

    chrome_options = Options()
    chrome_options.page_load_strategy = 'eager'
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
    
    # Evitar HTTP read timeouts (Selenium default es 120s) al procesar reportes pesados
    if hasattr(driver, "command_executor") and hasattr(driver.command_executor, "_client_config"):
        driver.command_executor._client_config.timeout = 310

    driver.set_page_load_timeout(180)   # Evitar quedarse trabado indefinidamente si el navegador espera elementos secundarios
    driver.set_script_timeout(180)
    wait = WebDriverWait(driver, 15)
    long_wait = WebDriverWait(driver, 180)

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
        try:
            driver.get(url)
        except TimeoutException:
            print("  ⚠️ La carga inicial de la página web de Supramax tardó más de 180s. Deteniendo carga con window.stop() para intentar login...")
            try:
                driver.execute_script("window.stop();")
            except:
                pass

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
        try:
            login_exitoso = _intentar_submit_login(driver, wait, pwd_input)
        except TimeoutException:
            print("  ⚠️ No se encontró el formulario de login a tiempo.")
            raise

        # Esperar pantalla principal (busca menú Reportes por title o por texto)
        REPORTES_XPATH = "//a[@title='Reportes'] | //a[normalize-space(text())='Reportes'] | //input[@value='Reportes']"
        try:
            WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.XPATH, REPORTES_XPATH)))
        except TimeoutException:
            if _esta_en_login(driver):
                print(f"❌ El formulario de login siguió visible después del envío. Se omite esta cuenta.")
            else:
                _guardar_diagnostico(driver, descargas_dir, username, "login")
            return

        scraped_tags = {}
        if only_tags or with_tags:
            try:
                scraped_tags = _scrape_active_vehicles(driver, wait)
            except Exception as e:
                print(f"❌ Error al raspar vehículos activos: {e}")

        if not only_tags:
            for fini, ffin in meses:
                print(f"\n[Mes {meses.index((fini, ffin))+1}/{len(meses)}] {fini} → {ffin}")
                
                # Dar un respiro al servidor de 1.5s antes de procesar para evitar sobrecargarlo
                time.sleep(1.5)
                
                try:
                    # Navegación a reportes
                    try:
                        wait.until(EC.element_to_be_clickable((By.XPATH, REPORTES_XPATH))).click()
                    except TimeoutException:
                        print("  ⚠️ El click en menú Reportes excedió el timeout. Forzando window.stop()...")
                        try:
                            driver.execute_script("window.stop();")
                        except:
                            pass

                    try:
                        wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'Reporte de consumos')]"))).click()
                    except TimeoutException:
                        print("  ⚠️ El click en Reporte de consumos excedió el timeout. Forzando window.stop()...")
                        try:
                            driver.execute_script("window.stop();")
                        except:
                            pass

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

                    # Esperar a que el servidor procese el reporte. Si el renderizado del navegador o la conexión
                    # de red se queda colgada por más de 150s, saltará un TimeoutException.
                    # Detendremos la carga con window.stop() para revisar si el DOM ya tiene los elementos listos.
                    try:
                        wait_condicion = (By.XPATH, "//td[contains(text(), 'No se encontraron registros')] | //a[contains(text(), 'Detalles por Venta Unitaria')]")
                        WebDriverWait(driver, 180).until(EC.presence_of_element_located(wait_condicion))
                    except TimeoutException:
                        _stop_loading(driver, "  ⚠️ El procesamiento tardó más de 180s o se quedó pegado esperando recursos. Forzando window.stop()...")
                    except WebDriverException as e:
                        if _renderer_timeout(e):
                            _stop_loading(driver, "  ⚠️ El renderer se tardó demasiado durante el procesamiento. Forzando window.stop() para revisar el DOM actual...")
                        else:
                            raise

                    # Verificar si no se encontraron registros de manera definitiva en el DOM actual
                    registros_vacios = False
                    try:
                        driver.find_element(By.XPATH, "//td[contains(text(), 'No se encontraron registros')]")
                        registros_vacios = True
                    except:
                        pass

                    if registros_vacios:
                        print("⚠️ Sin registros para este periodo, saltando...")
                        continue

                    # Si hay datos, buscar el botón Detalles por Venta Unitaria con un wait corto
                    try:
                        detalles_btn = WebDriverWait(driver, 30).until(
                            EC.presence_of_element_located((By.XPATH, "//a[contains(text(), 'Detalles por Venta Unitaria')]"))
                        )
                    except TimeoutException:
                        print("❌ No se encontró el botón 'Detalles por Venta Unitaria' tras procesar.")
                        raise TimeoutException("No se encontró el botón 'Detalles por Venta Unitaria'")

                    # Hacer click usando JS para navegar a los detalles
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", detalles_btn)
                    time.sleep(0.3)
                    try:
                        driver.execute_script("arguments[0].click();", detalles_btn)
                        print("  -> Click: Detalles por Venta Unitaria")
                    except TimeoutException:
                        print("  ⚠️ El click en Detalles por Venta Unitaria excedió el timeout de carga. Forzando window.stop()...")
                        try:
                            driver.execute_script("window.stop();")
                        except:
                            pass

                    # Esperar a que cargue la página de detalles y el botón de descarga XLS esté presente.
                    # Nuevamente, si se queda colgado esperando recursos secundarios (ej. imágenes pesadas), forzamos stop.
                    descargar_xls_xpath = (
                        "//tr[td[contains(normalize-space(text()), 'Todos los consumos.')]]//input[@type='image' and contains(@src, 'xls')]"
                        " | //a[img[contains(@src,'xls') or contains(@src,'excel')]]"
                        " | //a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'versión xls')]"
                        " | //a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), 'version xls')]"
                    )
                    try:
                        WebDriverWait(driver, 180).until(EC.presence_of_element_located((By.XPATH, descargar_xls_xpath)))
                    except TimeoutException:
                        _stop_loading(driver, "  ⚠️ La página de detalles tardó más de 180s o se quedó pegada. Forzando window.stop()...")
                    except WebDriverException as e:
                        if _renderer_timeout(e):
                            _stop_loading(driver, "  ⚠️ El renderer se tardó demasiado al cargar detalles. Forzando window.stop() para intentar descargar con el DOM actual...")
                        else:
                            raise

                    # Ahora que la página está estática en el navegador, hacemos scroll y hacemos click en Descargar XLS
                    time.sleep(1)
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(0.5)

                    descargar_xls_btn = wait.until(EC.element_to_be_clickable((By.XPATH, descargar_xls_xpath)))
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", descargar_xls_btn)
                    
                    # Iniciar monitoreo de descarga
                    inicio_descarga = time.time()
                    descargar_xls_btn.click()
                    print("  -> Click: Descargar XLS")

                    # Esperar descarga con timeout extendido (300s para cuentas con reportes pesados)
                    tiempo_espera = 0
                    archivo_descargado = None
                    while tiempo_espera < 300:
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
                            df_limpio = bq_ingestion.procesar_supramax(archivo_descargado, empresa=empresa or username)
                            if df_limpio is not None:
                                bq_ingestion.ingest_to_bigquery(df_limpio)
                        except Exception as e:
                            print(f"❌ Error en ingesta a BQ: {e}")
                        finally:
                            gcs_uploader.subir_y_borrar_local(archivo_descargado, 'Supramax', empresa=empresa or username)
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

    return scraped_tags if (only_tags or with_tags) else fallos


def main():
    import argparse
    parser = argparse.ArgumentParser(description="RPA para Supramax")
    parser.add_argument("--only-tags", action="store_true", help="Solo descarga y actualiza la lista de tags/vehículos activos")
    parser.add_argument("--with-tags", action="store_true", help="Descarga los reportes de consumos y actualiza la lista de tags/vehículos activos")
    
    args, unknown = parser.parse_known_args()

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
    
    all_scraped_tags = {}

    # Iterar por cada cuenta
    for idx, acc in enumerate(credenciales):
        print(f"\n{'='*50}")
        print(f"🔄 PROCESANDO CUENTA {idx + 1} DE {len(credenciales)}")
        print(f"{'='*50}")
        fallos = process_account(
            acc['Usuario'], 
            acc['Contraseña'], 
            empresa=acc.get('Empresa'),
            only_tags=args.only_tags,
            with_tags=args.with_tags
        )
        # Si process_account devuelve el mapa de tags cuando solo_tags o con_tags están activados
        if isinstance(fallos, dict):
            all_scraped_tags.update(fallos)
        
    if (args.only_tags or args.with_tags) and all_scraped_tags:
        output_dir = "HTML_SUPRAMAX"
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        output_file = os.path.join(output_dir, "tags_mapeados.json")
        
        # Mezclar con los que ya existan para no perder registros
        existing_tags = {}
        if os.path.exists(output_file):
            try:
                with open(output_file, "r", encoding="utf-8") as f:
                    existing_tags = json.load(f)
            except Exception:
                pass
        existing_tags.update(all_scraped_tags)
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(existing_tags, f, indent=4, ensure_ascii=False)
        print(f"\n✅ Se guardaron {len(existing_tags)} tags mapeados en {output_file}")
    
    print("\n✅ Proceso global de Supramax finalizado.")

if __name__ == "__main__":
    main()
