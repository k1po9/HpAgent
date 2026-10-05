# 关键时序

## Web / QQ 聊天

1. Surface 标准化身份与输入，统一命令边界提交 Message、chat Run、幂等事实及 `start_run` Outbox。
2. Dispatcher 启动 `AgentLifecycleWorkflow`，读取冻结 `chat_agent` 策略并启动 Agent 子 Workflow。
3. Execution Activity 组装 Chat Context，通过 ReAct / Plan-and-Execute 调用模型和工具。
4. 通用服务提交 Run 终态、聊天 Message 投影与通知；Terminal Publisher 唤醒 Web SSE。
5. QQ Adapter 消费统一 notifications / deliveries，记录发送回执；投递失败不重新执行 Run。

## Work 接受、调度与执行

1. API 或 Main 工具提交 Work、不可变 requirement、checkpoint、continuation、预算和 wakeup。
2. PostgreSQL due loop 安装 schedule version，按时区计算 occurrence / catch-up；重复、旧版本或禁用回调不 admission。
3. admission 在 Work 锁下检查协调权、revision / epoch、预算和容量，创建无聊天 Message 的 Run 与 root Execution，冻结需求、checkpoint、资源、输入引用和策略。
4. `start_run` 进入同一 Lifecycle：reminder 通知入队，research_report 固定研究图，artifact_html 成果 Activity，work_agent 通用 Agent。
5. operation / receipt 与输出提交；Run 成功由 `WorkCompletionPolicy` 判断证据、required save、渠道接受及用户验收。
6. ready continuation 事务性创建下一 wakeup；其他等待状态持久保存，不忙循环执行。

## 模型请求与工具往返

1. 检查 Account entitlement / endpoint tier。
2. Provider serializer v2 校验 transcript 和 tool-call/result 配对，OpenAI 使用嵌套 function 和 JSON 字符串 arguments；Anthropic 使用独立 system、tool_use / tool_result。
3. 冻结 Model Input Snapshot；单独事务原子预留 Account/day → Work（若有）→ Run budget。
4. 分发原冻结请求体并记录尝试身份 / HTTP 类别；结算实际或估算用量，发送前失败释放预留。
5. 工具结果作为明确的 tool observation 进入下一轮，不猜测普通 user 文本。Snapshot 存在不等于请求已送达。

## Wait / 控制 / 恢复

1. Activity 持久记录 Approval / wait，Workflow 等待 Signal，segment 释放租约。
2. 用户决定提交后发送 Signal，恢复 segment 重新获 attempt lease / fence。
3. Work revise / pause / stop 提交 control epoch 与取消意图；旧生产回执只保留历史事实。
4. 有未知外部结果时保留 pausing / stopping blocker，核对与决议后收敛。

## 长期文件使用与版本更新

1. 所有者上传 ready 文件并保存 entry；“供当前对话使用”另外提交 Conversation grant，失败可补授权而不重传。
2. Work 或 Conversation 授权 entry / 递归目录。新 Run 固定候选；Context 展示第一页元数据，搜索在固定集合内分页。
3. 首次选择检查当前权限并固定 file / revision，受控读取重复检查；新文件或新授权在下一 Run 生效。
4. 新输出先发布不可变 Run 文件，再按冻结保存目标创建长期入口或 CAS 更新；required 保存成功是独立完成证据。
5. 移除入口解除长期引用；消息、Run、Work 输入、Artifact 版本、修订与 pending operation 均纳入保留 / GC 判断。

## Artifact 与投递

1. 用户选择完成回复创建 Artifact Version；异步 HTML 构建接受 artifact_build Work 并由其 Run 承担成本。
2. 保存生产 Run / Execution / operation；Work 对精确版本的采用另记 revision，下载 / 长期保存不改写生产来源。
3. 不可变 Notification 选择版本化 Target，为每个目标建立 Delivery。
4. Web 接受为私有 inbox 提交；QQ 接受为 Adapter 回执，均非已读。发送丢回执转 uncertain，经显式决议处理。

## Heavy Document

1. File Analysis 使用稳定 operation 请求 `NormalizeDocumentWorkflow`。
2. Lifecycle 队列编排，Activity 调度至 `hpagent-document` 独立 Worker。
3. 读取固定输入、转换、结算用量并提交结果；不创建第二套 Work 或文件状态。
