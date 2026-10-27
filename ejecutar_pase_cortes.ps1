$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDirectory = Join-Path $ProjectRoot "logs_tareas"

# Las tareas programadas heredan la página de códigos de Windows (cp1252).
# Forzamos UTF-8 porque los scrapers escriben caracteres Unicode en el log.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"

Set-Location -LiteralPath $ProjectRoot
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null

$LogPath = Join-Path $LogDirectory ("pase_cortes_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmmss"))

try {
    $ExistingRun = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "scrapers[\\./]pase_rpa" } |
        Select-Object -First 1

    if ($ExistingRun) {
        "Pase ya está en ejecución (PID $($ExistingRun.ProcessId)). Se omite esta corrida." |
            Add-Content -LiteralPath $LogPath -Encoding UTF8
        exit 0
    }

    # En Windows PowerShell 5.1, stderr nativo se convierte en NativeCommandError.
    # No dejamos que ErrorActionPreference=Stop corte y oculte el traceback.
    $PreviousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    # La tarea corre a diario: con --hasta-nuevo-corte sólo trabaja mientras el
    # ciclo vigente (día 13 en adelante) no tenga un corte nuevo, y sale con 2 si
    # sigue pendiente. 28121 (ACP COMBUSTIBLES) se quitó: cuenta sin cierres ni
    # cruces y con tags inactivos (revisado el 2026-10-27).
    & $PythonExe -m scrapers.pase_rpa --clientes "16072" --posiciones "1" --hasta-nuevo-corte *>> $LogPath
    $PythonExitCode = $LASTEXITCODE
    $ErrorActionPreference = $PreviousErrorActionPreference
    exit $PythonExitCode
}
catch {
    $_ | Out-String | Add-Content -LiteralPath $LogPath -Encoding UTF8
    exit 1
}
