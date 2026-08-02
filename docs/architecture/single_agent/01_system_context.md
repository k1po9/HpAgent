# 01 — 系统上下文（单 Agent）

> 对应视觉文件：[`diagrams/01_system_context.excalidraw`](diagrams/01_system_context.excalidraw)
>
> 本文是单 Agent 系统边界的设计事实源；Excalidraw 只做人工维护的视觉表达。

## 目的

系统上下文只回答一个问题：**HpAgent 与哪些外部人员或系统交互？**

本层不描述供应商选择、模型类型、API、协议、端口、数据结构、内部组件或处理步骤。这些实现细节可能频繁变化，应在更低层级的容器、组件或时序文档中说明。

## 系统边界

本图将 **HpAgent 单 Agent 应用** 视为一个完整系统。模型选择、工具选择、回合处理、沙箱、本地文件和内嵌索引等均属于 HpAgent 内部实现，不作为系统上下文节点。

HpAgent 的职责是接收用户消息，结合外部模型、工具、记忆和运行时基础设施完成处理，并把结果回复给用户。

## 外部参与者与系统

| 外部对象 | 类型 | 与 HpAgent 的关系 |
|---|---|---|
| QQ 用户 | 人员 | 通过 QQ 与 HpAgent 发起对话并接收回复 |
| QQ 平台 | 外部系统 | 承载用户与机器人之间的消息传递 |
| 消息接入系统（当前为 NapCat） | 外部系统 | 在 QQ 平台与 HpAgent 之间接入和转发消息 |
| AI 模型服务 | 外部系统 | 接收 HpAgent 的推理请求并返回模型结果；具体供应商和模型能力不属于本层事实 |
| 外部工具服务 | 外部系统 | 向 HpAgent 提供可调用能力；具体 MCP 服务器及工具清单不属于本层事实 |
| 长期记忆系统（当前为 Hindsight） | 外部系统 | 保存并检索跨会话记忆 |
| 工作流编排系统（当前为 Temporal） | 外部系统 | 管理会话工作流的执行、等待与恢复 |
| 状态存储系统（当前为 Redis） | 外部系统 | 保存运行期间需要共享或恢复的状态 |

## 上下文关系

```text
QQ 用户 ⇄ QQ 平台 ⇄ 消息接入系统 ⇄ HpAgent

HpAgent ⇄ AI 模型服务
HpAgent ⇄ 外部工具服务
HpAgent ⇄ 长期记忆系统
HpAgent ⇄ 工作流编排系统
HpAgent ⇄ 状态存储系统
```

箭头仅表示存在交互关系，不承诺具体协议、API、端口或消息格式。

## 本层明确不展示

以下内容不得出现在系统上下文图中：

- 具体 LLM、Embedding 或 Rerank 供应商及模型名称；
- 具体 MCP 服务器、工具名称或业务能力列表；
- REST、WebSocket、gRPC、OneBot 等协议和 API 细节；
- HyDE、RAG、fallback、工具循环等内部处理机制；
- ChromaDB、本地文件、workspace、WAL 等内部存储实现；
- HpAgent 内部模块、类、方法及调用顺序。

这些信息分别属于 [`02_container.md`](02_container.md)、[`03_component.md`](03_component.md) 或 [`04_sequence.md`](04_sequence.md) 的范围。
