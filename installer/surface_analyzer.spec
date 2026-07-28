# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


project_root = Path(SPECPATH).parent
version_file = project_root / "installer" / "generated_version_info.txt"
icon_file = project_root / "assets" / "SurfaceRxyZxyAnalyzer.ico"
icon_png = project_root / "assets" / "SurfaceRxyZxyAnalyzer.png"

if not version_file.exists():
    raise SystemExit(
        "Missing installer/generated_version_info.txt. "
        "Run scripts/build_release.ps1 instead of invoking this spec directly."
    )
if not icon_file.exists():
    raise SystemExit(
        "Missing assets/SurfaceRxyZxyAnalyzer.ico. "
        "Run scripts/build_release.ps1 instead of invoking this spec directly."
    )

demo_files = [
    project_root / "demo_data" / "V3.9_StepDemo_README.txt",
    project_root / "demo_data" / "V3.9_StepDemo_VR_height_matrix.csv",
    project_root / "demo_data" / "V3.9_StepDemo_XYZ_points.csv",
]
datas = [(str(path), "demo_data") for path in demo_files if path.exists()]
if icon_png.exists():
    datas.append((str(icon_png), "assets"))

hiddenimports = collect_submodules("surface_analyzer") + [
    "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_agg",
    "mpl_toolkits.mplot3d",
    "scipy.ndimage",
    "scipy.spatial._ckdtree",
    "openpyxl",
    "xlrd",
]

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt5",
        "PySide2",
        "PySide6",
        "tkinter",
        "pytest",
        "IPython",
        "jupyter",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SurfaceRxyZxyAnalyzer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_file),
    version=str(version_file),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SurfaceRxyZxyAnalyzer",
)
