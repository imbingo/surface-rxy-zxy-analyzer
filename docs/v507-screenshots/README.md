# V5.0.7 实际页面与报告截图

这些图片由真实 Qt 页面及 Matplotlib 报告渲染，使用合成数据；不是设计原型。此图库为 Linux 源码运行截图，覆盖普通尺寸及高 DPI；Windows 打包 EXE 的截图、报告和验证 JSON 在对应 Actions 附件的 `validation/` 中。

| 功能 | 截图 |
|---|---|
| PLUX 实际拖放导入 | [主控页面](plux-dropped-main.png)、[最近文件](plux-recent-menu.png) |
| 膜厚数据层原页面 | [数据层](thickness-data.png) |
| 膜厚配准原页面 | [配准](thickness-registration.png) |
| 膜厚最终结果 | [扣减后四视图](thickness-four-views.png) |
| 膜厚报告 | [五图同页](thickness-five-view-report.png) |
| VIG 原始 image 结果 | [四视图](vig-four-views.png)、[报告](vig-four-view-report.png) |
| 主控独立编辑副本 | [ROI／滤波／去倾斜](vig-secondary-roi-filter-residual.png)、[编辑后报告](vig-secondary-edited-report.png) |
| VIG 组合 Recipe | [复用原运行确认表](vig-composite-table.png) |
| 指标选项与空上限 | [原判定规格窗口](metric-configuration.png) |
| 窄窗口全部指标 | [六项均分两行](single-narrow-six-metrics.png) |
| VIG 判定规格上下文 | [打开即选中模块规格页](module-spec-context.png) |
| 两项指标自适应 | [PV 与 3σ](single-two-metrics.png) |
| 主控浅色／深色 | [浅色](single-light.png)、[深色](single-dark.png) |
| 下拉列表修复 | [修改前](popup-before.png)、[浅色状态](popup-states-light.png)、[深色状态](popup-states-dark.png) |

[验证上下文](validation-context.json) 记录实际操作断言及源码提交。原始 VIG 数组在删点、撤销、ROI、滤波和显示面型操作后保持不变；内存副本不能伪装成文件型组合角色。
