# Surface Rxy ZXY Analyzer V4.2.1

面型及 Rxy 分析工具当前版本为 **V4.2.1**。GitHub `main` 根目录保留当前版本的运行入口、模块、文档、测试和 Demo；旧版本文件统一放在 [`archive/legacy_versions`](archive/legacy_versions/README.md)。

## 当前版本入口

- `面型及Rxy分析工具V4.2.1.py`：V4.2.1 Python 启动入口。
- `start_surface_analyzer_v4_2_1.bat`：Windows 推荐启动脚本，自动创建并复用仓库内 `.venv`。
- `surface_analyzer/`：模块化 GUI、分析、文件导入、ROI、Recipe、报告和公共接口实现。
- `requirements.txt`：Python 依赖清单。

## 运行

Windows 推荐双击：

```text
start_surface_analyzer_v4_2_1.bat
```

命令行运行：

```powershell
.\start_surface_analyzer_v4_2_1.bat
```

只检查环境和模块导入：

```powershell
.\start_surface_analyzer_v4_2_1.bat --check
```

也可以直接使用 Python 入口：

```powershell
python .\面型及Rxy分析工具V4.2.1.py
python -m surface_analyzer
```

## V4.2.1 修正

- 调整品牌图标中的三阶鞍形曲面位置和振幅，在物镜与曲面之间保留明确工作距离；只有测量光束接触曲面，避免小尺寸图标出现镜头与曲面粘连。

## V4.2.0 重点

- 全新透明背景品牌图标：简化光学探头与三阶鞍形彩色面型，并应用于窗口、任务栏、EXE 与 Setup。
- 启动时立即显示阶段式启动画面；启动失败会明确提示并写入本地错误日志。
- 高度矩阵无显式缺测标记时只兼容精确历史哨兵，不再误删所有小于 -999 的真实深负值。
- Recipe 大文件参数统一按 UI 合法范围钳制，避免异常配置造成过量内存分配。
- 批量报告对重名源文件自动追加序号，并在汇总中记录源文件与报告完整路径。
- BF 平面残差 PV 明确标注“仅去倾斜、未去曲率”；高阶拟合病态时在 UI 与报告中警告。
- 抽样分析报告强化估算状态，批量汇总增加 `sampled`、`extrema_preserved` 和 `warnings` 字段。
- 正式构建把真实 Git commit 写入安装包，`--check` 可追溯到准确源码提交。

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
