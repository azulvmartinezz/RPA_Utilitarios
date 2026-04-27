import os
import time
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
        
        print("\nEsperando instrucciones para el siguiente paso...")
        time.sleep(300) # Dejar abierto para inspeccionar
        
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
