# 第二轮：消息阅读、紧凑输入栏与现有执行卡

2026-10-10，按用户“第二轮实施”完成指导方案 C / D / E。保留第一轮外壳和顶栏变更，仅调整现有组件的呈现与样式。

## 本轮变更

- 用户与助手使用现有图标库中的本地头像，正文、附件、执行块和成果列表沿同一正文列对齐。用户采用浅蓝气泡，助手采用文档排版；正文为 16px / 1.8，统一段落、标题、列表、行内代码、代码块和表格间距。长代码与表格在自身区域滚动。
- 输入、附件、已有文件、使用资料、执行模式和发送组成一条输入栏。中央区域宽度大于 740px 时空闲高度为 66px，较窄时采用输入行与工具行；多行输入自然增高，文本框到 160px 上限后内部滚动，删除内容后恢复高度。手机上限还受视口高度约束。
- 附件、真实授权资料、加载、失败、停止中和操作反馈仍可展示；数量多时所在状态行可以滚动。仅在输入栏中收起空资料文案和重复的“高级资料管理”按钮，管理弹层继续由现有顶栏“对话资料”入口打开；独立资料组件仍保留原入口。
- 已有文件选择器增加统一弹出面板，缓存列表与查询错误同时显示，桌面与手机按中央区域定位。选择、加载更多、移除和失败处理继续使用原回调。
- 现有执行状态重排为状态标题、已有摘要或错误、操作区。详情入口在左侧，原有停止或重试在右侧；历史记录仍是轻量详情入口。没有新增步骤、时间或进度数据。
- 将本轮涉及的消息、输入和执行规则放回 conversation.css / run.css，移除 base、visual 和 unification 中的重复覆盖。

IME、Enter / Shift+Enter、草稿、发送 / 停止 / 重试资格、资料授权和文件处理继续使用原实现。没有修改 API、业务 Store、SSE、权限、后端或生成模板；HTML 成果卡和 Inspector 内部结构仍留待第三轮。

## 实际看图与迭代

before、iteration1、final 均为实际 React 应用截图，没有注入截图专用 CSS 或搬移 DOM。final 和 check-fixture 截图使用最终生产构建；check-fixture 仅通过浏览器读取接口和 SSE 夹具呈现既有状态，数据没有接入产品入口或写入数据库。

1. 阅读目标图、第一轮代码和相关组件，保存 12 张同条件基线图。输入区原高度为 215px，手机约 218px。
2. 实现第一版后查看桌面四栏和手机截图。确认头像与正文同列，宽屏输入栏为 66px，窄中央区域自动分为两行。针对已有文件选择器，统一错误与缓存列表所在面板，并修正弹出层的左右定位，避免窄屏裁切。
3. 查看现有浏览器用例的运行中、资料授权和上传失败截图。发现空回复占位让执行卡向下偏移，改为隐藏空文本呈现；多行输入时工具停在垂直中部，改为底部对齐；增大执行 spinner，使状态更清楚。
4. 重新构建，检查完整执行状态、长代码与表格、21 组宽度条件和多行输入，再保存 12 张 final 图。人工查看最终 1672px、1440px、390px、空对话、空间和任务页面，以及运行恢复、无法安全重试、长 Markdown 和多行输入截图。

## 对照图

| 场景 | 第二轮修改前 | 最终 |
| --- | --- | --- |
| 1672 × 941 四栏 | [before](before-artifact-1672.png) | [final](final-artifact-1672.png) |
| 1440 × 900 四栏 | [before](before-artifact-1440.png) | [final](final-artifact-1440.png) |
| 1366 × 900 收起侧栏 | [before](before-artifact-1366.png) | [final](final-artifact-1366.png) |
| 1280 × 900 | [before](before-artifact-1280.png) | [final](final-artifact-1280.png) |
| 1024 × 900 模态 Inspector | [before](before-artifact-1024.png) | [final](final-artifact-1024.png) |
| 390 × 844 AI 页面 | [before](before-ai-390.png) | [final](final-ai-390.png) |
| 390 × 844 对话抽屉 | [before](before-sidebar-390.png) | [final](final-sidebar-390.png) |
| 空间 | [before](before-workspace-1672.png) | [final](final-workspace-1672.png) |
| 任务 | [before](before-tasks-1672.png) | [final](final-tasks-1672.png) |
| 新对话 | [before](before-new-ai-1672.png) | [final](final-new-ai-1672.png) |

[目标图](../ui-target-20261010/target.png)、[第一版四栏](iteration1-artifact-1672.png)、[多行工具对齐修正前](review-multiline-before.png)、[多行最终](check-multiline-capped.png)、[两行输入](check-multiline-two.png)。

现有浏览器用例产生的 [执行中](e2e-ui2/streaming-desktop.png)、[资料与反馈](e2e-ui2/ai-390x844.png)、[附件失败](e2e-ui2/attachment-failure.png) 是最后一次细节修正前的迭代依据。最终构建的读取夹具截图：[运行与连接恢复](check-fixture-run-running-mobile.png)、[无法安全重试的失败](check-fixture-run-failed-unsafe-mobile.png)、[安全失败可重试](check-fixture-run-failed-mobile.png)、[取消中](check-fixture-run-cancelling-mobile.png)、[文件列表和错误](check-fixture-files-error.png)、[320px 文件面板](check-fixture-files-320.png)、[长 Markdown](check-fixture-wide-markdown-mobile.png)。

## 数据与验证边界

同屏对照复用第一轮隔离数据库 hpagent_visual_20261009、Redis DB 14、测试文件目录和同一条视觉检查对话。使用实际 API 和现有测试执行器，没有运行正式模型或 QQ 通信，也没有实际执行计划任务。消息和 HTML 内容未替换；浏览器改名检查恢复了原标题，但其 updated_at 和日期分组产生真实变化。新对话截图只打开空白界面。

现有 UI2 / UI3 浏览器测试在同一隔离环境使用独立测试账户；包含实际 API 交互和明确标注的网络夹具。其源码只复制到忽略的临时目录以重定向截图，不修改测试行为。状态补充检查只拦截浏览器读取与 SSE；不将模拟状态写入服务，也不以夹具证明正式执行器能力。

## 验证结果

- 6 个相关前端测试文件、49 项测试通过：消息输入、资料、执行状态、详情、App 和 Surface。最后一次细节修正后复跑其中 3 文件、20 项，仍通过；20 项是原 49 项的子集。
- 14 项现有 UI2 / UI3 Playwright 测试通过：IME 和发送、草稿与幂等提交、对话切换、资料、附件失败、历史锚点、响应式和焦点、执行 Inspector / Tabs / 输出 / 审批与权限可见性。
- 最终生产构建检查 21 组宽度 / Inspector 组合（19 个不同宽度），包括中央区域 741 / 740px、外壳 1408 / 1407px 和 988 / 987px 边界，最小 320px。输入控制没有越出输入栏，页面没有横向溢出，跨页面与尺寸保留输入 DOM 和草稿。
- 多行输入增长、上限和缩回；7 类执行状态的操作可见性；窄屏文件列表、缓存查询错误、长代码 / 表格局部滚动均通过。共记录 33 组几何状态，[结果](visual-regression.json)无页面运行错误。
- 第一轮外壳回归再检查 16 个宽度及扩大阅读边界；标题编辑、真实改名并恢复、资料与查询入口、Inspector / 抽屉焦点、iframe 身份及 reduced-motion 通过，[结果](regression.json)。
- typecheck、生产 build、变更文件 ESLint / Prettier、git diff --check 通过。构建仍有已有的大 bundle 提示，本轮没有扩大到代码拆分。

空闲输入区实测：1672px 四栏 215 → 66px；1440px 四栏中央宽 636px，215 → 118px（两行）；390px 约 218 → 114px（两行）。多行输入从 44px 增高到 160px 后内部滚动，外壳随之增高到 182px。尺寸见[基线](before-geometry.json)、[最终](final-geometry.json)与[交互状态](visual-regression.json)。检查日志保存在本目录。
