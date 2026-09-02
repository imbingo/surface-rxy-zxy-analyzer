# V4.6.0 Release Notes

V4.6.0 聚焦文件导入一致性，量测算法、ROI 语义、Smart ROI 逻辑以及 Rx/Ry/PV/TTV/RMS/Mean Z 定义保持不变。

## 主要变更

- XYZ、Pixel XY 和 Z Matrix 的逗号、Tab、中英文分号及 pipe 文本共用标准 quoted CSV tokenizer。
- 修复 Keyence VR ImageDataCsv 数值字段被双引号包围时的布局识别与流式读取。
- quoted 空字段会正确映射为 missing cell，不压缩 `_matrix_row` / `_matrix_col` 拓扑坐标。
- 自动分隔符识别改为使用逻辑字段宽度，避免引号内分隔符干扰。
- 矩阵固定宽度会先 canonicalize：可信宽度下允许尾部补空，只裁掉超出的 missing 字段，非空超宽会明确报错。
- 三种导入类型统一使用“正文搜索起始行”；`0` 为自动，正数为 1-based 搜索下界。
- Recipe 升级为 schema 7，新文件只保存 `input.search_start_row`，并按新版字段、旧矩阵起始行、旧点表起始行的顺序迁移。
- 编码失败与布局失败会聚合成精简诊断，不输出完整宽矩阵行。

## 兼容与验证

- 公共 `load_xyz_points` 签名和 `LoadedPoints` 结构保持不变。
- 旧 Recipe 仅在加载时读取旧起始行字段，运行和重新保存均不再使用它们。
- 完整回归测试 106 项通过，包含 Golden Sample 量测结果、ROI 点集和拓扑结果不变验证。
- 按用户最新指示，本次发布不再要求搜索或附加真实 73 MB Keyence 文件，使用 GBK Keyence 合成回归用例验收。
