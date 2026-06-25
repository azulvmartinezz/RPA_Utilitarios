import os
import re
import json
from bs4 import BeautifulSoup

def main():
    print("Iniciando extracción local de tags desde archivos HTML...")
    tags_map = {}
    
    # Archivos HTML a procesar
    files_to_parse = [
        "HTML_SUPRAMAX/Lista_activos.html",
        "HTML_SUPRAMAX/Lista_inactivos.html",
        "HTML_SUPRAMAX/Vehículos_Activos.html"
    ]
    
    for relative_path in files_to_parse:
        abs_path = os.path.abspath(relative_path)
        if not os.path.exists(abs_path):
            print(f"⚠️ Archivo no encontrado, omitiendo: {relative_path}")
            continue
            
        print(f"Procesando {relative_path}...")
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                soup = BeautifulSoup(fh.read(), "html.parser")
                
            # Buscamos todas las filas en la tabla del reporte
            rows = soup.find_all("tr")
            count_file = 0
            for r in rows:
                cells = r.find_all("td")
                # Las tablas del reporte en Supramax tienen típicamente 5 o 6 columnas.
                # Columna de Placas es la 1 (índice 1) y Tag es la 4 (índice 4).
                if len(cells) >= 5:
                    placas_text = cells[1].text.strip().upper().replace(" ", "").replace(".", "")
                    tag_text = cells[4].text.strip()
                    
                    # Normalizar Placas a ECO tipo AU-XXX o CA-XXX
                    m = re.match(r"^(AU|CA)-?(\d{1,3})(?!\d)", placas_text)
                    if m and tag_text and tag_text.lower() != "nan" and tag_text != "":
                        eco_norm = f"{m.group(1)}-{m.group(2).zfill(3)}"
                        tags_map[eco_norm] = tag_text
                        count_file += 1
            print(f"  -> Extraídos {count_file} tags válidos de {relative_path}")
        except Exception as e:
            print(f"  ❌ Error procesando {relative_path}: {e}")
            
    if tags_map:
        output_dir = "HTML_SUPRAMAX"
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        output_file = os.path.join(output_dir, "tags_mapeados.json")
        
        # Mezclar con los que ya existan
        existing_tags = {}
        if os.path.exists(output_file):
            try:
                with open(output_file, "r", encoding="utf-8") as f:
                    existing_tags = json.load(f)
            except Exception:
                pass
        existing_tags.update(tags_map)
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(existing_tags, f, indent=4, ensure_ascii=False)
            
        print(f"\n✅ Extracción local terminada. Se guardaron {len(existing_tags)} tags en total en {output_file}")
    else:
        print("❌ No se encontraron tags válidos para extraer.")

if __name__ == "__main__":
    main()
