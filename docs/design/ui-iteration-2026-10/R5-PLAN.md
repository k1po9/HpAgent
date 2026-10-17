> 历史设计/交接记录：保留当时基线和验证边界；当前实现与操作从[文档导航](../../README.md)进入。

# R5 已接受并冻结

2026-10-16，用户指定0.35×、收束及下一次展开两个端点缓动区间各减半，并明确“代表我通过”。两项已完成且增量复验通过，R5整体正式接受。默认A、真实跨栏星流、会话切换及R5.0语义保持。

240份源码指纹 `d767ac3b8d22330f20dbe63eb930f8020b0da56b36e8279c929f5dcf03996d22`。最新[接受决定](../../../artifacts/product-acceptance/ui-iteration/R5/DECISION.md)、[合同](../../../artifacts/product-acceptance/ui-iteration/R5/attempt-05-accepted-timing/ACCEPTED_CONTRACT.md)、[交接/启动/恢复](../../../artifacts/product-acceptance/ui-iteration/R5/attempt-05-accepted-timing/HANDOFF.md)。入口http://127.0.0.1:5344/REVIEW.html。历史完整验证和未测边界保留，本次只重跑时序增量。

R6未启动，后续需明确授权；未提交、推送或部署。

<details><summary>历史：候选、实施与待人审记录；最新接受决定优先</summary>

# R5 当前交付：会话切换、跨栏终点与主题配色

当前状态：用户明确授权按方案实施，并要求星流收束终点越过主会话区进入左侧列表。本轮已完成工程自审、修复与验证，停在 **R5整体待人审**。R5.0仍已接受；attempt-02与当前attempt-03都没有自动接受。R6未启动，无提交/推送/部署。

左侧点击B：原导航guard先执行，获准后A真实阅读DOM短暂只读退场并进入A条目；原B数据请求立即发出，真实B内容确认后从B条目展开。主粒子经过窄通道进入条目内部，不是停在画布边缘再补一根线。快切/错误/减动/宿主丢失优先真实状态；目标活跃Run直接呈现执行。保留单一Composer、草稿、附件、阅读锚点及R3/R4/Inspector/表单语义。

颜色读取原Paper Flame token，默认A冷瓷主体+15%焰橙，B35%和C75%为同动作评审候选，尚待独立讨论确认。240份源码指纹2eba49f64826ccd18994fc15370e9f5eeb51b4326cef2d7f98ecee690789cd60；较前一候选13原文件改动、4新增、223不变；原未提交File/Task成果保留。

593前端测试、24切换场景、19+22+9收纳回归、四类/R3/16子页/7弹层、真实隔离API5组、80次切换资源与旧版每次请求数对照通过。真实设备IME/OS冻结/其他浏览器/GPU/长期堆分析等未测，详见报告。中断后同份源码未变，原隔离容器重验并仅恢复丢失的专用tmpfs测试数据，真实API整链复验；没有触及产品数据库。

- [本轮交互合同](R5-CONVERSATION-TRANSIT-CONTRACT.md) · [逐项验收](R5-CONVERSATION-TRANSIT-ACCEPTANCE.md)
- [交接/启动/恢复（已归档）](../../development/repository-archive-index.md)（原路径 `artifacts/product-acceptance/ui-iteration/R5/attempt-03-conversation-transit/HANDOFF.md`） · [验证结果（已归档）](../../development/repository-archive-index.md)（原路径 `artifacts/product-acceptance/ui-iteration/R5/attempt-03-conversation-transit/VALIDATION_RESULTS.md`）
- 本地总入口 http://127.0.0.1:5342/REVIEW.html；单文件离线 offline/REVIEW.Standalone.html；三种颜色只在评审包中切换，不新增产品设置。

<details><summary>历史：上一轮R5.1–R5.3范围与交付；“不新增切换动画”已由最新用户授权更新</summary>

# R5 Mercury 实施计划与当前交付

当前状态（2026-10-16）：R5.0已接受冻结。用户明确授权“进行 R5.1–R5.3 的实施”，三个阶段已按顺序实施、自审、修复并验证；最终停 **R5整体待人审**。没有自动接受阶段，没有启动R6，没有提交、推送或部署。沿用Mercury Motion Skill，暂停的双流程离线封装不恢复。

| 阶段 | 实际完成 | 状态 |
|---|---|---|
| R5.0 | 产品语义、真实DOM基础收纳、草稿/附件/锚点及业务保护 | 已接受，原决定有效 |
| R5.1 | 灰白星点、固定ID/seed、可逆阻尼进度、主阅读区边界 | 工程自审完成，阶段对照保留 |
| R5.2 | 同二维phase的DOM软松解/粒子出生、真实会话宿主少量细点吸纳 | 工程自审完成，阶段对照保留 |
| R5.3 | 局部密度低亮度短银丝、质量降档与异常直达终态、整轮回归与离线包 | 工程验证完成，整体待人审 |

本轮仅阅读区收纳/展开，不新增会话切换动画。原useReadingFold独占展示目标，新增controller只持有装饰进度/速度/几何；API/store/Run/权限/路由/Inspector/Composer业务不改。过渡真实DOM保留挂载并inert/aria-hidden，收纳终态hidden；自动恢复不等视觉回调。窄屏/减动/不可见宿主/异常直接正确终态。细化合同是本轮候选，不冒充已接受规则。

R5.0基线229/229一致后完整备份。最终236份源码指纹831565b57260bf62d0a1e827b3114f2cb5c3113153329a3ecf54c86b149f1649，3个原文件修改、7新增，其他226原hash；已接受的未提交File/Task成果保留。69文件589测试、类型/生产构建/局部静态、50收纳生产场景、四类/R3/16子页/7弹层、真实隔离API、30完整往返资源等验证通过。实测条件和未测项详见报告，不代表真实硬件60fps。

- [合同](R5.1-R5.3-INTERACTION-CONTRACT.md) · [逐项验收](R5.1-R5.3-ACCEPTANCE.md)
- [本轮交接（已归档）](../../development/repository-archive-index.md)（原路径 `artifacts/product-acceptance/ui-iteration/R5/attempt-02-mercury-r51-r53/HANDOFF.md`） · [验证（已归档）](../../development/repository-archive-index.md)（原路径 `artifacts/product-acceptance/ui-iteration/R5/attempt-02-mercury-r51-r53/VALIDATION_RESULTS.md`）
- 本地入口 http://127.0.0.1:5341/REVIEW.html；完整离线包为同attempt下offline/REVIEW.Standalone.html。
- R5整体接受后须另获明确授权再推进后续；本轮没有接受决定。

<details><summary>历史：R5.0接受与后续建议（当时的“未授权/停分段人审”已由本次授权更新）</summary>

# R5 Mercury 实施计划

2026-10-16，R5.0 已获用户明确接受并冻结；后续效果仅提出方案，尚未实施。最新决定见 [R5.0 接受决定](../../../artifacts/product-acceptance/ui-iteration/R5/R5.0-DECISION.md)。R1–R4 已接受冻结，R4 最新 DECISION 优先于历史 attempt。HEAD 9d47e55 的未提交 File/Task 成果是基线组成部分，不可用 HEAD 替代。

R5.0：产品语义与无特效基础收纳，已完成并获独立人审接受。229份源码/配置指纹 dbd14e7ac01cf6267331ab2087579e4836652c5f1c95479532513905afd8d673，本次工作区和原候选快照均229/229一致；不重新计入历史测试通过数。

- [本轮合同](R5.0-INTERACTION-CONTRACT.md)
- [本轮验收](R5.0-ACCEPTANCE.md)
- [attempt-01（已归档）](../../development/repository-archive-index.md)（原路径 `artifacts/product-acceptance/ui-iteration/R5/attempt-01-basic-fold/HANDOFF.md`）

核实 209/209 冻结文件一致，汇总 3f157303d1526c5357cfb899fe445035e01bc3c0d78926dd4240a80d573b5758。227 份源码/配置、工作区与暂存差异、原始交接输入已备份。无祖先/子目录 AGENTS.md。Mercury 本地 Skill 及三个接入/实施/验收参考已阅读；以本窗口用户授权优先，仅实现建议产品合同，不把原型当产品事实。

实现边界：HpThread 保留 assistant-ui Runtime、真实消息节点、ExecutionBlock 与 Composer；局部 hook 只持有当前 scope 的折叠位与滚动快照。Header 持久按钮通过 React portal 接入现有 Canvas header，消息不会迁移到 portal。加载历史和回到底部控件一起 hidden/inert。ChatPane 继续映射现有 auth/workbench/shell 权威事实，不增加业务请求、订阅或领域状态机。Inspector 仍独立。

后续颗粒/可逆进度、共同相位、松解吸纳、密度材质及 R6 都未授权，必须在本步接受并收到明确后续授权后再实施。不自动提交、推送或部署。


## 后续方案 · 尚未授权实施

建议按 R5.1 → R5.2 → R5.3 分步交付，每步自审修复并停本步人审；本划分为本次方案，不冒充用户已批准。

### R5.1 可逆星点运动基础（建议下一步）

目标：收纳有明确去向、展开能原路返回，中途反向不跳变。

- 真实阅读区域提供几何起点，左侧当前会话条目为归属终点；先用低亮度灰白/银白星点检验弯曲流动、速度层次与方向，暂不做材质、辉光、软相变或复杂捕获反馈。
- 复用同一批固定身份粒子和同一条可逆进度轴。收纳/展开中再次操作，从当前进度和速度反向，避免清场重播或复制另一批粒子。正常全程约1秒作为首版试调起点，非已接受参数。
- 颗粒主体限制在主阅读画布，进入侧栏仅少量细点/连接；避开Composer、Inspector、原生dialog。当前会话宿主不可见/离屏、窄屏、减动、后台或异常时直接正确终态。
- R5.0业务合同保持：输入和恢复按钮始终可用；有效发送、新执行/未知事实、权限/账户/会话变化优先恢复阅读，立即取消旧装饰，不等动画、不抢输入焦点、不重发。切换目标和已完成终态明确区分；过渡期间真实消息保留挂载，隐藏业务内容hidden/inert，控制含义与ARIA和当前目标一致。
- 先做无材质几何基线。正文与粒子的最终软松解衔接留到R5.2，本步不能将基线切换冒充完整Mercury视觉定稿。

实现位置：在现有mercury目录扩展纯呈现controller/overlay，与useReadingFold配合；读取现有业务事实，不增加会话/Run/Inspector状态机。不移植离线原型调试控制器，不复制消息截图，不每帧写全局store。

验证：真实production React中正常收纳/展开、早/中/晚三段反向、快速连续操作；DOM/草稿/附件/滚动/焦点/网络身份；resize、hidden、宿主失联/卸载、减动、账户切换；与R3/R4/表单guard组合；目标尺寸、实际帧间隔与资源计数。给同数据灰白点关键帧和可操作HTML，性能按测量条件报告，不预先承诺60fps。完成后停“R5.1 可逆星点运动待人审”。

### R5.2 阅读表面松解与宿主吸纳（后续待授权）

将阅读表面的可视退场与星点释放由共同空间相位驱动，解决正文直接消失、星点另播的拼贴感；加入克制的会话条目捕获/反向重建。真实DOM仍保留，业务和可访问性不由视觉完成回调决定。复验中途反向、异常和R3/R4组合，再停人审。

### R5.3 密度材质、性能与R5整轮收口（后续待授权）

在已认可运动/相位上增加局部密度形成的低亮度银丝与材质，保持星点可读、不形成大块亮膜；按实测划分性能档与回退。完成长时间和跨轮组合回归，交付完整R5整轮待人审。R6只有R5整轮接受并获明确授权后再开始。

本轮仅记录R5.0接受及后续方案，没有实施上述效果，没有提交、推送或部署。

</details>

</details>

</details>
