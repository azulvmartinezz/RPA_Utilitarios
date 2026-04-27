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
from twocaptcha import TwoCaptcha
from webdriver_manager.chrome import ChromeDriverManager

# Cargar variables de entorno
load_dotenv()

EDENRED_USER = os.getenv('EDENRED_USER')
EDENRED_PASSWORD = os.getenv('EDENRED_PASSWORD')
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
    print("Iniciando RPA para Edenred...")
    
    if not TWOCAPTCHA_API_KEY:
        print("ERROR: Por favor agrega tu TWOCAPTCHA_API_KEY al archivo .env")
        return

    chrome_options = Options()
    # Ejecutando con Google Chrome
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    wait = WebDriverWait(driver, 15)
    
    url = "https://sso.edenred.com.mx/SSOV280/Account/LogOn?ReturnUrl=%2fssov280"
    
    try:
        print(f"Navegando a: {url}")
        driver.get(url)
        
        # 1. Aceptar cookies si aparece el banner
        try:
            print("Buscando banner de cookies...")
            cookie_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Aceptar todas las cookies')]")))
            cookie_btn.click()
            print("Cookies aceptadas.")
        except Exception:
            print("No se encontró el banner de cookies o ya estaba aceptado.")
            
        # 2. Llenar usuario
        print("Ingresando usuario...")
        user_input = wait.until(EC.presence_of_element_located((By.ID, "UserName")))
        user_input.send_keys(EDENRED_USER)
        
        # 3. Esperar a que aparezca el ReCaptcha o el div del captcha
        time.sleep(2) # Pausa breve para que cargue dinámicamente si es necesario
        
        print("Buscando widget de ReCaptcha para obtener el sitekey...")
        try:
            # Buscamos el elemento que suele tener el data-sitekey
            recaptcha_div = driver.find_element(By.CLASS_NAME, "g-recaptcha")
            sitekey = recaptcha_div.get_attribute("data-sitekey")
            
            if sitekey:
                print(f"Sitekey encontrado: {sitekey}")
                
                # 4. Resolver captcha
                token = solve_recaptcha(sitekey, driver.current_url)
                
                if token:
                    # 5. Inyectar el token en la página
                    print("Inyectando token en la página...")
                    driver.execute_script(f"document.getElementById('g-recaptcha-response').innerHTML = '{token}';")
                    time.sleep(1)
            else:
                print("No se encontró el data-sitekey en el widget.")
        except Exception as e:
            print(f"No se encontró el widget de ReCaptcha o no es necesario: {e}")
            
        # 6. Clic en Continuar
        print("Haciendo clic en Continuar...")
        continue_btn = driver.find_element(By.ID, "ButtonLogin")
        continue_btn.click()
        
        # 7. Esperar a la siguiente pantalla para ingresar contraseña
        print("\nEsperando a la pantalla de contraseña...")
        try:
            # Damos un par de segundos para que termine la animación de la ventana
            time.sleep(2)
            pwd_input = wait.until(EC.element_to_be_clickable((By.ID, "TallyHawk")))
            print("Ingresando contraseña...")
            pwd_input.send_keys(EDENRED_PASSWORD)
            
            # Enviar formulario usando la tecla ENTER
            print("Enviando formulario de inicio de sesión...")
            pwd_input.send_keys(Keys.RETURN)
        except Exception as e:
            print(f"No se pudo ingresar la contraseña: {e}")
            
        # 8. Esperar a que cargue el dashboard y hacer clic en "Apps premium"
        print("\nEsperando a que cargue el dashboard...")
        try:
            # Esperamos a que el botón 'Apps premium' sea clickeable
            apps_premium_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//input[@value='Apps premium']")))
            print("Haciendo clic en 'Apps premium'...")
            apps_premium_btn.click()
            
            # Dejamos un tiempo para que carguen los elementos de la pestaña
            time.sleep(2) 
        except Exception as e:
            print(f"No se pudo encontrar o hacer clic en 'Apps premium': {e}")
            
        # 9. Hacer clic en Ticket Car ®
        print("\nBuscando el acceso a 'Ticket Car ®'...")
        try:
            # Seleccionamos la imagen por su atributo alt y subimos al elemento <a> padre para hacer clic
            ticket_car_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//img[@alt='Ticket Car ®']/parent::a")))
            print("Haciendo clic en 'Ticket Car ®'...")
            ticket_car_btn.click()
            
            # Pausa para que cargue la nueva página de operación
            time.sleep(5)
        except Exception as e:
            print(f"No se pudo encontrar o hacer clic en 'Ticket Car ®': {e}")
            
        # 10. Cambiar a la nueva pestaña de Ticket Car (suele abrirse en pestaña nueva)
        print("\nCambiando a la pestaña de Ticket Car...")
        driver.switch_to.window(driver.window_handles[-1])
        
        # 11. Clic en "Reportes" del menú superior
        print("Buscando menú 'Reportes'...")
        reportes_menu = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'Reportes') and contains(@href, 'MicrositioReportes')]")))
        reportes_menu.click()
        
        # 12. Clic en "Resumen de Reportes" en el menú lateral
        print("Buscando 'Resumen de Reportes'...")
        resumen_menu = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), 'Resumen de Reportes')]")))
        resumen_menu.click()
        
        # 13. Clic en "Reportes Financieros"
        print("Seleccionando 'Reportes Financieros'...")
        financieros_btn = wait.until(EC.element_to_be_clickable((By.ID, "ctl00_contenido_ucCategoriasReportes_lnkFinancieros")))
        financieros_btn.click()
        
        # 14. Seleccionar "DETALLE DE MOVIMIENTOS POR FACTURACIÓN" (clic en su ícono)
        print("Buscando reporte 'DETALLE DE MOVIMIENTOS POR FACTURACIÓN'...")
        detalle_fact_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//tr[td[contains(text(), 'DETALLE DE MOVIMIENTOS POR FACTURACIÓN')]]//input[@type='image']")))
        detalle_fact_btn.click()
        
        # 15. Seleccionar PERIODO FACTURACION (Mes anterior dinámico)
        print("\nCalculando el mes anterior para seleccionar el periodo correcto...")
        hoy = datetime.date.today()
        primer_dia_este_mes = hoy.replace(day=1)
        ultimo_dia_mes_pasado = primer_dia_este_mes - datetime.timedelta(days=1)
        mes_pasado_str = ultimo_dia_mes_pasado.strftime("%m/%Y") # Ej. 03/2026
        
        periodo_select_elem = wait.until(EC.presence_of_element_located((By.ID, "ctl00_contenido_ucListadoReportes_cpFormulario_ddlPERIODOFACTURACION")))
        from selenium.webdriver.support.ui import Select
        periodo_select = Select(periodo_select_elem)
        
        opcion_encontrada = False
        for option in periodo_select.options:
            if mes_pasado_str in option.text:
                print(f"Seleccionando el periodo: {option.text}")
                periodo_select.select_by_visible_text(option.text)
                opcion_encontrada = True
                break
                
        if not opcion_encontrada:
            print(f"⚠️ No se encontró un periodo para {mes_pasado_str} en la lista.")
            
        time.sleep(2) # Pausa para que carguen posibles postbacks
        
        # 16. Escribir destinatario y enviar
        email_destino = os.getenv('DESTINATARIO_EMAIL', 'correo@ejemplo.com')
        print(f"\nEnviando reporte al correo: {email_destino}")
        
        txt_correo = wait.until(EC.presence_of_element_located((By.ID, "ctl00_contenido_ucListadoReportes_cpDestinatarios_txtWriteMail")))
        txt_correo.clear()
        txt_correo.send_keys(email_destino)
        
        btn_agregar_correo = driver.find_element(By.ID, "ctl00_contenido_ucListadoReportes_cpDestinatarios_btnAgregarMail")
        btn_agregar_correo.click()
        time.sleep(1)
        
        print("Haciendo clic en 'Aceptar' para finalizar envío...")
        btn_aceptar = driver.find_element(By.ID, "ctl00_contenido_ucListadoReportes_btnAceptar")
        btn_aceptar.click()
        
        print("\n✅ ¡El reporte se ha programado para enviarse por correo correctamente!")
        time.sleep(10) # Pausa para ver el mensaje de confirmación antes de cerrar
    except Exception as e:
        print(f"Ocurrió un error en el flujo: {e}")
    finally:
        print("Cerrando el navegador...")
        driver.quit()

if __name__ == "__main__":
    main()
