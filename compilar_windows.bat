@echo off
echo ===================================================
echo COMPILANDO RPA UTILITARIOS PARA WINDOWS (.EXE)
echo ===================================================
.venv\Scripts\pyinstaller --noconsole --onefile --add-data "scrapers;scrapers" --add-data "extractors;extractors" --add-data "bigquery;bigquery" --add-data "gcs_uploader.py;." --add-data "scripts;scripts" --add-data "scripts_onedrive;scripts_onedrive" --collect-all google.cloud.bigquery --collect-all google.cloud.storage --collect-all google.api_core --collect-all google.auth --collect-all google.cloud --collect-all selenium --collect-all undetected_chromedriver --collect-all msal --collect-all O365 --collect-all requests --collect-all twocaptcha --collect-all bs4 --collect-all webdriver_manager --collect-all pyarrow --collect-all db_dtypes --collect-all openpyxl --collect-all lxml --collect-all xlrd --distpath RPA_Utilitarios_Ejecutable --clean ejecutable/app.py
echo ===================================================
echo COMPILACION COMPLETADA!
pause
