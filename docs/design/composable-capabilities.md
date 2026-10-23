# 可组合能力：剩余验证与设计边界

现行契约已归入[能力边界](../architecture/capabilities.md)、[执行与恢复](../architecture/execution.md)、[Work](../architecture/work.md)、[Workspace](../architecture/workspace.md)和[Web 契约](../architecture/web-ui.md)。设计页只保留尚需确认的范围，不维护历史执行请求、费用授权或阶段报告。

## 当前完成边界

Main / Work / Run / Execution 及可组合能力共用持久编排。自然语言建立 generic_work、后台 Agent 自主检索与读取、不可变 Markdown 发布、typed evidence、Work 完成、Web inbox 接受及本人认证读取的后端路径已在独立环境验证。自动接续、Worker 中断恢复和权限控制另由隔离测试验证，不能混作真实模型证明。

Web Retriever v1 作为共享 Search/Fetch 模块保留；轻量查询由 Agent 使用 web_search/web_fetch，不强制走 research_report 或 HTML。Search 是闭环素材，专业研究质量与该闭环的通过分别判断。

## 待验证：独立目录中的浏览器旅程

在固定代码版本、私有配置及专用资源的独立目录执行浏览器提交、离页、返回、inbox 和正文下载。后端验证不证明现有部署或浏览器已更新。具体步骤和证据边界统一见[真实浏览器全链路](../development/testing.md#真实浏览器全链路)；本页不提供重复的执行脚本或启动授权。

普通 Markdown 目前通过认证文件下载阅读，尚无任务页内正文预览。是否增加直接阅读展示属于后续产品决定；HTML、Workspace 长期保存、QQ 外发、复杂修改和专业报告检查不作为简单文字任务的前置条件。

## 保留的工程约束

- 复用 Work、Execution、Temporal、Registry、预算和 Workspace，不另建任务运行时。
- 状态与 Outbox 同事务；持久成果、Work 完成、投递接受、用户已读与用户验收分别记账。
- 保留账号隔离、当前授权、冻结输入、revision/lease/fence、幂等、uncertain、不可变版本与 CAS。
- 真实检索、模型、浏览器、目标网络与渠道分别验证；夹具不冒充真实执行，不手改状态或补证据凑通过。
- 真实调用仍需按当前任务授权读取累计账本，冻结有限次数与费用上界；换目录、账户或批次不重置占额。文档中的旧授权不自动授权新调用。
- 证据、日志、截图、trace 和费用账本保存在仓库外或忽略目录，迁移与可执行测试 fixture 留在版本控制。

## 后续扩展

任意 Agent 递归、跨 Work DAG、动态插件、任意事件自动化、应用认证/数据库供应及长期托管不属于当前闭环。扩大并行、深度或外部副作用需单独设计与验证。专业 Research 的概念覆盖、事实准确性和 HTML 交互质量仍按对应能力验证，历史失败不会因通用文字闭环通过而改判。
