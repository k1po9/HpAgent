# 参考图布局迁移 · 2026-10-12

用户先要求保存颜色基线，再授权按图片迁移布局与视觉层次。本轮实际调整空间比例和组件构成，继续使用已确定的 Paper Flame；图片中的深色实验主题、演示任务、模拟进度、主题切换和收纳控制不作为产品功能。

基线代码/证据已提交：4191251 / 18d3818。当前分支：codex/reference-layout，从 18d3818 开始；旧基线仍可独立回退。新代码提交以本报告所在的 Git commit 为准。

[用户参考图](reference.png) · [本轮 Git diff](code.diff) · [几何与对比度记录](comparison.json)。

## 实际迁移

- **四区重新分配**：Rail 104→84、Context 280→252、Inspector 420→344（扩大 520→460），四区有 Inspector 的 1680px 页面主 Canvas 876→1000。保持四区职责与 600px Canvas 最小宽度，CSS Token 仍被 useShellWidth 共用，侧栏按实际容纳能力进抽屉。
- **居中阅读**：消息与 Composer 共用 760px 内容列；原本横跨整块画布的 Composer 改为独立双行输入，真实高度 66→120px。输入文本在上，附件/资料/模式/发送在下；输入、IME、草稿及发送/停止逻辑保持。
- **消息层次**：用户气泡靠右，助手身份与真实时间置于卡片外，正文使用白色阅读卡；代码、表格、引用、附件、Run 和 Artifact 的真实 DOM 保留。时间来自已有 created_at 映射后的 createdAt，未造最后消息预览或进度。
- **上下文侧栏**：标题旁的加号新建入口、图标/标题/更新时间、分组分隔与当前条目细线；底部显示真实已加载的当前对话。筛选、分页、选择与 data-conversation-id 不变。
- **Rail / Header**：已有 Lucide Sparkles 构成品牌图形，紧凑的导航与账户锚点；主 Header 增加页面职能与标题两层排版，原标题编辑与业务操作保持。
- **Inspector**：Run 与 Task 概览变为真实信息卡片，统一标题、Tabs、按钮与间距；Artifact 保持 iframe、中段滚动及底部保存/下载；File 保持资料/权限/版本能力。
- **Workspace / Tasks**：在画布中形成有上限的内容列，文件表和任务卡调整行高、留白、操作分隔，保留列表、树、筛选与权限/任务操作。
- **响应式**：780px 保留 56px Rail；390px 继续底部导航和全屏 Surface，Composer 输入为 16px；紧凑 Inspector、扩大阅读与抽屉保持原生焦点契约。

新组合样式集中在 `web/src/styles/workbench.css`，位于 visual.css 之后、entry.css 之前。复用 Paper Flame Token，无颜色硬编码、无新增动画控制器或业务 Store/API 改动。为 Artifact 明确保留 body 的 padding:0，避免 Inspector 的通用内边距重复叠加。

## 真实页面前后

before 是已提交的 Paper Flame 颜色基线；after 是本轮生产 dist 的 React 页。复用同一隔离 API、同账户、同记录、同视口、同页签；没有临时替换 CSS、移 DOM、注入 Store 或用参考图重建业务数据。参考图只提供比例与审美方向。

| 页面 · 1680×1000   | 颜色基线                            | 新布局                            |
| ------------------ | ----------------------------------- | --------------------------------- |
| AI / Conversation  | [before](before-ai-1680.png)        | [after](after-ai-1680.png)        |
| 新对话             | [before](before-new_ai-1680.png)    | [after](after-new_ai-1680.png)    |
| AI + Artifact      | [before](before-artifact-1680.png)  | [after](after-artifact-1680.png)  |
| AI + Run Inspector | [before](before-run-1680.png)       | [after](after-run-1680.png)       |
| Workspace          | [before](before-workspace-1680.png) | [after](after-workspace-1680.png) |
| File Inspector     | [before](before-file-1680.png)      | [after](after-file-1680.png)      |
| Tasks              | [before](before-tasks-1680.png)     | [after](after-tasks-1680.png)     |
| Task Inspector     | [before](before-task-1680.png)      | [after](after-task-1680.png)      |

39 对截图另含 1280×1000 / 780×1000 / 390×844，以及登录/注册验证、资料弹窗与草稿焦点；另有最终 Rail 焦点图。[手机 AI](after-ai-390.png)、[手机文件](after-file-390.png)、[手机任务](after-task-390.png)。

人工读图修正了旧 display:flex 覆盖侧栏新网格、Artifact 双层 padding，以及孤立的 Run 入口背景；最终确认桌面中心列、窄屏输入、Inspector 读写操作及长标题。用户生成 HTML 的 iframe 内排版仍来自其原内容，不冒充本轮设计。

## 验证

- **typecheck / 变更范围 ESLint / Prettier / git diff --check：通过**；不运行完整 CI。[静态检查](static.log)。
- **14 文件、106 单测通过**：[日志](unit.log)。覆盖 App、恢复、HpThread、Conversation、Run、Surface、Inspector、Task、Workspace、Artifact。新建入口改为加号后，App 测试改用同一可访问名称“新建对话”查找按钮，不改变原行为断言。
- **14 浏览器用例通过**：[日志](e2e.log)。覆盖 IME、按提交创建对话、草稿、标题/资料/焦点、历史 prepend 锚点与 streaming、不就绪附件/字号；四类 Inspector/iframe/查询次数跨 13 尺寸与边界、嵌套保存、任务草稿 guard、键盘 Tabs、扩大阅读；登录实际粒子像素、清理和 reduced-motion。harness 两项使用 Vite 开发页，未声称其在 dist 中存在。
- **生产构建通过**：[日志](build.log)；保留已有 >500kB bundle 提示。
- **最终生产截图脚本通过**：[日志](after.log)，40 张截图无页面运行错误、无页面横向溢出。before 的 39 张单独保存：[基线数据](before-geometry.json)、[最终数据](after-geometry.json)。
- **已采样对比度通过**：1515 个可见文字组合（包括跨视口重复），低于相应 4.5/3:1 的组合为 0，遗留饱和蓝文字为 0。保留输入、按钮和深色 Rail 焦点；disabled/屏外/用户 iframe 内容不计入扫描，不宣称全站 WCAG 认证。

环境仍为独立测试 API 8196、数据库 hpagent_phase1_20261012、Redis 15 和本机 headless Chrome；消息/HTML 为现有 Fake executor 内容，属于真实 React/API 验收数据，不是正式模型回复。未测正式模型、QQ、计划任务实际执行、Firefox/Safari、物理移动设备及 Mercury 性能。没有修改日常业务账户或生产数据库。

## 修改文件

- web/src/styles/tokens.css、styles.css、styles/workbench.css：布局 Token 与组合样式。
- web/src/components/shell/AppShell.tsx：品牌图形和标题层次。
- web/src/components/ConversationSidebar.tsx：现有新建按钮、真实条目与当前对话提示。
- web/src/adapters/assistant-ui/HpThread.tsx：消息身份/真实时间与新对话空态。
- web/src/components/run/RunInspector.tsx、tasks/TaskInspector.tsx：概览卡片包装。
- web/src/App.test.tsx：按可访问名称查找新建入口。
- web/scripts/phase1-visual.mjs：允许独立证据目录，原 Phase 1 证据不覆盖。
- web/playwright.layout.config.ts：受影响浏览器用例配置。
- 本目录：参考图、实际前后截图、diff、几何与检查日志。

## 边界与回退

本轮按用户新的布局授权推进；不启动 Mercury 正式迁入、不新增收纳按钮或 folded Store，不复制 P09。原 API、导航、guard、消息、Run、Workspace、Task、Artifact 语义和 iframe sandbox 保持。未新增假头像、百分比、阶段或页面路由。

可回退本轮 Git commit 恢复 18d3818 颜色基线；无需数据库迁移。截图和浏览器记录保存于本目录，临时测试服务可停止而不影响源码。
