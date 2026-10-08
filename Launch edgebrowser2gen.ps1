$ErrorActionPreference = 'Stop'
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Create the project virtual environment and install dependencies first. See README.md.'
}
Push-Location -LiteralPath $PSScriptRoot
try {
    & $taskPython -m app
    if ($LASTEXITCODE -ne 0) { throw "The app exited with code $LASTEXITCODE." }
} finally {
    Pop-Location
}
