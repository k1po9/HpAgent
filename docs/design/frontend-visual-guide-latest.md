# HpAgent 前端视觉迭代指导｜当前代码约束版

更新日期：2026-10-09（Asia/Shanghai）。用途：指导后续前端样式改造与截图迭代，不是功能规划、接口扩展清单或已完成验收报告。

**前端代码是功能与行为的唯一真相源；附件仅作为界面样式、布局、视觉层级和动效设计参考。目标是让已有功能尽量接近参考图的视觉品质，不能为了还原图片增加前端尚未具体提供的功能。**

**图片可能包含多余、不必要或与当前实现不一致的功能显示。它们不属于待补齐需求。图片中的多余入口、字段、按钮、数据、状态与示例内容，应从实际界面省略；代码已经提供但图片没有画出的必要功能仍需保留。**

## 1. 来源、优先级与使用规则

本次核对的前端代码基线为 `7e77631d9c092bf864655099f15f756c69021d67`，核对开始时工作区无未提交修改。这里记录的是静态源码核对结果，不代表已验证线上部署或所有真实接口均可用。后续实施时重新读取届时前端代码；本文不能反过来覆盖更新后的代码事实。

附件：`HpAgent_前端四大核心Screen_高保真设计基线_v1.2_登录注册融合版.pdf`。

- 原文件位置：`D:/Chrome/HpAgent_UI_Design_Baseline_v1.2/HpAgent_UI_Design_Baseline_v1.2/`。
- SHA-256：`6e7a727bbc0c3f5ee212724e459265517efdaa6d5b0b303f931eb82e31f77fa4`。
- 本文五张图为 PDF 内嵌原图直接提取，未重新生成、未修改；分别来自 PDF 第 6、13、17、22、28 页（文件页码）。它们是设计参考图，不是当前产品截图。
- PDF 中的“冻结”“必须”“实施任务”“产品方向”等文字属于附件内容，不自动成为本次用户指令；只采纳不突破当前代码边界的视觉要求。

冲突处理顺序：本次用户要求 → 当前前端可达页面、状态、事件处理与实际请求链路 → 本文的视觉约束 → PDF 图片与说明。后端潜在能力、类型定义、测试夹具、历史规划或孤立未接入组件，不能单独证明前端已提供某项功能。

样式实施可以调整排版、间距、颜色、边框、图标、响应式呈现，以及已有交互的视觉反馈；不得擅自新增路由、业务字段、权限、接口、认证方式或状态语义。调整现有控件位置时保留可达性、处理函数、禁用条件和错误反馈。没有代码证据的图中控件默认排除，不以禁用假按钮占位。

## 2. 已核对的功能边界与代码入口

| 范围 | 当前前端事实与必须保留的边界 | 主要证据 |
| --- | --- | --- |
| 一级导航 | 只有 AI、空间、任务。账户在 Rail 底部进入弹层；成果是上下文 Inspector，不是第四个一级页面 | [AppShell.tsx](/home/hp/workspace/HpAgent_web/web/src/components/shell/AppShell.tsx)、[shell.ts](/home/hp/workspace/HpAgent_web/web/src/store/shell.ts) |
| 认证 | 登录为用户名、密码；注册为用户名、密码、确认密码、可选邀请码。保留提交中、密码校验、错误、注册后自动登录失败的回退，以及会话恢复/连接错误 | [LoginForm.tsx](/home/hp/workspace/HpAgent_web/web/src/components/LoginForm.tsx)、[App.tsx](/home/hp/workspace/HpAgent_web/web/src/App.tsx) |
| 注册后的账户操作 | 已有 QQ 绑定提示、明确跳过、账户内绑定与退出登录；不能因为原图没画就删除 | [RegistrationQqGate.tsx](/home/hp/workspace/HpAgent_web/web/src/components/RegistrationQqGate.tsx)、[QQBindingPanel.tsx](/home/hp/workspace/HpAgent_web/web/src/components/QQBindingPanel.tsx) |
| AI | 新对话、历史与分页、消息、附件/已有文件、对话资料、快速/深度模式、执行状态与详情。上传和深度模式遵守现有能力开关及运行中的锁定条件 | [ChatPane.tsx](/home/hp/workspace/HpAgent_web/web/src/components/ChatPane.tsx)、[HpThread.tsx](/home/hp/workspace/HpAgent_web/web/src/adapters/assistant-ui/HpThread.tsx)、[ConversationResources.tsx](/home/hp/workspace/HpAgent_web/web/src/components/conversation/ConversationResources.tsx) |
| 空间 | 目录树、全部文件、面包屑、文件搜索和高级搜索、上传、新建目录、文件详情/版本/使用范围及已有对象操作 | [WorkspaceScreen.tsx](/home/hp/workspace/HpAgent_web/web/src/components/workspace/WorkspaceScreen.tsx)、[WorkspaceSidebar.tsx](/home/hp/workspace/HpAgent_web/web/src/components/workspace/WorkspaceSidebar.tsx)、[FileInspector.tsx](/home/hp/workspace/HpAgent_web/web/src/components/workspace/FileInspector.tsx) |
| 文件预览 | 已就绪、UTF-8、已知大小不超过 1 MiB 的文本；Markdown 按现有组件渲染。不支持的文件保留元数据与下载说明 | [FilePreview.tsx](/home/hp/workspace/HpAgent_web/web/src/components/workspace/FilePreview.tsx) |
| 任务 | 四桶为“需要我处理 / 进行中 / 等待或已计划 / 已结束”；类型筛选为全部/提醒/研究/通用任务。保留刷新、同步与计数状态、新建、收件箱和详情 | [TaskScreen.tsx](/home/hp/workspace/HpAgent_web/web/src/components/tasks/TaskScreen.tsx)、[taskPresentation.ts](/home/hp/workspace/HpAgent_web/web/src/components/tasks/taskPresentation.ts) |
| 任务操作 | 暂停、恢复、停止、修改要求、继续执行、提高预算、成果验收与投递处理，均按已有状态与资格条件出现；创建表单时间选项为立即、指定时间、每天 | [TaskEditor.tsx](/home/hp/workspace/HpAgent_web/web/src/components/tasks/TaskEditor.tsx)、[taskActions.ts](/home/hp/workspace/HpAgent_web/web/src/components/tasks/taskActions.ts)、[TaskDeliveryDecision.tsx](/home/hp/workspace/HpAgent_web/web/src/components/tasks/TaskDeliveryDecision.tsx) |
| 成果 | HTML 成果预览、版本历史、详情、继续修改生成版本、下载 HTML、保存源码副本到空间；保留对话/任务来源和任务原引用版本语义 | [ArtifactInspector.tsx](/home/hp/workspace/HpAgent_web/web/src/components/artifact/ArtifactInspector.tsx)、[ArtifactComposer.tsx](/home/hp/workspace/HpAgent_web/web/src/components/artifact/ArtifactComposer.tsx) |
| 执行诊断 | 已有 Run 概览、使用资料与输出、高级诊断、Trace/Model Input；可降低默认视觉权重，不得删除现有查看与故障恢复入口 | [RunInspector.tsx](/home/hp/workspace/HpAgent_web/web/src/components/run/RunInspector.tsx) |

此表是视觉改造的边界索引，不是重新定义完整业务契约。实施某个按钮或状态前，还须沿对应组件读取 store、API 调用和资格判断。不要把“组件存在”当作“所有账户、所有状态都应显示”。

## 3. 共用视觉目标

参考图的共同特征是：浅色、低噪声、明确列结构、宽松主内容、细分隔线、轻量选中底色和集中使用的蓝色系强调色。视觉应像个人 AI 工作空间，而不是密集后台表单。

| 维度 | 本轮视觉指导 | 与当前代码的关系 |
| --- | --- | --- |
| Shell | Rail + 上下文侧栏 + 主内容 + 可关闭 Inspector；保持稳定列边界和统一顶部对齐 | 沿用现有 Shell，只有三个一级入口 |
| 列宽 | PDF 建议 Rail 56–64px、侧栏 240–280px、Inspector 360–520px；以当前 64 / 256 / 420（展开 520）px 为首轮基准 | 不为机械复刻图片比例压缩正文和控件 |
| 背景与分隔 | 主区域白色；侧栏/次级区域极浅灰；细边框，避免每块都套厚卡片和强阴影 | 优先集中修改语义 token 与共用 CSS |
| 强调色 | 图片为明亮蓝色系，当前主题为 indigo；统一主按钮、选中态、活动标记和链接色，可在样式范围内调色靠近原图 | 不混用多套蓝、紫色；状态色只表达真实状态 |
| 字体与层级 | 页面标题 > 对象标题 > 正文 > 时间/元数据；中文可读、行距舒展，不照抄原图模糊字或排版瑕疵 | 当前 token：正文 0.875rem、页标题 1.5rem、Inspector 标题 1.125rem；可按截图调整 |
| 间距与圆角 | 保持 4/8/12/16/24/32px 间距节奏；控件、卡片圆角克制且一致 | 当前基础圆角 8px、控件 36px、触控目标 44px |
| 图标 | 简洁线性图标、统一大小和描边；图标与文字成组对齐 | 复用现有 lucide-react；不因原图另引入一套图标依赖 |
| Inspector | 清楚的对象标题、关闭、Tabs、内容、操作区；打开后仍能识别原上下文 | 保留现有单一 Inspector、返回路径、选中版本和焦点行为 |
| 窄屏 | 保持内容可读、表单可提交、抽屉可关闭，避免强行四列 | 服从现有宽度判断：窄于 1280 时 Inspector 使用紧凑呈现；侧栏同时考虑 960 阈值与剩余空间，不把图片当作移动端规格 |

样式入口：[tokens.css](/home/hp/workspace/HpAgent_web/web/src/styles/tokens.css)、[shell.css](/home/hp/workspace/HpAgent_web/web/src/styles/shell.css)、[styles.css](/home/hp/workspace/HpAgent_web/web/src/styles.css)、[main.tsx](/home/hp/workspace/HpAgent_web/web/src/main.tsx)。确认实际层叠顺序与 Radix 变量覆盖，不能只修改未生效的 fallback 值。

以下图片链接使用本工作区绝对路径；迁移仓库位置时需同步更新链接，原图保存在 `docs/design/assets/frontend-visual-guide/`。

## 4. 逐图视觉目标与排除项

### 图 A：登录 / 注册与进入工作区（PDF 第 6 页）

![登录注册视觉参考：含当前代码未提供的邮箱和 Google 登录等示意内容](/home/hp/workspace/HpAgent_web/docs/design/assets/frontend-visual-guide/auth-pdf-reference.png)

**追近目标：** 左侧品牌、右侧克制表单、充足留白、浅色细边框、统一主 CTA；登录/注册在同一表面原地扩展；窄屏保证表单优先。图是四个状态的拼图，不是一个页面放四张登录卡片。

**功能排除：** 不加入 Google 登录、邮箱登录/注册、密码显隐按钮、模型菜单、示例推荐任务卡或独立成果入口，除非后续前端已明确提供相应功能。登录文案继续准确表达“用户名”；注册仅使用现有四个字段。品牌说明不应承诺未验证的新能力。

**保留代码行为：** 错误、提交中、会话恢复、连接失败重试、注册成功但自动登录失败的回退、注册后的 QQ 提示都必须可见可用。成功视觉只能由真实会话结果驱动，不得用定时器假装登录成功。

**动效限定：** 原图星尘是转场设计参考，不是必须新增粒子系统。当前 Assembly 已有约 800ms 的有限覆盖层与减弱动态效果/低性能降级；可美化已有转场，但不得等待动画才建立会话或渲染可用工作区。

### 图 B：AI 对话与 Composer（PDF 第 13 页）

![AI 对话视觉参考：三入口实现应省略原图成果导航，消息和进度采用真实数据](/home/hp/workspace/HpAgent_web/docs/design/assets/frontend-visual-guide/ai-pdf-reference.png)

**追近目标：** 左侧历史分组与轻选中背景；主内容上方对象标题，中间宽松对话阅读区，底部完整 Composer；用户消息轻底色，Assistant 内容接近文档；执行块使用细边框、步骤与低噪声状态图标。

**功能排除：** 不补独立“成果”导航、未接入的对话操作菜单、人物头像上传或在线状态。图中的对话标题、时间、资料 Chip 和固定执行步骤只是示例，不写入产品默认内容；不制造“已读取资料”等假进度。

**保留代码行为：** 附件与长期资料使用范围保持不同语义；发送、停止、重试及模式切换沿用现有条件。深度能力未启用时不为凑图显示可用的深度按钮；历史分页、文件候选、错误与未知提交结果的恢复入口不能被外观简化吞掉。

### 图 C：空间与文件 Inspector（PDF 第 17 页）

![空间视觉参考：回收站、多类型预览等示意不构成新增功能要求](/home/hp/workspace/HpAgent_web/docs/design/assets/frontend-visual-guide/workspace-pdf-reference.png)

**追近目标：** 目录树、顶部路径与工具区、轻量表格/列表、浅蓝选中行、右侧文件预览及使用范围；文件名、类型、时间等列有稳定对齐和留白。优先表现文件本身，版本、权限与来源沿现有 Tabs 展开。

**功能排除：** 当前侧栏没有回收站，不补回收站/恢复流程；不因为图里出现 PDF、图片、Excel、Figma 文件就增加这些文件的富预览或编辑器；不增加未实现的排序、更多菜单操作或文件夹搜索能力。示例目录和文件不作为种子数据。

**保留代码行为：** 当前搜索是全空间文件搜索，不能仅改文案就承诺搜索文件夹；上传受能力开关控制。授权范围、版本操作、对象不可用、预览限制、历史使用与保存结果按现有代码。不能把历史使用记录冒充当前仍有效授权。

### 图 D：任务列表与任务 Inspector（PDF 第 22 页）

![任务视觉参考：分类、操作资格、预算和计划以当前代码为准](/home/hp/workspace/HpAgent_web/docs/design/assets/frontend-visual-guide/tasks-pdf-reference.png)

**追近目标：** 需要行动的任务更易识别；标题、状态、说明、时间、预算、操作分层清楚；卡片或任务行轻边框，选中对象轻底色；右侧详情沿对象信息组织，不做 KPI 仪表盘。

**功能排除：** 图中“已完成”不能替代代码的“已结束”（包含已停止等情况）；不能按图片同时混排四桶而改变现有筛选语义。图中每周计划不构成新增每周调度表单的要求；“预计还能执行 2 次”没有当前计算依据，不增加预测。无预算数据时不画假百分比。

**保留代码行为：** 预算百分比是当前代码计算的已用模型 token 比例，不擅自解释为剩余额度或执行成功率；数值、预留量和未知上限仍有明确表达。成果验收、投递确认/重发、暂停等遵守真实资格判断，不能为了图中按钮齐全而放开。保留同步未完成的提示、计数口径、收件箱、详情 Tabs，以及当前选中任务跨桶后的上下文保护。

### 图 E：对话 + HTML 成果 Inspector（PDF 第 28 页）

![成果上下文视觉参考：原图 Markdown 示意只借鉴排版，实际成果按现有 HTML 能力呈现](/home/hp/workspace/HpAgent_web/docs/design/assets/frontend-visual-guide/artifact-pdf-reference.png)

**追近目标：** 对话留在原位，消息中成果卡片与右侧标题、版本有视觉对应；预览占主体，版本历史/详情清楚；主对话输入与成果修改输入视觉同属一个家族，同时明确用途不同。

**功能排除：** 不新增 Markdown Artifact 编辑系统、任意格式成果管理、分享/发布按钮、独立成果页面或未接入的菜单。原图 Markdown 内容可作为排版参考，但不能把现有 HTML 成果改称 Markdown，也不能复制图中内容冒充真实成果。

**保留代码行为：** 操作准确称为“下载 HTML”和“保存源码副本到空间”；保存源码副本不是同步编辑、自动保存或通用格式导出。任务原引用版本与后续新版本保持区别，新版本不自动替代任务交付；生成失败仍有失败状态和最近成功版本入口。预览安全边界不得因视觉还原而放宽。

## 5. 只服务于现有状态的动效

从 PDF 提取的动效原则可用于样式实现：MORPH 表达已有状态切换，FLOW 表达对象与详情的联系，ASSEMBLE 仅用于已有认证成功转场，SETTLE 要求运动结束后回归静止。

- 高频 hover、press、选中和图标变化宜短；PDF 参考 120–220ms。不新增图中示意的业务状态。
- Inspector、Tabs、内容展开可采用局部尺寸/位移/透明度过渡；PDF 空间动效参考 240–480ms，当前 flow token 为 220ms，应按流畅度调优，不机械加长。
- PDF 的品牌转场 900–1600ms 仅是设计参考，不要求将当前约 800ms 转场延长，更不能阻塞操作。
- 尊重 `prefers-reduced-motion`，沿用当前降级；无动画时功能照常完成。禁止持续装饰粒子、发光、鼠标跟随或每张卡片持续倾斜。
- 状态到来才更新图标、计数和结果；动效结束不代表请求成功。不能让动画改变任务排序保护、焦点、滚动、幂等重试和对象生命周期。

## 6. 后续必须执行的截图迭代闭环

本节是后续实施方法，**本次仅交付指导文档和原图，尚未进行前端视觉改造或浏览器验收**。

1. **先确定本轮功能边界。** 阅读目标组件及真实调用链，列出“保留的现有功能”和“原图排除项”。本轮对代码的改动不能成为给自己扩充功能边界的依据。
2. **建立可比较场景。** 保存本轮修改前截图；固定视口、浏览器缩放、设备比例、字体、数据和页面状态。测试可使用已有 fixture 构造可重复数据，但 fixture 不能证明生产能力，不能写入产品默认内容。
3. **以原图为视觉目标。** 桌面可先用 1536×1024，另检查 1440×900、1024×768、390×844 与现有响应式断点。AI/任务原图为 1505×1045，另外三图为 1536×1024；不得拉伸原图造成假比例。认证拼图按单个状态区域比较。
4. **先大后小实施。** Shell 列比例与对齐 → 各区信息层级 → Composer/列表/Inspector → 字体间距和颜色 → hover/focus/disabled → 动效。先修影响最多页面的共用样式，再修局部差异。
5. **每轮实际截图并看图。** 保存修改后截图，和对应参考图并排或局部叠加检查；记录最明显的 3–5 个差异、位置和下一步修正，继续实施、截图、比较。不能只读 CSS 或以“代码已完成”代替视觉核对。
6. **将排除项单独标记。** 独立成果入口、Google 登录、邮箱、回收站等应标为“功能边界内有意不复刻”，不计入必须修复的视觉差异。比较剩余区域的构图、密度、颜色和层级，不追求包含多余功能的整图像素一致。
7. **每轮做针对性回归。** 检查本轮涉及的现有交互、权限与能力关闭状态；按变更运行 typecheck、相关组件测试和 Playwright 用例。遵循仓库长任务等待规则，不反复短轮询；测试完成就继续推进。
8. **未达标继续迭代。** 仍有明显布局偏差、文字截断、控件遮挡、误导状态或功能回归时不结束。达到下面的验收条件再汇报；若缺少运行环境或真实接口，只能标记该部分待验，不得声称已经视觉收敛。

建议后续证据目录为 `artifacts/product-acceptance/visual-baseline/YYYY-MM-DD/iteration-NN/`，每轮保存 `before/`、`after/`、`comparison/` 与 `review.md`；这是后续保存约定，本次未生成这些验收结果。

每轮 review 至少记录：代码版本、参考图编号、环境与视口、场景/数据来源、差异及优先级、原图功能排除项、改动文件、测试结果、未解决问题、下一轮动作。可采用下表：

| 区域/场景 | 对应图 | 当前差异 | 性质 | 本轮改动与证据 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| 待填写 | A–E | 具体描述位置、比例或层级问题 | 视觉缺陷 / 功能回归 / 有意不复刻 | 截图路径与测试结果 | 修正或已验收 |

## 7. 视觉收敛与功能守门条件

- 五类参考场景均有对应的当前界面截图：登录/注册、AI、空间+文件详情、任务+详情、对话+HTML 成果详情；不得因没有独立成果页面而额外创建页面。
- 大结构、对齐、留白、文字层级、控件家族、选中态与参考图接近；所有有意偏离都能用代码边界或响应式需要解释。排除项不算未完成需求。
- 桌面及窄屏无意外整体横向溢出、按钮遮挡或不可读正文；关闭 Inspector/抽屉、键盘焦点、滚动和 Tabs 正常。
- 空、加载、失败、成功、能力未开通、无权限/对象不可用等已有状态没有被“漂亮的默认态”掩盖；无需伪造新状态来凑齐图片。
- 登录注册与 QQ 提示、发送与停止、资料使用范围、任务状态资格、版本选择、下载与保存副本等受改动影响的流程通过适当回归；请求、状态、授权语义未因视觉改造改变。
- 每轮改动有截图和差异记录；最终无未解决的明显视觉问题或功能回归。无法验证的项目明确标为待验证，不能用截图观感代替功能验证。
