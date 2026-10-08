$ErrorActionPreference = 'Stop'
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Set up the project virtual environment first; see README.md.'
}
& $taskPython (Join-Path $PSScriptRoot 'edge_to_zen.py') scan
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
