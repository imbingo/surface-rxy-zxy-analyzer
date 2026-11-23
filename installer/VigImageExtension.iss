#define ExtensionVersion "1.0.0"

[Setup]
AppId={{D28E59E8-5016-4D72-9A9C-77A76BE55A12}
AppName=Surface Analyzer VIG Image Extension
AppVersion={#ExtensionVersion}
AppPublisher=GCT
DefaultDirName={localappdata}\SurfaceRxyZxyAnalyzer\module_extensions\vig-image
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\release
OutputBaseFilename=SurfaceAnalyzer_VigImageExtension_Setup_V{#ExtensionVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Surface Analyzer VIG Image Extension

[Files]
Source: "..\extensions\vig-image\extension.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\extensions\vig-image\__init__.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\extensions\vig-image\engine.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\extensions\vig-image\module.py"; DestDir: "{app}"; Flags: ignoreversion

[Messages]
WelcomeLabel2=Installs VIG image-plane analysis for Surface Analyzer V5.0.6 or later.%n%nInstall the main application first. Close and restart Surface Analyzer after installing this extension.
