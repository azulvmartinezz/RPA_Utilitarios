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
from webdriver_manager.chrome import ChromeDriverManager

# Cargar variables de entorno
load_dotenv()

def process_account(username, password):
    print(f"\n--- Iniciando proceso para la cuenta: {username} ---")
    
    chrome_options = Options()
    # Descomentar para modo silencioso (headless) una vez que esté terminado y probado
    # chrome_options.add_argument("--headless") 
    
    # Forzar descargas a una carpeta controlada para poder leer el archivo
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
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    wait = WebDriverWait(driver, 15)
    
    url = os.getenv('SUPRAMAX_URL')
    
    try:
        print(f"Navegando a: {url}")
        driver.get(url)
        
        # 1. Esperar bloque de inicio de sesión
        print("Esperando elementos de inicio de sesión...")
        
        # 2. Llenar usuario y contraseña
        print("Ingresando credenciales...")
        user_input = wait.until(EC.presence_of_element_located((By.ID, "username")))
        user_input.clear()
        user_input.send_keys(username)
        
        pwd_input = driver.find_element(By.ID, "password")
        pwd_input.clear()
        pwd_input.send_keys(password)
        
        # 3. Hacer clic en Entrar
        print("Haciendo clic en Entrar...")
        login_btn = driver.find_element(By.ID, "login")
        login_btn.click()
        
        # 4. Navegar a la pestaña 'Reportes'
        print("\nEsperando a que cargue la pantalla principal...")
        reportes_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[@title='Reportes']")))
        print("Haciendo clic en 'Reportes'...")
        reportes_btn.click()
        
        # 5. Clic en 'Reporte de consumos'
        print("Esperando la lista de reportes...")
        reporte_consumos_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[@title='Reporte de consumos']")))
        print("Haciendo clic en 'Reporte de consumos'...")
        reporte_consumos_btn.click()
        
        # 6. Calcular fechas del mes anterior
        today = datetime.date.today()
        first_day_this_month = today.replace(day=1)
        last_day_prev_month = first_day_this_month - datetime.timedelta(days=1)
        first_day_prev_month = last_day_prev_month.replace(day=1)
        
        fini_str = first_day_prev_month.strftime("%d/%m/%Y")
        ffin_str = last_day_prev_month.strftime("%d/%m/%Y")
        
        print(f"\nConfigurando fechas del mes anterior: {fini_str} al {ffin_str}")
        
        # 7. Inyectar las fechas y disparar eventos para que la página registre el cambio
        # Esperamos a que los campos existan en el DOM
        wait.until(EC.presence_of_element_located((By.ID, "fini")))
        
        js_code = """
        var e_fini = document.getElementById('fini');
        e_fini.value = arguments[0];
        e_fini.dispatchEvent(new Event('input', { bubbles: true }));
        e_fini.dispatchEvent(new Event('change', { bubbles: true }));
        e_fini.dispatchEvent(new Event('blur', { bubbles: true }));
        
        var e_ffin = document.getElementById('ffin');
        e_ffin.value = arguments[1];
        e_ffin.dispatchEvent(new Event('input', { bubbles: true }));
        e_ffin.dispatchEvent(new Event('change', { bubbles: true }));
        e_ffin.dispatchEvent(new Event('blur', { bubbles: true }));
        """
        driver.execute_script(js_code, fini_str, ffin_str)
        time.sleep(1) # Pausa para que el JS de la página asimile el cambio
        
        print("Fechas configuradas exitosamente.")
        
        # 8. Hacer clic en Procesar
        print("\nHaciendo clic en 'Procesar'...")
        procesar_btn = driver.find_element(By.ID, "btn_submit")
        procesar_btn.click()
        
        # 9. Clic en 'Detalles por Venta Unitaria'
        print("\nEsperando a que cargue el resumen del reporte (puede demorar en procesar la base de datos)...")
        long_wait = WebDriverWait(driver, 120) # Aumentamos la espera a 2 minutos
        
        # Esperar a que aparezca el botón de Detalles O el texto de "No se encontraron registros"
        long_wait.until(
            lambda d: d.find_elements(By.XPATH, "//a[contains(text(), 'Detalles por Venta Unitaria')]") or 
                      d.find_elements(By.XPATH, "//td[contains(text(), 'No se encontraron registros')]")
        )
        
        # Verificar qué apareció en pantalla
        if driver.find_elements(By.XPATH, "//td[contains(text(), 'No se encontraron registros')]"):
            print("⚠️ No se encontraron registros de consumo para este periodo. Saltando descarga...")
        else:
            detalles_btn = driver.find_element(By.XPATH, "//a[contains(text(), 'Detalles por Venta Unitaria')]")
            print("Haciendo clic en 'Detalles por Venta Unitaria'...")
            detalles_btn.click()
            
            # 10. Descargar XLS de 'Todos los consumos'
            print("\nEsperando a que cargue la tabla detallada de consumos...")
            # Usamos un XPath que busque el input de imagen dentro de la fila que dice 'Todos los consumos.'
            descargar_xls_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//tr[td[contains(normalize-space(text()), 'Todos los consumos.')]]//input[@type='image']")))
            print("Haciendo clic en el botón de Excel de 'Todos los consumos'...")
            descargar_xls_btn.click()
            
            # 11. Esperar a que la descarga se inicie/complete
            print("\nDescarga iniciada. Esperando a que aterrice en la carpeta de descargas temporales...")
            
            tiempo_espera = 0
            archivo_descargado = None
            while tiempo_espera < 45: # Esperamos hasta 45 segundos por si el archivo es pesado
                archivos = os.listdir(descargas_dir)
                archivos_xls = [f for f in archivos if f.endswith('.xls')]
                archivos_temp = [f for f in archivos if f.endswith('.crdownload') or f.endswith('.tmp')]
                
                if archivos_xls and not archivos_temp:
                    archivo_descargado = os.path.join(descargas_dir, archivos_xls[0])
                    break
                time.sleep(1)
                tiempo_espera += 1
                
            if archivo_descargado:
                print(f"✅ Archivo detectado: {archivo_descargado}")
                print("🚀 Mandando a la aduana de BigQuery...")
                from bigquery import bq_ingestion
                try:
                    df_limpio = bq_ingestion.procesar_supramax(archivo_descargado)
                    # Opcional: si la función te devuelve None porque falló, detenemos
                    if df_limpio is not None:
                        bq_ingestion.ingest_to_bigquery(df_limpio)
                except Exception as e:
                    print(f"❌ Error durante la limpieza o ingesta a BQ: {e}")
                finally:
                    # Siempre limpiamos la carpeta para que la siguiente empresa inicie con la carpeta vacía
                    os.remove(archivo_descargado)
            else:
                print("⚠️ Tiempo de espera agotado. No se detectó el archivo en la carpeta.")
        
        # Clic en SALIR
        print("Cerrando sesión (Clic en SALIR)...")
        try:
            # Asegurarnos de volver al frame principal si es que estamos en algún iframe,
            # aunque de base el botón parece estar en el top (target="_top").
            driver.switch_to.default_content() 
            salir_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'SALIR') or contains(@href, 'index.php?lg=1')]")))
            salir_btn.click()
            time.sleep(2)
        except Exception as e:
            print(f"⚠️ No se pudo hacer clic en SALIR, cerrando el navegador igualmente... ({e})")
            
        print(f"✅ Proceso de descarga completado para {username}.")
        
    except Exception as e:
        print(f"Ocurrió un error con la cuenta {username}: {e}")
    finally:
        print(f"Cerrando sesión/navegador para {username}...")
        driver.quit()

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
        print(f"🔄 PROCESANDO EMPRESA {idx + 1} DE {len(credenciales)}: {acc['Empresa']}")
        print(f"{'='*50}")
        process_account(acc['Usuario'], acc['Contraseña'])
        
    print("\n✅ Proceso global de Supramax finalizado.")

if __name__ == "__main__":
    main()
