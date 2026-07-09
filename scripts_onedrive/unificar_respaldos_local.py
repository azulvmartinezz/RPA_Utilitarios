import os
import re
import sys
import hashlib
import pandas as pd
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from pase_utils import parse_pase_fecha, read_pase_csv_lossless

load_dotenv()

def _normalize_eco(val):
    s = str(val).strip().upper().replace('.', '')
    # Check if it has LZC
    has_lzc = 'LZC' in s
    s_clean = s.replace('LZC', '').replace(' ', '')
    
    m = re.match(r'^(AU|CA)-?(\d{1,3})(?!\d)', s_clean)
    if m:
        eco = f"{m.group(1)}-{m.group(2).zfill(3)}"
        if has_lzc:
            return f"{eco} LZC"
        return eco
    return str(val).strip().upper()

def _limpiar_edenred(df):
    df = df.copy()
    df.columns = df.columns.str.strip()
    limpio = pd.DataFrame()
    
    if 'Vehículo' in df.columns:
        limpio['ECO'] = df['Vehículo'].apply(_normalize_eco)
    else:
        limpio['ECO'] = df.iloc[:, 7].apply(_normalize_eco)
        
    limpio['Fecha'] = pd.to_datetime(df['Fecha Transacción'], dayfirst=True, errors='coerce')
    limpio['Concepto'] = "COMBUSTIBLE"
    limpio['Tipo'] = df.get('Mercancía')
    limpio['Cantidad'] = pd.to_numeric(df.get('Cantidad Mercancía'), errors='coerce')
    limpio['Importe'] = pd.to_numeric(df.get('Importe Transacción'), errors='coerce')
    limpio['Sistema'] = "Edenred"
    if 'Archivo_Origen' in df.columns:
        limpio['Archivo_Origen'] = df['Archivo_Origen']
    if 'Id_Origen' in df.columns:
        limpio['Id_Origen'] = df['Id_Origen']
        
    limpio = limpio.dropna(subset=['Importe', 'Fecha', 'ECO'])
    limpio = limpio[limpio['ECO'].str.match(r'^(AU|CA)-\d{3}(?:\s*LZC)?$', na=False)]
    return limpio

import json

REGISTRY_PATH = os.path.join(PROJECT_ROOT, "processed_files_registry.json")

def load_registry():
    if os.path.exists(REGISTRY_PATH):
        try:
            with open(REGISTRY_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Error al leer el registro de archivos procesados: {e}")
    return {}

def save_registry(registry):
    try:
        with open(REGISTRY_PATH, 'w', encoding='utf-8') as f:
            json.dump(registry, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Error al guardar el registro de archivos procesados: {e}")

def is_file_processed(filepath, registry):
    if filepath not in registry:
        return False
    try:
        stat = os.stat(filepath)
        recorded = registry[filepath]
        return recorded.get("size") == stat.st_size and recorded.get("mtime") == stat.st_mtime
    except Exception:
        return False

def mark_file_processed(filepath, registry):
    try:
        stat = os.stat(filepath)
        registry[filepath] = {
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "processed_at": pd.Timestamp.now().isoformat()
        }
    except Exception as e:
        print(f"⚠️ Error al registrar {filepath}: {e}")


def _should_force_full_rebuild(*output_paths):
    for path in output_paths:
        if not os.path.exists(path):
            return True
        try:
            if os.path.getsize(path) == 0:
                return True
        except OSError:
            return True
    return False


def _output_missing_columns(path, required_columns):
    if not os.path.exists(path):
        return True
    try:
        header = pd.read_csv(path, nrows=0)
    except Exception:
        return True
    missing = [col for col in required_columns if col not in header.columns]
    return bool(missing)


def _build_row_ids(df, system_name):
    if df.empty:
        return pd.Series(dtype="object")

    normalized = df.copy()
    normalized.columns = [str(col).strip() for col in normalized.columns]
    for col in normalized.columns:
        normalized[col] = normalized[col].fillna("").astype(str).str.strip()

    row_signature = normalized.apply(
        lambda row: "||".join(f"{col}={row[col]}" for col in normalized.columns),
        axis=1,
    )
    occurrence = row_signature.groupby(row_signature, sort=False).cumcount()
    return pd.Series(
        [
            hashlib.sha1(f"{system_name}|{signature}|{count}".encode("utf-8")).hexdigest()
            for signature, count in zip(row_signature.tolist(), occurrence.tolist())
        ],
        index=df.index,
    )


def _collect_files(root_dir, allowed_suffixes, registry, rebuild_all):
    matched = []
    for root, dirs, files in os.walk(root_dir):
        for file in files:
            if file.endswith(allowed_suffixes):
                full_path = os.path.join(root, file)
                if rebuild_all or not is_file_processed(full_path, registry):
                    matched.append(full_path)
    return matched


def unificar_respaldos_desde_onedrive(rebuild_all=False):
    respaldos_dir = os.getenv('ONEDRIVE_RESPALDOS_DIR')
    if not respaldos_dir or not os.path.exists(respaldos_dir):
        print(f"Error: La ruta local de respaldos en OneDrive no existe: {respaldos_dir}")
        return
        
    print(f"=== UNIFICANDO RESPALDOS LOCALES DESDE ONEDRIVE ({respaldos_dir}) ===")
    if rebuild_all:
        print("Modo reconstruccion total: se reprocesaran todos los respaldos locales encontrados.")
    else:
        print("Modo incremental: solo se agregaran respaldos nuevos o modificados.")

    registry = load_registry()
    registry_updated = False
    
    # 1. SUPRAMAX
    supramax_dir = os.path.join(respaldos_dir, 'Supramax')
    if os.path.exists(supramax_dir):
        supramax_output = "CONSOLIDADO_CRUDO_SUPRAMAX.csv"
        force_full_rebuild = rebuild_all or _should_force_full_rebuild(supramax_output)
        if not force_full_rebuild and _output_missing_columns(supramax_output, ["Id_Origen"]):
            force_full_rebuild = True
            print("Supramax: consolidado sin Id_Origen. Se forzará una reconstrucción completa por única vez.")
        all_supra_files = _collect_files(
            supramax_dir,
            ('.xls', '.xlsx'),
            registry,
            force_full_rebuild,
        )
                    
        if all_supra_files:
            if force_full_rebuild:
                print(f"Reconstruyendo Supramax completo desde {len(all_supra_files)} archivo(s) locales...")
            else:
                print(f"Procesando {len(all_supra_files)} nuevos/modificados archivos de Supramax...")
            lista_supra = []
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                for local_path in all_supra_files:
                    file = os.path.basename(local_path)
                    try:
                        raw = pd.read_excel(local_path, engine='xlrd', header=None)
                        header_row = next(i for i, row in raw.iterrows() if row.astype(str).str.strip().eq('PLACAS').any())
                        df = raw.iloc[header_row+1:].copy()
                        df.columns = raw.iloc[header_row].astype(str).str.strip()
                        df['Id_Origen'] = _build_row_ids(df, "Supramax")
                        df['Archivo_Origen'] = file
                        lista_supra.append(df)
                        mark_file_processed(local_path, registry)
                        registry_updated = True
                    except Exception as e:
                        try:
                            raw = pd.read_excel(local_path, engine='openpyxl', header=None)
                            header_row = next(i for i, row in raw.iterrows() if row.astype(str).str.strip().eq('PLACAS').any())
                            df = raw.iloc[header_row+1:].copy()
                            df.columns = raw.iloc[header_row].astype(str).str.strip()
                            df['Id_Origen'] = _build_row_ids(df, "Supramax")
                            df['Archivo_Origen'] = file
                            lista_supra.append(df)
                            mark_file_processed(local_path, registry)
                            registry_updated = True
                        except Exception as e2:
                            print(f"  Error en {file}: {e} | {e2}")
            if lista_supra:
                existing_df = pd.DataFrame()
                if not force_full_rebuild and os.path.exists(supramax_output):
                    try:
                        existing_df = pd.read_csv(supramax_output)
                    except Exception:
                        pass
                new_df = pd.concat(lista_supra, ignore_index=True)
                combined = pd.concat([existing_df, new_df], ignore_index=True).drop_duplicates(
                    subset=['Id_Origen']
                ).copy()
                combined.to_csv(supramax_output, index=False, encoding='utf-8-sig')
                print(f"Actualizado: {supramax_output} (Total: {len(combined)} registros)")
        else:
            print("Supramax: Sin archivos locales nuevos por procesar.")
    else:
        print("No existe la carpeta Supramax en la ruta de respaldos.")

    # 2. PASE
    pase_dir = os.path.join(respaldos_dir, 'Pase')
    if os.path.exists(pase_dir):
        pase_output = "CONSOLIDADO_CRUDO_PASE.csv"
        force_full_rebuild = rebuild_all or _should_force_full_rebuild(pase_output)
        if not force_full_rebuild and _output_missing_columns(pase_output, ["Id_Origen"]):
            force_full_rebuild = True
            print("Pase: consolidado sin Id_Origen. Se forzará una reconstrucción completa por única vez.")
        all_csv_files = _collect_files(
            pase_dir,
            ('.csv',),
            registry,
            force_full_rebuild,
        )
        
        if all_csv_files:
            if force_full_rebuild:
                print(f"Reconstruyendo Pase completo desde {len(all_csv_files)} archivo(s) locales...")
            else:
                print(f"Procesando {len(all_csv_files)} nuevos/modificados archivos de Pase...")
            lista_pase = []
            for local_path in all_csv_files:
                file = os.path.basename(local_path)
                try:
                    df = read_pase_csv_lossless(local_path)
                    row_ids = _build_row_ids(df, "Pase")
                    cols = {c: c.lower().replace(' ', '').replace('ó', 'o').replace('.', '') for c in df.columns}
                    col_eco = next((c for c, n in cols.items() if 'noeconomico' in n or 'economico' in n), None)
                    col_fecha = next((c for c, n in cols.items() if 'fechadecruce' in n or n == 'fecha'), None)
                    col_importe = next((c for c, n in cols.items() if 'importeal100' in n or n == 'importe'), None)
                    col_tarjeta = next((c for c, n in cols.items() if 'tarjetaidmx' in n), None)
                    if not col_tarjeta:
                        col_tarjeta = next((c for c, n in cols.items() if n == 'tarjeta' or 'tarjeta' in n or n == 'tag'), None)
                    
                    df_std = pd.DataFrame()
                    df_std['ECO'] = df[col_eco].astype(str).str.strip() if col_eco else None
                    df_std['Fecha'] = parse_pase_fecha(df[col_fecha]) if col_fecha else None
                    if col_importe:
                        df_std['Importe'] = pd.to_numeric(
                            df[col_importe].astype(str).str.replace(r'[$,]', '', regex=True),
                            errors='coerce',
                        ).abs()
                    if col_tarjeta:
                        df_std['Tarjeta IDMX'] = df[col_tarjeta].astype(str).str.strip().str.rstrip('.')
                    df_std['Id_Origen'] = row_ids.values
                    df_std['Archivo_Origen'] = file
                    df_std = df_std.dropna(subset=['Importe', 'Fecha', 'ECO'])
                    df_std['ECO'] = df_std['ECO'].apply(_normalize_eco)
                    lista_pase.append(df_std)
                    mark_file_processed(local_path, registry)
                    registry_updated = True
                except Exception as e:
                    print(f"  Error en {file}: {e}")
            if lista_pase:
                existing_df = pd.DataFrame()
                if not force_full_rebuild and os.path.exists(pase_output):
                    try:
                        existing_df = pd.read_csv(pase_output)
                    except Exception:
                        pass
                new_df = pd.concat(lista_pase, ignore_index=True)
                combined = pd.concat([existing_df, new_df], ignore_index=True).drop_duplicates(
                    subset=['Id_Origen']
                ).copy()
                combined.to_csv(pase_output, index=False, encoding='utf-8-sig')
                print(f"Actualizado: {pase_output} (Total: {len(combined)} registros)")
        else:
            print("Pase: Sin archivos locales nuevos por procesar.")
    else:
        print("No existe la carpeta Pase en la ruta de respaldos.")

    # 3. EDENRED
    edenred_dir = os.path.join(respaldos_dir, 'Edenred')
    if os.path.exists(edenred_dir):
        edenred_crudo_output = "CONSOLIDADO_CRUDO_EDENRED.csv"
        edenred_limpio_output = "CONSOLIDADO_LIMPIO_EDENRED.csv"
        force_full_rebuild = rebuild_all or _should_force_full_rebuild(
            edenred_crudo_output,
            edenred_limpio_output,
        )
        if not force_full_rebuild and (
            _output_missing_columns(edenred_crudo_output, ["Id_Origen"]) or
            _output_missing_columns(edenred_limpio_output, ["Id_Origen"])
        ):
            force_full_rebuild = True
            print("Edenred: consolidado sin Id_Origen. Se forzará una reconstrucción completa por única vez.")
        all_edenred_files = _collect_files(
            edenred_dir,
            ('.csv', '.xlsx'),
            registry,
            force_full_rebuild,
        )
                    
        if all_edenred_files:
            if force_full_rebuild:
                print(f"Reconstruyendo Edenred completo desde {len(all_edenred_files)} archivo(s) locales...")
            else:
                print(f"Procesando {len(all_edenred_files)} nuevos/modificados archivos de Edenred...")
            lista_eden = []
            lista_eden_limpio = []
            for local_path in all_edenred_files:
                file = os.path.basename(local_path)
                try:
                    if file.endswith('.csv'):
                        df = pd.read_csv(local_path, encoding='latin1')
                    else:
                        df = pd.read_excel(local_path, header=5)
                    df['Id_Origen'] = _build_row_ids(df, "Edenred")
                    df['Archivo_Origen'] = file
                    lista_eden.append(df)
                    lista_eden_limpio.append(_limpiar_edenred(df))
                    mark_file_processed(local_path, registry)
                    registry_updated = True
                except Exception as e:
                    print(f"  Error en {file}: {e}")
            if lista_eden:
                existing_crudo = pd.DataFrame()
                if not force_full_rebuild and os.path.exists(edenred_crudo_output):
                    try:
                        existing_crudo = pd.read_csv(edenred_crudo_output)
                    except Exception:
                        pass
                new_crudo = pd.concat(lista_eden, ignore_index=True)
                combined_crudo = pd.concat([existing_crudo, new_crudo], ignore_index=True).drop_duplicates(
                    subset=['Id_Origen']
                ).copy()
                combined_crudo.to_csv(edenred_crudo_output, index=False, encoding='utf-8-sig')
                
                existing_limpio = pd.DataFrame()
                if not force_full_rebuild and os.path.exists(edenred_limpio_output):
                    try:
                        existing_limpio = pd.read_csv(edenred_limpio_output)
                    except Exception:
                        pass
                new_limpio = pd.concat(lista_eden_limpio, ignore_index=True)
                combined_limpio = pd.concat([existing_limpio, new_limpio], ignore_index=True).drop_duplicates(
                    subset=['Id_Origen']
                ).copy()
                combined_limpio.to_csv(edenred_limpio_output, index=False, encoding='utf-8-sig')
                print(f"Actualizado: Edenred consolidado (Crudo: {len(combined_crudo)}, Limpio: {len(combined_limpio)})")
        else:
            print("Edenred: Sin archivos locales nuevos por procesar.")
    else:
        print("No existe la carpeta Edenred en la ruta de respaldos.")

    if registry_updated:
        save_registry(registry)

    print("=== PROCESO FINALIZADO ===")

if __name__ == "__main__":
    unificar_respaldos_desde_onedrive()
