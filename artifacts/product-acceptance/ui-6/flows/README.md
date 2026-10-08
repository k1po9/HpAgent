# UI-6 操作证据

日期：2026-10-08（Asia/Shanghai）。真实 API/PG/Redis、Fake Executor 与合成网络响应分开记录；不使用生产账户或业务数据库。

- 成功：`artifact.spec.ts` 从完成回复显式生成 HTML，运行 iframe 内交互、下载 v1、保存源码副本、关闭回到原 Composer，刷新后打开已有对象。下载名使用真实标题与版本。
- 命令恢复：`ui-6-artifact.spec.ts` 在真实创建/修改 POST 已完成后丢弃响应，页面显示恢复入口；两个创建请求共享 key，两个修改请求共享 key；正文不加入聊天消息。v2 完成时 URL 仍指向 v1，用户从历史主动切版，关闭重开保留草稿，详情页深链可刷新。
- 保存恢复：新增源码保存用例在真实 PUT 完成后丢弃响应，继续时读取 ready metadata，不再 PUT；合成 409 明确拒绝后改名，复用 ready file_id，成功返回真实 node_id 并打开空间。检查初始化一次、PUT 一次、保存两次、无授权/accept-result。
- 来源与布局：合成 Work 无 Conversation，原引用固定 v1，查看 v2 有边界提示；返回原 Task 成果页再进入 v1。七种视口、长标题、reduced-motion、保存层 Esc 返回触发按钮；缺失版本不替换，构建失败/无版本/403 分别呈现。
- 并发与迟到：Vitest 延迟 Promise 验证同对象提交锁、A→B→A 查询 token、退出后的 POST/poll 丢弃、多个构建每版单 poller、消息查询限四个、旧账户请求不阻塞新账户查询。提交期间编辑及不确定重放保留原草稿 revision，旧成功不清新草稿。
- 后端边界：真实 Artifact admission/版本 key 重放；手工 v2 基于最近成功 v1，由新 Work/Run 产生，原 Work 引用仍 v1。对原 Work 接受 v2、过期 requirement revision、已通过 pause/resume 增长 control epoch 的旧证据均拒绝；原有效 v1 接受仍成功。未修改 Run 不可变事实或服务器协议。

各流程结果以证据目录最终日志为准，不以用例存在宣称已通过。截图不能证明真实软键盘、屏幕阅读器或真实模型生成质量。
