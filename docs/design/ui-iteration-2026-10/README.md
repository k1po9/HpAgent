# HpAgent UI 迭代总方案与调度合同

当前状态（2026-10-17）：用户明确“R6 通过，进行收尾工作，清理，代码迁入，commit”，**R1–R6 UI迭代已正式接受**。接受241份源码/配置，指纹bcc85cb4b9f68570d33a217709636e937de1594d65910cf559ca5223a0efe867，含最新会话切换/阅读控件共用8.5×0.35时序。见[R6决定](../../../artifacts/product-acceptance/ui-iteration/R6/DECISION.md)、[接受合同](R6-ACCEPTED-CONTRACT.md)和[正式收尾](../../../artifacts/product-acceptance/ui-iteration/R6/attempt-04-accepted-closeout/CLOSEOUT.md)。当前生产源码已具备全部接受改动，本次授权正式本地提交；评审入口15346、隔离API+Fake15336。未推送/部署、不启动R7；真机/供应商/生产Worker等未测项不因UI接受变为已验证。以下保留历史。

历史状态（2026-10-16）：R1–R5已正式接受，R5最后两项调整为0.35×及两个宿主端点缓动区间减半。240份源码指纹 `d767ac3b8d22330f20dbe63eb930f8020b0da56b36e8279c929f5dcf03996d22`。见[R5接受决定](../../../artifacts/product-acceptance/ui-iteration/R5/DECISION.md)和[R5计划](R5-PLAN.md)，入口http://127.0.0.1:5344/REVIEW.html。R6未启动，未提交/推送/部署。以下保留历史。

日期：2026-10-13（Asia/Shanghai）。审计基线：`main@b8a5a02eb83b4e122367e321040e78e1aae7cd3e`。

历史状态（R5本次接受前，2026-10-16）：R1、R2、R3、R4 均已正式接受并冻结。用户“通过，下一步干什么”接受 attempt-16 的四类组合、跨轮回归与真实隔离 API 收口，完成 R4 整体验收；冻结209份源码/配置，指纹 `3f157303d1526c5357cfb899fe445035e01bc3c0d78926dd4240a80d573b5758`。决定见 [R4 DECISION](../../../artifacts/product-acceptance/ui-iteration/R4/DECISION.md)。R5.0 产品语义与基础收纳已正式接受并冻结；用户最新授权补正左侧会话切换、真实跨栏收束与主题配色候选，attempt-03已完成工程自审修复和验证，停在 **R5 整体待人审**；R6 未启动。入口见 [R5 计划](R5-PLAN.md)与[本轮交接](../../../artifacts/product-acceptance/ui-iteration/R5/attempt-03-conversation-transit/HANDOFF.md)，本地评审 http://127.0.0.1:5342/REVIEW.html；无提交/推送/部署授权。下方历史记录保留。

2026-10-13 调度补充：R1 首轮候选已经交付，S1 被选择为方向；用户随后指出一次选择不足以代表静态审美收敛。R1 拆为 R1.1 方向选择、R1.2 整体打磨、R1.3 收敛验收。历史 attempt-01 的“已接受 S1”仅作为方向选择记录，不作为 R1 整轮放行。下述更新及新 Prompt 优先于旧 R1 退出措辞；保留历史证据，不改写过去记录。

## 1. 目标与完成含义

目标是提高 HpAgent 的视觉辨识度、静态审美和交互连续性。保留已建立的业务架构，采用 Paper Flame + EXP-02 A/B/C + Mercury 的统一呈现语言。

三个门槛独立成立：

1. 业务正确：账户、权限、Run、提交、文件、任务、导航和焦点不回归。
2. 设计符合：冻结的颜色、动作、状态和空间因果得到实现。
3. 人审认可：用户在实际画面和交互中认可本轮结果。

测试通过、截图齐全、代理自评高分均不能替代人审。能够保证的是证据与迭代流程，不能预先保证主观满意度。

## 2. 初始只读审计的基线与缺口（历史）

| 部分 | 当前事实 | 后续方向 |
| --- | --- | --- |
| Paper Flame | `4191251` 已迁入 `--pf-*`、`--hp-*`、Radix 映射及四区色彩 | 保留身份，完善排版、材质和主次 |
| 布局 | `554763e` 已迁入居中阅读、双行 Composer、组合 Inspector；当前审计 HEAD 的 web/src 与其无差异 | 首轮保持比例，避免重做换色和挪间距 |
| A | 提交保护已存在；无火种接力/轨道捕获 | 在真实确认事件之后建立一次性呈现链 |
| B | Surface/路由存在；Inspector 仅约 220ms 淡入平移 | 数据与呈现分轨、抵达同步、真正关闭与装饰壳收束 |
| C | 通用 Spinner 和状态文字 | 统一事实映射、六态图形、双尺度、连续色彩 |
| Mercury | 预留光学 Token；无阅读收纳控制器 | 先确定产品语义，再实现可逆运动 |
| 验收 | 历史业务/几何/对比度证据较充分 | 增加明确的审美假设、同数据对照、人审决策和返修追踪 |

历史参考：

- [最近布局验收](../../../artifacts/product-acceptance/reference-layout-20261012/README.md)
- [Paper Flame 验收](../../../artifacts/product-acceptance/phase1-paper-flame-20261012/README.md)
- [业务架构](../../architecture/web-ui.md)
- [测试与隔离要求](../../development/testing.md)

这些是历史证据，实施窗口必须核实实际 HEAD、工作区、文件与环境；不得把本文件基线当实时事实。

## 3. 设计权威与自由空间

真实业务/权限/可访问性契约优先；用户最新决定优先于 Skill 中的旧历史描述。

| 来源 | 权威内容 | 可继续探索 |
| --- | --- | --- |
| Paper Flame | 明亮冷瓷、深石墨 Rail、焰橙行动色、语义色 | 字级、留白、局部材质、组件边界 |
| A2.1/A4.1/A4.2.1 | 内焰细缝、五锚点渐冷、同路径抵达补环、捕获后旋转 | 时长、路径弯曲、尾迹细节 |
| B6/B5.3 | Re-seat/Shift/Elevate/Relay、强化银丝、无缩略卡片、抵达同步、Soft Close | 光学细节、间距、阴影 |
| C3 | 六态颜色/形态、compact/detail、约 520ms 连续交接 | 信息密度、局部表现强度 |
| Mercury | 同对象/同 seed、可逆进度、共同相位、宿主吸纳 | 产品收纳边界、性能档位与材质参数 |

B/C 中“A 尚未收敛”的说明已经落后于最新 A Skill；以 A4.2.1 为准。暂不引入 Morphicons。实验 HTML/PNG 是设计参考，不是生产实现或生产 E2E 证据。

静态方向：正文强调阅读，对象卡强调身份与可操作性，Composer 强调行动，Inspector 强调对象事实与证据。创新应建立在这四种角色的差异和联系上。普通回答、成果、执行与输入不能都成为同权重的白色描边卡。

## 4. 目标架构与必须处理的接缝

保留 React/TypeScript/Vite/Zustand/assistant-ui/Radix、四区 Shell、真实消息 DOM、原生 dialog 与现有领域 Store。

呈现模块建议分为 vitality、input-action、spatial、mercury；共享减动偏好、目标 ref、取消与层级策略，不建立一个接管业务的全局动画状态机。每帧数据留在 controller/ref，不逐帧写全局 Zustand。

### Inspector

- `route.inspector` 继续决定业务目标；呈现协调器独立维护 receiving/landed/settled/closing。
- 当前查询部分依赖子组件挂载。不能“到达才挂载”而延迟业务请求；需要稳定数据容器或局部提取查询生命周期。
- 新内容的标题、Tab、正文在实际抵达后才可见，且与最新目标身份匹配；未抵达时中性接纳，旧对象不可见。
- 避免为动画重挂载同对象 iframe/编辑器。版本与导航代际必须参与失效检查；同对象 Tab 不重新发射银丝。
- 当前 X 与返回均绑定 back。后续 B 轮显式分开 X=closeInspector、返回=back；脏编辑 guard 优先，实际关闭获准后才能退场。
- 逻辑关闭后仅装饰壳继续收束，不能保留焦点陷阱、敏感旧正文或可操作按钮。

### Run 与提交

- 主对话读取 workbench，Inspector 可使用 live Run 或独立快照。共享展示映射，不强行合并领域 Store。
- 历史/Work Run 缺可信 phase 时只显示已知状态；终态覆盖旧 progress，SSE 退化不代表失败。
- 审批目前按组件生命周期加载，需区分“未知”和“已确认没有审批”，不能默认主对话已知道审批事实。
- A 必须关联账户代际、conversationId、提交标识与 runId；不能把恢复已有 running 当作新发送。
- A 模板需要适配连续发送、queued 直接终态、停止失败、快速进入 Thinking/Tool；运行事实变化优先于补完动画。
- 发送、停止、重试资格沿用业务权威，动画不负责清草稿或再次请求。

### 样式与动效组合

- 当前 base/领域样式/unification/visual/workbench 存在叠加覆盖。随组件改造收敛归属，不再增加全局最终覆盖文件，也不先全站搬家。
- A、B、Mercury 保持独立 controller/overlay；Surface、权限、业务错误和停止优先。
- B 改变 A 目标几何时，A 可取消并直接交还 C；Mercury 不与跨区接力争抢主要视觉空间。
- reduced-motion、hidden、resize、账户退出、路由变化及时清理；新代际使旧 RAF/Promise/timeout 回调失效。

## 5. Mercury 建议产品合同（R5 前待人审）

第一版建议：收纳当前阅读 viewport；Composer 保留；只保存当前会话本地展示态；不修改路由/消息/Run。活跃 Run 期间不开放收纳。收纳后新回复或再次发送时恢复阅读，不抢输入焦点；展开恢复阅读锚点。宿主不可见、窄屏、减动或异常时直达正确终态。

这尚未获用户确认，不得在 R1 顺手实现。若要支持运行中收纳，先设计独立可见的运行、停止与审批入口。

## 6. 分轮计划

| 轮次 | 范围与交付 | 人审重点 | 状态 |
| --- | --- | --- | --- |
| R0 | 只读架构审计、总方案和交接合同 | 目标与冻结边界 | 审计已交付，未重新运行应用验收 |
| R1 | R1.1方向选择→R1.2整体打磨→R1.3收敛验收 | 静止时的审美、层级和阅读 | 已正式接受，见 attempt-06 最终决定 |
| R2 | B Conversation→Rail→Surface；C 映射/双尺度/连续切换；A 微状态 | 常用反馈和生命状态统一 | 已接受，见 R2/attempt-06 |
| R3 | A 提交确认→渐冷光迹→同路径成环→C 接管 | 真实交互的因果与连贯性 | 已接受，见 R3/attempt-03；功能清单核查已完成 |
| R4 | B Inspector 请求/呈现分轨、强化银丝、Arrival Gate、Soft Close | 进入、关闭、快切与四类对象 | 已正式接受并冻结，见 R4/DECISION.md |
| R5 | Mercury 无特效语义→可逆颗粒→共同相位→密度材质 | 松解、吸纳、反向和性能 | 已正式接受并冻结，见 R5/DECISION.md |
| R6 | Workspace/Tasks/登录/异常/移动/组合回归及全站收敛 | 产品一致性、长时使用与最终接受 | 已正式接受，见R6 DECISION |

R2 内各子项仍按小步实现和验证。R3/R4 后必须展示连续“输入→执行→详情→关闭→继续阅读”，独立 Demo 不能代替组合验收。

## 7. 总调度与新对话协议

总调度对话负责目标、范围、用户决策、跨轮冲突和下一轮 Prompt；实施对话负责本轮代码、证据、自审、修复、预览和交接。用户可直接在实施对话做人审，把决定及 HANDOFF 带回总调度。

状态词统一为：未启动、实施中、自审未通过、待人审、人审返修、已接受、环境阻塞。技术完成可以进入待人审；只有用户明确认可才能标为已接受。不可自行进入下一大轮。

每轮建议路径：`artifacts/product-acceptance/ui-iteration/Rn/attempt-01/`，返修递增 attempt，不覆盖上一轮或失败证据。报告与证据使用仓库相对链接，最终消息给绝对路径。

各实施对话不能假设拥有前面对话历史。启动必须读取本文件、该轮计划、Skill 和适用仓库约束，并记录真实 HEAD 与工作区变化。

当前计划文件和 `ui_skill_delete_after_done/` 可能尚未提交。新对话使用同一项目时可以读取；若使用隔离 worktree，必须先确认这些输入已可用，并记录来源哈希。不能假定 Git 自动携带未跟踪文件，不可因缺失而自行猜测冻结设计。

同一工作区同一时间仅一个实施窗口修改前端。总调度阶段避免并行改这些文件。不得覆盖用户已有改动、自动发布/合并或删除 Skill 目录。提交与分支状态必须在交接中明确记录；未提交结果同样可审。

## 8. 审查与退出条件

- 每轮依次完成契约检查、静态读图、适用的动态检查、反例检查及修复复验。
- 人审包包含假设、相同数据前后/候选对照、真实 React 预览、已知限制、推荐与不推荐理由。
- R1 以静态图和可操作预览为主；动态阶段要求原速因果可检查。用户在 R3 改为直接 HTML 交互审核、不需要录像；R4 沿用此偏好，提供真实 React 交互入口、时序断言与关键帧，录像可选。慢速仅用于定位，不能代替原速交互。
- 审美六项：层级、排版、材质、状态辨识、运动因果、整体一致性；不适用项写 N/A。1–5 分仅用于追踪，建议各适用项≥4才提交候选，不代表用户通过。
- 用户否决与功能阻断缺陷不能被平均分抵消。同一问题两轮无改善，应重新检查假设/结构。
- 测试分 pass/fail/blocked/not-run；实验 harness、真实 API、生产构建证据分别标注。历史通过数不计入本轮。
- 默认覆盖 1680/1280/780/390、1279/1280 边界、短屏、200%文字、中英文长内容、减动。真机 IME/软键盘不能由模拟替代。
- 性能要记录环境与测量条件；60fps 是目标。功能降级与视觉关闭开关必须回到正确业务状态。

## 9. 当前可用启动材料

- [R6 新窗口完整实施 Prompt](R6-PROMPT.md)（实施范围历史；R6现已接受，见最新决定）
- [R3 功能清单核查](R3-FUNCTION-AUDIT.md)
- [R4 实施计划](R4-PLAN.md)
- [R4 新窗口完整 Prompt](R4-PROMPT.md)
- [R4 功能、自审与人审清单](R4-ACCEPTANCE.md)
- [R2 实施计划](R2-PLAN.md)
- [R2 新窗口完整 Prompt](R2-PROMPT.md)
- [R2 自审、人审与证据矩阵](R2-ACCEPTANCE.md)
- [R1 实施计划](R1-PLAN.md)
- [R1 新窗口完整 Prompt](R1-PROMPT.md)
- [R1.2 整体打磨 Prompt](R1.2-PROMPT.md)
- [R1.3 收敛验收 Prompt](R1.3-PROMPT.md)
- [每轮审查与交接模板](REVIEW-TEMPLATE.md)

R1.2 与 R1.3 顺序执行，不能并行修改共享前端。R1.3 Prompt 是预先准备的工作流程，实际设计与返修清单必须来自 R1.2 最新交接和用户意见。选择 S1、认可某个局部、同意继续打磨均不等于 R1 最终接受。

后续轮次 Prompt 由总调度依据上一轮实际 HANDOFF 和用户决定编写，不提前虚构 R1 已接受的设计。

## R1 最终人审决定 · 2026-10-13

用户对 R1.3 attempt-06 最终生产交付明确回复“人审通过”，**R1 已接受**。该决定对应当前整体静态体验，包括任务 B + 账户 A；不同于此前 S1 方向选择和 R1.2 局部认可。

[最终 DECISION](../../../artifacts/product-acceptance/ui-iteration/R1.3/attempt-06/DECISION.md) · [最终 HANDOFF](../../../artifacts/product-acceptance/ui-iteration/R1.3/attempt-06/HANDOFF.md)。受审源码指纹 `a32a65e6c18ce16031fda8f860f21aa6c5d157b52fb59f0c361fe834f1609eaa`，工作区仍为未提交结果。验证范围与未测限制保留。

R2 实施未启动。用户随后要求 R2 Prompt 与启动资料，现已准备上述三份文档；新窗口按 R2.1 B 局部人审、R2.2 C 局部人审、R2.3 A 与整体终审顺序推进。不自动创建或联系其他对话，不提交、发布或删除 Skill。

## R2 接续状态 · 2026-10-14

R2.1 已获用户明确接受，决定在 R2/attempt-02/STAGE-DECISION.md。R2.2 已开始但尚未验收，未完成源码、检查日志与交接保存在 R2/attempt-03；R2.3 未启动，R2 整轮未接受。用户要求换窗口，旧实施窗口停止修改。上述“R2 未启动”等文字为历史状态。新窗口完整入口：[R2.2 及后续接续 Prompt](R2.2-RESUME-PROMPT.md)，先完成 C 并停在人审，不能自动跨阶段或大轮。

## R2.2 本次接续交付 · 2026-10-14

R2.2 C实现、自审、修复与生产评审包已完成，停在 **R2.2 待人审**。入口：[attempt-03 HANDOFF](../../../artifacts/product-acceptance/ui-iteration/R2/attempt-03/HANDOFF.md)。源码指纹 `2ed669e7cdd3096e90ec0f6bd78d88ba2e3f06a9a01c851cc7012a78086072c0`（247文件扩展清单）。R2.1仍已接受，R2.3未启动，R2整轮未接受；只有用户明确认可C才放行下一子阶段。无提交/推送/合并/部署。

## R2.2 已接受、R2.3 启动 · 2026-10-14

用户对C回复“可以通过，进入下一轮”；R2.2局部已接受，见attempt-03/STAGE-DECISION.md。本窗口顺序进入R2.3输入微状态与整体验证，证据写attempt-04，停在R2整体待人审；尚未启动R3。

## R2.3 本次交付 · 2026-10-14

A输入微状态与B/C/A组合验证完成，停在 **R2整体待人审**。本轮入口：[attempt-04 HANDOFF](../../../artifacts/product-acceptance/ui-iteration/R2/attempt-04/HANDOFF.md)，源码 `13dda45b2ef303fde3285e5c78629622b88b58f354cbc92ffb28e7e145420b35`（255文件）。B/C局部已接受，R2整轮未接受；未启动R3。

## R2 整轮已接受 · 2026-10-14

用户明确“整个 R2 通过，进入 R3”。③粗版与停止符号已生产定稿，接受源码 `e110cba5daf3eff505c1eb7c931b3218cea46e7b394b64804c1af9f1a10974ce`。最终决定/合同/交接在R2/attempt-06。R3获授权，在本窗口继续顺序实施；其他大轮未启动。

## R3 本次交付 · 2026-10-14

R2整轮已明确接受，R3发送确认→渐冷光迹→同路径成环→C接管已实现、自审返修并完成生产评审包。停在 **R3待人审**，未进入R4。入口：[R3/attempt-01 HANDOFF](../../../artifacts/product-acceptance/ui-iteration/R3/attempt-01/HANDOFF.md)。当前源码 `78a41004fcb0882ebb96fee841e46c9253b6ad697917798129c5ccecadbe4e0b`（269文件），仍未提交。121相关单测、19状态场景、13实际操作、7几何/缩放配置及生产评审页通过；原速只能抽帧读图，真实供应商未跑。

## R3 人审返修交付 · 2026-10-14

评审代理登录与成环/加载方向已修复，新增免登录原速视频与局部放大。当前 **R3待人审**，未进入R4。最新入口：[attempt-02 HANDOFF](../../../artifacts/product-acceptance/ui-iteration/R3/attempt-02/HANDOFF.md)，源码 `61596c5ebb24bcf7a4e9b98fa78f937586d6db09a0a1df56d15aa4bc521475c6`（270文件）。

## R3 接受后即接力返修 · 2026-10-14

用户批准排队圆环/运行菱形、原状态颜色保留，并要求HTML交互审核。实现与自审修复完成，当前 **R3待人审**，未进入R4。最新入口：[attempt-03 HANDOFF](../../../artifacts/product-acceptance/ui-iteration/R3/attempt-03/HANDOFF.md)；源码 `eb0790f3f20ec49e35d0772ae0173fc2b1b4093d71c4f3b7958dd9c9a08931c0`（271文件）。

## R3 整轮已接受 · 2026-10-14

用户对 attempt-03 明确回复“人审通过”，**R3 已接受**。接受源码 `eb0790f3f20ec49e35d0772ae0173fc2b1b4093d71c4f3b7958dd9c9a08931c0`（271文件），工作区与冻结源码清单一致，仍未提交。最新[DECISION](../../../artifacts/product-acceptance/ui-iteration/R3/attempt-03/DECISION.md)、[冻结合同](../../../artifacts/product-acceptance/ui-iteration/R3/attempt-03/INTERACTION-CONTRACT.md)、[HANDOFF](../../../artifacts/product-acceptance/ui-iteration/R3/attempt-03/HANDOFF.md)。R4未启动，不自动跨大轮。

## R3 功能核查与 R4 预备 · 2026-10-14

按用户要求只检查功能实现列表，不重审 R3 效果。工作区及冻结副本的 271 个清单文件均逐文件一致；按最终合同核查未发现功能清单缺项。原 FEEDBACK-MATRIX 末行“待人审”为历史残留，以最终 DECISION 为准；原验收包保持不变。

已准备 R4 计划、验收清单与 Prompt。新窗口先完成 R4.0/R4.1 Run 接力与开关闭环并停局部人审，认可后继续 R4.2 四类对象与整体终审。当前只改调度文档，未实施 R4、未修改产品源码、未提交或发布。

## R4.0 / R4.1 本次交付 · 2026-10-14

R4.0基线与guard接缝核实完成；R4.1 Run接力、实际抵达门控、真关闭与空壳收束已实现、自审修复并交付production React交互包，当前 **R4.1 待人审**。最新[HANDOFF](../../../artifacts/product-acceptance/ui-iteration/R4/attempt-01/HANDOFF.md)。165项相关测试及类型/构建/局部静态检查通过，浏览器/真实隔离API与夹具范围分别记录。R4.2未启动，R4整轮未接受；认可R4.1后才在本窗口继续，不自动跨大轮。保持未提交成果。

## R4.1 人审返修 · attempt-02 · 2026-10-14

用户反馈4k与正常侧栏仍只有弹跳。已复现并修复自动focus-scroll造成2ms取消的路径；新导航先等几何稳定再出发，业务请求即时，保留实际tip门控及用户中断降级。新免登录生产React评审与取消诊断在[attempt-02 HANDOFF](../../../artifacts/product-acceptance/ui-iteration/R4/attempt-02/HANDOFF.md)。当前R4.1待复审，未声称用户WebView根因已确认；R4.2未启动。

## R4.1 / R4.2.6 视觉迁回版已接受 · 2026-10-15

用户认可当前改善 UI，授权“先commit，再逐步推进”。接受基线见 R4/attempt-10-r426-migration/STAGE-DECISION.md 与 INTERACTION-CONTRACT.md。原计划 R4.2 四类对象阶段仍待实施；下一步 Artifact，再 File/Task 与完整组合。R4 整轮未接受，R5 不启动。

## R4.2 Artifact 第一步交付 · 2026-10-15

先提交接受UI基线 `1a21a93` 与R1/R2/R3冻结文档 `8ca20b9`。Artifact已局部接入既有光丝/标题框，补齐版本归一化与同版本iframe保持；527项单测及production React夹具浏览器检查通过。当前 **Artifact待人审**，最新[HANDOFF](../../../artifacts/product-acceptance/ui-iteration/R4/attempt-11-artifact/HANDOFF.md)。真实隔离API本轮未跑；File/Task与四类组合仍待逐步推进，R4整轮未接受。

## AI Inspector 离线美化交接 · 2026-10-15

用户改为把当前React导出给外部模型美化，范围为AI下全部Inspector、展开与所有子页/按钮/呈现。新增[交接包](../../../artifacts/product-acceptance/ui-iteration/R4/attempt-12-ai-inspector-handoff/README.md)，包含完整离线HTML、可美化/业务保护边界、剩余工程要求、源码映射快照和返回指纹校验。16页默认/展开与深层内容等28项浏览器检查通过，外部请求0。当前等待返回美化HTML后迁回与逐项回归，File/Task新增接力暂不继续实现，R4整轮未接受。

## File/目录接力本步交付 · 2026-10-16

File/目录已接入共享实际抵达/标题/关闭机制，明确真实入口来源，保留预览、版本输入和授权草稿DOM，修复获准放弃与权限失败清理。当前 **File/目录接力待人审**，R4整轮未接受；Task完整接力、四类组合及真实隔离API仍按后续人审顺序推进。入口与独立交接见[attempt-14 HANDOFF](../../../artifacts/product-acceptance/ui-iteration/R4/attempt-14-file-relay/HANDOFF.md)。真实React与完整离线HTML、源码备份/指纹、逐项I01–I20台账与本轮证据均保留。未提交/推送/部署；R5/R6未启动。


## File 接受与 Task 授权 · 2026-10-16

用户明确“通过，下一步执行”。[File 接受决定](../../../artifacts/product-acceptance/ui-iteration/R4/attempt-14-file-relay/DECISION.md)记录接受范围。Task 新 attempt-15 已备份当前未提交源码、指纹、差异与接受构建；本步仅实施 Task，停“Task 接力待人审”。


## Task 本步交付 · 2026-10-16

真实Task完整接力已实施、自审、修复并验证，当前 **Task 接力待人审**。复用现有接力/标题/关闭机制，保留4Tab及表单DOM，修复历史guard与保留查询的生命周期接缝；全部16子页及关联弹层已接受视觉保持。入口与独立交接见[attempt-15 HANDOFF](../../../artifacts/product-acceptance/ui-iteration/R4/attempt-15-task-relay/HANDOFF.md)。完整556测试、生产构建/静态检查及明确夹具浏览器验证通过；真实API仍未测。Task接受后再做四类组合与跨轮/隔离API，整轮R4未接受。未提交/推送/部署，R5/R6未启动。

## 2026-10-16 · attempt-16 四类组合收口交付

Run/Artifact/File/Task组合、R1–R3定向回归、真实隔离API已执行并自审；209份生产源码/配置与Task接受版完全一致，无新产品差异。556前端测试、186隔离后端测试、29独立原浏览器场景最终通过；夹具、真实API+Fake及canonical Worker事实准备分开记录，初次失败与未测项保留。

评审入口 http://127.0.0.1:5333/REVIEW.html 。详见 [attempt-16交接](../../../artifacts/product-acceptance/ui-iteration/R4/attempt-16-four-kind-closure/HANDOFF.md) 和 [逐项验收](../../../artifacts/product-acceptance/ui-iteration/R4/attempt-16-four-kind-closure/CONTRACT_CHECKLIST.md)。**当前停“R4 整体待人审”**，尚未写整轮接受DECISION，R5/R6未启动。未提交、推送或部署。

## 2026-10-16 · R4 整体接受与冻结

用户“通过，下一步干什么”明确接受attempt-16的R4整轮交付。当前源码209份指纹核对一致，正式决定与冻结清单见 [R4 DECISION](../../../artifacts/product-acceptance/ui-iteration/R4/DECISION.md)。历史attempt、失败/未测证据及未提交成果保留。下一轮按总计划为R5 Mercury，先明确阅读区收纳/恢复语义，R5/R6尚未启动。


## R5.0 基础收纳候选交付 · 2026-10-16

本窗口已完成 R5.0 审计、实施、自审修复与真实 production React 验证，停 **R5.0 产品语义与基础收纳待人审**。候选合同仍需独立人审，不表示已接受。仅收纳当前消息阅读表面，Composer/真实DOM保留；活跃/未知执行禁用；发送/新事实/身份变化恢复阅读；无颗粒/松解/吸纳/相位/密度材质。

[计划](R5-PLAN.md) · [候选合同](R5.0-INTERACTION-CONTRACT.md) · [交接与入口](../../../artifacts/product-acceptance/ui-iteration/R5/attempt-01-basic-fold/HANDOFF.md)。R4 209份冻结文件已逐文件核对；已接受未提交成果完整保留。68文件582前端测试、类型/构建/局部静态、22收纳浏览器场景、四类组合/16页同数据对照及隔离API+Fake整链证据均在新attempt。真机/系统冻结/真实供应商等未测限制明确。R5后续效果与R6未启动，未提交、推送或部署。


## R5.0 已接受与下一步方案 · 2026-10-16

用户“通过，下一步任务是什么，先给出你的方案”明确接受R5.0。工作区及原候选快照229/229重新核实一致，冻结指纹dbd14e7ac01cf6267331ab2087579e4836652c5f1c95479532513905afd8d673。[接受决定](../../../artifacts/product-acceptance/ui-iteration/R5/R5.0-DECISION.md)优先于原attempt交付时的待人审文字；原包不改写。

建议下一步R5.1可逆星点运动基础，再分步推进R5.2共同相位/松解吸纳、R5.3密度材质/性能及R5整轮收口。具体见[R5计划](R5-PLAN.md)。这是方案，尚未授权实施效果；R5整轮未接受，R6未启动，未提交/推送/部署。


## 历史：R5 速度候选补充（attempt-04）

用户反馈切换过快，默认改0.50×；提供1.00/0.65/0.50/0.35/0.25五档评审选项。入口http://127.0.0.1:5343/REVIEW.html，离线与增量证据见artifacts/product-acceptance/ui-iteration/R5/attempt-04-speed-options。attempt-03历史完整保留，R5整体仍待人审。


2026-10-17 评审入口恢复：用户Windows截图显示连接被拒绝。核实Windows保留端口5286–5385包含旧5346/5336，改为Windows回环评审 http://127.0.0.1:15346/REVIEW.html 与隔离API预览 http://127.0.0.1:15336/ 。源码241/241与attempt-01一致，未改产品；原包/证据保留。Windows端完整HTTP读取、真实隔离登录/me/退出204已验证。启动与边界见R6/attempt-02-windows-review-entry/README.md。仍停R6整体待人审。


2026-10-17 · 用户要求切换对话的粒子收束/展开与“展开阅读区”参数一致。R6 attempt-03将两入口共用8.5×0.35与hostTempo=2，手动阅读原速度保持，切换从23基础系数放慢；真实数据等待/路径/配色不变。当前入口15346已切换到该候选，证据及恢复见artifacts/product-acceptance/ui-iteration/R6/attempt-03-unified-mercury-timing/HANDOFF.md。R6整体仍待人审，未提交/推送/部署。
