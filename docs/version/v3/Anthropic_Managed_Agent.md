# 基于Anthropic Managed Agents的Agent项目改造接口定义与架构框架
**文档定位**：宏观改造指导手册，定义模块边界、核心接口、数据契约和交互规范；所有设计严格遵循"接口比实现活得久"的核心哲学，兼容现有系统平滑迁移。

## 一、改造核心原则与整体分层
### 1.1 不可违反的三大铁律
1. **状态唯一原则**：所有系统状态只能存储在Session层，其他所有模块必须完全无状态
2. **执行隔离原则**：所有可能产生副作用的操作（代码执行、网络调用、消息发送）只能在Sandbox层执行
3. **凭据隔离原则**：所有敏感凭据只能存储在Resources层，Harness和Sandbox永远无法直接获取原始凭据

### 1.2 最终七模块分层架构
| 层级 | 模块 | 核心定位 | 与Anthropic原架构的对应关系 |
|------|------|----------|------------------------------|
| **调度层** | Orchestration | 全局流量入口与资源调度器 | 官方未单独拆分，是托管服务的内部组件 |
| **数据层** | Session | 唯一真实数据源 | 完全对应官方Session |
| **决策层** | Harness | 无状态AI决策大脑 | 完全对应官方Harness |
| **执行层** | Sandbox | 隔离执行环境 | 完全对应官方Sandbox |
| | Channel | 多渠道接入抽象 | Sandbox内的特殊工具集 |
| | Tools | 通用可执行工具集 | Sandbox内的核心执行单元 |
| **支撑层** | Resources | 全局共享资源池 | 官方未单独拆分，是安全与可扩展性的核心 |

---

## 二、各模块核心接口定义与设计约束
### 2.1 Session模块（数据层）
**核心定位**：append-only不可变事件日志，系统所有状态的唯一来源
**设计约束**：
- 只允许追加操作，不允许修改或删除已写入的事件
- 所有接口必须幂等
- 不做任何语义处理，只负责数据持久化和查询
- 不直接与用户或外部服务交互

**核心接口**
| 接口名称 | 接口职责 | 输入 | 输出 |
|----------|----------|------|------|
| `CreateSession` | 创建新会话 | 会话元数据（创建者、渠道、标签） | 全局唯一session_id |
| `EmitEvent` | 追加事件到会话日志 | session_id、事件对象 | 事件唯一event_id |
| `GetEvents` | 获取指定范围的事件 | session_id、起始偏移量、结束偏移量、事件类型过滤 | 事件列表 |
| `RewindSession` | 回滚会话到指定事件点 | session_id、目标event_id | 回滚后的会话状态 |
| `ArchiveSession` | 归档已完成的会话 | session_id | 归档状态 |
| `ListSessions` | 分页查询会话列表 | 过滤条件（时间、状态、标签） | 会话摘要列表 |

**核心数据契约**：统一事件格式
```
Event {
  event_id: 全局唯一字符串
  session_id: 所属会话ID
  timestamp: UTC时间戳
  event_type: 事件类型枚举（user_message/model_message/tool_call/tool_result/error/config_change）
  content: 结构化事件内容
  metadata: 非业务元数据（token消耗、执行耗时、错误栈）
}
```

---

### 2.2 Orchestration模块（调度层）
**核心定位**：全局流量入口，负责资源管理、负载均衡、失败重试和生命周期管理
**设计约束**：
- 无状态，可无限水平扩展
- 不做任何业务逻辑处理，只负责调度
- 所有失败重试策略在此定义，Harness不处理重试

**核心接口**
| 接口名称 | 接口职责 | 输入 | 输出 |
|----------|----------|------|------|
| `ReceiveRequest` | 接收外部用户请求 | 原始请求数据、渠道标识 | 请求接收确认 |
| `AllocateHarness` | 分配空闲Harness实例 | 会话优先级、模型需求 | Harness实例地址 |
| `ProvisionSandbox` | 创建并初始化沙箱实例 | 资源配置（CPU/内存/网络）、工具列表 | sandbox_id |
| `DestroySandbox` | 销毁沙箱实例 | sandbox_id | 销毁确认 |
| `RetryTask` | 失败任务重试调度 | session_id、失败事件ID | 重试任务ID |
| `CancelTask` | 取消正在执行的任务 | session_id | 取消确认 |

---

### 2.3 Harness模块（决策层）
**核心定位**：AI决策循环，负责上下文管理、模型调用和工具路由
**设计约束**：
- **绝对无状态**：所有状态必须从Session读取，禁止在内存中缓存会话数据
- 不直接执行任何工具或发送任何消息，所有执行操作必须路由到Sandbox
- 可随时替换实现，无需修改其他模块
- 支持同时运行多个不同推理范式的Harness（ReAct/Plan-and-Execute等）

**核心接口**
| 接口名称 | 接口职责 | 输入 | 输出 |
|----------|----------|------|------|
| `Wake` | 唤醒Harness并执行指定会话 | session_id | 任务执行状态 |
| `BuildContext` | 将事件日志转换为LLM上下文 | 事件列表 | LLM可理解的上下文格式 |
| `CallModel` | 调用大语言模型 | 上下文、工具定义、模型配置 | 模型响应 |
| `RouteToolCall` | 路由工具调用到对应Sandbox | tool_call对象 | 工具执行结果 |
| `HandleError` | 处理模型和工具执行错误 | 错误事件 | 错误处理策略 |

---

### 2.4 Sandbox模块（执行层）
**核心定位**：隔离的执行环境，所有副作用操作的唯一执行场所
**设计约束**：
- 懒初始化：只有需要执行时才创建，执行完成立即销毁
- 完全隔离：不同Sandbox之间无法互相访问
- 统一接口：所有工具和Channel都必须实现相同的执行接口
- 无持久化：Sandbox销毁后所有数据全部丢失

**核心接口**
| 接口名称 | 接口职责 | 输入 | 输出 |
|----------|----------|------|------|
| `Execute` | 执行指定工具或操作 | 工具名称、输入参数 | 执行结果字符串 |
| `ListTools` | 列出当前Sandbox可用的工具 | 无 | 工具定义列表 |
| `HealthCheck` | 沙箱健康检查 | 无 | 健康状态 |

---

### 2.5 Channel模块（多渠道抽象，Sandbox子模块）
**核心定位**：统一多渠道消息的接收和发送接口，屏蔽不同IM平台的差异
**设计约束**：
- 每个Channel实例运行在独立的Sandbox中
- 所有渠道消息必须先标准化为统一格式，再写入Session
- 不做任何业务逻辑处理，只负责消息格式转换和传输

**核心接口**
| 接口名称 | 接口职责 | 输入 | 输出 |
|----------|----------|------|------|
| `NormalizeMessage` | 将渠道原始消息转换为统一格式 | 渠道原始消息 | 标准化消息对象 |
| `SendMessage` | 将统一格式消息转换为渠道格式并发送 | 标准化消息对象 | 发送状态 |
| `StartMonitor` | 启动渠道消息监听 | 回调地址 | 监听状态 |
| `StopMonitor` | 停止渠道消息监听 | 无 | 停止确认 |

**核心数据契约**：统一消息格式
```
UnifiedMessage {
  message_id: 全局唯一字符串
  session_id: 所属会话ID
  sender_id: 发送者ID
  channel_type: 渠道类型枚举（telegram/discord/slack/wechat/web）
  content: 消息内容（文本/图片/文件/语音）
  timestamp: UTC时间戳
  metadata: 渠道特定元数据
}
```

---

### 2.6 Tools模块（通用工具集，Sandbox子模块）
**核心定位**：所有可执行工具的抽象集合
**设计约束**：
- 所有工具必须实现统一的`Execute`接口
- 工具执行过程中如需访问外部资源，必须通过Resources代理
- 禁止在工具中持有任何状态

**核心接口**
| 接口名称 | 接口职责 | 输入 | 输出 |
|----------|----------|------|------|
| `Execute` | 执行工具逻辑 | 输入参数 | 执行结果 |
| `GetDefinition` | 获取工具定义（用于LLM调用） | 无 | OpenAI格式的工具定义 |

---

### 2.7 Resources模块（支撑层）
**核心定位**：全局共享资源池，负责所有敏感资源的管理和代理访问
**设计约束**：
- 所有敏感凭据只能存储在此模块
- 所有外部服务调用必须通过此模块代理
- 提供细粒度的权限控制

**核心接口**
| 接口名称 | 接口职责 | 输入 | 输出 |
|----------|----------|------|------|
| `GetModelClient` | 获取指定模型的客户端 | 模型名称、配置参数 | 模型客户端代理 |
| `GetCredential` | 获取指定资源的凭据 | 资源ID、权限范围 | 临时访问令牌 |
| `ProxyRequest` | 代理外部服务请求 | 目标URL、请求参数、资源ID | 代理响应 |
| `GetFile` | 从文件存储读取文件 | 文件路径 | 文件内容 |
| `SaveFile` | 保存文件到文件存储 | 文件内容、路径 | 文件URL |
| `QueryVectorDB` | 查询向量数据库 | 查询向量、过滤条件 | 相似文档列表 |

---

## 三、跨模块交互规范
### 3.1 统一错误处理规范
1. 所有模块的错误都必须转换为统一的错误格式，并写入Session的`error`类型事件
2. 错误处理逻辑由Harness负责，Orchestration只负责透明重试
3. 错误分为可重试错误和不可重试错误，不可重试错误直接终止任务并通知用户

### 3.2 工具调用规范
```
ToolCall {
  id: 工具调用唯一ID
  name: 工具名称
  arguments: 结构化参数
}

ToolResult {
  tool_call_id: 对应工具调用的ID
  status: success/error
  content: 执行结果或错误信息
}
```

### 3.3 可观测性规范
1. 每个模块必须暴露标准的Prometheus指标接口
2. 所有请求和事件必须包含全局唯一的trace_id
3. 所有操作日志必须关联session_id和event_id

---

## 四、完整端到端交互流程（含多渠道）
```
1. 用户在Discord发送消息
   ↓
2. Discord Channel Sandbox接收消息，调用NormalizeMessage转换为统一格式
   ↓
3. Channel Sandbox调用Orchestration的ReceiveRequest接口
   ↓
4. Orchestration调用Session的CreateSession和EmitEvent("user_message")
   ↓
5. Orchestration分配空闲Harness实例，调用Wake(session_id)
   ↓
6. Harness调用Session的GetEvents获取事件日志
   ↓
7. Harness调用BuildContext构建LLM上下文
   ↓
8. Harness调用Resources的GetModelClient获取模型客户端
   ↓
9. Harness调用模型，获取工具调用指令
   ↓
10. Harness调用Session的EmitEvent("model_message")记录模型响应
    ↓
11. Harness请求Orchestration创建Sandbox实例
    ↓
12. Orchestration调用ProvisionSandbox创建包含所需工具的沙箱
    ↓
13. Harness调用Sandbox的Execute执行工具
    ↓
14. 工具执行过程中如需外部资源，调用Resources的ProxyRequest接口
    ↓
15. Sandbox返回工具执行结果给Harness
    ↓
16. Harness调用Session的EmitEvent("tool_result")记录结果
    ↓
17. 重复步骤6-16，直到任务完成
    ↓
18. Harness生成最终回复，写入Session
    ↓
19. Orchestration销毁Sandbox实例
    ↓
20. Harness调用Sandbox的Execute("discord_send", {"content": 最终回复})
    ↓
21. Discord Channel Sandbox调用SendMessage将回复发送给用户
    ↓
22. Harness通知Orchestration任务完成
    ↓
23. Orchestration调用Session的EmitEvent("session_complete")
```

---

## 五、现有项目改造路线图（分四阶段平滑迁移）
### 阶段1：数据层解耦
- 搭建独立的Session服务，实现核心接口
- 编写数据迁移脚本，将现有会话数据转换为事件日志格式
- 所有新会话优先写入新的Session服务，旧会话保持兼容

### 阶段2：调度与决策层解耦
- 搭建Orchestration服务，作为全局流量入口
- 将现有Agent逻辑包装为第一个Harness实现，确保完全无状态
- 所有新请求通过Orchestration路由到Harness，旧请求逐步迁移

### 阶段3：执行层解耦
- 搭建Sandbox服务，实现Docker容器化隔离
- 逐个迁移现有工具到Sandbox，实现统一的Execute接口
- 迁移所有Channel到独立的Sandbox实例
- 搭建Resources服务，集中管理所有凭据和外部资源

### 阶段4：优化与扩展
- 实现多Harness支持，可切换不同推理范式
- 实现多模型支持，通过Resources统一管理
- 添加完整的监控、日志和告警系统
- 实现会话回滚、断点续跑等高级功能
