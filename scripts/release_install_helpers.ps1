function Resolve-ReleaseUninstaller {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$InstallRoot,
          [Parameter(Mandatory=$true)][string]$UninstallCommand)
    $command=$UninstallCommand.Trim()
    if ($command -match '^"([^"\r\n]+\.exe)"$') { $candidate=$Matches[1] }
    elseif ($command -notmatch '["\r\n]' -and $command.EndsWith('.exe',[StringComparison]::OrdinalIgnoreCase)) { $candidate=$command }
    else { throw 'Unexpected registered uninstall command; refusing to execute it.' }
    $root=[IO.Path]::GetFullPath($InstallRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $resolved=[IO.Path]::GetFullPath($candidate)
    if (-not [IO.Path]::GetDirectoryName($resolved).Equals($root,[StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($resolved) -notmatch '^unins\d+\.exe$' -or
        -not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw 'Registered uninstaller is not an existing Inno uninstaller in the QA installation directory.'
    }
    return $resolved
}
