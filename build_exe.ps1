param(
    [string]$OutputRoot = '.publish'
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '.')).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing $python. Create the virtual environment and install requirements.txt first."
}

$publish = [System.IO.Path]::GetFullPath((Join-Path $root $OutputRoot))
$dist = Join-Path $publish 'ChatGPTWebAutomation'
$work = Join-Path $publish 'build_gui'
$spec = Join-Path $publish 'spec_gui'

function Remove-PublishPath([string]$PathToRemove) {
    $full = [System.IO.Path]::GetFullPath($PathToRemove)
    $prefix = $publish.TrimEnd('\') + '\'
    if (-not $full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to delete a path outside the publish directory: $full"
    }
    if (Test-Path -LiteralPath $full) {
        Remove-Item -LiteralPath $full -Recurse -Force
    }
}

New-Item -ItemType Directory -Force -Path $publish | Out-Null
Remove-PublishPath $dist
Remove-PublishPath $work
Remove-PublishPath $spec

& $python -m PyInstaller --noconfirm --clean --onedir --windowed `
    --name ChatGPTWebAutomation `
    --distpath $publish `
    --workpath $work `
    --specpath $spec `
    --collect-all playwright `
    (Join-Path $root 'gui.py')
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Copy-Item -LiteralPath (Join-Path $root 'config.example.yaml') -Destination $dist -Force
Copy-Item -LiteralPath (Join-Path $root 'README.md') -Destination $dist -Force
Copy-Item -LiteralPath (Join-Path $root 'prompts') -Destination (Join-Path $dist 'prompts') -Recurse -Force
$bat = @(
    '@echo off'
    'cd /d "%~dp0"'
    'ChatGPTWebAutomation.exe'
)
$bat | Set-Content -LiteralPath (Join-Path $dist 'start_gui.bat') -Encoding ascii

Write-Host "Build complete: $dist\ChatGPTWebAutomation.exe"
Write-Host "Copy config.example.yaml to config.yaml and edit your conversation URL and prompts."
