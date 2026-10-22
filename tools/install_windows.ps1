# MedMH v3.9.1 local Windows installation. Uses installed 64-bit CPython; never compiles pandas.
$ErrorActionPreference = 'Stop'
$Project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $Project
$Log = Join-Path $Project 'INSTALL_LOG.txt'
$startedTranscript = $false
$exitStatus = 1

function Execute-Step {
    param(
        [string] $Label,
        [string] $Executable,
        [string[]] $Arguments = @()
    )
    Write-Host ("`n" + $Label) -ForegroundColor Cyan
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed (exit code $LASTEXITCODE)."
    }
}

function Compatible-Python {
    param([string] $Executable, [string[]] $Prefix = @())
    try {
        & $Executable @Prefix 'tools\python_compat_check.py' *> $null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

try {
    try {
        Start-Transcript -Path $Log -Force -ErrorAction Stop | Out-Null
        $startedTranscript = $true
    } catch {
        Write-Host "Could not start logging: $($_.Exception.Message)" -ForegroundColor Yellow
    }

    Write-Host '============================================'
    Write-Host ' MedMH AI Workforce v3.9.1 - Install'
    Write-Host '============================================'
    Write-Host "Project: $Project"

    foreach ($relative in @('app.py', 'requirements.txt', 'whatsapp\package.json', 'tools\python_compat_check.py', 'check_setup.py')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Project $relative))) {
            throw "Missing project file: $relative. Extract the fix into the same folder as START.bat."
        }
    }

    $venvPath = Join-Path $Project '.venv'
    $venvPython = Join-Path $venvPath 'Scripts\python.exe'
    $checker = Join-Path $Project 'tools\python_compat_check.py'
    $venvGood = $false

    if (Test-Path -LiteralPath $venvPython) {
        & $venvPython $checker
        $venvGood = ($LASTEXITCODE -eq 0)
        if ($venvGood) { Write-Host 'Reusing compatible existing Python environment.' -ForegroundColor Green }
    }

    if (-not $venvGood) {
        if (Test-Path -LiteralPath $venvPath) {
            $suffix = Get-Date -Format 'yyyyMMdd-HHmmss'
            $backupName = '.venv-incomplete-' + $suffix
            Write-Host "Backing up incomplete .venv to $backupName ..." -ForegroundColor Yellow
            Move-Item -LiteralPath $venvPath -Destination (Join-Path $Project $backupName) -ErrorAction Stop
        }

        $candidates = @(
            @{ Exe = 'py';  Args = @('-3.14'); Display = 'py -3.14' },
            @{ Exe = 'py';  Args = @('-3.13'); Display = 'py -3.13' },
            @{ Exe = 'py';  Args = @('-3.12'); Display = 'py -3.12' },
            @{ Exe = 'py';  Args = @('-3.11'); Display = 'py -3.11' },
            @{ Exe = 'python'; Args = @(); Display = 'python from PATH' }
        )
        $found = $false
        foreach ($candidate in $candidates) {
            if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }
            if (-not (Compatible-Python -Executable $candidate.Exe -Prefix $candidate.Args)) { continue }
            Write-Host "Trying $($candidate.Display) ..."
            try {
                $createArgs = @($candidate.Args) + @('-m', 'venv', '.venv')
                & $candidate.Exe @createArgs
                if (($LASTEXITCODE -eq 0) -and (Test-Path -LiteralPath $venvPython)) {
                    & $venvPython $checker
                    if ($LASTEXITCODE -eq 0) {
                        $found = $true
                        Write-Host "Selected $($candidate.Display)" -ForegroundColor Green
                        break
                    }
                }
            } catch {
                Write-Host "Failed with $($candidate.Display): $($_.Exception.Message)" -ForegroundColor Yellow
            }
            if (Test-Path -LiteralPath $venvPath) {
                $suffix = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
                Move-Item -LiteralPath $venvPath -Destination (Join-Path $Project ('.venv-incomplete-' + $suffix)) -ErrorAction Stop
            }
        }
        if (-not $found) {
            throw 'No compatible standard 64-bit Python 3.11-3.14 found. Run py -0p and share that output.'
        }
    }

    Execute-Step '[1/5] Upgrade pip' $venvPython @('-m','pip','install','--upgrade','pip')
    Execute-Step '[1/5] Install Python dependencies (binary wheels only)' $venvPython @('-m','pip','install','--only-binary=:all:','-r','requirements.txt')
    Execute-Step '[2/5] Install Playwright Chromium' $venvPython @('-m','playwright','install','chromium')
    Execute-Step '[3/5] Check Python installation' $venvPython @('check_setup.py')

    if (-not (Get-Command 'node.exe' -ErrorAction SilentlyContinue)) { throw 'Node.js is missing. Install Node.js 20+.' }
    if (-not (Get-Command 'npm.cmd' -ErrorAction SilentlyContinue)) { throw 'npm is missing. Install Node.js 20+.' }
    # Windows PowerShell 5.1 can strip inner quotes from native node -e commands.
    # Read the version via --version instead, so no JavaScript quoting is required.
    Write-Host "`n[4/5] Check Node.js version" -ForegroundColor Cyan
    $nodeVersion = & node.exe --version
    if ($LASTEXITCODE -ne 0) { throw 'Could not run node.exe --version.' }
    $nodeVersionText = ([string]($nodeVersion | Select-Object -First 1)).Trim()
    if ($nodeVersionText -notmatch '^v([0-9]+)\.') { throw "Unexpected Node.js version: $nodeVersionText" }
    if ([int]$Matches[1] -lt 20) { throw "Node.js 20 or later is required (found $nodeVersionText)." }
    Write-Host "Node.js $nodeVersionText verified." -ForegroundColor Green
    Push-Location -LiteralPath (Join-Path $Project 'whatsapp')
    try {
        Execute-Step '[4/5] Install WhatsApp dependencies' 'npm.cmd' @('install','--no-audit','--no-fund')
    } finally {
        Pop-Location
    }
    Execute-Step '[5/5] Apply WhatsApp compatibility layer' 'node.exe' @('whatsapp\compat\install.js')
    Execute-Step '[5/5] Prepare local AI secrets and settings' $venvPython @('ai_prepare.py')

    Write-Host "`nINSTALL COMPLETE. Run START.bat." -ForegroundColor Green
    $exitStatus = 0
} catch {
    Write-Host "`nINSTALLATION ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Please send the last part of INSTALL_LOG.txt if help is needed.' -ForegroundColor Yellow
} finally {
    if ($startedTranscript) {
        try { Stop-Transcript | Out-Null } catch { }
    }
}
exit $exitStatus
