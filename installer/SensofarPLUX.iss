#define ExtensionVersion "1.0.0"

[Setup]
AppId={{08FD4D44-80B8-4580-9609-1D76F02CC142}
AppName=Surface Analyzer Sensofar PLUX Extension
AppVersion={#ExtensionVersion}
AppPublisher=GCT
DefaultDirName={localappdata}\SurfaceRxyZxyAnalyzer\format_extensions\sensofar-plux
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\release
OutputBaseFilename=SurfaceAnalyzer_SensofarPLUX_Setup_V{#ExtensionVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Surface Analyzer Sensofar PLUX Extension

[Files]
Source: "..\extensions\sensofar-plux\extension.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\extensions\sensofar-plux\reader.py"; DestDir: "{app}"; Flags: ignoreversion

[Messages]
WelcomeLabel2=Installs Sensofar PLUX support for Surface Analyzer V4.7.7 or later.%n%nInstall the main application first. Close and restart Surface Analyzer after installing this extension.%n%nThis version supports the known single-height-layer PLUX layout. Validate measurement scales against your instrument before production use.
