import os
import time
import datetime
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import sys

raiz_proyecto = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if raiz_proyecto not in sys.path:
    sys.path.insert(0, raiz_proyecto)
import gcs_uploader

load_dotenv()

FLEETUP_URL = os.getenv('FLEETUP_URL', 'https://online.fleetuptrace.com/')
FLEETUP_USER = os.getenv('FLEETUP_USER')
FLEETUP_PASSWORD = os.getenv('FLEETUP_PASSWORD')

def _guardar_diagnostico(driver, stage_name):
    descargas_dir = os.path.join(os.getcwd(), "descargas_temporales")
    os.makedirs(descargas_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.join(descargas_dir, f"fleetup_{stage_name}_{timestamp}")
    try:
        driver.save_screenshot(f"{base}.png")
        with open(f"{base}.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print(f"📸 Diagnóstico guardado: {os.path.basename(base)}")
    except Exception as e:
        print(f"⚠️ No se pudo guardar diagnóstico ({e})")

def main():
    print("Iniciando RPA para FleetUp...")
    
    if not FLEETUP_USER or not FLEETUP_PASSWORD:
        print("ERROR: Por favor agrega FLEETUP_USER y FLEETUP_PASSWORD a tu archivo .env")
        return

    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    
    descargas_dir = os.path.join(os.getcwd(), "descargas_temporales")
    os.makedirs(descargas_dir, exist_ok=True)
    
    # Limpiar archivos viejos
    for f in os.listdir(descargas_dir):
        if f.endswith('.xlsx') or f.endswith('.xls') or f.endswith('.crdownload') or f.endswith('.tmp'):
            try:
                os.remove(os.path.join(descargas_dir, f))
            except:
                pass

    prefs = {
        "download.default_directory": descargas_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True
    }
    chrome_options.add_experimental_option("prefs", prefs)
    
    profile_path = os.path.join(os.getcwd(), "chrome_profile", "fleetup")
    os.makedirs(profile_path, exist_ok=True)
    chrome_options.add_argument(f"--user-data-dir={profile_path}")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_window_size(1920, 1080)
    wait = WebDriverWait(driver, 25)
    
    try:
        print(f"Navegando a: {FLEETUP_URL}")
        driver.get(FLEETUP_URL)
        time.sleep(5)
        
        # 1. Login
        if "login" in driver.current_url or len(driver.find_elements(By.ID, "userId")) > 0:
            print("Realizando login...")
            user_input = wait.until(EC.element_to_be_clickable((By.ID, "userId")))
            user_input.clear()
            user_input.send_keys(FLEETUP_USER)
            
            pwd_input = wait.until(EC.element_to_be_clickable((By.ID, "password")))
            pwd_input.clear()
            pwd_input.send_keys(FLEETUP_PASSWORD)
            
            btn_signin = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@name='4' or contains(., 'Sign In')]")))
            btn_signin.click()
            time.sleep(5)
            
        print("Esperando redirección post-login...")
        try:
            print("Buscando botón de acceso (icon-home)...")
            home_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[span/i[contains(@class, 'icon-home')]]")))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", home_btn)
            time.sleep(0.5)
            driver.execute_script("arguments[0].click();", home_btn)
            print("Acceso a cuenta clicado.")
            time.sleep(5)
        except Exception as e:
            print(f"No se pudo encontrar o hacer clic en el botón de acceso de la cuenta: {e}")
            _guardar_diagnostico(driver, "error_acceso_cuenta")
            
        # 3. Navegación directa al Dashboard/Reports V4
        try:
            target_url = "https://online.fleetuptrace.com/v4/index.do#!/Dashboard%20/%20Reporte"
            print(f"Navegando directamente a: {target_url}")
            driver.get(target_url)
            time.sleep(8)
        except Exception as e:
            print(f"No se pudo navegar a Dashboard / Reporte (v4): {e}")
            _guardar_diagnostico(driver, "error_direct_navigation")
            
        # 4. Clic en la pestaña "Informe"
        try:
            print("Esperando la pestaña 'Informe'...")
            informe_tab = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'Informe')]")))
            driver.execute_script("arguments[0].click();", informe_tab)
            print("Clic en pestaña 'Informe'.")
            time.sleep(3)
        except Exception as e:
            print(f"No se pudo hacer clic en la pestaña 'Informe': {e}")
            _guardar_diagnostico(driver, "error_pestana_informe")
            
        # 5. Clic en "Generar nuevo informe"
        try:
            print("Buscando botón 'Generar nuevo informe'...")
            generar_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Generar nuevo informe') or contains(., 'Generar Nuevo')]")))
            driver.execute_script("arguments[0].click();", generar_btn)
            print("Clic en 'Generar nuevo informe'.")
            time.sleep(3)
        except Exception as e:
            print(f"No se pudo hacer clic en 'Generar nuevo informe': {e}")
            _guardar_diagnostico(driver, "error_btn_generar")
            
        # 6. Configuración del informe en el Popup
        try:
            print("Seleccionando 'Reporte de Datos de Viaje'...")
            select_elem = wait.until(EC.presence_of_element_located((By.ID, "selectedReportOpt")))
            select_report = Select(select_elem)
            select_report.select_by_visible_text("Reporte de Datos de Viaje")
            time.sleep(1.5)
            
            print("Abriendo datepicker en el modal...")
            datepicker_trigger = wait.until(EC.element_to_be_clickable((By.XPATH, "//div[contains(@class, 'popup')]//div[contains(@class, 'rangeDatepicker')]//input | //section[contains(@class, 'popups-wrp')]//div[contains(@class, 'rangeDatepicker')]//input")))
            datepicker_trigger.click()
            time.sleep(1.5)
            
            print("Seleccionando 'El mes pasado'...")
            opts = driver.find_elements(By.XPATH, "//li[@data-range-key='El mes pasado']")
            clicked = False
            for opt in opts:
                if opt.is_displayed():
                    try:
                        opt.click()
                        print("Opción 'El mes pasado' clicada con éxito.")
                        clicked = True
                        break
                    except Exception as click_err:
                        print(f"Intento de clic fallido en opción visible: {click_err}")
            
            if not clicked:
                print("Forzando clic vía JS en 'El mes pasado'...")
                driver.execute_script("arguments[0].click();", opts[0] if opts else driver.find_element(By.XPATH, "//li[@data-range-key='El mes pasado']"))
            
            time.sleep(1.5)
            
            print("Haciendo clic en 'Generar XLS'...")
            generar_xls_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Generar XLS') or span[contains(text(), 'Generar XLS')]]")))
            driver.execute_script("arguments[0].click();", generar_xls_btn)
            time.sleep(3)
            
            print("Buscando botón 'Omitir'...")
            try:
                omitir_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Omitir') or contains(text(), 'Omitir')]")))
                omitir_btn.click()
                print("Clic en 'Omitir'.")
                time.sleep(3)
            except Exception as omitir_e:
                print(f"No se detectó botón 'Omitir' o se cerró solo: {omitir_e}")
                
            print("Buscando botón 'Verificar estado'...")
            try:
                verificar_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Verificar estado')]")))
                verificar_btn.click()
                print("Clic en 'Verificar estado'.")
                time.sleep(3)
            except Exception as verificar_e:
                print(f"No se detectó botón 'Verificar estado': {verificar_e}")
                
        except Exception as e:
            print(f"Error configurando el reporte: {e}")
            _guardar_diagnostico(driver, "error_config_reporte")
            raise e
            
        # 7. Polling de descarga
        print("Iniciando ciclo de polling...")
        ready = False
        max_attempts = 40  # 40 intentos * 20s = ~13 minutos
        
        # Determinar el mes anterior
        today = datetime.date.today()
        first_day_current_month = today.replace(day=1)
        last_day_prev_month = first_day_current_month - datetime.timedelta(days=1)
        prev_year = last_day_prev_month.year
        prev_month = last_day_prev_month.month
        print(f"Periodo del reporte: Año {prev_year}, Mes {prev_month}")
        
        for attempt in range(1, max_attempts + 1):
            try:
                # Buscar primer renglón
                first_row = driver.find_element(By.XPATH, "//table[contains(@class, 'with-accordion')]/tbody/tr[contains(@ng-repeat-start, 'row in rows')][1]")
                cols = first_row.find_elements(By.TAG_NAME, "td")
                
                report_type = cols[0].text.strip()
                report_format = cols[1].text.strip()
                created_by = cols[2].text.strip()
                requested_time = cols[3].text.strip()
                
                button = cols[4].find_element(By.TAG_NAME, "button")
                status_text = button.text.strip()
                
                print(f"[Intento {attempt}/{max_attempts}] Primer reporte: {report_type} ({report_format}) solicitado a las {requested_time}. Botón: '{status_text}'")
                
                if status_text == "Descargar" and button.is_enabled():
                    print("¡Reporte listo! Haciendo clic en 'Descargar'...")
                    driver.execute_script("arguments[0].click();", button)
                    ready = True
                    break
                
            except Exception as row_err:
                print(f"Error al leer la tabla en el intento {attempt}: {row_err}")
            
            # Hacer clic en Actualizar
            try:
                refresh_btn = driver.find_element(By.XPATH, "//button[contains(., 'Actualizar') and .//i[contains(@class, 'fa-refresh')]]")
                driver.execute_script("arguments[0].click();", refresh_btn)
                print("Clic en botón 'Actualizar' realizado.")
            except Exception as ref_err:
                print(f"No se pudo hacer clic en 'Actualizar': {ref_err}")
                
            time.sleep(20)
            
        if not ready:
            print("ERROR: El reporte no estuvo listo a tiempo o no se pudo descargar.")
            _guardar_diagnostico(driver, "reporte_no_listo")
            return
            
        # Esperar a que se complete la descarga del archivo xls
        print("Esperando la descarga del archivo...")
        downloaded_file = None
        for _ in range(30):
            files = os.listdir(descargas_dir)
            xls_files = [f for f in files if (f.endswith('.xls') or f.endswith('.xlsx')) and not f.startswith('.')]
            if xls_files:
                if not any(f.endswith('.crdownload') or f.endswith('.tmp') for f in files):
                    downloaded_file = os.path.join(descargas_dir, xls_files[0])
                    print(f"Archivo descargado encontrado: {downloaded_file}")
                    break
            time.sleep(2)
            
        if not downloaded_file:
            print("ERROR: No se detectó el archivo descargado en descargas_temporales.")
            return
            
        # Subir a GCS y borrar local
        print(f"Subiendo {downloaded_file} a GCS para el periodo {prev_year}-{prev_month}...")
        gcs_uploader.subir_y_borrar_local(downloaded_file, 'FleetUp', year=prev_year, month=prev_month)
        print("¡Proceso FleetUp RPA completado con éxito!")
        
    except Exception as e:
        print(f"Error en flujo FleetUp: {e}")
        _guardar_diagnostico(driver, "error_flujo_completo")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
