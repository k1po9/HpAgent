# HpAgent 前端视觉迭代交付（2026-10-09）

当前分支 `main`，基线 `7e77631`。未切换分支、未提交，保留用户原有 `docs/README.md` 修改与设计资料。实际进行了三轮修改、浏览器操作、截图与原图人工核对。最终截图由生产构建生成。

## 对照图片

| 页面 | 原始目标 | 修改前 | 修改后 |
| --- | --- | --- | --- |
| 登录 | [目标图](/home/hp/workspace/HpAgent_web/docs/design/assets/frontend-visual-guide/auth-pdf-reference.png) | [原版](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/before-auth.png) | [最终生产截图](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/after-auth.png) |
| 注册 | [目标图](/home/hp/workspace/HpAgent_web/docs/design/assets/frontend-visual-guide/auth-pdf-reference.png) | [原版](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/before-register.png) | [最终生产截图](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/after-register.png) |
| AI 对话 | [目标图](/home/hp/workspace/HpAgent_web/docs/design/assets/frontend-visual-guide/ai-pdf-reference.png) | [原版](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/before-ai.png) | [最终生产截图](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/after-ai.png) |
| 空间 + 文件 Inspector | [目标图](/home/hp/workspace/HpAgent_web/docs/design/assets/frontend-visual-guide/workspace-pdf-reference.png) | [原版](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/before-workspace.png) | [最终生产截图](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/after-workspace.png) |
| 任务 + Inspector | [目标图](/home/hp/workspace/HpAgent_web/docs/design/assets/frontend-visual-guide/tasks-pdf-reference.png) | [原版](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/before-tasks.png) | [最终生产截图](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/after-tasks.png) |
| AI + HTML 成果 | [目标图](/home/hp/workspace/HpAgent_web/docs/design/assets/frontend-visual-guide/artifact-pdf-reference.png) | [原版](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/before-artifact.png) | [最终生产截图](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/after-artifact.png) |

移动端：[认证/注册](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/after-auth-mobile.png)、[AI](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/after-ai-mobile.png)、[空间列表](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/after-workspace-mobile.png)、[文件详情](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/after-file-mobile.png)、[任务列表](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/after-tasks-list-mobile.png)、[任务详情](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/after-tasks-mobile.png)、[HTML 成果](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/after-artifact-mobile.png)。另有 [1440×900](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/after-tasks-1440.png) 与 [1024×768](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/after-tasks-1024.png)。

认证目标是四种状态的拼图，只比较单个状态的品牌与表单区域。原图未拉伸、未重绘。`before-*` 的业务页面最终用原始 HEAD 源码的临时副本、同一隔离服务和测试记录重新拍摄，保证比较的是样式变化；没有回放或注入 Store。初始化时的认证截图也已保存。`round1-*`、`round2-*` 保留中间迭代证据。

## 变化与保护

- 共享主题改为蓝色/Slate，语义背景、边框、文字和选中色显式生效；主画布白色，侧栏极浅灰。明确中文无衬线字体回退。
- 登录/注册为左侧品牌、右侧表单；窄屏上下排列。字段、提交事件、密码规则、错误、注册自动登录失败回退和 QQ 提示未改。
- 对话侧栏扩大新建入口，统一线性图标与选中底色。主标题使用现有对话标题；原重命名入口仍可达。
- 用户消息浅蓝，助手消息用文档排版；Composer 为完整轻边框表面。保留历史定位、附件与资料语义、发送/停止/深度开关、错误和恢复入口。历史 Run 的详情入口不伪造状态卡。
- 空间合并路径与工具的排布、压缩搜索高度，文件名变为轻量入口；用现有 Inspector ID 显示选中行。新建目录仍是原表单，搜索仍是全空间文件搜索。文件元数据、版本、使用范围、权限与预览限制保留。
- 任务分类沿原四桶，任务行和选中态更清楚；新建按钮为主操作，同步/计数/刷新/收件箱/诊断保留。没有改变任务资格或预算解释。
- HTML 成果卡片明确区分标题与版本；保留原无障碍名称。Inspector Tabs、预览外框和修改输入统一。下载 HTML、源码副本、选中版本和任务原引用语义保留。
- Shell 的 64 / 256 / 420 / 520px Token、JS 宽度公式及 599/959/1279 等断点数值均未改。Surface 仅替换关闭图标；挂载、焦点和模态逻辑未改。
- 未改 Store、API、SSE、业务控制器、assistant-ui 数据源、后端或迁移；没有新增整体 UI 框架、运行时依赖或生产测试数据。

## 三轮看图记录

| 轮次 | 看图发现 | 定向修正与回归 |
| --- | --- | --- |
| 修改前 → 第一轮 | 画布偏灰、用户气泡浓蓝、助手回复窄卡片、Composer 缺少完整轮廓；中文回退宋体，文件名像按钮 | 改主题与字体、对话阅读宽度、输入外壳、表格和轻选中态。实际操作注册、发送、上传、文件预览、任务创建、HTML 生成；看五个区域桌面/移动截图，检查导航和草稿 |
| 第一轮 → 第二轮 | 空间工具区纵向过长、侧栏新建入口弱、成果与元数据不分层、移动端发送按钮落到左侧 | 压缩空间工具、扩大侧栏现有新建按钮、成果卡片分层、移动工具栏收紧。组件测试发现拆分文字后的无障碍名称变化，恢复原名称后通过；看第二轮桌面/移动截图，验证草稿 |
| 第二轮 → 第三轮 | Tab 底边弯曲、历史执行块空框过重、主按钮 hover 可能失去对比、附件焦点不明显 | 直线 Tab、历史详情轻量链接、主按钮蓝色 hover、附件键盘焦点；末次状态检查将 CTA 蓝色加深，白字默认/hover 对比度为 4.78:1 / 5.43:1；生产构建截图再看图，与原图和原版对照，做真实 API、断点、禁用/焦点与 reduced-motion 检查 |

## 有意不复刻

独立“成果”一级导航、Google/邮箱认证、密码显隐、模型菜单、推荐任务卡、在线头像/状态、回收站、未实现的 PDF/图片/表格/Figma 富预览、排序/更多业务菜单、每周调度、预算预测、固定假进度、Markdown Artifact 系统、分享/发布入口。没有假按钮占位、示例 Store 或为了图形生成的预算/成功数据。

代码必需但图片没展示的高级资料管理、执行编号查询、错误恢复、任务同步/草稿/权限提示、新建目录表单、预算预留和任务操作资格保留。

## 验证

- `npm run typecheck`、生产 `npm run build` 通过。构建仍有大于 500 kB 的 chunk 提示，本轮没有扩展为打包优化任务。
- 10 个相关测试文件，87 项全部通过：LoginForm、Surface、shell、HpThread UI2、ArtifactMessageItems、TaskSelfReview、TaskIntegration、WorkspaceScale、FileInspector、UI4SelfReview。
- 新增 Vite-only 配置的 2 项浏览器检查通过；原 `R2: native modal` 定向检查 1 项通过。未运行全仓 CI、大规模 E2E 或迁移。
- 变更文件 ESLint、Prettier 与 `git diff --check` 通过。
- [几何记录](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/geometry.json)：320–1536px 的 15 个宽度无页面级横向溢出，Inspector 模态按原阈值切换；textarea DOM 和草稿未因宽度变化重建，移动侧栏 ESC 可关闭。
- [认证记录](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/auth-regression.json)：错误凭据、密码不一致、实际注册/自动登录、QQ 明确跳过、退出、重新登录及新账户会话隔离通过。另用请求中断检查连接错误后的重试，未伪造响应体。
- [真实 API 记录](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/real-regression.json)：搜索/文本预览/版本与范围 Tabs、HTML sandbox 及交互、下载、新版本 v2、保存源码文本副本、任务暂停/恢复、编辑草稿继续/放弃、发送/在线 SSE/停止/再次发送通过。
- [状态记录](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/states.json)：发送启用/禁用、主按钮 hover 对比、焦点截图和 reduced-motion 检查。
- 生产 JS/HTML 未发现工具名称、Playwright 或测试账户/测试密码字符串；工具没有接入生产入口。生产截图沿用真实应用 API。

## 环境、工具与限制

隔离数据库 `hpagent_visual_20261009` 从已有测试库复制，没有迁移。新账户通过真实表单注册；公开文件真实上传，任务/成果通过真实 API 创建，文件根 `/tmp/hpagent-visual-20261009-files`、Redis DB 14、API 8196。没有访问生产账号、日常浏览器或当前业务库，也没有在应用中替换 API。

Run 与 Artifact 沿用仓库隔离测试执行器，形成真实测试数据库记录，但不证明真实模型、Temporal 执行器或 QQ 投递。原图中的内容、条目数和进度不能作为此环境数据目标。

使用现有 Playwright 1.62.1 与安装的 Chromium。Impeccable 4.1.0 下载失败，npm registry 连接超时，未安装/执行引擎或 hooks；视觉评审由原图人工核对完成。React Grab 未接入；使用实际 DOM 与源码定位。没有安装独立 CLI、Motion、VibeCurb 或 MCP；没有修改任何 lockfile。

尚未验证：真实模型/QQ/外部投递、完整断线和未知提交幂等恢复、真实深度能力运行、所有任务验收/预算/权限状态、全量附件格式及生产多账户端到端隔离。相关现有组件/状态测试通过不等同这些业务全部验收。

剩余视觉差异：功能保留造成工具/提示比目标更多；任务按当前桶显示，不混排四桶；真实 HTML iframe 的内部排版由成果本身决定，不注入目标 Markdown；中文字体依赖操作系统，本机 WSL 的 WenQuanYi 回退与原图不同。没有机械复制头像、示例内容或星尘粒子。

原版和新版在 `serviceWorkers: block` 的浏览器上下文中都出现一次 sandbox 内访问 `navigator.serviceWorker` 的错误，来源尚未进一步定位；交互预览通过，没有放宽 `allow-same-origin`。不声称控制台完全无错误。

## 复验入口

[独立 Playwright 配置](/home/hp/workspace/HpAgent_web/web/playwright.visual.config.ts)、[2 项检查](/home/hp/workspace/HpAgent_web/web/e2e/visual/frontend-visual.spec.ts)。该配置只启动 Vite，不启动后端或迁移。事先准备专用测试 API 与有公开记录的账户，显式提供 `HPAGENT_VISUAL_ISOLATED=1`、`HPAGENT_VISUAL_USER`、`HPAGENT_VISUAL_PASSWORD`；没有这些值则跳过，不创建 mock 数据。示例命令：

```bash
cd /home/hp/workspace/HpAgent_web/web
# 在环境中设置上述隔离 API 与凭据；不要连接共享业务库。
npm run test:e2e -- --config=playwright.visual.config.ts
```

截图配置默认应用端口 5290 / API 8196；后端须匹配这个同源 Origin。测试快照默认不保留 trace 或 storageState。浏览器探索会话和启动脚本位于被忽略的 `.data/visual-20261009`，不会进入生产包。

## 涉及文件

- [web/src/components/ConversationSidebar.tsx](/home/hp/workspace/HpAgent_web/web/src/components/ConversationSidebar.tsx)
- [web/src/components/LoginForm.tsx](/home/hp/workspace/HpAgent_web/web/src/components/LoginForm.tsx)
- [web/src/components/artifact/ArtifactMessageItems.tsx](/home/hp/workspace/HpAgent_web/web/src/components/artifact/ArtifactMessageItems.tsx)
- [web/src/components/shell/AppShell.tsx](/home/hp/workspace/HpAgent_web/web/src/components/shell/AppShell.tsx)
- [web/src/components/shell/Surface.tsx](/home/hp/workspace/HpAgent_web/web/src/components/shell/Surface.tsx)
- [web/src/components/tasks/TaskScreen.tsx](/home/hp/workspace/HpAgent_web/web/src/components/tasks/TaskScreen.tsx)
- [web/src/components/tasks/TaskSidebar.tsx](/home/hp/workspace/HpAgent_web/web/src/components/tasks/TaskSidebar.tsx)
- [web/src/components/workspace/FileList.tsx](/home/hp/workspace/HpAgent_web/web/src/components/workspace/FileList.tsx)
- [web/src/components/workspace/WorkspaceScreen.tsx](/home/hp/workspace/HpAgent_web/web/src/components/workspace/WorkspaceScreen.tsx)
- [web/src/components/workspace/WorkspaceSidebar.tsx](/home/hp/workspace/HpAgent_web/web/src/components/workspace/WorkspaceSidebar.tsx)
- [web/src/main.tsx](/home/hp/workspace/HpAgent_web/web/src/main.tsx)
- [web/src/styles.css](/home/hp/workspace/HpAgent_web/web/src/styles.css)
- [web/src/styles/tokens.css](/home/hp/workspace/HpAgent_web/web/src/styles/tokens.css)
- [web/src/styles/visual.css](/home/hp/workspace/HpAgent_web/web/src/styles/visual.css)
- [web/playwright.visual.config.ts](/home/hp/workspace/HpAgent_web/web/playwright.visual.config.ts)
- [web/e2e/visual/frontend-visual.spec.ts](/home/hp/workspace/HpAgent_web/web/e2e/visual/frontend-visual.spec.ts)

[完整环境清单](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/visual-2026-10-09/manifest.json)。
