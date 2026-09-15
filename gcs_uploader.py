import os
import shutil
import hashlib
import pandas as pd
from datetime import datetime
from google.cloud import storage
from dotenv import load_dotenv
import re


def _sanitize_path_component(value, default="sin_empresa"):
    s = str(value or "").strip()
    if not s:
        return default
    s = re.sub(r"[\\/]+", "-", s)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Za-z0-9._-]", "", s)
    return s or default


def _resolve_local_backup_root(onedrive_dir):
    if onedrive_dir and os.path.exists(onedrive_dir):
        return onedrive_dir, "OneDrive"
    fallback_dir = os.path.join(os.getcwd(), "respaldos_locales")
    os.makedirs(fallback_dir, exist_ok=True)
    return fallback_dir, "Local"

def obtener_mes_año_real(archivo, sistema):
    try:
        if sistema == 'Pase' or archivo.endswith('.csv'):
            try:
                df = pd.read_csv(archivo, encoding='latin1', index_col=False)
            except:
                df = pd.read_csv(archivo, index_col=False)
        elif sistema == 'Supramax' and archivo.endswith('.xls'):
            try:
                raw = pd.read_excel(archivo, engine='xlrd', header=None)
                header_row = next(i for i, row in raw.iterrows() if row.astype(str).str.strip().eq('PLACAS').any())
                df = pd.read_excel(archivo, engine='xlrd', header=header_row)
            except:
                df = pd.read_html(archivo, encoding='latin1')[0]
        elif sistema == 'Edenred':
            try:
                df = pd.read_excel(archivo, header=5)
            except:
                df = pd.read_csv(archivo, encoding='latin1')
        else:
            return None, None
            
        df.columns = df.columns.str.strip()
        cols_norm = {c: c.lower().replace(' ', '').replace('ó', 'o').replace('.', '') for c in df.columns}
        
        col_fecha = next((c for c, norm in cols_norm.items() if 'fechadecruce' in norm), None)
        if not col_fecha: col_fecha = next((c for c, norm in cols_norm.items() if 'fecha' in norm), None)
        
        if col_fecha and col_fecha in df.columns:
            if sistema == 'Supramax':
                fechas_validas = pd.to_datetime(
                    df[col_fecha],
                    format='%Y/%m/%d %H:%M:%S',
                    errors='coerce'
                ).dropna()
            elif sistema == 'Edenred':
                fechas_validas = pd.to_datetime(df[col_fecha], errors='coerce', dayfirst=True).dropna()
            else:
                # Pase: Intentar formato YYYY/MM/DD primero (para pospago)
                # y luego DD/MM/YYYY (para prepago)
                texto = df[col_fecha].astype(str).str.strip()
                serie = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
                
                # 1) YYYY/MM/DD
                mask_ymd = texto.str.match(r"^\d{4}/\d{2}/\d{2}$", na=False)
                if mask_ymd.any():
                    serie.loc[mask_ymd] = pd.to_datetime(
                        texto.loc[mask_ymd], format="%Y/%m/%d", errors="coerce"
                    )
                
                # 2) Restantes
                restantes = serie.isna()
                if restantes.any():
                    serie.loc[restantes] = pd.to_datetime(
                        texto.loc[restantes], dayfirst=True, errors="coerce"
                    )
                
                fechas_validas = serie.dropna()
                
            if not fechas_validas.empty:
                fecha_frecuente = fechas_validas.mode().iloc[0]
                return fecha_frecuente.strftime("%Y"), fecha_frecuente.strftime("%m")
    except Exception as e:
        print(f"Error extrayendo fecha de {archivo}: {e}")
        
    return None, None

def subir_y_borrar_local(archivo_local, sistema, empresa=None, year=None, month=None, name_tag=None):
    load_dotenv()
    onedrive_dir = os.getenv('ONEDRIVE_RESPALDOS_DIR')
    
    # 1. Obtener periodo del archivo
    nombre_original = os.path.basename(archivo_local)
    
    # Si el scraper ya sabe el periodo correcto, confiar en ese dato.
    if year and month:
        anio, mes = str(year), f"{int(month):02d}"
    else:
        # Extraer mes y año real leyendo el archivo
        anio, mes = obtener_mes_año_real(archivo_local, sistema)
    if not anio or not mes:
        # Fallback a la fecha actual si el archivo está vacío o roto
        dt = datetime.now()
        anio, mes = dt.strftime("%Y"), dt.strftime("%m")
        
    # Clave determinística por contenido
    with open(archivo_local, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()[:12]
    original_base, original_ext = os.path.splitext(nombre_original)
    sanitized_tag = _sanitize_path_component(name_tag, default="sin_rango") if name_tag else None
    nombre_partes = [sistema.lower()]
    if sanitized_tag and sanitized_tag not in original_base:
        nombre_partes.append(sanitized_tag)
    nombre_partes.extend([original_base, digest])
    nombre_limpio = "_".join(nombre_partes) + original_ext

    backup_root, backup_label = _resolve_local_backup_root(onedrive_dir)

    if empresa:
        empresa_limpia = _sanitize_path_component(empresa)
        nombre_final = f"{empresa_limpia}_{nombre_limpio}"
    else:
        empresa_limpia = None
        nombre_final = nombre_limpio

    # 1. Respaldo local (OneDrive si está configurado, si no una carpeta del
    # proyecto). Es una conveniencia para revisar sin entrar a GCS -- la
    # fuente de verdad que lee `extractors/pase_cruces.py` es la nube.
    try:
        dest_dir = os.path.join(backup_root, sistema, str(anio), f"{int(mes):02d}")
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, nombre_final)
        shutil.copy(archivo_local, dest_path)
        print(f"[{backup_label}] Archivo guardado localmente: {dest_path}")
    except Exception as e:
        print(f"❌ Error al guardar respaldo local: {e}")

    # 2. Subida a GCS -- esta es la que de verdad importa. Sin ella,
    # `extractors/pase_cruces.py` nunca ve el archivo nuevo, sin importar
    # cuántas veces se corra: solo lee de `gs://<bucket>/Pase/`.
    #
    # Se quitó por accidente el 2026-07-09 (migración a Flet UI): la función
    # se quedó con el nombre `subir_y_borrar_local` pero dejó de subir a
    # ningún lado. Desde entonces cada CSV nuevo se queda en la Mac de quien
    # corre el scraper. Ver PENDIENTES.md.
    project_id = os.getenv('GCP_PROJECT_ID')
    bucket_name = os.getenv('GCP_BUCKET_RESPALDOS', f"{project_id}-respaldos-rpa" if project_id else None)

    if not project_id or not bucket_name:
        print("⚠️ No se puede subir a GCS: falta GCP_PROJECT_ID en el .env")
        return

    try:
        client = storage.Client(project=project_id)
        bucket = client.bucket(bucket_name)

        ruta_gcs = (
            f"{sistema}/{empresa_limpia}/{anio}/{mes}/{nombre_limpio}"
            if empresa_limpia
            else f"{sistema}/{anio}/{mes}/{nombre_limpio}"
        )

        print(f"☁️ Subiendo a la nube: gs://{bucket_name}/{ruta_gcs}")
        blob = bucket.blob(ruta_gcs)
        blob.upload_from_filename(archivo_local)
        print("✅ Subida a GCS exitosa.")

        os.remove(archivo_local)
        print(f"🗑️ Archivo temporal '{nombre_original}' borrado de la Mac.")
    except Exception as e:
        print(f"❌ Error al subir a GCS: {e}")
