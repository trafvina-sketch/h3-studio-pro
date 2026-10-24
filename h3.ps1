<#
.SYNOPSIS
    Run MiniMax H3 Colab Skill via WSL Ubuntu.

.DESCRIPTION
    Wrapper script that calls runner.py through WSL Ubuntu-24.04
    where google-colab-cli is properly installed.

.PARAMETER Action
    Action to perform: usage, single, batch

.PARAMETER Image
    Reference image paths (for single mode, 1-9 images)

.PARAMETER Prompt
    Path to prompt text file (for single mode)

.PARAMETER Output
    Output MP4 path (for single mode)

.PARAMETER Manifest
    Path to batch manifest JSON (for batch mode)

.PARAMETER OutputDir
    Output directory for batch results (for batch mode)

.PARAMETER Gpu
    GPU model (default: A100)

.PARAMETER Timeout
    Execution timeout in seconds (default: 3600)

.EXAMPLE
    # Check compute units balance
    .\h3.ps1 -Action usage

.EXAMPLE
    # Authenticate (first time)
    .\h3.ps1 -Action auth

.EXAMPLE
    # Generate single video
    .\h3.ps1 -Action single -Image "D:\refs\girl.png" -Prompt "D:\prompts\intro.txt" -Output "D:\output\video.mp4"

.EXAMPLE
    # Generate batch
    .\h3.ps1 -Action batch -Manifest "D:\jobs\manifest.json" -OutputDir "D:\output"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("auth", "usage", "single", "batch")]
    [string]$Action,

    [string[]]$Image,
    [string]$Prompt,
    [string]$Output,
    [string]$Manifest,
    [string]$OutputDir,
    [string]$Gpu = "A100",
    [int]$Timeout = 3600
)

$ErrorActionPreference = 'Stop'
$WSL_DISTRO = "Ubuntu-24.04"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

# Convert Windows path to WSL path
function ConvertTo-WslPath([string]$WinPath) {
    $resolved = (Resolve-Path $WinPath -ErrorAction Stop).Path
    $resolved = $resolved -replace '\\', '/'
    if ($resolved -match '^([A-Za-z]):(.*)$') {
        return "/mnt/$($Matches[1].ToLower())$($Matches[2])"
    }
    return $resolved
}

# WSL runner path
$WslScriptDir = ConvertTo-WslPath $ScriptDir
$WslRunner = "$WslScriptDir/scripts/runner.py"

# Source uv env before any colab command
$EnvSetup = "source ~/.local/bin/env 2>/dev/null; "

switch ($Action) {
    "auth" {
        Write-Host "`n  Authenticating with Google Colab..." -ForegroundColor Cyan
        Write-Host "  A browser window will open. Follow the instructions to authorize.`n" -ForegroundColor Gray
        wsl -d $WSL_DISTRO -- bash -c "${EnvSetup}colab --auth=oauth2 usage"
    }

    "usage" {
        Write-Host "`n  Checking Colab compute units...`n" -ForegroundColor Cyan
        wsl -d $WSL_DISTRO -- bash -c "${EnvSetup}python3 '$WslRunner' usage --json"
    }

    "single" {
        if (-not $Image -or $Image.Count -lt 1) {
            Write-Error "Provide at least one -Image path."
            exit 2
        }
        if (-not $Prompt) {
            Write-Error "Provide a -Prompt file path."
            exit 2
        }

        # Build args
        $wslArgs = "single"
        foreach ($img in $Image) {
            $wslImg = ConvertTo-WslPath $img
            $wslArgs += " --image '$wslImg'"
        }
        $wslPrompt = ConvertTo-WslPath $Prompt
        $wslArgs += " --prompt '$wslPrompt'"
        $wslArgs += " --gpu $Gpu --timeout $Timeout"

        if ($Output) {
            # Create output directory if needed
            $outDir = Split-Path -Parent $Output
            if ($outDir -and -not (Test-Path $outDir)) {
                New-Item -ItemType Directory -Path $outDir -Force | Out-Null
            }
            $wslOutput = ConvertTo-WslPath (Split-Path -Parent $Output)
            $wslOutput += "/" + (Split-Path -Leaf $Output)
            $wslArgs += " --output '$wslOutput'"
        }

        Write-Host "`n  Starting MiniMax H3 inference..." -ForegroundColor Cyan
        Write-Host "  Images : $($Image.Count)" -ForegroundColor Gray
        Write-Host "  GPU    : $Gpu" -ForegroundColor Gray
        Write-Host "  Timeout: ${Timeout}s`n" -ForegroundColor Gray

        wsl -d $WSL_DISTRO -- bash -c "${EnvSetup}python3 '$WslRunner' $wslArgs"
    }

    "batch" {
        if (-not $Manifest) {
            Write-Error "Provide a -Manifest JSON file path."
            exit 2
        }
        if (-not $OutputDir) { $OutputDir = ".\output" }
        if (-not (Test-Path $OutputDir)) {
            New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
        }

        $wslManifest = ConvertTo-WslPath $Manifest
        $wslOutputDir = ConvertTo-WslPath $OutputDir

        Write-Host "`n  Starting batch inference..." -ForegroundColor Cyan
        Write-Host "  Manifest: $Manifest" -ForegroundColor Gray
        Write-Host "  GPU     : $Gpu" -ForegroundColor Gray
        Write-Host "  Output  : $OutputDir`n" -ForegroundColor Gray

        wsl -d $WSL_DISTRO -- bash -c "${EnvSetup}python3 '$WslRunner' batch --manifest '$wslManifest' --gpu $Gpu --timeout $Timeout --output-dir '$wslOutputDir'"
    }
}

$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    Write-Host "`n  Command failed with exit code $exitCode" -ForegroundColor Red
    exit $exitCode
} else {
    Write-Host "`n  Done!" -ForegroundColor Green
}
