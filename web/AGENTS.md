# Web 修改指南

继承[根指南](../AGENTS.md)。产品契约见
[Web 架构](../docs/architecture/web-ui.md)，按任务入口见[代码地图](../docs/reference/repository-layout.md)。

## 状态和交互

- App 在身份 checking/error/signedOut 时不挂载业务内容。账户切换同步 dispose 请求、feed、轮询、Blob、草稿和 Shell；响应同时核对账户、对象与查询 generation。
- `store/shell.ts`
  管理路由、导航 token、唯一 Inspector 和返回栈。组件发显式意图，领域 store 不根据缓存更新自动跳页。dirty
  guard 获准后再改变路由。
- store 保存权威查询、分页及命令意图；UI 状态与服务端领域状态分开。不要从动画、Trace 或 SSE 推断完成。
- 写命令固定 payload/key；未知响应先对账，再重放原意图。仅匹配草稿版本成功后清空；Work 写入保持 If-Match，CAS 冲突保留输入。
- SSE 先 snapshot 后 delta，gap/断线恢复依赖权威查询。Chat 与 Work
  feed 独立，隐藏/卸载/退出要清理定时链与订阅。
- Run/Artifact/File/Task 保持同一 Inspector 对象身份；调整断点/宽度不重建 DOM、iframe、草稿或请求。Esc 仅关闭最上层，焦点/inert/模态边界统一走 Surface。
- 上传、ready、保存、授权是独立阶段，失败只重试未完成阶段。所有者可见不等于 Agent 获授权，关联 Conversation 不给 Work 授权。
- Artifact 版本和生产来源不可改写；手选版本不被迟到初始化抢占；accepted 不等于已读，单次 Run 成功不等于 Work 完成。
- 保留单 Composer、真实消息 DOM、IME、附件、阅读锚点与底部跟随。真实数据等待与 Mercury 抵达分别门控；动画不改变领域状态。
- 视觉使用语义 tokens 与现有 Mercury 时间轴；减动、窄屏、隐藏、失联、长帧与异常要收敛并清理资源，空闲不维持 RAF。
- Run 诊断仅展示可证实的调用、用量、耗时与来源；缺失/异常标未知，不从 Trace 数量推算请求数、不猜 token 拆分或供应商延迟。

## 控件与布局

- 普通控件使用 `components/ui/`；Radix 的基础控件导入限于该目录，由现有 ESLint 约束。Button 明确
  `type`，主操作用显式 variant，原生表单事件、ref/id/aria/data 属性透传。
- 基础控件和 InspectorTabs 的样式归 CSS Modules，使用 `--hp-*`
  语义 tokens；Field 的 label/description/error 与生成 ID 关联到内部原生控件，已有 aria-describedby 追加保留。有说明或字段错误时使用显式 label，固定输入 id 时以 htmlFor 对齐；API/表单级错误留在表单层，不能据此给全部字段设置 aria-invalid。领域 CSS 保留宽度、排列、对象状态与具名专用交互（如 Composer、Inspector 扩宽、标题编辑和入口确认态）；全局低优先级焦点规则仅作回退，页签使用内收焦点环以适应紧凑布局。迁移时删旧通用外观规则，不叠加全局 button/input 覆盖。hidden/inert、接收门控、减动和拖宽期间的行为保护继续保留。
- Shell 尺寸计算复用
  `components/shell/layout.ts`；视口决定模式，页面内部按容器排布。保留现有任务看板与聊天容器查询。
- 第三方浮层通过项目接口适配 Surface。Portal 在所属原生 dialog 内，Esc 只关闭前景，保持主题、inert 和焦点返回；不能用 z-index 绕过 top
  layer。

## 检查

在根目录运行
`make ci-web`：lint、src/E2E/harness/config 类型检查、单元和生产构建。已有服务使用 web/dist 时，进入 web 后用
`npm run build -- --outDir /tmp/hpagent-web-check` 写独立目录。生产构建使用
`tsconfig.build.json`，只检查产品源码；测试源码及仓库共享 fixture 的类型检查由 `npm run typecheck`
保留，不能以生产构建代替完整检查。

浏览器按[测试指南](../docs/development/testing.md)选择合同夹具或独立 API 环境。Playwright `--list`
只证明用例可发现；不能当作执行通过。保持对迟到响应、账户切换、幂等意图、dirty
guard、焦点、减动和资源释放的相关回归。前端静态检查不证明真实模型或 QQ 链路。
