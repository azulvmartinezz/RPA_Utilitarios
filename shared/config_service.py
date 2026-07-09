import os

from dotenv import load_dotenv


CONFIG_FIELDS = [
    ("ONEDRIVE_RESPALDOS_DIR", "Carpeta Respaldos OneDrive"),
    ("EXCEL_MAESTRO_PATH", "Excel Tabla Maestra"),
    ("EXCEL_MANTENIMIENTO_PATH", "Excel Mantenimientos"),
    ("EXCEL_OUTPUT_PATH", "Excel Reporte Salida (Dashboard)"),
    ("EXCEL_MOV_NOLABORALES_PATH", "Excel/Carpeta Movimientos Fuera Horario"),
]


def load_config_values():
    return {key: os.getenv(key, "") for key, _ in CONFIG_FIELDS}


def save_config_values(base_dir: str, updates: dict[str, str]) -> None:
    env_path = os.path.join(base_dir, ".env")
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as file:
            lines = file.readlines()

    new_lines = []
    updated_keys = set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _ = stripped.split("=", 1)
            key = key.strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}\n")

    with open(env_path, "w", encoding="utf-8") as file:
        file.writelines(new_lines)

    for key, value in updates.items():
        os.environ[key] = value

    load_dotenv(env_path, override=True)
