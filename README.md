# HpAgent — 智能对话代理框架

基于 Anthropic Managed Agent "手脑分离" (hand-brain separation) 架构的智能对话代理系统，使用 Temporal Workflow 作为编排引擎。

## 理念：静若处子，动若脱兔

在 QQ 端，它轻如问候，柔似提醒，是每日陪伴的挚友——一句关心，一次准时的任务提示，以最轻量的方式渗透你的日常，宛如处子般安静守候。

在 Web 端，它化身沉稳的管家，以更正式的对话承接你的重托：  
- **委托**——它跟进整个过程的进展，而非仅仅交付一个答案，让每一次任务都成为一份被郑重承担的承诺；  
- **透明**——它将过往行为凝练成可回溯的总结，让信任建立在可审计的事实之上；  
- **专属**——它把对你的理解具象为一组可预览、可掌控的专属 Skill，将“懂你”从感受变成可视化的能力清单，让专属感不再抽象，而是可以被查看、调整和信赖的资产。

## 架构哲学：四层手脑分离

```
Orchestrator (指挥)  →  调度协调大脑与双手
    ↓
Harness (大脑)       →  纯无状态执行器：组装上下文、调用模型、路由工具
Session (记忆)       →  持有全部用户历史事件，由 Temporal Event Sourcing 持久化
Sandbox (双手)       →  所有外部操作、工具执行、渠道 I/O 均通过沙箱代理
```

- **Harness (大脑)**：纯无状态执行器，不含任何持久化或调度逻辑。通过 Temporal Activities 将每个非确定性操作（模型调用、工具执行、上下文构建、渠道 I/O）分解为独立的无状态函数。
- **Session (记忆)**：持有全部对话历史。在 Temporal 模式下，事件历史由 Workflow Event Sourcing 自动持久化；也提供 `SessionManager` 文件系统实现用于兼容。
- **Sandbox (双手)**：所有外部操作和工具执行必须通过沙箱代理。每个沙箱拥有独立的工具注册表和资源配额。渠道（Console、NapCat）也是沙箱的一部分。
- **Orchestrator (指挥)**：`OrchestrationWorkflow` 是确定性编排核心，协调上述三层完成 agentic loop（构建上下文 → 调用模型 → 执行工具 → 发送回复）。

## 项目结构

```
src/
├── main.py                     # 入口：加载配置，启动 Orchestration Worker
├── orchestration/              # 指挥层
│   ├── workflow.py             #   OrchestrationWorkflow — 确定性编排核心
│   └── worker.py               #   Worker 启动：初始化依赖、连接 Temporal、启动渠道监听
├── harness/                    # 大脑层（纯无状态）
│   ├── activities.py           #   Temporal Activities：模型调用、工具执行、上下文构建、渠道 I/O
│   └── context_builder.py      #   上下文构建器：渠道感知 prompt 组装
├── session/                    # 记忆层
│   ├── session_manager.py      #   SessionManager（文件） / TemporalSessionManager（Workflow Query）
│   ├── models.py               #   数据模型：Session, EventRecord
│   └── repositories.py         #   持久化仓库
├── sandbox/                    # 双手层
│   ├── sandbox.py              #   沙箱实例：工具执行环境
│   ├── sandbox_manager.py      #   沙箱生命周期管理
│   ├── channels/               #   渠道（Console, NapCat）
│   └── tools/                  #   工具体系（原生, MCP, Skill）
├── resources/                  # 外部资源访问
│   ├── resource_pool.py        #   模型调用统一入口（降级链）
│   ├── credentials.py          #   凭证管理
│   └── model_client.py         #   低层 HTTP 客户端
└── common/                     # 公共基础设施
    ├── types.py                #   核心数据类型
    ├── interfaces.py           #   核心接口（ISession, IResources, ISandbox, IChannel, ITool）
    └── errors.py               #   错误类型体系
```

## 快速开始

1. 创建 `src/config.yaml`（参考上述 AppConfig 结构）
2. 启动 Temporal Server：`temporal server start-dev`
3. 启动 HpAgent：`cd src && python main.py`

## 技术栈

- **Python 3.11+** — 异步优先（asyncio）
- **Temporal** — 工作流编排引擎，提供持久化执行、自动重试、事件溯源
- **httpx** — 异步 HTTP 客户端
- **websockets** — NapCat WebSocket 通信
