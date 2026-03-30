param(
    [int]$ChainlitPort = 8001,
    [int]$FlaskPort = 8002
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$chainlitCmd = "python -m chainlit run app.py --port $ChainlitPort --host 0.0.0.0"
$flaskCmd = "python app_v3.py --port $FlaskPort"

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$root'; $chainlitCmd"
)

Start-Sleep -Seconds 1

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$root'; `$env:PORT='$FlaskPort'; python app_v3.py"
)

Start-Sleep -Seconds 2

$chainlitUrl = "http://localhost:$ChainlitPort"
$flaskStatusUrl = "http://localhost:$FlaskPort/status"
$flaskRunUrl = "http://localhost:$FlaskPort/run/examples"

Start-Process $chainlitUrl
Start-Process $flaskStatusUrl
Start-Process $flaskRunUrl

Write-Host "Launched:"
Write-Host " - Chainlit: $chainlitUrl"
Write-Host " - Flask status: $flaskStatusUrl"
Write-Host " - Flask examples: $flaskRunUrl"
