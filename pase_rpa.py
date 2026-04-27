import os
import time
from dotenv import load_dotenv
from selenium import webdriver
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

    # Configuración Anti-Detección usando undetected_chromedriver
    chrome_options = uc.ChromeOptions()
    
    # Crear una carpeta local para guardar las cookies y el historial (Perfil Persistente)
    profile_path = os.path.join(os.getcwd(), "chrome_profile")
    
    # Ejecutando con undetected-chromedriver para saltar Radware WAF
    # Fijamos la versión 147 para que coincida con tu navegador instalado
    driver = uc.Chrome(options=chrome_options, user_data_dir=profile_path, version_main=147)
    
    wait = WebDriverWait(driver, 15)
    
    url = "https://apps.pase.com.mx/uc/"
    
    try:
        print(f"Navegando a: {url}")
        driver.get(url)
        time.sleep(5)
        
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
        
        # Esperar a que cargue la pantalla de selección de cliente
        print("\nEsperando la pantalla de selección de cliente...")
        
        # El desplegable es un div personalizado de Material-UI. Lo buscamos a través de su input oculto.
        dropdown_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//input[@name='cliente']/preceding-sibling::div[@role='button']")))
        print("Abriendo el menú desplegable...")
        dropdown_btn.click()
        
        # Esperar a que se despliegue la lista flotante
        time.sleep(1)
        
        # Localizar todas las opciones (li con role='option')
        opciones = wait.until(EC.presence_of_all_elements_located((By.XPATH, "//li[@role='option']")))
        
        if opciones:
            print(f"Se encontraron {len(opciones)} clientes. Seleccionando el primero...")
            opciones[0].click()
            time.sleep(1) # Pausa para que se registre la selección
            
            # Clic en el botón Continuar
            print("Haciendo clic en 'Continuar'...")
            btn_continuar = driver.find_element(By.XPATH, "//button[span[contains(text(), 'Continuar')]]")
            btn_continuar.click()
            
            print("\n¡Bienvenido al panel principal de Pase!")
            
            # --- 5. Descarga de Reportes ---
            print("\nIniciando descarga de reportes de consumo...")
            
            # El XPath asegura buscar el botón de CSV específicamente en la tarjeta de "DET. CRUCES (NUEVO)"
            xpath_csv_btn = "//h6[contains(text(), 'DET. CRUCES (NUEVO)')]/ancestor::div[3]//a[@title='Descargar archivo separado por comas']"
            
            # Al entrar, el sistema carga automáticamente el corte más reciente (índice 0)
            print("Esperando botón de descarga del corte actual (Mes más reciente)...")
            btn_csv_actual = wait.until(EC.element_to_be_clickable((By.XPATH, xpath_csv_btn)))
            print("Descargando archivo CSV del corte actual...")
            btn_csv_actual.click()
            time.sleep(5) # Dar tiempo para que el navegador intercepte e inicie la descarga
            
            # Ahora seleccionamos el corte anterior (índice 1) para tener el mes completo
            print("\nBuscando el menú desplegable de 'Periodo'...")
            periodo_dropdown = wait.until(EC.element_to_be_clickable((By.XPATH, "//input[@placeholder='Periodo']/preceding-sibling::div[@role='button']")))
            periodo_dropdown.click()
            time.sleep(1.5)
            
            # Localizar las opciones del periodo que flotan en pantalla
            opciones_periodo = wait.until(EC.presence_of_all_elements_located((By.XPATH, "//li[@role='option']")))
            if len(opciones_periodo) >= 2:
                print("Seleccionando el corte del mes anterior cerrado...")
                opciones_periodo[1].click()
                
                print("Esperando a que la página actualice la información del nuevo periodo...")
                time.sleep(6) # Pausa para que la página haga el Request y actualice los botones de descarga
                
                # Volver a ubicar el botón porque la página reconstruyó los elementos (DOM)
                btn_csv_anterior = wait.until(EC.element_to_be_clickable((By.XPATH, xpath_csv_btn)))
                print("Descargando archivo CSV del corte anterior...")
                btn_csv_anterior.click()
                
                print("\nEsperando 15 segundos para asegurar que ambos archivos se terminen de descargar...")
                time.sleep(15)
            else:
                print("No hay suficientes periodos en el historial para descargar uno anterior.")
                
            print("✅ ¡Proceso de PASE completado exitosamente!")
            
        else:
            print("No se encontraron clientes en el menú desplegable.")
        
    except Exception as e:
        print(f"Ocurrió un error en el flujo: {type(e).__name__} - {e}")
    finally:
        print("Cerrando el navegador...")
        driver.quit()

if __name__ == "__main__":
    main()
