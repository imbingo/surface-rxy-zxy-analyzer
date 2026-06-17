# Surface Rxy ZXY Analyzer

面型及 Rxy 分析工具，当前主程序已更新到 V3.5.2。

## 文件

- `面型及Rxy分析ZXY版.py`: PyQt6 GUI 主程序，内容为 V3.5.2 最新版。
- `requirements.txt`: 运行所需 Python 依赖。

## 运行

```powershell
python -m pip install -r requirements.txt
python .\面型及Rxy分析ZXY版.py
```

## V3.5.2 重点

- 支持超大 TXT / ASC / XYZ 文件按文件位置预抽样导入。
- 支持 Zeiss 文本常见缺测值如 `***`、`--`、`NA` 按空值处理。
- 导入状态会显示文件大小、导入方式、导入点数和显示点数。
- 绘图显示抽样上限可配置，指标按导入后的分析数据计算。
- Recipe 导出/导入可保存单位、列映射、旋转组合、滤波参数、显示和大文件策略。
