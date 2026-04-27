import os
import time
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from twocaptcha import TwoCaptcha
from webdriver_manager.chrome import ChromeDriverManager

# Cargar variables de entorno
load_dotenv()

PASE_USER = os.getenv('PASE_USER')
PASE_PASSWORD = os.getenv('PASE_PASSWORD')
TWOCAPTCHA_API_KEY = os.getenv('TWOCAPTCHA_API_KEY')

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

def main():
    print("Iniciando RPA para Pase...")
    
    if not TWOCAPTCHA_API_KEY:
        print("ERROR: Por favor agrega tu TWOCAPTCHA_API_KEY al archivo .env")
        return
        
    if not PASE_USER or not PASE_PASSWORD:
        print("ERROR: Faltan credenciales PASE_USER o PASE_PASSWORD en el archivo .env")
        print("Asegúrate de tener PASE_USER=... y PASE_PASSWORD=... configurados.")
        return

    chrome_options = Options()
    # Ejecutando con Google Chrome
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    wait = WebDriverWait(driver, 15)
    
    url = "https://apps.pase.com.mx/uc/"
    
    try:
        print(f"Navegando a: {url}")
        driver.get(url)
        
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
                    # Insertar en el textarea oculto
                    driver.execute_script(f"document.getElementById('g-recaptcha-response').innerHTML = '{token}';")
                    time.sleep(1)
                    
                    # Dato vital: Pase requiere que ejecutemos el callback del captcha
                    print("Ejecutando el callback onRecaptchaValid...")
                    driver.execute_script(f"if(typeof onRecaptchaValid !== 'undefined') {{ onRecaptchaValid('{token}'); }}")
                    time.sleep(2)
            else:
                print("No se encontró el data-sitekey en el widget.")
        except Exception as e:
            print(f"Ocurrió un error con ReCaptcha: {e}")
            
        # 3. Clic en Entrar
        print("Haciendo clic en Entrar...")
        # El botón no tiene ID fácil, lo ubicamos por el span que dice 'Entrar'
        continue_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[span[text()='Entrar']]")))
        continue_btn.click()
        
        # Esperar en el dashboard
        print("\nEsperando en la pantalla principal (dashboard)...")
        time.sleep(300) # Dejar abierto para inspeccionar
        
    except Exception as e:
        print(f"Ocurrió un error en el flujo: {type(e).__name__} - {e}")
    finally:
        print("Cerrando el navegador...")
        driver.quit()

if __name__ == "__main__":
    main()
