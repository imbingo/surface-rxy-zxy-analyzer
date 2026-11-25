param([Parameter(Mandatory=$true)][string]$WorkRoot)
$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $PSScriptRoot
$Key='HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{D28E59E8-5016-4D72-9A9C-77A76BE55A12}_is1'
if (Test-Path $Key) { throw 'VIG already installed; refusing to replace user installation.' }
$WorkRoot=[IO.Path]::GetFullPath($WorkRoot)
New-Item -ItemType Directory -Force $WorkRoot | Out-Null
$Install=Join-Path $WorkRoot 'installed-vig'
function Run-QA([string]$Exe,[string[]]$Arguments,[int]$Seconds=240) {
    $p=Start-Process $Exe -ArgumentList $Arguments -WindowStyle Hidden -PassThru
    if (!$p.WaitForExit($Seconds*1000)) { & taskkill /PID $p.Id /T /F; throw 'VIG installation QA timeout' }
    $p.Refresh()
    if ($p.ExitCode -ne 0) { throw "VIG installation QA failed: $($p.ExitCode)" }
}
function Install-QA([string]$Setup,[string]$Log) {
    Run-QA $Setup @('/CURRENTUSER','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOICONS',"/DIR=`"$Install`"","/LOG=`"$WorkRoot/$Log.log`"")
}
# Build the previous extension from the merged V5.0.6 commit. It was not
# assumed to have been published as a GitHub Release.
$Previous=Join-Path $WorkRoot 'previous-source'
New-Item -ItemType Directory -Force $Previous | Out-Null
git -C $Root archive origin/main -o "$WorkRoot/previous.zip"
if ($LASTEXITCODE -ne 0) { throw 'Cannot read previous extension source' }
Expand-Archive "$WorkRoot/previous.zip" $Previous
$Manifest=Get-Content "$Previous/extensions/vig-image/extension.json" -Raw | ConvertFrom-Json
if ($Manifest.version -ne '1.0.0') { throw 'Baseline extension changed; review upgrade QA baseline' }
$Iscc=@((Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),'C:\Program Files (x86)\Inno Setup 6\ISCC.exe','C:\Program Files\Inno Setup 6\ISCC.exe') | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
& $Iscc "$Previous/installer/VigImageExtension.iss"
if ($LASTEXITCODE -ne 0) { throw 'Previous extension build failed' }
Install-QA "$Previous/release/SurfaceAnalyzer_VigImageExtension_Setup_V1.0.0.exe" 'previous'
if ((Get-Content "$Install/extension.json" -Raw | ConvertFrom-Json).version -ne '1.0.0') { throw 'Previous installation missing' }
Install-QA "$Root/release/SurfaceAnalyzer_VigImageExtension_Setup_V1.1.0.exe" 'upgrade'
if ((Get-Content "$Install/extension.json" -Raw | ConvertFrom-Json).version -ne '1.1.0') { throw 'Extension upgrade failed' }
$Main="$Root/dist/SurfaceRxyZxyAnalyzer/SurfaceRxyZxyAnalyzer.exe"
Run-QA $Main @('--check-v507','--vig-extension',"`"$Install`"",'--output-json',"`"$WorkRoot/installed-vig-check.json`"")
$Registered=(Get-ItemPropertyValue $Key -Name UninstallString).Trim('"')
if (![IO.Path]::GetFullPath($Registered).StartsWith($Install+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected extension uninstaller path' }
Run-QA $Registered @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART')
if (Test-Path "$Install/extension.json") { throw 'Extension uninstall left manifest behind' }
@{ previous='1.0.0';version='1.1.0';app_id_preserved=$true;isolated_install=$true;upgrade=$true;frozen_vig=$true;uninstall=$true } | ConvertTo-Json | Set-Content "$WorkRoot/extension-installer-qa.json"
