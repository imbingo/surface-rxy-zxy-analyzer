# Surface Rxy ZXY Analyzer V4.1.0

面型及 Rxy 分析工具当前版本为 **V4.1.0**。GitHub `main` 根目录保留当前版本的运行入口、模块、文档、测试和 Demo；旧版本文件统一放在 [`archive/legacy_versions`](archive/legacy_versions/README.md)。

## 当前版本入口

- `面型及Rxy分析工具V4.1.0.py`：V4.1.0 Python 启动入口。
- `start_surface_analyzer_v4_1_0.bat`：Windows 推荐启动脚本，自动创建并复用仓库内 `.venv`。
- `surface_analyzer/`：模块化 GUI、分析、文件导入、ROI、Recipe、报告和公共接口实现。
- `requirements.txt`：Python 依赖清单。

## 运行

Windows 推荐双击：

```text
start_surface_analyzer_v4_1_0.bat
```

命令行运行：

```powershell
.\start_surface_analyzer_v4_1_0.bat
```

只检查环境和模块导入：

```powershell
.\start_surface_analyzer_v4_1_0.bat --check
```

也可以直接使用 Python 入口：

```powershell
python .\面型及Rxy分析工具V4.1.0.py
python -m surface_analyzer
```

## V4.1.0 重点

- 文件导入策略中显式选择 `XYZ 点表` 或 `Z 矩阵`，选择会自动记忆并写入 Recipe。
- 去除原先依赖“8列宽度”的点表/矩阵自动猜测，避免宽点表被误判为高度矩阵。
- 支持 TXT/Excel 单个物理列内用英文/中文分号等分隔的 XYZ 逻辑字段。
- 前置探头型号、光强设置、扫描参数等说明不会阻断导入，并进入可追溯元数据。
- 新增一阶、二阶、三阶去除后残差四视图，以及各阶残差 PV、RMS、R² 诊断结果。
- 保留现有最佳拟合平面法向 PV、Rx/Ry 和 TTV 口径，高阶显示不会静默修改权威结果。

## V4.0.3 基础能力

- 新增面型量测品牌图标，并统一应用于窗口、任务栏、EXE 和 Setup 安装程序。
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
