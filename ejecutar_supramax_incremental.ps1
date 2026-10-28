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

$LogPath = Join-Path $LogDirectory ("supramax_incremental_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmmss"))

try {
    $ExistingRun = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "extractors[\\./]supramax_consumos_http" } |
        Select-Object -First 1

    if ($ExistingRun) {
        "Supramax ya está en ejecución (PID $($ExistingRun.ProcessId)). Se omite esta corrida." |
            Add-Content -LiteralPath $LogPath -Encoding UTF8
        exit 0
    }

    # En Windows PowerShell 5.1, stderr nativo se convierte en NativeCommandError.
    # No dejamos que ErrorActionPreference=Stop corte y oculte el traceback.
    $PreviousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    # Consumo por HTTP, sin Chrome, igual que flujo_supramax del orquestador:
    # los últimos 10 días, así una noche perdida se recupera en la siguiente.
    # Sale con 1 si fallan todas las cuentas (p. ej. contraseñas rotadas).
    & $PythonExe -m extractors.supramax_consumos_http --dias 10 *>> $LogPath
    $PythonExitCode = $LASTEXITCODE
    $ErrorActionPreference = $PreviousErrorActionPreference
    exit $PythonExitCode
}
catch {
    $_ | Out-String | Add-Content -LiteralPath $LogPath -Encoding UTF8
    exit 1
}
