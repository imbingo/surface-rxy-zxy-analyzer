# Windows 安装与升级

V4.1.0 使用与 Overlay Measure 相同的两层打包方式：

1. PyInstaller `onedir` 构建 `dist/SurfaceRxyZxyAnalyzer/`。
2. Inno Setup 将运行目录封装为一个 `SurfaceRxyZxyAnalyzer_Setup_Vx.y.z.exe`。

安装程序默认安装到：

```text
C:\Program Files\Surface Rxy ZXY Analyzer
```

所有后续版本必须保持 `SurfaceRxyZxyAnalyzer.iss` 中的 `AppId` 不变，这样新版本
Setup 才能识别原安装目录并覆盖升级。

## 构建环境

- Windows 10/11 x64
- Python 3.10+
- `requirements-dev.txt`
- Inno Setup 6

## 一键构建

在仓库根目录运行：

```powershell
.\scripts\build_release.bat -Python ".\.venv\Scripts\python.exe"
```

脚本依次执行：

1. 校验版本号。
2. 运行完整单元测试。
3. 生成应用图标和 Windows EXE 版本资源。
4. 使用 PyInstaller 构建文件夹式应用。
5. 以 `--check` 验证打包后的 EXE。
6. 使用 Inno Setup 构建安装程序。
7. 生成 `update_manifest.json` 和 `.sha256`。
