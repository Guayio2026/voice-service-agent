$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonExe = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $pythonExe)) { throw 'Missing .venv. Run: uv venv --python 3.12 .venv' }
& $pythonExe (Join-Path $projectRoot 'src/server.py')

