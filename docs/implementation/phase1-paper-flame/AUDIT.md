> 历史设计/交接记录：保留当时基线和验证边界；当前实现与操作从[文档导航](../../README.md)进入。

# Phase 1 实施前审查 · 2026-10-12

基线：main / a47d73b06a993d353b4177e282b69ddd28ffdb36，工作区无已有修改。实施分支：codex/phase1-paper-flame。

规范源：用户附件 HpAgent_Paper_Flame_Color_Skill_v1.0.zip 与 Mercury_HpAgent_Motion_Skill_v1.0.zip（读取 SKILL.md、设计规范、repo-adaptation、兼容性与验收资料）。附件原型及分阶段 Prompt 是参考，当前用户限定的 Phase 1 范围优先。

| 实际文件                                                 | 当前来源 / 风险                                                                                                          | 最小修改                                                                      |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------- |
| web/src/main.tsx                                         | Radix light / blue / slate；Radix CSS 先于项目 CSS                                                                       | tomato 基础 + 精确语义色映射                                                  |
| web/src/styles.css                                       | tokens→base→shell→conversation→run→workspace→tasks→artifact→unification→visual→entry                                     | 保持导入顺序，源头消除覆盖中的旧色                                            |
| web/src/styles/tokens.css                                | hp accent/focus/selection 都为蓝色；布局宽度被 useShellWidth 读取                                                        | 建立 pf 官方色族与 hp 语义别名；保留全部尺寸/时长                             |
| web/src/styles/shell.css                                 | Rail #fcfdff；品牌和导航消费浅底文字 Token；Canvas 无显式背景；Inspector 白色                                            | Rail 独立深色 Token，Context/Canvas/Inspector 独立空间 Token，Portal 同样生效 |
| web/src/styles/visual.css                                | 登录、上传、新任务、文件图标、选中任务和 hover 均有后置蓝色硬编码；Rail hover 浅色                                       | 逐条迁移，避免新增全局覆盖层；普通文件图标/状态用中性色                       |
| web/src/styles/base.css                                  | Trace 使用 blue-10/11/a3；gray-9/10 小字；错误与警告依赖 Radix                                                           | 改用 hp neutral/success/danger/warning；保留文字和图标                        |
| web/src/styles/conversation.css                          | 新对话、send 蓝底；placeholder #8b97a9；用户气泡与 AI marker 共用 selection；Composer 低对比边界                         | 主按钮深橙配白字、品牌独立亮橙；用户蓝灰气泡、中性 AI marker；控件边界和焦点  |
| web/src/styles/run.css                                   | 默认 heading accent，诊断灰色及 accent ramp                                                                              | 运行中用中性状态，成功/失败现有选择器保留；语义化诊断表面                     |
| web/src/styles/workspace.css                             | Radix gray/selection + visual 后置覆盖；输入与按钮轻边框                                                                 | 统一表面、边界、选中；不改树/表格/权限逻辑                                    |
| web/src/styles/tasks.css + TaskRow.tsx                   | 所有 status 被 visual.css 涂蓝；组件已有 presentTask 输出但无样式状态属性                                                | 只读 data-status/data-bucket，用当前状态和 bucket 着色；不新增状态模型        |
| web/src/styles/artifact.css                              | hover/主按钮存在旧蓝；iframe 使用 sandbox/srcDoc                                                                         | 迁移外围卡片与操作；用户 HTML 内容保留                                        |
| web/src/styles/entry.css + components/entry/particles.ts | 登录确认按钮复用 accent；粒子读取 hp accent/muted，fallback 蓝；GSAP 为单向登录/恢复过渡                                 | 只更新光学来源与 fallback；不改粒子数量、轨迹、帧缓存、生命周期               |
| AppShell / InspectorHost / Surface / useShellWidth       | fit=rail+sidebar+canvasMin+inspector+gap；<960 抽屉，<1280 Inspector modal，<600 底栏；Surface 嵌套 portal 的 Theme      | 不改结构/阈值/导航；验证深色底栏及模态变量继承                                |
| ChatPane / HpThread / ConversationSidebar                | viewport 包含消息和 ExecutionBlock；composer 独立；data-conversation-id 为真实宿主；业务由 workbench/conversationUi 控制 | 本轮仅记录 Mercury 接入边界，不添加 ref/收纳入口/展示态                       |

风险优先级：P1 Radix/Portal 蓝色回退；P1 文本与交互对比；P1 状态色语义；P1 窄屏可操作性；P2 CSS 后置覆盖与粒子颜色。

验证计划：当前 React 页同数据同视口 before/after；1680/1280/780/390；四类 Inspector、登录/注册/Modal、Tab 焦点、reduced-motion、草稿与 DOM 身份跨断点；typecheck、变更文件 lint/format、受影响组件单测与必要浏览器回归，不运行完整 CI。
