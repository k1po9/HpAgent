可以。下面是**当前 最新组件架构图**，

**1. 最新总架构图**

````
```mermaid
flowchart TD
    User["用户<br/>QQ / Console / Web"]

    subgraph Transport["Transport Layer：外部窗口"]
        Channels["channels/<br/>NapCatChannel<br/>OfficialQQChannel<br/>ConsoleChannel"]
        Router["ChannelRouter"]
    end

    subgraph Application["Application Layer：业务接待层"]
        Ingress["MessageIngressService<br/>消息入站 / @过滤 / 群聊上下文写入"]
        Conversation["ConversationService<br/>账号解析 / Workflow start-signal<br/>workspace / sandbox 初始化"]
        Reply["ReplyService<br/>最终回复 / 工具进度提示 / 群聊@策略"]
    end

    subgraph Orchestration["Orchestration Layer：时间编排层"]
        Worker["worker.py<br/>依赖组装 / 渠道监听 / Temporal Worker"]
        Workflow["Temporal Workflow<br/>排队 / signal / 空闲归档"]
        Activities["Temporal Activities<br/>薄封装"]
    end

    subgraph Turn["Turn Layer：一轮对话导演"]
        TurnOrch["TurnOrchestrator<br/>只编排一轮对话"]
    end

    subgraph Brain["Brain Layer：脑"]
        BrainEngine["BrainEngine<br/>模型调用 / HyDE改写 / BrainDecision"]
        Decision["BrainDecision<br/>content + action_requests"]
    end

    subgraph Action["Action Layer：手"]
        ActionRuntime["ActionRuntime<br/>工具选择 / 工具执行 / 结果摘要"]
        ActionReq["ActionRequest"]
        ActionRes["ActionResult"]
    end

    subgraph Memory["Memory Layer：档案员"]
        TurnMemory["TurnMemoryService<br/>事件记录 / 记忆召回 / 留存 / 归档"]
        SessionStore["SessionStore<br/>WAL / Redis / Hindsight"]
    end

    subgraph Sandbox["Sandbox Layer：工具间"]
        SandboxMgr["SandboxManager<br/>按 session 创建 sandbox"]
        SandboxCore["Sandbox<br/>select_tools / execute"]
        Tools["ToolRegistry<br/>Native / MCP / Skill tools"]
    end

    subgraph Resources["Resources & Persistence：供能层"]
        ResourcePool["ResourcePool<br/>模型降级链"]
        Workspace["Workspace / FileStore / GitRepo"]
        Redis["Redis"]
        Hindsight["Hindsight Memory"]
    end

    User --> Channels
    Channels --> Ingress
    Ingress --> Conversation
    Conversation --> Workflow
    Workflow --> Activities
    Activities --> TurnOrch

    TurnOrch --> TurnMemory
    TurnMemory --> SessionStore
    SessionStore --> Redis
    SessionStore --> Hindsight

    TurnOrch --> BrainEngine
    BrainEngine --> ResourcePool
    BrainEngine --> Decision
    Decision --> TurnOrch

    TurnOrch --> ActionReq
    ActionReq --> ActionRuntime
    ActionRuntime --> SandboxMgr
    SandboxMgr --> SandboxCore
    SandboxCore --> Tools
    ActionRuntime --> ActionRes
    ActionRes --> TurnOrch

    TurnOrch --> Reply
    Reply --> Router
    Router --> Channels
    Channels --> User

    Conversation --> Workspace
    SandboxMgr --> Workspace
```
````

**2. 更适合画 Excalidraw 的分层版**

你可以按从左到右画：

```
[外部用户]
    ↓
[channels: QQ / Console / Web]
    ↓
[MessageIngressService]
    ↓
[ConversationService]
    ↓
[Temporal Workflow / Activities]
    ↓
[TurnOrchestrator]
    ├── [TurnMemoryService] -> [SessionStore / Redis / Hindsight]
    ├── [BrainEngine] -> [ResourcePool / LLM]
    ├── [ActionRuntime] -> [SandboxManager / Sandbox / Tools]
    └── [ReplyService] -> [ChannelRouter] -> [channels]
```

**3. 一轮对话跑起来是什么样**

````
```mermaid
sequenceDiagram
    participant U as 用户
    participant C as channels
    participant I as MessageIngressService
    participant V as ConversationService
    participant W as Temporal Workflow
    participant T as TurnOrchestrator
    participant M as TurnMemoryService
    participant B as BrainEngine
    participant A as ActionRuntime
    participant S as Sandbox
    participant R as ReplyService

    U->>C: 发消息
    C->>I: normalize -> UnifiedMessage
    I->>I: 群聊上下文写入 / @过滤
    I->>V: handle(message)
    V->>W: start 或 signal workflow
    W->>T: process_turn(user_message)

    T->>M: ensure_session / record_user_message
    T->>M: load_recent_events
    T->>B: rewrite_recall_query
    T->>M: recall_memories

    loop ReAct 工具循环
        T->>A: select_tools
        A->>S: sandbox.select_tools
        T->>B: generate_chat_decision
        B-->>T: BrainDecision(content, action_requests)

        alt 有工具动作
            T->>R: send_progress
            T->>A: execute_request(ActionRequest)
            A->>S: sandbox.execute
            A-->>T: ActionResult
            T->>M: record_tool_result
        else 无工具动作
            T->>M: record_model_message
        end
    end

    T->>R: send_final
    R->>C: ChannelRouter.send
    C->>U: 回复用户
    T->>M: retain_memories
```
````

**4. 各组件一句话职责**

```
channels
外部窗口。只负责接收外部消息、发送外部回复。

MessageIngressService
门房。判断消息要不要进入 Agent，例如群聊 @ 过滤、群上下文写入。

ConversationService
调度台。负责账号解析、session/workflow 查找、workspace 和 sandbox 准备。

Temporal Workflow / Activities
时间编排器。负责排队、signal、空闲归档，不做业务细节。

TurnOrchestrator
导演。安排一轮对话怎么走，但不亲自问模型、不亲自执行工具、不亲自翻记忆库。

TurnMemoryService
档案员。记录事件、召回记忆、留存记忆、归档 WAL、反思指标。

BrainEngine
脑。负责 HyDE 改写、模型调用，输出 BrainDecision。

BrainDecision
大脑决策单。里面有回复文本、停止原因、输入快照、动作请求列表。

ActionRuntime
手的调度员。负责选择工具、执行动作请求、摘要长工具输出、返回 ActionResult。

SandboxManager / Sandbox
工具间。真正绑定 workspace、注册工具、执行工具、安全隔离。

ReplyService
发言人。负责最终回复、工具进度提示、群聊 @ 策略。

ResourcePool
能源站。负责模型调用和模型降级链。
```

**5. 当前最核心的“手脑分离”结构**

你画 Excalidraw 时可以把这块画大一点：

```
                 ┌────────────────────┐
                 │  TurnOrchestrator   │
                 │      回合导演        │
                 └─────────┬──────────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  ▼
┌──────────────┐   ┌────────────────┐   ┌──────────────┐
│ BrainEngine  │   │ ActionRuntime  │   │TurnMemorySvc │
│     脑        │   │      手         │   │    档案员     │
└──────┬───────┘   └───────┬────────┘   └──────┬───────┘
       │                   │                   │
       ▼                   ▼                   ▼
BrainDecision        ActionResult        SessionStore
ActionRequest        Sandbox/Tools       Hindsight
```

现在的理解可以浓缩成一句：

```
TurnOrchestrator 不做专业活，只负责调度专业的人。
BrainEngine 想，ActionRuntime 做，TurnMemoryService 记，ReplyService 说，channels 听和传。
```