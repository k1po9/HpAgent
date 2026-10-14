# R4 新窗口实施 Prompt

你在 `/home/hp/workspace/HpAgent` 实施 HpAgent UI 的 R4，此窗口只负责本轮。总调度已经只读核查 R3 功能清单；R1、R2、R3 均已正式接受，代码未提交。不要重审或重做已接受效果。

先完整读取适用 AGENTS.md，以及：

1. `docs/design/ui-iteration-2026-10/README.md`、`R3-FUNCTION-AUDIT.md`、`R4-PLAN.md`、`R4-ACCEPTANCE.md`、`REVIEW-TEMPLATE.md`。
2. `artifacts/product-acceptance/ui-iteration/R3/attempt-03/` 的 DECISION、INTERACTION-CONTRACT、HANDOFF、VALIDATION、source-manifest；R2/attempt-06 的决定、合同和交接；R1.3/attempt-04→05→06 的继承静态合同。R3 最终决定优先于 FEEDBACK-MATRIX 尾部旧“待人审”文案。
3. `ui_skill_delete_after_done/` 内 Paper Flame、Mercury、EXP-02 A/B/C 五个目录的完整 SKILL.md，按要求读取本轮相关引用。B 的 decision-log、spatial-language、relay-protocol、repo-adaptation、integration-plan、acceptance、visual-tokens 全部必读；查看其参考图/实验，读取 relay-state 与 ArrivalGate 示例时只能按实际业务适配。Paper Flame 负责材质，A/C 负责已接受接力与状态兼容，Mercury 仅保留边界，本轮不实现收纳。

用户最终决定覆盖 Skill 旧描述：R3 发送确认后 queued/running 即可接力，waiting 是圆环、running 是菱形，颜色不变；正常 phase 变化不中断本次接力；用户采用直接 HTML 交互评审，无需强制录像。不要回退这些结果。

现在按 R4-PLAN 完成 R4.0 基线与 R4.1 Run 纵向闭环的实现、自审、修复和实际 production React 交互评审包，停在“R4.1 待人审”。获得局部认可后，在本窗口继续 R4.2 四类对象与整体终审。不得把 R4.1 认可当 R4 整轮完成，不自动进入 R5 或新建/联系其他窗口。

## 实施必须落地

- 核实工作区与 R3 接受清单（271文件，指纹见 DECISION）。基线包含未提交/未跟踪成果，不 reset，不用干净 HEAD 替代，不假定新 worktree 带有所有资料。
- 读取当前 InspectorHost/Tabs/useInspectorFlow/Surface/AppShell、shell store、四类 Inspector 和全部真实打开入口。请求/订阅依赖组件生命周期，不能到达才挂载并拖延业务请求。
- 业务导航/授权/查询及时执行，呈现独立 receiving/landed/settled/closing；guard 拦截时不出发。复现并定向处理 openInspector 在 navigate 被拦截后仍写 backStack 的接缝。
- 强化银丝、无缩略卡、真实 source/target 坐标；仅同 token 的实际头部抵达可揭示当前目标标题/Tab/正文。接纳中无旧内容、无隐藏可聚焦控件；错误/权限失效可直接安全降级。
- X=closeInspector，返回=back，Escape 最高层；逻辑关闭与焦点立即生效，仅无业务内容的壳/布局占位继续约680ms收束。关闭中重开取消旧退出，AppShell 不先瞬间删列。
- 完整目标/版本/账户/会话代际校验；同对象 Tab/展开不发银丝，Artifact 默认版本归一化不重播，主动选版本不串旧数据，同对象 iframe/编辑器不为动画重挂载。
- 保留 native dialog 和既有 Surface 层级；窄屏/缺来源/减动/hidden/滚动/resize/异常直接收敛，不卡 receiving。每帧局部 controller/ref，不写全局 Store，不建立接管业务的大状态机。
- R3 装饰接力可被真正的 Inspector 导航取消，C 始终按当前事实显示；不改变发送、停止、幂等、权限和 Run 业务。不重做 R1/R2，不提前实现 Mercury。

## 检查与交付

按 R4-ACCEPTANCE 跑有意义的状态、集成和浏览器测试，包含真实请求时序、旧回调、guard/backStack、版本、焦点、移动 modal、减动和 R3 组合。只跑相称的检查，不将历史数量算成本轮结果。相关 typecheck/build 与局部静态检查必须完成。

先验证隔离服务、端口、账户与数据用途，历史 5294/8197/5317 不保证仍可用。不要覆盖 R3 冻结评审，不使用默认 DSN 清库/迁移，不发真实 QQ、不调用付费供应商。单会话账户的浏览器测试串行。真实 API/Fake 与浏览器夹具分开标注。

证据放 `artifacts/product-acceptance/ui-iteration/R4/attempt-NN/`，记录 stage，保留失败与返修历史。提供 README、SELF-REVIEW、VALIDATION、FEEDBACK-MATRIX、HANDOFF、源码指纹、时序/焦点日志与关键帧；最终接受后增加 DECISION、INTERACTION-CONTRACT 和冻结源码。

人审使用可直接操作的 production React HTML 专注页和同数据前后对照；正常打开、快切、慢请求、关闭重开场景可复现，评审按钮/夹具不进产品。沿用直接交互偏好，不要求用户先看录像。先自行真实操作检查并修复，再交给用户；工具能力不足必须说明，不能虚构实际检查。

最终回复说明本次 stage、完成的功能、可点击入口、需要用户判断的少量具体问题、测试与未测范围。人审否决继续当前阶段返修，局部认可后才进入后续子阶段；明确接受整轮后才称 R4 完成。不自动提交、推送、合并、部署、删除 Skill 或跨入 R5。
