# Install: .\packages.ps1 install requests
# Update:  .\packages.ps1 install --upgrade requests
# Remove:  .\packages.ps1 uninstall requests
# Refresh versions of tracked packages only: .\packages.ps1
$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$requirements = Join-Path $PSScriptRoot 'requirements.txt'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Project Python not found. Create .venv before running this script.'
}

$requested = @()
if ($args.Count -gt 0) {
    if ($args[0] -notin @('install', 'uninstall')) {
        throw 'Use install, uninstall, or no arguments to refresh requirements.txt.'
    }
    foreach ($argument in ($args | Select-Object -Skip 1)) {
        if ($argument -in @('--upgrade', '-U', '--no-deps', '--pre', '--no-index', '--yes', '-y')) {
            continue
        }
        if ($argument -notmatch '^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[A-Za-z0-9,._-]+\])?(?:[<>=!~].*)?$') {
            throw "Use package names (optionally with extras/versions); unsupported argument: $argument"
        }
        $requested += $matches[1] + $matches[2]
    }
    if ($requested.Count -eq 0) { throw 'Specify at least one package.' }
    & $python -m pip @args
    if ($LASTEXITCODE -ne 0) {
        throw 'pip failed; requirements.txt was not updated.'
    }
}

$tracked = @{}
if (Test-Path -LiteralPath $requirements) {
    foreach ($line in Get-Content -LiteralPath $requirements) {
        if ($line -match '^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[A-Za-z0-9,._-]+\])?==') {
            $key = $matches[1].ToLowerInvariant() -replace '[-_.]+', '-'
            $tracked[$key] = $matches[1] + $matches[2]
        }
    }
}
foreach ($package in $requested) {
    $key = (($package -split '\[')[0]).ToLowerInvariant() -replace '[-_.]+', '-'
    if ($args[0] -eq 'uninstall') {
        $tracked.Remove($key)
    } elseif (-not $tracked.ContainsKey($key) -or $package.Contains('[')) {
        $tracked[$key] = $package
    }
}

$installedJson = & $python -m pip list --format=json --disable-pip-version-check
if ($LASTEXITCODE -ne 0) {
    throw 'Could not read installed packages; requirements.txt was not updated.'
}
$packages = @(
    foreach ($package in ($installedJson | ConvertFrom-Json)) {
        $key = $package.name.ToLowerInvariant() -replace '[-_.]+', '-'
        if ($tracked.ContainsKey($key)) {
            "$($tracked[$key])==$($package.version)"
        }
    }
) | Sort-Object

$temporary = "$requirements.tmp"
try {
    [IO.File]::WriteAllLines($temporary, [string[]]$packages, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $requirements -Force
} finally {
    if (Test-Path -LiteralPath $temporary) {
        Remove-Item -LiteralPath $temporary
    }
}
Write-Host 'Updated requirements.txt with tracked project packages only.'
