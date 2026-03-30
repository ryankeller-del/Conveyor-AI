param(
    [int]$ChainlitPort = 8001,
    [int]$FlaskPort = 8002
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Update-DesktopShortcut {
    param(
        [string]$ShortcutName = "Live Swarm.lnk",
        [string]$TargetScript = (Join-Path $root "launch_swarm_ui.ps1")
    )

    $desktop = [Environment]::GetFolderPath("Desktop")
    $shortcutPath = Join-Path $desktop $ShortcutName
    $ws = New-Object -ComObject WScript.Shell
    $shortcut = $ws.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = "powershell.exe"
    $shortcut.Arguments = "-ExecutionPolicy Bypass -File `"$TargetScript`""
    $shortcut.WorkingDirectory = $root
    $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,44"
    $shortcut.Save()
    return $shortcutPath
}

function Wait-ForHttp {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return $true
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }

    return $false
}

$chainlitUrl = "http://localhost:$ChainlitPort"
$flaskHomeUrl = "http://localhost:$FlaskPort"
$flaskStatusUrl = "http://localhost:$FlaskPort/status"
$flaskRunUrl = "http://localhost:$FlaskPort/run/examples"

$chainlitLog = Join-Path $root "chainlit.launch.log"
$flaskLog = Join-Path $root "flask.launch.log"
$shortcutPath = Update-DesktopShortcut

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$root'; python -m chainlit run app.py --port $ChainlitPort --host 0.0.0.0 *> '$chainlitLog'"
)

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$root'; `$env:PORT='$FlaskPort'; python app_v3.py *> '$flaskLog'"
)

if (-not (Wait-ForHttp -Url $chainlitUrl -TimeoutSeconds 90)) {
    Write-Host "Chainlit did not become ready at $chainlitUrl"
}

if (-not (Wait-ForHttp -Url $flaskHomeUrl -TimeoutSeconds 90)) {
    Write-Host "Flask did not become ready at $flaskHomeUrl"
}

Start-Process $chainlitUrl
Start-Process $flaskHomeUrl
Start-Process $flaskStatusUrl
Start-Process $flaskRunUrl

Write-Host "Launched:"
Write-Host " - Chainlit: $chainlitUrl"
Write-Host " - Flask home: $flaskHomeUrl"
Write-Host " - Flask status: $flaskStatusUrl"
Write-Host " - Flask examples: $flaskRunUrl"
Write-Host "Logs:"
Write-Host " - Chainlit: $chainlitLog"
Write-Host " - Flask: $flaskLog"
Write-Host "Desktop shortcut refreshed: $shortcutPath"
