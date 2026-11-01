<#
  Sets ThingBD MediPOS up on this Windows PC and adds its shortcuts with the ThingBD icon:
    Desktop:     ThingBD MediPOS             starts MediPOS if needed and opens the POS
    Start menu:  ThingBD MediPOS, Stop MediPOS
    Startup:     ThingBD MediPOS (background) starts MediPOS when you sign in to Windows

  Double-click scripts\windows\Install-MediPOS.bat, or run:
    powershell -ExecutionPolicy Bypass -File scripts\windows\install.ps1 [-NoStartup] [-ShortcutsOnly] [-Remove]

  Running it again is safe: it updates the packages and the database and keeps your .env and data.
#>
param(
    [switch]$NoStartup,     # don't start MediPOS with Windows
    [switch]$ShortcutsOnly, # only (re)make the shortcuts
    [switch]$Remove         # take the shortcuts away (the program and data stay)
)
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $Root "venv\Scripts\python.exe"
$PythonW = Join-Path $Root "venv\Scripts\pythonw.exe"
$Launcher = Join-Path $Root "scripts\windows\medipos_desktop.py"
$Icon = Join-Path $Root "static\img\thingbd.ico"
$EnvFile = Join-Path $Root ".env"
$Name = "ThingBD MediPOS"

$Desktop = [Environment]::GetFolderPath("Desktop")
$Programs = Join-Path ([Environment]::GetFolderPath("Programs")) $Name
$Startup = [Environment]::GetFolderPath("Startup")

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Magenta }

function New-Shortcut($Path, $Arguments, $Description) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Path)
    $link.TargetPath = $PythonW
    $link.Arguments = ('"{0}" {1}' -f $Launcher, $Arguments).Trim()
    $link.WorkingDirectory = $Root
    $link.IconLocation = "$Icon,0"
    $link.Description = $Description
    $link.Save()
}

function Find-Python {
    # The Python launcher (py) first, then python on PATH; MediPOS needs 3.12 or newer.
    foreach ($candidate in @(@("py", "-3.13"), @("py", "-3.12"), @("py", "-3"), @("python"))) {
        $exe = $candidate[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $rest = @($candidate | Select-Object -Skip 1)
        $version = & $exe @rest -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -and [version]$version -ge [version]"3.12") { return , $candidate }
    }
    return $null
}

function Invoke-Checked($exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "This step failed: $exe $($arguments -join ' ')" }
}

if ($Remove) {
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $Desktop "$Name.lnk"), (Join-Path $Startup "$Name.lnk")
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $Programs
    Write-Host "Shortcuts removed. The MediPOS folder and its data are still in $Root."
    exit 0
}

Set-Location $Root

if (-not $ShortcutsOnly) {
    Step "Python"
    if (-not (Test-Path $Python)) {
        $found = Find-Python
        if (-not $found) {
            Write-Host "Python 3.12 or newer was not found. Install Python 3.13 from https://www.python.org/downloads/windows/" -ForegroundColor Red
            Write-Host "(tick 'Add python.exe to PATH'), then run this installer again." -ForegroundColor Red
            exit 1
        }
        $rest = @($found | Select-Object -Skip 1) + @("-m", "venv", "venv")
        Invoke-Checked $found[0] $rest
    }
    & $Python --version

    Step "Packages (needs internet the first time)"
    Invoke-Checked $Python @("-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", "requirements.txt")

    Step "Settings (.env)"
    if (Test-Path $EnvFile) {
        Write-Host "Keeping the .env that is already here."
    } else {
        $secret = & $Python -c "import secrets; print(secrets.token_urlsafe(50))"
        $lines = @(
            "# ThingBD MediPOS on this PC. Made by scripts\windows\install.ps1; never share this file.",
            "DJANGO_SETTINGS_MODULE=config.settings.desktop",
            "SECRET_KEY=$secret",
            "MEDIPOS_PORT=8800",
            "# 127.0.0.1 = only this PC. 0.0.0.0 = other PCs on the shop network too (add this PC's IP to ALLOWED_HOSTS).",
            "MEDIPOS_HOST=127.0.0.1",
            "ALLOWED_HOSTS=localhost,127.0.0.1",
            "SITE_URL=http://localhost:8800",
            "# Empty = SQLite file db.sqlite3 in this folder.",
            "DATABASE_URL=",
            "TRIAL_DAYS=14",
            "SUBSCRIPTION_GRACE_DAYS=3",
            "MESSAGES_SEND_NOW=False",
            "# Mail server for emailed bills and password resets. Gmail: smtp.gmail.com, 587, TLS, an app password.",
            "DEFAULT_FROM_EMAIL=MediPOS <no-reply@example.com>",
            "EMAIL_HOST=",
            "EMAIL_PORT=587",
            "EMAIL_HOST_USER=",
            "EMAIL_HOST_PASSWORD=",
            "EMAIL_USE_TLS=True",
            "# SMS through the platform's gateway (MediPOS SMS credits). Empty = SMS are only logged.",
            "SMS_GATEWAY_URL=",
            "SUPPORT_AI_API_KEY="
        )
        # UTF-8 without a BOM, so the first line reads cleanly.
        [IO.File]::WriteAllText($EnvFile, ($lines -join "`r`n") + "`r`n")
        Write-Host "Made .env with a new secret key."
    }

    Step "Database"
    $env:DJANGO_SETTINGS_MODULE = "config.settings.desktop"
    Invoke-Checked $Python @("manage.py", "migrate", "--noinput")

    $hasAdmin = & $Python manage.py shell -c "from django.contrib.auth import get_user_model; print(get_user_model().objects.filter(is_superuser=True).exists())"
    if ("$hasAdmin".Trim() -ne "True") {
        Write-Host ""
        Write-Host "Make the platform admin account (the owner of this MediPOS). Type its email and a strong password."
        & $Python manage.py createsuperuser
    }
}

Step "Shortcuts"
New-Item -ItemType Directory -Force $Programs | Out-Null
New-Shortcut (Join-Path $Desktop "$Name.lnk") "" "Open the ThingBD MediPOS till"
New-Shortcut (Join-Path $Programs "$Name.lnk") "" "Open the ThingBD MediPOS till"
New-Shortcut (Join-Path $Programs "Stop MediPOS.lnk") "--stop" "Stop ThingBD MediPOS on this PC"
$startupLink = Join-Path $Startup "$Name.lnk"
if ($NoStartup) {
    Remove-Item -Force -ErrorAction SilentlyContinue $startupLink
    Write-Host "Desktop and Start menu shortcuts made. MediPOS will not start with Windows."
} else {
    New-Shortcut $startupLink "--background" "Start ThingBD MediPOS when Windows starts"
    Write-Host "Desktop and Start menu shortcuts made. MediPOS starts by itself when you sign in to Windows."
}

Step "Starting MediPOS"
Start-Process -FilePath $PythonW -ArgumentList ('"{0}"' -f $Launcher) -WorkingDirectory $Root
Write-Host "MediPOS opens in its own window in a few seconds. Next time, use the ThingBD MediPOS icon on the desktop."
