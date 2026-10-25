param([string]$Id = 'sensofar-plux')
$ErrorActionPreference = 'Stop'
if ($Id -ne 'sensofar-plux') { throw 'No installer definition registered for this extension.' }
$Root = Split-Path -Parent $PSScriptRoot
$Manifest = Get-Content -LiteralPath (Join-Path $Root "extensions/$Id/extension.json") -Raw | ConvertFrom-Json
$Candidates = @(
    (Join-Path $env:LOCALAPPDATA 'Programs/Inno Setup 6/ISCC.exe'),
    'C:/Program Files (x86)/Inno Setup 6/ISCC.exe'
)
$Compiler = $Candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Compiler) { throw 'Inno Setup 6 is required.' }
& $Compiler (Join-Path $Root 'installer/SensofarPLUX.iss')
if ($LASTEXITCODE -ne 0) { throw 'Extension installer compilation failed.' }
$Artifact = Join-Path $Root "release/SurfaceAnalyzer_SensofarPLUX_Setup_V$($Manifest.version).exe"
$Hash = (Get-FileHash -LiteralPath $Artifact -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$Artifact.sha256" -Value "$Hash  $([IO.Path]::GetFileName($Artifact))" -Encoding ascii
Write-Output $Artifact
