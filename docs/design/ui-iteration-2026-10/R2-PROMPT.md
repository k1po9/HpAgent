# R2 新实施窗口 Prompt

你在 `/home/hp/workspace/HpAgent` 实施 HpAgent UI 的 R2。此窗口是 R2 实施窗口，总调度在另一个对话。R1 已正式人审接受；本轮目标是在已接受的静态设计上完成日常空间反馈、六态执行生命信号和输入微状态。不要重新做 R1，也不要提前实施 R3/R4/R5。

## 必须先完整读取

1. 适用的 AGENTS.md，`docs/design/ui-iteration-2026-10/README.md`、`R2-PLAN.md`、`R2-ACCEPTANCE.md`、`REVIEW-TEMPLATE.md`。
2. `artifacts/product-acceptance/ui-iteration/R1.3/attempt-06/` 中 DECISION、HANDOFF、STATIC-DESIGN-CONTRACT、VALIDATION、FEEDBACK-MATRIX，以及其继承的 attempt-04、attempt-05 静态合同。后续用户决定优先，旧 S1 图不是最终基线。
3. 下列五个 Skill 的完整 SKILL.md，并按各自要求读取本轮相关引用与验收材料；先声明使用目的，再实施：
   - `ui_skill_delete_after_done/hpagent-paper-flame/SKILL.md`
   - `ui_skill_delete_after_done/mercury-motion-hpagent/SKILL.md`
   - `ui_skill_delete_after_done/hpagent-input-action/SKILL.md`
   - `ui_skill_delete_after_done/hpagent-living-components-spatial/SKILL.md`
   - `ui_skill_delete_after_done/hpagent-agent-vitality/SKILL.md`

Paper Flame 用于延续材质，B 用于空间语法，C 用于执行状态，A 用于输入微状态，Mercury 仅用于兼容边界，本轮不实施 Mercury。必须阅读 C 的 state-mapping/design-contract/acceptance，A 的 composer-states，B 的 spatial-language/visual-tokens 及 Skill 要求的其他资料；实际看对应参考图和实验。参考实验不能代替真实 React 产品预览。

## 启动后直接推进

先核实工作区与 R1 最终 source-manifest，记录差异、HEAD 和未提交改动来源。当前 accepted baseline 是 dirty 工作区，不是纯 Git HEAD；208 文件指纹见 R1 DECISION。不得 reset、覆盖用户改动或假定新 worktree 自带未提交资料。核实隔离服务/端口/账户，历史预览地址不等于当前可用地址，不使用默认 DSN 迁移或清理数据。

按 R2-PLAN 顺序执行：R2.0 基线 → R2.1 B 空间反馈 → B 局部人审 → R2.2 C 生命状态 → C 局部人审 → R2.3 A 微状态与整体验收。现在完成 R2.0 和 R2.1 的实现、自审、必要修复、可操作预览与人审包，然后停在 `R2.1 待人审`。这一停止点来自用户所要求的分段人审流程，不是在工作前请求重复授权。

同一实施对话收到用户继续/认可后再进入下一子阶段。返修在当前阶段完成，不自动创建其他对话、不联系总调度窗口、不跨入下一大轮。例行可逆实施和检查自主完成；遇到影响设计合同或业务语义的新选择，先给具体证据与可审方案再问用户。

## 不能破坏的核心约束

- 保持最终银瓷回答面、Composer 无内下划线、当前布局比例、任务 B 四区与焦点保位、账户 A 和真实业务保护。
- B 只做 Conversation Re-seat / Rail Shift / 普通 Surface Elevate。保持导航 guard、native dialog、焦点与嵌套；不要给 Inspector 叠入场，不实现银丝/到达门控/Soft Close。
- C 使用共享事实映射和 compact/detail，业务文本/按钮即时更新，视觉从当前颜色连续过渡。终态优先；取消中性；finalizing 不是成功；断连不是失败；历史和未知审批不臆造状态。保留原有查询/订阅归属。
- A 只做冷瓷聚焦、真实 ready 的局部内焰、Send 意图接力；保留 IME、幂等、提交锁和草稿 revision。无飞线、轨道或虚构接收确认。
- 共享系统/本地减动、代际取消和清理；无逐帧 Store 更新、无大型动画总管。偏好改变、hidden、卸载、换账户/Run 后不遗留或补播效果。
- 不把脚本检查通过当审美完成，不把 B/C 局部认可当 R2 最终接受。

## 每次人审交付

按 R2-ACCEPTANCE 和模板，将证据写到 `artifacts/product-acceptance/ui-iteration/R2/attempt-NN/`，保存 stage、源码指纹、同数据前后对照、原速视频、关键帧、SELF-REVIEW、VALIDATION、FEEDBACK-MATRIX、HANDOFF。先实际读图/检查运动，再修复并复验；记录具体问题而非空泛评分。无法原速读视频要明确限制，不能声称已看。

验证与改动相称，覆盖 guard、快速切换、层级/焦点、中断、减动、移动宽度、200% 文字、R1 核心回归。区分真实 API 与确定性注入，使用串行隔离浏览器会话，不真实外发 QQ 或调用付费供应商。最终需要 production build 的真实可操作页面；历史测试数不得算作本轮通过数。

最终用户明确接受 R2 后才写 DECISION 和 R2 交互合同、完整交接。未接受时写准确阶段与待判断问题。最终消息给用户可点击预览、原速证据、具体改善、限制及本次需要判断的少量问题。不自动提交、推送、合并、部署或删除 Skill。
