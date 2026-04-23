现有的代码已经将 `Harness` 和 `ResourcePool` 做了初步拆分，但存在几个耦合过紧、职责不清的问题，离「无状态 harness + 可 fallback 的资源池」还有距离。

下面从问题诊断、解耦设计、fallback 实现三个方面给出具体方案。

---

### 1. 当前代码的核心问题

**Harness 侧**
- `_call_model_internal` 自己拼凑了一个全局未定义的 `model_config`，直接传给 `ResourcePool.get_model_client`，要求资源池既懂配置来源、又懂具体模型 ID。
- `Harness` 本身不应该关心“从哪拿到模型客户端”，它只需要一个调用接口。

**ResourcePool 侧**
- `get_model_client` 签名不接受任何参数，实现里又用了个未定义的 `config`，根本无法支持不同模型。
- 没有模型路由、健康检查、fallback 支持。

> 结果：Harness 实际上隐式管理了模型选择，ResourcePool 只是个不完整的工厂。

---

### 2. 解耦目标：无状态 Harness + 智能资源池

参考 Anthropic Managed Agent 的模式，我们想要：
- **Harness（无状态）**：只持有会话、上下文构建、工具路由逻辑，模型调用全部委托给一个抽象的「模型服务」。
- **ResourcePool（有状态资源池）**：管理多个模型客户端、凭据、请求代理，并对外提供统一的 `generate` 接口，内部支持 fallback。

关键改变：**Harness 不再直接拿 client，而是调用 `ResourcePool.generate(...)`**，由资源池完成模型选择、调用、fallback 重试。

---

### 3. 重构后的接口与职责

#### ResourcePool 新接口（模型调用门面）

```python
class ResourcePool:
    def __init__(self, ...):
        self._model_registry: Dict[str, ModelClient] = {}
        self._fallback_policy: FallbackPolicy = ...

    async def register_model(self, model_id: str, client: ModelClient, priority: int = 0):
        """注册模型客户端"""
        ...

    async def generate(
        self,
        model_selector: str,          # 逻辑名或配置键
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        stream: bool = False,
    ) -> ModelResponse:
        """统一生成接口，内部执行 fallback"""
        ...
```

**职责**：
- 持有多个 `ModelClient` 实例（可以是不同 provider、不同模型）。
- 根据 `model_selector` 选出主模型，按 fallback 策略执行调用。
- 自动处理临时错误重试、切换备用模型。

#### Harness 调整为纯逻辑编排

```python
class Harness:
    def __init__(self, session_store, resource_pool, sandbox_manager, 
                 system_prompt="...", max_turns=20, default_model="main":
        self._rp = resource_pool
        self._default_model = default_model
        # ...

    async def _call_model_internal(self, messages, tools):
        # 不再关心 client 创建，只传递自身配置的模型选择器
        return await self._rp.generate(
            model_selector=self._default_model,
            messages=messages,
            tools=tools if tools else None,
            stream=False,
        )
```

**优点**：
- Harness 彻底无状态，模型配置外置。
- 更换模型、添加 fallback 完全在 ResourcePool 内完成，Harness 零改动。

---

### 4. Fallback 实现策略

在 `ResourcePool.generate` 中实现“尝试主模型，失败后依次降级到备用模型”的循环。

**简单示范代码：**

```python
class ResourcePool:
    def __init__(self):
        self._clients: Dict[str, ModelClient] = {}
        self._fallback_groups: Dict[str, List[str]] = {}  # group_name -> [model_id1, model_id2, ...]

    def configure_fallback(self, group_name: str, primary: str, *fallbacks: str):
        self._fallback_groups[group_name] = [primary] + list(fallbacks)

    async def generate(self, model_selector, messages, tools=None, stream=False):
        # 支持两种用法：直接指定 model_id，或者使用 fallback 组名
        candidate_ids = self._fallback_groups.get(model_selector, [model_selector])
        last_error = None

        for model_id in candidate_ids:
            client = self._clients.get(model_id)
            if not client:
                continue
            try:
                return await client.generate(messages=messages, tools=tools, stream=stream)
            except (ModelAPIError, ConnectionError, TimeoutError) as e:
                last_error = e
                # 可选：记录日志，通知监控
                continue
            except Exception:
                raise  # 非可恢复错误直接抛出

        raise ModelAPIError(f"All models in group '{model_selector}' failed.") from last_error
```

**可扩展的 fallback 策略**（未来增强）：
- **按权重随机选择**：用于负载分流。
- **断路器**：连续失败 N 次自动熔断，定时恢复。
- **响应时间监测**：优先选择平均延迟更低的模型。

---

### 5. 完整解耦流程图

```
Harness._call_model_internal()
        |
        v
ResourcePool.generate(model_selector, messages, tools)
        |
        v
FallbackGroup: [primary_model, fallback_model_1, ...]
        |
   for client in group:
        |
        +--> try: client.generate(...) --> return ModelResponse
        |
        +--> except (可恢复错误): continue
        |
   raise 最终失败
```
