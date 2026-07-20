# Surface Rxy ZXY Analyzer V4.0.2

面型及 Rxy 分析工具当前稳定版本为 **V4.0.2**。GitHub `main` 根目录只保留当前版本的运行入口、模块、文档、测试和 Demo；旧版本文件统一放在 [`archive/legacy_versions`](archive/legacy_versions/README.md)。

## 当前版本入口

- `面型及Rxy分析工具V4.0.2.py`：V4.0.2 Python 启动入口。
- `start_surface_analyzer_v4_0_2.bat`：Windows 推荐启动脚本，自动创建并复用仓库内 `.venv`。
- `surface_analyzer/`：模块化 GUI、分析、文件导入、ROI、Recipe、报告和公共接口实现。
- `requirements.txt`：Python 依赖清单。

## 运行

Windows 推荐双击：

```text
start_surface_analyzer_v4_0_2.bat
```

命令行运行：

```powershell
.\start_surface_analyzer_v4_0_2.bat
```

只检查环境和模块导入：

```powershell
.\start_surface_analyzer_v4_0_2.bat --check
```

也可以直接使用 Python 入口：

```powershell
python .\面型及Rxy分析工具V4.0.2.py
python -m surface_analyzer
```

## V4.0.2 重点

- 支持 XYZ、DAT、ASC、CSV、Excel 和 VR/基恩士高度矩阵。
- 支持 Zeiss/菲索类多行参数头和大文件预抽样。
- 高度矩阵可比较多个数值候选区，自动处理顶部列坐标、左侧行号和尾部空列。
- 支持矩形、圆形和智能抓面 ROI，并可保存到 Recipe 和报告。
- 支持局部中位数、Sigma 迭代滤波、手动删点及删除操作 Recipe 重放。
- 支持多层胶厚扣减、平行度、台阶高度、报告图和 CSV 导出。
- 提供无界面 Python/CLI 接口，便于 C# 平台调用。
- 主控和报告的 XY 视图保持物理等比例，不随窗口比例拉伸。
- 3D 视图按真实 X/Y 范围自适应，仅对过小的 Z 范围做受控视觉增强。
- 平行度页面在低高度窗口中使用纵向滚动，报告结果文字不再重叠。

## 文档

- [V4.0.2 接口文档](docs/V4.0.2_接口文档.md)
- [V4.0.2 模块架构](docs/V4.0.2_架构说明.md)
- [V4.0.2 本地测试清单](docs/V4.0.2_本地测试清单.md)
- [历史版本归档索引](archive/legacy_versions/README.md)

## 自动测试

```powershell
python -m unittest discover -s tests -v
```

## 历史版本

旧单文件、旧启动器和 V4.0 模块拆分工具已移至 `archive/legacy_versions`。旧 BAT 仅用于保留历史文件，不等同于冻结源码；需要准确恢复某一版本时，请按归档索引使用对应 Git 提交。
