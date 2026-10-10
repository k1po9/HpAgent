# 第一轮：工作台外壳、侧栏与唯一顶栏

2026-10-10，按用户“开始第一轮”实施。只调整现有页面的布局、样式和组件位置，不实现目标图里尚不存在的功能。

## 本轮变更

- 桌面主导航宽 104px，显示 HpAgent 字标，保留 AI、空间、任务三个入口；调整导航项尺寸和选中态。
- 对话侧栏宽 280px；新建按钮独占一行，筛选框带图标，分组、标题和更新时间分层。更新时间直接使用 API 的 updated_at。
- 将对话标题及原改名组件移入 Shell 顶栏，消除原来的第二层标题栏。长标题截断，原保存、取消及忙碌状态继续使用；切换 AI / 空间 / 任务时未保存标题与消息草稿保持挂载。
- 宽度和折叠计算共同读取 CSS tokens；普通 Inspector 420px，扩大后 520px。1408 / 1407px 和 1508 / 1507px 是桌面四栏折叠边界。Inspector 在 1280px 以下继续采用原模态机制，因此此时不占据主画布布局宽度；普通三栏所需宽度为 988px。
- 手机标题与现有操作分行，保持底部导航和原抽屉。整理本轮涉及的重复 CSS 覆盖。

消息排版、头像、输入区紧凑化、执行卡、成果卡及 Inspector 内部内容排布仍按第二、三轮进行。本轮没有修改 API、业务 Store、SSE、后端、权限、生成模板或 HTML iframe 文档。

## 实际看图与修正

所有 before / iteration1 / final 截图均由实际 React 应用渲染；没有为截图注入临时 CSS、改写 DOM 或替换页面内容。final 和 check 截图使用本轮生产构建。与指导方案目录内的早期布局试验区分。

1. 阅读目标图和现有代码，启动隔离 API 与实际前端，保存 12 张基线图。
2. 第一版源码实现后，查看桌面四栏、1440px、手机及抽屉截图。桌面宽度和侧栏节奏符合本轮方向；手机标题与操作混在两行，按截图将操作组合为独立一行。
3. 查看长标题和展开执行查询截图。标题截断后改名按钮仍可达；执行查询展开时标题与操作拆散，调整桌面排布，让标题和已有按钮保留在同一行，查询表单独占下一行。
4. 重新构建并保存 final 截图，人工查看桌面、手机、抽屉、空间和任务页面。

## 对照图

| 场景 | 修改前 | 最终 |
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

[原目标图](../ui-target-20261010/target.png)、[第一版手机](iteration1-ai-390.png)、[展开查询修正前](review-run-lookup-before.png)、[展开查询最终](check-run-lookup.png)、[长标题手机](check-long-title-390.png)、[键盘焦点手机](check-title-focus-mobile.png)。

## 数据与检查边界

使用既有隔离测试数据库 hpagent_visual_20261009、Redis DB 14 和测试文件目录，复用既有测试账户、对话、HTML 成果、空间文件及任务。API 是实际服务，执行内容来自既有测试执行器；这些内容不是正式模型回复，也不是产品新增功能。

基线和最终使用同一条对话、相同消息记录及截图滚动位置。浏览器回归通过实际 PATCH 检查长标题，然后恢复原标题，所以对话更新时间和日期分组发生真实变化；没有修改消息正文或 HTML 内容。新对话截图只打开现有空白界面，没有发送消息创建记录。

本轮检查没有运行正式模型、QQ 通信、任务实际执行、上传发送全流程；没有对这些能力作验收结论。

## 验证

- 6 个相关前端测试文件，45 项测试通过；增加了标题草稿随页面切换保持挂载的断言。
- 7 项现有入口 Playwright 测试通过，使用契约夹具；包括标题焦点、手机深链接、转场和 reduced-motion。
- 实际隔离 API 浏览器检查：登录后标题焦点；单一标题；页面切换保留改名与输入草稿；真实改名并还原；资料弹层和执行查询；Inspector / 抽屉打开、关闭和焦点返回；跨尺寸保留修改草稿、iframe 与输入框 DOM 身份；手机操作及 reduced-motion。
- 16 个检查宽度：1672、1440、1408、1407、1366、1280、1279、1024、988、987、960、959、600、599、390、320px；另检查扩大阅读的 1508 / 1507 / 1440px。页面无横向溢出，所检查顶栏控件没有越出顶栏水平范围。
- typecheck、生产 build、变更文件 ESLint / Prettier、git diff --check 通过。生产构建仍提示已有的大 bundle 警告。

截图尺寸：[基线](before-geometry.json)、[最终](final-geometry.json)；浏览器回归：[结果](regression.json)。检查日志保存在本目录。

1672px 下实测：导航 104px、侧栏 280px、中央区域 868px、Inspector 420px；普通顶栏 72px。1440px 下中央区域 636px，仍可保留四栏。
