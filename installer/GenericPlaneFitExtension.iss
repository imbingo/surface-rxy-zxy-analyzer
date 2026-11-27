#ifndef ExtensionVersion
  #define ExtensionVersion "1.3.1"
#endif

[Setup]
AppId={{B658009D-8400-4336-A914-E3B89044D2F1}
AppName=Surface Analyzer Generic Plane Fit Extension
AppVersion={#ExtensionVersion}
AppPublisher=GCT
DefaultDirName={localappdata}\SurfaceRxyZxyAnalyzer\module_extensions\generic-plane-fit
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\release
OutputBaseFilename=SurfaceAnalyzer_GenericPlaneFitExtension_Setup_V{#ExtensionVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "..\extensions\generic-plane-fit\extension.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\extensions\generic-plane-fit\__init__.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\extensions\generic-plane-fit\engine.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\extensions\generic-plane-fit\module.py"; DestDir: "{app}"; Flags: ignoreversion

[Messages]
WelcomeLabel2=Installs the optional generic 3D plane fitting module. Install Surface Analyzer V5.0.9 or later and restart it after installation.
