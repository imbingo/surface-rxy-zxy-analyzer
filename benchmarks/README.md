# V4.6.9 Performance 审计工具

本目录只包含基准和观测代码，不修改生产算法、ROI 语义或渲染后端。

## 运行

在仓库根目录使用项目 `.venv/Scripts/python.exe`：

```powershell
.venv/Scripts/python.exe benchmarks/performance_benchmark.py --suite kernel --points 100000,500000,1000000,2000000,5000000,8000000
.venv/Scripts/python.exe benchmarks/performance_benchmark.py --suite import --kind csv --points 2000000,5000000
.venv/Scripts/python.exe benchmarks/performance_benchmark.py --suite import --kind matrix --points 2000000,5000000 --sampled
.venv/Scripts/python.exe benchmarks/performance_benchmark.py --suite smart --kind matrix --points 100000,500000
.venv/Scripts/python.exe benchmarks/performance_benchmark.py --suite smart --kind physical --points 100000 --profile
.venv/Scripts/python.exe benchmarks/performance_benchmark.py --suite render --points 30000,60000,100000,300000,1000000
.venv/Scripts/python.exe benchmarks/performance_benchmark.py --suite gui --points 2000000,5000000
.venv/Scripts/python.exe benchmarks/performance_benchmark.py --suite raster_stages --points 2000000,5000000
```

完整串行矩阵：`benchmarks/run_audit.py`。请勿并行运行基准，以免结果相互干扰。
`--output` 指定新的结果目录；默认 CSV 为追加写入，复测请使用新目录。

`--kind` 导入支持 csv、quoted、space、tab、pixel、zygo、matrix、keyence。
Smart ROI 支持 matrix、sampled_matrix、physical。
`--profile` 另外执行 cProfile，输出 `.prof` 和函数热点 CSV；profile 耗时不混入原始墙钟计时。

## 测量口径

- 各规模独立子进程；数据生成、启动与 GC 不计入单项耗时。
- 数值 kernel 默认重复 3 次；导入和昂贵的 topology/growth 默认 1 次。
- 时间是本机墙钟时间。每 20ms 采样 Windows 进程 RSS，单位 MiB；不是精确的分配字节数或系统总内存峰值。
- `memory_mb` 是该步骤采样到的进程峰值，`delta_peak_mb` 相对步骤开始；跨步骤保留的对象和内存分配器缓存仍计入基线。
- `stage` 行是嵌套函数的 inclusive 时间，不可重复相加；这些行不独立测量内存。
- 生成文件随即读取，属于 OS warm-cache；不能作为冷盘或网络盘性能结论。
- `--sampled` 运行当前标准抽样配置；不带该参数强制全量读取。抽样与全量不是相同计算输入，不能用其倍率宣称算法加速。
- render 是 QtAgg 离屏的固定 600×400 canvas 压力测试，直接绘制所列点数；生产默认已有 30k LOD 和 XY Raster。
- gui 是实际 SurfaceAnalyzerPro 窗口与事件循环（1600×900，Qt offscreen），包括 XY 后台完成等待；不等于桌面合成器/显示器端到端帧率。
- 合成输入是按行排列的规则扫描，Physical 情况添加小幅 XY 抖动；不是所有真实设备文件的代表。
- 干净数值文本的 pandas C / fromstring 仅是候选基准，未接入应用。其输入约束比生产 parser 窄。
- 默认每个案例最多 600 秒，进程 RSS 预算 7500 MiB；达到限制会留下 timeout/memory_limit 记录，不把外推值写成实测值。

## 后续性能改造的正确性门槛

同一输入及参数，保留源行/缺测统计、矩阵孔洞、ROI mask、Smart ROI mask 与邻接次序语义。
Rx/Ry、法向 PV/RMS、TTV、Mean Z 和高阶垂直残差必须分别比较；容差按量纲和数值尺度指定。
不要用抽样数据代替全量、不要把 surface_following 改成全局阈值连通域、不要默认启用 fastmath。
