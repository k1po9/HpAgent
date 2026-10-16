# R6 新窗口实施 Prompt

以下内容可直接复制到同一项目的新窗口。当前窗口仅准备交接，没有启动R6。

```text
这是 HpAgent「R6：全站产品一致性、边界体验与组合验收」实施窗口。
请直接审计、实施、自审、修复和验证，不停留在分析或方案阶段，不中途反复询问是否继续。

工作目录：/home/hp/workspace/HpAgent

一、当前接受基线

- R1、R2、R3、R4、R5均已正式接受。R6尚未实施。
- 最新R5决定：artifacts/product-acceptance/ui-iteration/R5/DECISION.md
- 冻结清单：artifacts/product-acceptance/ui-iteration/R5/ACCEPTED_SOURCE_FINGERPRINTS.json
- 冻结快照：artifacts/product-acceptance/ui-iteration/R5/attempt-05-accepted-timing/source-snapshot
- 240份源码/配置，SHA-256汇总：
  d767ac3b8d22330f20dbe63eb930f8020b0da56b36e8279c929f5dcf03996d22
- HEAD记录为9d47e55，但接受版本包含未提交的File/Task/R5成果；实际工作区、接受清单与快照才是基线，不能用纯HEAD替代。
- R5定稿：主运动0.35×；宿主progress≥0.98处收束末段与下一次展开起步的两个区间各减半（hostTempo=2）；真实数据等待不压缩。默认A冷瓷灰＋15%焰橙，真实星流进入左侧对应会话条目。
- 不再调R5速度、配色、轨迹或更换动效Skill。历史五速度/B/C颜色仅属于评审包，不引入产品设置。
- 旧attempt中的“待人审”、旧速度或“R5/R6未启动”是历史状态，最新DECISION优先。

二、先读与核实

先检查仓库和祖先目录AGENTS.md、git状态、当前源码；不得reset、覆盖或清理已有成果。同一工作区只允许一个实施窗口修改前端。

阅读 docs/design/ui-iteration-2026-10/：
- README.md（当前状态优先于下方历史）
- R2-ACCEPTANCE.md
- R3-FUNCTION-AUDIT.md
- R4-PLAN.md、R4-ACCEPTANCE.md
- INSPECTOR-MIGRATION-ACCEPTED.md
- R5-PLAN.md
- R5-CONVERSATION-TRANSIT-CONTRACT.md及ACCEPTANCE.md

阅读 artifacts/product-acceptance/ui-iteration/：
- R4/DECISION.md
- R4/attempt-16-four-kind-closure/HANDOFF.md、CONTRACT_CHECKLIST.md、REMAINING_WORK.md
- R5/DECISION.md、ACCEPTED_SOURCE_FINGERPRINTS.json
- R5/attempt-05-accepted-timing/ACCEPTED_CONTRACT.md、HANDOFF.md、VALIDATION_RESULTS.md、USER_REQUEST.md
- R5/attempt-03-conversation-transit/HANDOFF.md、SELF_REVIEW.md、VALIDATION_RESULTS.md、REMAINING_WORK.md、TIMING_AND_IDENTITY.md

最后两项R5调整只做了增量验证；此前593测试、24切换、四类/16子页/隔离API/资源数据是历史证据，不可冒充本次新测。R6需要补做当前最终源码的组合回归。

搜索已有R6计划、合同、任务与测试；若已有，先阅读并避免覆盖。读取相关WorkspaceScreen/Sidebar、TaskScreen/Sidebar、LoginForm、AppShell、路由/权限/查询与样式，不从离线HTML推断产品事实。
本地Mercury Skill仅在确实涉及其兼容检查时按需读取：
ui_skill_delete_after_done/mercury-motion-hpagent/SKILL.md
用户冻结决定优先，不能把Skill研究建议当作重新设计授权，也不能删除Skill目录。

三、本轮范围

R6是原UI迭代计划的最后收敛轮。授权完成以下全部工作，不因内部小阶段而反复停下来询问是否继续；最终统一停“R6整体待人审”。以下R6.1–R6.3是实施分组，不冒充已接受阶段。

R6.1：Workspace和Tasks的完整使用体验
- 审计实际主页面、列表、筛选/选择、目录/文件、任务创建/编辑/操作、关联详情和表单。
- 在既有Paper Flame与已接受视觉内，修复实际存在的排版层级、控件语义、状态反馈、焦点、滚动和窄屏问题；无问题的界面不为凑改动重做。
- 重点补齐空、加载、失败、重试、无权限、处理中、禁用原因、长名称/长内容及脏表单离开保护。
- File/Task Inspector及16子页已接受，保留既有视觉、DOM身份与操作合同。目录/文件授权、预算、成果验收、投递确认、任务完成分别保留独立含义。

R6.2：登录、异常、移动与可访问性
- 覆盖登录成功/失败、会话过期、退出、换账户、权限变化；旧请求/旧动画不得把上个账户或对象重新带回页面。
- 审计现有请求失败、重试、连接中断/恢复与错误入口；只在现有机制中补齐真实缺口，不新建后台同步或错误业务状态机。
- 验证390/780/1279/1280/1680、短屏、200%文字及减动；侧栏、Inspector、Composer、原生dialog和表单可用，不被装饰遮挡。
- 完整检查键盘Tab/Enter/Space/Escape、可访问名称、焦点进入/返回、hidden/inert和嵌套原生dialog层级。
- 手动和模拟覆盖范围分开记录，不能把事件模拟当作真机IME、软键盘、读屏或系统后台冻结。

R6.3：R1–R5全站组合、稳定性与最终收口
- 在当前最终production React上实际串联：登录→AI输入→发送→执行/审批→四类详情→返回/关闭→继续阅读→会话切换/收纳→Workspace/Tasks→再次返回→退出/换账户。
- 覆盖成功、发送/停止失败、queued/running/等待审批/未知/终态、晚回复、连续快切、重试、权限变化和脏表单拒绝/放弃。
- 检查R3真实提交后的接力与执行状态、R4请求立即/光头实际抵达/标题先显/Tab及展开身份/X真关闭/纯壳收束、R5单次切换双向星流/跨栏终点/0.35与半间隔没有回归。
- 做有明确环境和时长的连续使用与资源检查，比较监听器、RAF、timer、查询/SSE/轮询、Canvas与可选堆快照；发现累积则修复并复验。不把短时计数稳定写成长期无泄漏或真机60fps保证。

四、必须保留的业务与设计合同

- API、鉴权、权限、幂等、账户/对象代际失效、查询/SSE/轮询的原义与资格。
- 会话/消息/Run/对象/版本/来源身份、路由、返回历史、输入草稿/附件/IME和发送/停止/重试资格。
- 单一Composer；收纳/展开与退场不复制消息，不用截图代替业务，不引发无谓重挂载。
- Inspector、Task/File表单、授权、预算、成果验收、投递确认与完成规则；原生dialog及dirty guard。
- R1–R5已接受布局、16子页、主题和效果。不新增特效，不重设计主导航/对话区/输入区，不用全局最终覆盖样式掩盖归属问题。
- 允许修复有源码/复现证据的前端和必要共享状态缺陷；保持后端接口和业务合同。需要改变业务语义、数据库结构或新增产品能力时，先完成独立工作并记录具体冲突，再仅就不可安全推断的决定提问。

五、基线、实施与证据

1. 逐份比较240文件与R5冻结清单；任何新增差异先保存、定位来源，不覆盖或默认回退。
2. 创建新的artifacts/product-acceptance/ui-iteration/R6/attempt目录，保留原始输入、实际HEAD/工作区与暂存差异、修改前源码/指纹和环境记录。
3. 创建或完善R6-PLAN、交互合同、页面/状态矩阵和逐项验收清单。把发现的问题、最小修复、复验与未测分别记账。
4. 先复现再做相称修复，持续自审，遇到失败完成修复和复验；不交付仅截图好看但真实操作失败的候选。
5. 执行当前完整前端测试、类型检查、production构建、改动范围静态检查；若修改共享/后端逻辑，增加对应有意义的验证。
6. 实际操作production React；保存相同数据前后截图、关键状态/时序、DOM/预览/输入身份、焦点、滚动锚点、网络与订阅计数。录像可选，可操作真实React与完整独立离线HTML必须交付，离线包不得嵌入产品。
7. 静态夹具、真实隔离API、FakeRun执行器、canonical Worker事实模拟和真实Worker/供应商结果分别记录。不要用夹具代替真实API，不把Worker事实模拟标作真实执行。
8. 复用服务前核实监听进程、环境、镜像/容器、数据库、文件存储与internal网络隔离；旧端口存在不是复用依据。R5期间专用tmpfs库/文件曾在环境重启后丢失，恢复脚本只能核实后使用，不能覆盖现存库/文件。不接产品库或真实供应商，不进行真实外部投递。
9. 本地Linux Node可参考/home/hp/.local/share/hpagent-review/node；先核实可用，避免误用Windows工具链。
10. 测试必须标pass/fail/blocked/not-run，提供实际运行条件。没有条件的真机/浏览器/长时项目明确留项，不伪造通过。若存在功能阻断失败，继续修复；无法推进时给出具体复现与阻塞，不把测试缺失包装成完成。

六、交付和停止

新attempt至少包含：基线备份/指纹/差异、R6合同和验收矩阵、源码实施差异、自审修复、测试日志与浏览器证据、未测/后续、最终源码快照/指纹、真实production React入口、完整独立离线HTML、HANDOFF与启动/恢复命令。更新总README和R6计划，历史决定与证据保留。

最终停在“R6整体待人审”。不得自行接受R6或写接受决定；由用户明确接受后再冻结。不得自动提交、推送、部署，不开始未定义的R7、业务扩展或生产接入。这里只授权本轮UI收敛与所需验证。
```
