<#
.SYNOPSIS
    Run one MiniMax H3 Ref2VA inference on Google Colab.

.DESCRIPTION
    PowerShell equivalent of run_colab_inference.sh.
    Uploads 1-9 local reference images and a UTF-8 prompt file,
    runs the bundled notebook on a Colab GPU session, and downloads the MP4.

.PARAMETER Image
    One or more local reference image paths (1-9). Order maps to <Picture 1> through <Picture 9>.

.PARAMETER Prompt
    Path to a local UTF-8 prompt text file (required).

.PARAMETER Output
    Output MP4 path. Default: next to the first image with _minimax_h3.mp4 suffix.

.PARAMETER Help
    Show this help message.

.EXAMPLE
    .\run_colab_inference.ps1 -Image "C:\refs\girl.png" -Prompt "C:\prompts\intro.txt" -Output "C:\output\intro.mp4"

.EXAMPLE
    .\run_colab_inference.ps1 -Image "ref1.png","ref2.jpg" -Prompt prompt.txt

.NOTES
    Environment overrides:
      COLAB_AUTH           - CLI auth provider (default: oauth2)
      COLAB_GPU            - Colab GPU model (default: A100)
      COLAB_HIGH_MEM       - Request high-RAM (default: 1; set 0 to disable)
      COLAB_EXEC_TIMEOUT   - Notebook execution timeout in seconds (default: 3600)
      COLAB_SESSION_NAME   - Named session for the run
      H3_DURATION_SECONDS  - Clip duration 4-15 seconds (default: 12)
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string[]]$Image,

    [Parameter(Mandatory = $true)]
    [string]$Prompt,

    [string]$Output,

    [switch]$Help
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Runner = Join-Path $ScriptDir "scripts\runner.py"

if ($Help) {
    Get-Help $MyInvocation.MyCommand.Definition -Detailed
    exit 0
}

# --- Validate inputs ---
if ($Image.Count -lt 1 -or $Image.Count -gt 9) {
    Write-Error "Provide between 1 and 9 reference images with -Image."
    exit 2
}

foreach ($img in $Image) {
    $resolved = Resolve-Path $img -ErrorAction SilentlyContinue
    if (-not $resolved -or -not (Test-Path $resolved) -or (Get-Item $resolved).Length -eq 0) {
        Write-Error "Reference image is missing or empty: $img"
        exit 2
    }
}

$PromptResolved = Resolve-Path $Prompt -ErrorAction SilentlyContinue
if (-not $PromptResolved -or -not (Test-Path $PromptResolved) -or (Get-Item $PromptResolved).Length -eq 0) {
    Write-Error "Prompt file is missing or empty: $Prompt"
    exit 2
}

if (-not (Test-Path $Runner)) {
    Write-Error "Bundled runner not found: $Runner"
    exit 2
}

# Check colab CLI
$colabCmd = Get-Command colab -ErrorAction SilentlyContinue
if (-not $colabCmd) {
    Write-Error "Colab CLI is missing. Install it with: uv tool install google-colab-cli"
    exit 127
}

# --- Read environment overrides ---
$HighMem = if ($env:COLAB_HIGH_MEM) { $env:COLAB_HIGH_MEM } else { "1" }
if ($HighMem -notmatch '^[01]$') {
    Write-Error "COLAB_HIGH_MEM must be 0 or 1."
    exit 2
}

$ExecTimeout = if ($env:COLAB_EXEC_TIMEOUT) { $env:COLAB_EXEC_TIMEOUT } else { "3600" }
if ($ExecTimeout -notmatch '^\d+$' -or [int]$ExecTimeout -le 0) {
    Write-Error "COLAB_EXEC_TIMEOUT must be a positive integer."
    exit 2
}

$Gpu = if ($env:COLAB_GPU) { $env:COLAB_GPU } else { "A100" }

# --- Build arguments ---
$Args = @("single")
foreach ($img in $Image) {
    $Args += "--image"
    $Args += (Resolve-Path $img).Path
}
$Args += "--prompt"
$Args += (Resolve-Path $Prompt).Path
$Args += "--gpu"
$Args += $Gpu
$Args += "--timeout"
$Args += $ExecTimeout

if ($Output) {
    $Args += "--output"
    $Args += $Output
}

if ($HighMem -eq "0") {
    $Args += "--no-high-mem"
}

# --- Run ---
Write-Host "Starting MiniMax H3 inference..." -ForegroundColor Cyan
Write-Host "  Images : $($Image.Count)" -ForegroundColor Gray
Write-Host "  GPU    : $Gpu" -ForegroundColor Gray
Write-Host "  Timeout: ${ExecTimeout}s" -ForegroundColor Gray

& python $Runner @Args
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Error "Inference failed with exit code $exitCode"
    exit $exitCode
}
