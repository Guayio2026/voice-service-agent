$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonExe = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $pythonExe)) { throw 'Missing .venv. See docs/voice-setup.txt' }
Write-Host 'Voice integration: http://127.0.0.1:8766 (default port)'
& $pythonExe (Join-Path $projectRoot 'src/voice_server.py')
