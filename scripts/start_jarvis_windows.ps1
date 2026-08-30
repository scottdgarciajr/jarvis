$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectDir "data"
$StartupLog = Join-Path $LogDir "jarvis-startup.log"

Set-Location $ProjectDir
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-StartupLog($Message) {
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $StartupLog -Value "$Timestamp $Message"
}

try {
    if (-not (Test-Path $Python)) {
        Write-StartupLog "Creating .venv"
        python -m venv .venv
    }

    & $Python -c "import jarvis, fastapi, uvicorn, bleak, cryptography, cv2" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-StartupLog "Installing or refreshing Jarvis package"
        & $Python -m pip install -e .
    }

    Write-StartupLog "Starting Jarvis server"
    & $Python -m jarvis *>> $StartupLog
}
catch {
    Write-StartupLog "Startup failed: $($_.Exception.GetType().Name): $($_.Exception.Message)"
    throw
}
