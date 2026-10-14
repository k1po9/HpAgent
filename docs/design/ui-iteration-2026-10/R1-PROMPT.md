# R1 新实施对话启动 Prompt

复制下方正文到 HpAgent 项目的新对话。若仅发送路径，要求先完整读取本文件并按正文执行。工作目录：`/home/hp/workspace/HpAgent`。

---

你是 HpAgent UI 重构的 R1 实施窗口。另一个对话作为总调度台；你负责完成 R1 的实现、自审、验证与交接。本轮目标是提升静态审美和视觉辨识度，交付两个可实际操作、可比较的真实 React 候选，供我人审。请直接实施，不停在计划或能力说明。

先读取项目适用 AGENTS.md（如有），然后完整读取：

1. `docs/design/ui-iteration-2026-10/README.md`
2. `docs/design/ui-iteration-2026-10/R1-PLAN.md`
3. `docs/design/ui-iteration-2026-10/REVIEW-TEMPLATE.md`
4. `ui_skill_delete_after_done/hpagent-paper-flame/SKILL.md`
5. `ui_skill_delete_after_done/hpagent-input-action/SKILL.md`
6. `ui_skill_delete_after_done/hpagent-living-components-spatial/SKILL.md`
7. `ui_skill_delete_after_done/hpagent-agent-vitality/SKILL.md`
8. `ui_skill_delete_after_done/mercury-motion-hpagent/SKILL.md`

按 Skill 阅读静态设计、接入和验收相关 references，并实际查看 R1-PLAN 指定的参考图和历史生产截图。B/C 里“A尚未收敛”是旧说明，以最新 A4.2.1 冻结设计为准；暂不引入 Morphicons。

先核对当前 HEAD、分支、未提交改动及输入文件是否齐全。总审计基线是 main@b8a5a02，不假定当前仍相同。方案文件和 Skill 可能未提交，隔离 worktree 不一定有这些文件。保留用户现有改动；不要覆盖或删除。若建立分支，使用 codex/ 前缀。不要自动提交、合并、推送或发布。

本轮要产出：

- S1：开放正文＋明确对象卡，重点降低普通回答容器重量。
- S2：轻边界阅读面，以轻薄纸瓷承载建立归属，但避免白卡套白卡。

两版使用同一套真实组件、数据与四区比例。保留现有业务、权限、草稿、IME、附件、运行、导航、Surface焦点和iframe生命周期。第一轮固定现有布局比例，优先比较排版、正文表面、对象身份、行动层级与详情密度；不能只改圆角声称两版。

本轮允许必要局部语义DOM和作用域样式调整，随组件收敛已有CSS覆盖；不新增全局“最终美化覆盖层”。开发预览可以切换候选，但不要把开关加入正式产品流程，也不要通过页面临时注入CSS/移动DOM制造截图。保留可复现基线和两候选，不自行选定最终默认。

本轮不实现A接力、B银丝/Arrival Gate/Soft Close、C新映射与连续动画、Mercury收纳；不改X/back语义、后端或业务Store契约。已知后续架构问题只记录，不顺手扩大范围。受控harness可以补充极端数据，必须标明；至少提供真实应用集成预览，不把离线HTML当真实前端。

按 R1-PLAN 的 ST01–ST09 和证据预算实施。先完成短问答、长文、Run/Artifact详情的桌面和手机切片；实际看渲染图后修正。完成视觉契约/读图自审与交互/响应式自审，记录问题、修复和同场景复验。不得仅凭测试通过、截图数量或自己的审美分数宣称用户已接受。

运行与改动相称的 typecheck、lint/format、build、既有单测和浏览器检查。先读取 docs/development/testing.md，核实专用可丢弃数据库、Redis、文件目录和端口；不使用业务库或盲目沿用历史服务，旧证据不可覆盖。环境缺失时继续独立工作并明确 blocked/not-run，不能伪称完整验收。

在 artifacts/product-acceptance/ui-iteration/R1/attempt-01/ 交付 README.md、SELF-REVIEW.md、VALIDATION.md、HANDOFF.md 以及可追溯截图/日志。目录存在时保留旧证据，新建下一 attempt。使用 REVIEW-TEMPLATE 记录总调度所需的信息。

最终答复给我：两版各一句设计差异、预览入口、核心对照图、自审发现与修复、验证结果与限制、你的推荐及理由，以及 HANDOFF.md 的绝对路径链接。将状态标记为“待人审”（若技术条件不齐则明确阻塞），由我选择 S1/S2/定向融合/返修。不要自行进入 R2，不主动创建其他对话或向其他对话发消息。

这是设计实施任务。请完成具体、可操作、可比较的成果之后再交给我评审。
