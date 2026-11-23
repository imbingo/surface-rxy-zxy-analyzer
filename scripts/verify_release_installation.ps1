param(
    [Parameter(Mandatory=$true)][string]$Setup,
    [string]$PreviousSetup,
    [Parameter(Mandatory=$true)][string]$WorkRoot,
    [string]$ExpectedVersion = 'V5.0.5',
    [Parameter(Mandatory=$true)][string]$ExpectedCommit
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'release_install_helpers.ps1')
# Run on a fresh CI account. Refuse to replace an existing user's installation.
$UninstallKey='HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{052d48fd-7fab-45bc-b78d-1efd35e50075}_is1'
if (Test-Path -LiteralPath $UninstallKey) { throw 'Per-user installation already exists; refusing to replace it during QA.' }
$WorkRoot=[IO.Path]::GetFullPath($WorkRoot)
New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null
$Install=Join-Path $WorkRoot 'installed'
function Run-Hidden([string]$File,[string[]]$Arguments,[int]$Seconds=300) {
    $process=Start-Process -FilePath $File -ArgumentList $Arguments -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit($Seconds*1000)) {
        & taskkill.exe /PID $process.Id /T /F | Out-Null
        throw "QA process exceeded $Seconds seconds: $File"
    }
    $process.Refresh()
    if ($process.ExitCode -ne 0) { throw "QA process failed ($($process.ExitCode)): $File" }
}
function Install-Package([string]$Path,[string]$Log) {
    Run-Hidden $Path @('/CURRENTUSER','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOICONS',"/DIR=`"$Install`"","/LOG=`"$Log`"") 300
}
function Check-Installed([string]$Name) {
    $exe=Join-Path $Install 'SurfaceRxyZxyAnalyzer.exe'
    foreach ($mode in @('workspace','composite')) {
        $report=Join-Path $WorkRoot "$Name-$mode.json"
        Run-Hidden $exe @("--check-$mode",'--output-json',"`"$report`"") 300
        $data=Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
        if ($data.version -ne $ExpectedVersion -or $data.source_commit -ne $ExpectedCommit) { throw 'Installed source/version mismatch' }
        if ($mode -eq 'composite' -and -not $data.ok) { throw 'Installed composite protocol failed' }
    }
}
function Uninstall-Package {
    $registered=Get-ItemPropertyValue -LiteralPath $UninstallKey -Name 'UninstallString'
    $uninstaller=Resolve-ReleaseUninstaller -InstallRoot $Install -UninstallCommand $registered
    Run-Hidden $uninstaller @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') 180
}
$UserData=Join-Path $env:LOCALAPPDATA 'SurfaceRxyZxyAnalyzer\recipes'
New-Item -ItemType Directory -Force -Path $UserData | Out-Null
$Canary=Join-Path $UserData 'release-upgrade-canary.json'
if (Test-Path -LiteralPath $Canary) { throw 'QA canary exists; refusing to overwrite user data' }
'{"schema_version":9,"filter":{"mode_index":0},"library_metadata":{"name":"Upgrade QA"}}' | Set-Content -LiteralPath $Canary -Encoding utf8
$CanaryHash=(Get-FileHash -LiteralPath $Canary -Algorithm SHA256).Hash
$SettingKey='HKCU:\Software\SurfaceRxyZxyAnalyzer\SurfaceAnalyzer'
New-Item -Path $SettingKey -Force | Out-Null
New-ItemProperty -Path $SettingKey -Name 'release_upgrade_canary' -Value 'retain-v504' -PropertyType String -Force | Out-Null
function Assert-Data {
    if ((Get-FileHash -LiteralPath $Canary -Algorithm SHA256).Hash -ne $CanaryHash) { throw 'Recipe changed during install/upgrade/uninstall' }
    if ((Get-ItemPropertyValue -Path $SettingKey -Name 'release_upgrade_canary') -ne 'retain-v504') { throw 'User settings were lost' }
}
Install-Package $Setup (Join-Path $WorkRoot 'clean-install.log')
Check-Installed 'clean'
Assert-Data
Uninstall-Package
Assert-Data
if (Test-Path -LiteralPath (Join-Path $Install 'SurfaceRxyZxyAnalyzer.exe')) { throw 'Clean uninstall left executable behind' }
if ($PreviousSetup) {
    Install-Package $PreviousSetup (Join-Path $WorkRoot 'previous-install.log')
    Assert-Data
    Install-Package $Setup (Join-Path $WorkRoot 'upgrade-install.log')
    Assert-Data
    Check-Installed 'upgraded'
    Uninstall-Package
    Assert-Data
}
@{ version=$ExpectedVersion;source_commit=$ExpectedCommit;clean_install=$true;uninstall=$true;upgrade=[bool]$PreviousSetup;user_data_preserved=$true } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $WorkRoot 'installer-qa.json') -Encoding utf8
