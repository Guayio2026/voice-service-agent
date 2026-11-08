$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonExe = Join-Path $projectRoot '.venv/Scripts/python.exe'
$voicePort = '8766'
$envFile = Join-Path $projectRoot '.env'
if (Test-Path -LiteralPath $envFile) {
    foreach ($line in [System.IO.File]::ReadAllLines($envFile)) {
        if ($line -match '^\s*VOICE_PORT\s*=\s*(\d+)\s*$') { $voicePort = $Matches[1] }
    }
}
if ($env:VOICE_PORT) { $voicePort = $env:VOICE_PORT }
if ($voicePort -notmatch '^\d+$' -or [int]$voicePort -lt 1024 -or [int]$voicePort -gt 65535) { throw 'Invalid VOICE_PORT' }
$appUrl = "http://127.0.0.1:$voicePort"
function Test-VoiceApp {
    try {
        $status = Invoke-RestMethod -Uri "$appUrl/api/status" -TimeoutSec 2
        return $status.stage -eq 'cloud_voice_integration'
    } catch { return $false }
}
if (-not (Test-VoiceApp)) {
    if (Get-NetTCPConnection -LocalPort $voicePort -State Listen -ErrorAction SilentlyContinue) { throw 'Port already occupied by another application.' }
    if (!(Test-Path -LiteralPath $pythonExe)) { throw 'Missing project Python. See docs/voice-setup.txt.' }
    $runtime = Join-Path $projectRoot 'runtime'
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    $entryFile = Join-Path $projectRoot 'src/voice_server.py'
    $process = Start-Process -FilePath $pythonExe -ArgumentList @('"' + $entryFile + '"') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'voice-stdout.log') -RedirectStandardError (Join-Path $runtime 'voice-stderr.log') -PassThru
    $process.Id | Set-Content -LiteralPath (Join-Path $runtime 'voice.pid')
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 200
        if (Test-VoiceApp) { $ready = $true; break }
    }
    if (-not $ready) { throw 'Voice application did not start. See runtime/voice-stderr.log.' }
}
Start-Process "$appUrl/index.html"
Write-Output "Application: $appUrl/index.html"
