import os
import shutil
import sys
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import gcs_uploader
load_dotenv()

def organizar_archivos():
    respaldos_dir = os.getenv('ONEDRIVE_RESPALDOS_DIR')
    if not respaldos_dir or not os.path.exists(respaldos_dir):
        print(f"❌ Error: La ruta local de OneDrive no existe: {respaldos_dir}")
        return

    print("=== ORGANIZANDO ARCHIVOS POR AÑO Y MES EN ONEDRIVE ===")
    
    for sistema in ['Supramax', 'Pase', 'Edenred']:
        target_dir = os.path.join(respaldos_dir, sistema)
        if not os.path.exists(target_dir):
            continue
            
        print(f"\nAnalizando archivos en: {sistema}...")
        # Listar solo archivos en la raíz del directorio del sistema
        archivos = [
            f for f in os.listdir(target_dir) 
            if os.path.isfile(os.path.join(target_dir, f)) and not f.startswith('.')
        ]
        
        for file in archivos:
            filepath = os.path.join(target_dir, file)
            # Obtener año y mes del archivo
            anio, mes = gcs_uploader.obtener_mes_año_real(filepath, sistema)
            
            if anio and mes:
                # Crear carpeta de destino YYYY/MM
                dest_folder = os.path.join(target_dir, str(anio), f"{int(mes):02d}")
                os.makedirs(dest_folder, exist_ok=True)
                
                # Mover archivo
                dest_path = os.path.join(dest_folder, file)
                try:
                    shutil.move(filepath, dest_path)
                    print(f"  Mover: {file} -> {anio}/{int(mes):02d}/")
                except Exception as e:
                    print(f"  [Error] al mover {file}: {e}")
            else:
                print(f"  [Advertencia] No se pudo determinar el periodo para: {file} (se deja en la raiz)")
                
    print("\n=== ORGANIZACION DE CARPETAS COMPLETADA CON EXITO ===")

if __name__ == "__main__":
    organizar_archivos()
