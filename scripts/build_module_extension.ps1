param([string]$Id = 'vig-image')
$ErrorActionPreference = 'Stop'
if ($Id -ne 'vig-image') { throw 'No installer definition registered for this module extension.' }
$Root = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $Root "extensions/$Id/extension.json"
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ($Manifest.api_version -ne 1 -or $Manifest.id -ne $Id) {
    throw 'Invalid module extension manifest.'
}
$Candidates = @(
    (Get-Command "ISCC.exe" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    (Join-Path $env:LOCALAPPDATA 'Programs/Inno Setup 6/ISCC.exe'),
    'C:/Program Files (x86)/Inno Setup 6/ISCC.exe',
    'C:/Program Files/Inno Setup 6/ISCC.exe'
)
$Compiler = $Candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $Compiler) { throw 'Inno Setup 6 is required.' }
& $Compiler (Join-Path $Root 'installer/VigImageExtension.iss')
if ($LASTEXITCODE -ne 0) { throw 'Module extension installer compilation failed.' }
$Artifact = Join-Path $Root "release/SurfaceAnalyzer_VigImageExtension_Setup_V$($Manifest.version).exe"
$Hash = (Get-FileHash -LiteralPath $Artifact -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$Artifact.sha256" `
    -Value "$Hash  $([IO.Path]::GetFileName($Artifact))" -Encoding ascii
Write-Output $Artifact
