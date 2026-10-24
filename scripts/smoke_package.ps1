Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repository = Split-Path -Parent $PSScriptRoot
$scratchRoot = Join-Path $repository '.dev'
$scratch = Join-Path $scratchRoot ("package-smoke-" + [guid]::NewGuid().ToString('N'))
$normalizedRoot = [System.IO.Path]::GetFullPath($scratchRoot + [System.IO.Path]::DirectorySeparatorChar)
$normalizedScratch = [System.IO.Path]::GetFullPath($scratch)
if (-not $normalizedScratch.StartsWith($normalizedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Package smoke directory must stay inside .dev.'
}

$oldTemp = $env:TEMP
$oldTmp = $env:TMP
$oldPipCache = $env:PIP_CACHE_DIR
New-Item -ItemType Directory -Path (Join-Path $scratch 'tmp'),
    (Join-Path $scratch 'wheels'), (Join-Path $scratch 'pip-cache') -Force | Out-Null

try {
    $env:TEMP = Join-Path $scratch 'tmp'
    $env:TMP = $env:TEMP
    $env:PIP_CACHE_DIR = Join-Path $scratch 'pip-cache'

    & python -m pip wheel $repository --no-build-isolation --no-deps --no-index `
        --wheel-dir (Join-Path $scratch 'wheels')
    if ($LASTEXITCODE -ne 0) { throw 'Offline wheel build failed.' }
    $wheels = @(Get-ChildItem -LiteralPath (Join-Path $scratch 'wheels') `
        -Filter 'runvori_host-*.whl' -File)
    if ($wheels.Count -ne 1) { throw 'Expected exactly one runvori-host wheel.' }

    $venv = Join-Path $scratch 'venv'
    & python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Isolated virtual environment creation failed.' }
    $python = Join-Path $venv 'Scripts\python.exe'
    $entrypoint = Join-Path $venv 'Scripts\runvori.exe'
    & $python -m pip install --no-index --no-deps $wheels[0].FullName
    if ($LASTEXITCODE -ne 0) { throw 'Offline wheel installation failed.' }

    Push-Location (Split-Path -Parent $repository)
    try {
        & $entrypoint verify-contracts --workspace $repository
        if ($LASTEXITCODE -ne 0) { throw 'Installed CLI contract verification failed.' }
        & $python -c 'import runvori.codex.execution_probe, runvori.infrastructure.approval_journal, runvori.infrastructure.artifact_probe, runvori.infrastructure.instance_lock, runvori.infrastructure.locked_journal, runvori.infrastructure.pairing_journal'
        if ($LASTEXITCODE -ne 0) { throw 'Installed module import failed.' }
    }
    finally { Pop-Location }
    Write-Output 'PASS offline_wheel isolated_install installed_cli module_imports'
}
finally {
    $env:TEMP = $oldTemp
    $env:TMP = $oldTmp
    $env:PIP_CACHE_DIR = $oldPipCache
    if (Test-Path -LiteralPath $scratch) {
        if ((Get-Item -LiteralPath $scratch).Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw 'Refusing to remove reparse-point package smoke directory.'
        }
        Remove-Item -LiteralPath $scratch -Recurse -Force
    }
}
