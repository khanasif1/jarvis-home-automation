# Build the versioned Pi wheel, source archive, installer bundle, and checksums.
[CmdletBinding()]
param(
    [string]$Version,
    [string]$OutputDir
)

$ErrorActionPreference = 'Stop'
$env:PYTHONDONTWRITEBYTECODE = '1'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$piClientDir = (Resolve-Path (Join-Path $scriptDir '..')).Path
$sourceRoot = (Resolve-Path (Join-Path $piClientDir '..')).Path
if (-not $OutputDir) {
    $OutputDir = Join-Path $sourceRoot '.test-artifacts\pi-client-release'
}
$OutputDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDir)

$packageVersion = python -c "import runpy; print(runpy.run_path(r'$piClientDir\src\home_assistant_pi\version.py')['__version__'])"
if (-not $packageVersion) { throw 'Could not determine package version.' }
if ($Version -and $Version -ne $packageVersion) {
    throw "Requested version $Version does not match package version $packageVersion."
}

Write-Host "[build-release] Building home-assistant-pi $packageVersion"
if (Test-Path $OutputDir) { Remove-Item -Recurse -Force $OutputDir }
$distDir = Join-Path $OutputDir 'dist'
$bundleStage = Join-Path $OutputDir 'bundle-staging'
$buildSource = Join-Path $OutputDir 'build-source'
New-Item -ItemType Directory -Force -Path $distDir, $bundleStage, $buildSource | Out-Null
Copy-Item (Join-Path $piClientDir 'pyproject.toml') $buildSource
Copy-Item (Join-Path $piClientDir 'README.md') $buildSource
Copy-Item (Join-Path $piClientDir 'src') $buildSource -Recurse

python -m build --wheel --sdist --outdir $distDir $buildSource
if ($LASTEXITCODE -ne 0) { throw 'Wheel/source build failed.' }
Remove-Item -Recurse -Force $buildSource

$wheel = Get-ChildItem $distDir -Filter '*.whl' | Select-Object -First 1
$sdist = Get-ChildItem $distDir -Filter '*.tar.gz' | Select-Object -First 1
if (-not $wheel -or -not $sdist) { throw 'Build did not produce a wheel and sdist.' }

Copy-Item $wheel.FullName $bundleStage
Copy-Item (Join-Path $piClientDir 'scripts\install.sh') $bundleStage
Copy-Item (Join-Path $piClientDir 'scripts\update.sh') $bundleStage
Copy-Item (Join-Path $piClientDir 'scripts\uninstall.sh') $bundleStage
Copy-Item (Join-Path $piClientDir '.env.example') (Join-Path $bundleStage 'config.env.example')
Copy-Item (Join-Path $piClientDir 'requirements-runtime.txt') $bundleStage
Copy-Item (Join-Path $piClientDir 'THIRD_PARTY_NOTICES.md') $bundleStage
[IO.File]::WriteAllText((Join-Path $bundleStage 'VERSION'), "$packageVersion`n", [Text.UTF8Encoding]::new($false))

$wheelHash = (Get-FileHash -Algorithm SHA256 $wheel.FullName).Hash.ToLowerInvariant()
$manifest = [ordered]@{
    name = 'home-assistant-pi'
    version = $packageVersion
    wheel = $wheel.Name
    wheelSha256 = $wheelHash
    files = @(
        $wheel.Name,
        'install.sh',
        'update.sh',
        'uninstall.sh',
        'config.env.example',
        'requirements-runtime.txt',
        'THIRD_PARTY_NOTICES.md',
        'VERSION',
        'release-manifest.json'
    )
} | ConvertTo-Json -Depth 3
[IO.File]::WriteAllText((Join-Path $bundleStage 'release-manifest.json'), "$manifest`n", [Text.UTF8Encoding]::new($false))

$bundle = Join-Path $distDir "home-assistant-pi-bundle-$packageVersion.tar.gz"
python (Join-Path $scriptDir 'create-release-archive.py') $bundleStage $bundle
if ($LASTEXITCODE -ne 0) { throw 'Bundle archive creation failed.' }

$checksumFiles = @($wheel.FullName, $sdist.FullName, $bundle)
$checksumLines = foreach ($file in $checksumFiles) {
    $hash = (Get-FileHash -Algorithm SHA256 $file).Hash.ToLowerInvariant()
    "$hash  $(Split-Path -Leaf $file)"
}
[IO.File]::WriteAllText(
    (Join-Path $distDir 'SHA256SUMS'),
    (($checksumLines -join "`n") + "`n"),
    [Text.UTF8Encoding]::new($false)
)
Remove-Item -Recurse -Force $bundleStage
Write-Host "[build-release] Artifacts written to $distDir"
Get-ChildItem $distDir | Format-Table Name, Length
