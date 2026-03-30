param(
    [ValidateSet("healthy", "mixed", "stress", "balanced")]
    [string]$Profile = "mixed",
    [string]$Goal = "Prepare the swarm with a narrow, deterministic rehearsal.",
    [string]$TargetFiles = "app_v3.py",
    [string]$Language = "general",
    [ValidateSet("BOOTSTRAP", "SEED_LOADING", "TEST_WAVE_GEN", "IMPLEMENT", "HALLUCINATION_GUARD", "JUDGE", "STABILIZATION", "MEMORY_COMPACTION", "REPORTING")]
    [string]$Stage = "BOOTSTRAP",
    [switch]$ApplyIfBetter
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Update-DesktopShortcut {
    param(
        [string]$ShortcutName = "Rehearsal Runner.lnk",
        [string]$TargetScript = (Join-Path $root "launch_rehearsal.ps1")
    )

    $desktop = [Environment]::GetFolderPath("Desktop")
    $shortcutPath = Join-Path $desktop $ShortcutName
    $ws = New-Object -ComObject WScript.Shell
    $shortcut = $ws.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = "powershell.exe"
    $shortcut.Arguments = "-ExecutionPolicy Bypass -File `"$TargetScript`""
    $shortcut.WorkingDirectory = $root
    $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,167"
    $shortcut.Save()
    return $shortcutPath
}

$logPath = Join-Path $root "rehearsal.launch.log"
$shortcutPath = Update-DesktopShortcut
$cmd = @(
    "python",
    "run_rehearsal.py",
    "--profile", $Profile,
    "--goal", $Goal,
    "--target-files", $TargetFiles,
    "--language", $Language,
    "--stage", $Stage,
    "--root", $root
)

if ($ApplyIfBetter) {
    $cmd += "--apply-if-better"
}

$joined = $cmd -join " "
Write-Host "Launching offline rehearsal..."
Write-Host $joined

$pythonArgs = @(
    "run_rehearsal.py",
    "--profile", $Profile,
    "--goal", $Goal,
    "--target-files", $TargetFiles,
    "--language", $Language,
    "--stage", $Stage,
    "--root", $root
)

if ($ApplyIfBetter) {
    $pythonArgs += "--apply-if-better"
}

& python @pythonArgs *>&1 | Tee-Object -FilePath $logPath

$latestReport = Get-ChildItem -Path (Join-Path $root "swarm_runs\rehearsal") -Directory -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if ($latestReport) {
    $reportFile = Join-Path $latestReport.FullName "rehearsal_report.md"
    if (Test-Path $reportFile) {
        Write-Host "Opening rehearsal report: $reportFile"
        Start-Process $reportFile
    }
}

Write-Host "Rehearsal log: $logPath"
Write-Host "Desktop shortcut refreshed: $shortcutPath"
