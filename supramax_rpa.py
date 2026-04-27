import os
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
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    wait = WebDriverWait(driver, 15)
    
    url = "https://es09326.migasolinera.net/emaxclie/"
    
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
        
        # 7. Inyectar las fechas en los campos (como son readonly, usamos JavaScript)
        # Esperamos a que los campos existan en el DOM
        wait.until(EC.presence_of_element_located((By.ID, "fini")))
        driver.execute_script(f"document.getElementById('fini').value = '{fini_str}';")
        driver.execute_script(f"document.getElementById('ffin').value = '{ffin_str}';")
        
        print("Fechas configuradas exitosamente.")
        
        # 8. Hacer clic en Procesar
        print("\nHaciendo clic en 'Procesar'...")
        procesar_btn = driver.find_element(By.ID, "btn_submit")
        procesar_btn.click()
        
        # 9. Clic en 'Detalles por Venta Unitaria'
        print("\nEsperando a que cargue el resumen del reporte...")
        detalles_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'Detalles por Venta Unitaria')]")))
        print("Haciendo clic en 'Detalles por Venta Unitaria'...")
        detalles_btn.click()
        
        # 10. Descargar XLS de 'Todos los consumos'
        print("\nEsperando a que cargue la tabla detallada de consumos...")
        # Usamos un XPath que busque el input de imagen dentro de la fila que dice 'Todos los consumos.'
        descargar_xls_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//tr[td[contains(normalize-space(text()), 'Todos los consumos.')]]//input[@type='image']")))
        print("Haciendo clic en el botón de Excel de 'Todos los consumos'...")
        descargar_xls_btn.click()
        
        # 11. Esperar a que la descarga se inicie/complete
        print("\nDescarga iniciada. Esperando 15 segundos para asegurar que el archivo se termine de descargar...")
        time.sleep(15)
        
        print(f"✅ Proceso de descarga completado para {username}.")
        
    except Exception as e:
        print(f"Ocurrió un error con la cuenta {username}: {e}")
    finally:
        print(f"Cerrando sesión/navegador para {username}...")
        driver.quit()

def main():
    print("Iniciando RPA para Supramax...")
    
    # Para trabajar la prueba con 1 empresa, leeremos temporalmente estas variables.
    # En el futuro, reemplazaremos esto para que lea las 15 credenciales desde un CSV o archivo JSON.
    test_user = os.getenv('SUPRAMAX_TEST_USER_ABA')
    test_pass = os.getenv('SUPRAMAX_TEST_PASS_ABA')
    
    if not test_user or not test_pass:
        print("ERROR: Por favor agrega SUPRAMAX_TEST_USER y SUPRAMAX_TEST_PASS a tu archivo .env")
        return
        
    accounts_to_process = [
        {"username": test_user, "password": test_pass}
    ]
    
    # Iterar por cada cuenta (por ahora solo 1, pero listo para 15)
    for account in accounts_to_process:
        process_account(account['username'], account['password'])
        
    print("\nProceso global de Supramax finalizado.")

if __name__ == "__main__":
    main()
