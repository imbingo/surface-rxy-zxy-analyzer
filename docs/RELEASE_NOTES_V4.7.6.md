# V4.7.6 发布说明

V4.7.6 修复 CSV / 分隔文本点表在尾部分隔符场景中可能发生的静默整列错位。

## 修复内容

- pandas C fast parser 显式禁用隐式 index 推断。
- 使用项目 tokenizer 检查第一、四分之一、中间、四分之三和最后记录的源字段与 DataFrame 列位置。
- fast parser 对齐失败时自动丢弃结果并改用 robust line parser。
- 合法的尾部空分隔符可安全裁除；canonical width 之外的非空字段会明确失败。
- 导入详情记录 parser engine、完整性检查状态和 fast path 回退原因。
- 新增真实 12 列 Thickness 数据、引号、混合尾分隔符和多分隔符等价性测试。
- 所有数据、Recipe、批量、报告、CSV、平行度、胶厚和装调文件对话框分别记忆最近一次导入及导出目录。

Excel 导入链路以及 Rx、Ry、PV、TTV、RMS、Mean Z、ROI 和滤波定义均未改变。
