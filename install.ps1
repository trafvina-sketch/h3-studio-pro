<#
.SYNOPSIS
    Install the MiniMax H3 Colab skill into a Codex skills directory.

.DESCRIPTION
    PowerShell equivalent of install.sh.
    Copies SKILL.md, scripts/, and assets/ to the target skills directory.
    Non-destructive by default; use -Force to replace an existing installation.

.PARAMETER Dest
    Skills directory to use. Default: $env:CODEX_HOME\skills or $HOME\.codex\skills

.PARAMETER Force
    Replace an existing install after creating a timestamped backup.

.EXAMPLE
    .\install.ps1

.EXAMPLE
    .\install.ps1 -Dest "C:\my-codex\skills" -Force
#>

[CmdletBinding()]
param(
    [string]$Dest,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$SkillName = "minimax-h3-colab"
$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

# --- Determine target directory ---
if (-not $Dest) {
    if ($env:CODEX_HOME) {
        $SkillsRoot = Join-Path $env:CODEX_HOME "skills"
    } elseif ($HOME) {
        $SkillsRoot = Join-Path $HOME ".codex\skills"
    } else {
        Write-Error "Set CODEX_HOME or HOME before installing the skill."
        exit 2
    }
} else {
    $SkillsRoot = $Dest
}

$SkillsRoot = (Resolve-Path $SkillsRoot -ErrorAction SilentlyContinue)?.Path
if (-not $SkillsRoot) {
    $SkillsRoot = if ($Dest) { [System.IO.Path]::GetFullPath($Dest) }
                  elseif ($env:CODEX_HOME) { [System.IO.Path]::GetFullPath((Join-Path $env:CODEX_HOME "skills")) }
                  else { [System.IO.Path]::GetFullPath((Join-Path $HOME ".codex\skills")) }
}

$Target = Join-Path $SkillsRoot $SkillName

# --- Validate required source files ---
$RequiredFiles = @("SKILL.md", "scripts\runner.py", "assets\MiniMax_H3_Turbo_Colab.ipynb")
foreach ($f in $RequiredFiles) {
    $fullPath = Join-Path $RepoDir $f
    if (-not (Test-Path $fullPath)) {
        Write-Error "Repository is missing required file: $f"
        exit 2
    }
}

# --- Create skills root if needed ---
if (-not (Test-Path $SkillsRoot)) {
    New-Item -ItemType Directory -Path $SkillsRoot -Force | Out-Null
}

# --- Handle existing installation ---
if (Test-Path $Target) {
    if (-not $Force) {
        Write-Host "Skill already installed; preserving: $Target" -ForegroundColor Yellow
        Write-Host "Use -Force to replace it (the old directory will be backed up)."
        exit 0
    }

    # Backup existing
    $timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $BackupPath = "$Target.backup.$timestamp"
    $suffix = 0
    while (Test-Path $BackupPath) {
        $suffix++
        $BackupPath = "$Target.backup.$timestamp.$suffix"
    }

    Write-Host "Backing up existing installation to: $BackupPath" -ForegroundColor Gray
    Rename-Item -Path $Target -NewName (Split-Path -Leaf $BackupPath)
}

# --- Stage files in temp directory ---
$StagingDir = Join-Path $SkillsRoot ".${SkillName}.install.$([guid]::NewGuid().ToString('N').Substring(0,8))"
try {
    New-Item -ItemType Directory -Path $StagingDir -Force | Out-Null

    # Copy SKILL.md
    Copy-Item (Join-Path $RepoDir "SKILL.md") (Join-Path $StagingDir "SKILL.md")

    # Copy scripts/ and assets/
    Copy-Item (Join-Path $RepoDir "scripts") (Join-Path $StagingDir "scripts") -Recurse
    Copy-Item (Join-Path $RepoDir "assets")  (Join-Path $StagingDir "assets")  -Recurse

    # Remove __pycache__ if any
    Get-ChildItem -Path $StagingDir -Directory -Recurse -Filter "__pycache__" |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

    # Atomic rename
    Rename-Item -Path $StagingDir -NewName $SkillName
    $StagingDir = $null  # Prevent cleanup

    Write-Host "Installed $SkillName to $Target" -ForegroundColor Green
    if ($BackupPath -and (Test-Path $BackupPath)) {
        Write-Host "Previous installation preserved at $BackupPath" -ForegroundColor Gray
    }
}
catch {
    # Cleanup staging on failure
    if ($StagingDir -and (Test-Path $StagingDir)) {
        Remove-Item $StagingDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    # Restore backup if rename failed and target is gone
    if ($BackupPath -and (Test-Path $BackupPath) -and -not (Test-Path $Target)) {
        Rename-Item -Path $BackupPath -NewName $SkillName
    }
    Write-Error "Could not install skill at: $Target`n$_"
    exit 1
}
