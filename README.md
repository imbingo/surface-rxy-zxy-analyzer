# Surface Rxy ZXY Analyzer

面型及 Rxy 分析 ZXY 版工具，当前主程序已升级到 V3.3。

## 文件

- `面型及Rxy分析ZXY版.py`: PyQt6 GUI 主程序（V3.3）。
- `requirements.txt`: 运行所需的 Python 依赖。

## 运行

```powershell
python -m pip install -r requirements.txt
python .\面型及Rxy分析ZXY版.py
```

## 说明

- V3.3 支持 CSV/TXT/TSV/DAT/ASC/XYZ 和 Excel 文件（XLSX/XLS/XLSM）读取。
- 内部 Z 单位按 mm 计算，导出 `Z_um` 和 `Resid_um` 时会换算为 µm。
- `去倾斜显示` 只影响绘图和框选，不改变 Rx/Ry/PV/TTV 指标计算。
