# 文件格式扩展 API v1

主程序 V4.7.7 起支持可选 reader。扩展独立安装、升级及卸载，不加入主安装包。
安装位置：`%LOCALAPPDATA%/SurfaceRxyZxyAnalyzer/format_extensions/<id>/`。
主程序升级不会删除该位置；每个 Windows 用户独立安装。扩展是可执行 Python 代码，应仅安装可信来源的包。

## Manifest

`extension.json` 包含 `id`, `name`, `version`, `api_version`,
`minimum_app_version`, 可选 `maximum_app_version`, `suffixes`, `entrypoint`。
版本为 x.y.z；API 不匹配或主程序不兼容时拒绝加载。入口必须位于扩展目录内。
扩展不能覆盖主程序的通用文本或 Excel reader。多个启用扩展声明同一后缀时明确报错。
目录中的 `disabled` 标记用于禁用扩展。主界面“保存 Recipe”菜单中的“格式扩展管理”可检查状态和切换启用。

## Reader contract

入口模块仅在用户导入对应格式时加载：`read(path: Path, context) -> dict`。
context 提供 `check_cancel()`, `report(percent, text)`, `cancel_event`, `layer_id`。
reader 不得调用 Qt UI、不修改主程序状态，不依赖自身的全局安装环境。
主程序在现有后台导入 worker 中执行 reader，成功校验后才提交新数据。
异常和取消沿用主程序回滚行为。

API v1 坐标均为 mm。`units` 必须为 `mm`。

- `kind: height_matrix`：`z` 为二维浮点高度矩阵；`pitch_x_mm`, `pitch_y_mm` 正数；
  `origin_xy_mm` 可选，默认 `[0, 0]`。行递增 +Y，列递增 +X。
  缺测用 NaN。主程序维护 `_matrix_row/col`，统一应用采样上限。
- `kind: point_table`：`x`, `y`, `z` 为等长一维浮点数组；非有限坐标不进入分析。
- 通用可选字段：`metadata` 字典、`layer_id` 字符串、`notes` 字符串。

主程序保留源有效点数、缺测数、扩展 ID/版本/API/层名称，报告及 CSV 元数据含扩展溯源。
扩展不提供 fit_plane、metric 或 ROI 实现；这些始终由主程序统一负责。
API 变更须升级 API_VERSION，不能在 v1 中改变单位或字段含义。
后续层枚举、探测和流式读取可作为新能力或 API v2 添加，不能悄悄改变 v1。

## Sensofar PLUX 1.0.0 支持范围

独立实现 ZIP + index.xml + FILENAME_Z 指定的小端 float32 高度矩阵。
IMAGE_SIZE_X/Y 为列/行数；FOV_X/Y 按 µm/pixel，Z 按 µm，统一转 mm。
保持 NaN 孔洞。POSITION_X/Y/Z 仅作为仪器台面位置元数据保留，不自动叠加。
多高度层、未知存储结构、层尺寸不一致及无有效高度明确拒绝。
XML/矩阵大小有保护限制，读取 RAW 分块检查取消。

结构依据：Gwyddion sensofarx reader 已知字段行为；实现没有复制该 GPL 源码。
参考：https://raw.githubusercontent.com/cbuehler/gwyddion/master/modules/file/sensofarx.c

当前通过合成 ZIP/XML/二进制测试；尚无用户真实 PLUX 和原厂 DAT/X3P 对照，
因此不声称覆盖所有 Sensofar 版本或已完成计量验收。

构建扩展安装包：`powershell -File scripts/build_format_extension.ps1`。
主程序和扩展拥有不同稳定 AppId，支持各自独立卸载。
