$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $root 'venv\Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
    $venvPython = Join-Path $root '.venv\Scripts\python.exe'
}

if (-not (Test-Path $venvPython)) {
    throw "No Windows virtual environment found. Create one with: python -m venv venv"
}

$mainPy = Join-Path $root 'main.py'
& $venvPython $mainPy
