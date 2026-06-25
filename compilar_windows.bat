@echo off
echo ===================================================
echo COMPILANDO RPA UTILITARIOS PARA WINDOWS (.EXE)
echo ===================================================
.venv\Scripts\pyinstaller --noconsole --onefile --add-data "scrapers;scrapers" --add-data "extractors;extractors" --add-data "bigquery;bigquery" --add-data "gcs_uploader.py;." --add-data "scripts;scripts" --add-data "scripts_onedrive;scripts_onedrive" --distpath RPA_Utilitarios_Ejecutable --clean ejecutable/app.py
echo ===================================================
echo COMPILACION COMPLETADA!
pause
