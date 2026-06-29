#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYINSTALLER_CONFIG_DIR="$ROOT_DIR/.pyinstaller"
PYINSTALLER_WORK_DIR="$ROOT_DIR/build/pyinstaller_mac"
PYINSTALLER_SPEC_DIR="$ROOT_DIR/build/pyinstaller_spec"
APP_ENTRYPOINT="$ROOT_DIR/ejecutable/app.py"

if [ -x "$ROOT_DIR/.venv/bin/pyinstaller" ]; then
  PYINSTALLER_BIN="$ROOT_DIR/.venv/bin/pyinstaller"
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYINSTALLER_BIN="pyinstaller"
  PYTHON_BIN="python3"
fi

TCL_DATA_DIR="$("$PYTHON_BIN" -c 'from PyInstaller.utils.hooks.tcl_tk import tcltk_info; print(tcltk_info.tcl_data_dir)' 2>/dev/null)"
TK_DATA_DIR="$("$PYTHON_BIN" -c 'from PyInstaller.utils.hooks.tcl_tk import tcltk_info; print(tcltk_info.tk_data_dir)' 2>/dev/null)"
TK_PATCHLEVEL="$("$PYTHON_BIN" -c 'import tkinter as tk; interp = tk.Tcl(); print(interp.call("info", "patchlevel"))' 2>/dev/null || true)"

if [ -z "$TCL_DATA_DIR" ] || [ -z "$TK_DATA_DIR" ]; then
  echo "No se pudieron resolver las rutas de Tcl/Tk para PyInstaller." >&2
  exit 1
fi

if [ -z "$TK_PATCHLEVEL" ]; then
  echo "No se pudo detectar la version de Tk de este Python." >&2
  exit 1
fi

case "$TK_PATCHLEVEL" in
  8.6*|8.7*|9.*) ;;
  *)
    echo "Este Python usa Tk $TK_PATCHLEVEL, que no es compatible con CustomTkinter en macOS." >&2
    echo "Recompila usando un Python que venga enlazado con Tk 8.6 o superior." >&2
    echo "Sugerencia practica: Python de python.org o una instalacion de Homebrew que no use el Tk 8.5 del sistema." >&2
    exit 1
    ;;
esac

echo "==================================================="
echo "COMPILANDO RPA UTILITARIOS PARA MAC (.APP)"
echo "==================================================="
echo "Python de compilacion: $PYTHON_BIN"
echo "Tk detectado: $TK_PATCHLEVEL"
mkdir -p "$PYINSTALLER_CONFIG_DIR" "$PYINSTALLER_WORK_DIR" "$PYINSTALLER_SPEC_DIR"

PYINSTALLER_CONFIG_DIR="$PYINSTALLER_CONFIG_DIR" "$PYINSTALLER_BIN" -y --noconsole --onedir \
  --add-data "$ROOT_DIR/scrapers:scrapers" \
  --add-data "$ROOT_DIR/extractors:extractors" \
  --add-data "$ROOT_DIR/bigquery:bigquery" \
  --add-data "$ROOT_DIR/gcs_uploader.py:." \
  --add-data "$ROOT_DIR/scripts:scripts" \
  --add-data "$ROOT_DIR/scripts_onedrive:scripts_onedrive" \
  --add-data "$TCL_DATA_DIR:_tcl_data" \
  --add-data "$TK_DATA_DIR:_tk_data" \
  --paths . \
  --copy-metadata "google-cloud-bigquery" \
  --copy-metadata "google-cloud-storage" \
  --hidden-import "cryptography" \
  --hidden-import "customtkinter" \
  --hidden-import "google.cloud.bigquery" \
  --hidden-import "google.cloud.storage" \
  --hidden-import "google.cloud" \
  --hidden-import "pyarrow" \
  --hidden-import "db_dtypes" \
  --hidden-import "undetected_chromedriver" \
  --hidden-import "selenium" \
  --hidden-import "O365" \
  --hidden-import "msal" \
  --hidden-import "requests" \
  --hidden-import "twocaptcha" \
  --hidden-import "bs4" \
  --hidden-import "webdriver_manager" \
  --hidden-import "openpyxl" \
  --hidden-import "lxml" \
  --hidden-import "xlrd" \
  --workpath "$PYINSTALLER_WORK_DIR" \
  --specpath "$PYINSTALLER_SPEC_DIR" \
  --distpath RPA_Utilitarios_Ejecutable_Mac \
  --clean "$APP_ENTRYPOINT"
echo "==================================================="
echo "COMPILACION COMPLETADA!"
