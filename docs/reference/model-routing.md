# 模型分工与安全检查

模型配置分三层：`.env` 保存供应商凭据和模型目录，`config/models.yaml` 决定用途，`docker-compose.yaml` 将所需变量送入对应服务。只有定义变量，不会自动选用模型，也不会自动增加回退链。

## 以后去哪里修改

| 想改什么 | 编辑哪里 | 说明 |
| --- | --- | --- |
| API Key、接口地址、模型ID | 部署目录私有 `.env` | 不提交、不贴到聊天里；`.env.example` 只是模板 |
| 对话、摘要、推理选择哪个供应商和模型 | `config/models.yaml` | 修改对应链的 provider 和 model；多条表示有序回退 |
| 记忆提取和深度反思模型 | `.env` 的 `HINDSIGHT_API_LLM_*` | Hindsight独立运行，不读取Worker的models.yaml |
| 工具检索向量和重排 | `config/models.yaml` 的 embedding/rerank | 当前引用SiliconFlow对应变量 |
| Hindsight向量和重排 | `docker-compose.yaml` 的 hindsight.environment | 当前共用SiliconFlow向量／重排变量，但调用独立 |
| Web/QQ输出长度、等待预算 | `config/models.yaml` 的 channel_overrides | 不负责选择模型；具体调用仍可能有自己的覆盖 |
| HTML成果读取超时 | `.env` 的 `ARTIFACT_MODEL_READ_TIMEOUT_SECONDS` | 不会因此自动选择旗舰模型 |

Beta实际编辑路径为 `/home/hp/deploy/hpagent-beta/.env` 和 `/home/hp/deploy/hpagent-beta/config/models.yaml`。开发源码在另一目录；修改后需保持发布版本与Beta一致，不能认为开发目录改动会自动同步。

## 实际用途对照

| 环节 | 实际选择 | 当前供应商／模型变量 | 调用依据 |
| --- | --- | --- | --- |
| Web/QQ对话、Agent决策和最终回复 | chat | siliconflow / SILICONFLOW_STANDARD_MODEL | [BrainEngine](../../src/brain/engine.py) |
| Research报告综合 | chat | 同上 | [报告合成](../../src/research_adapters/synthesis.py)；Worker未覆盖其默认selector |
| HTML成果生成 | chat | 同上 | [成果生成](../../src/web_artifacts/generator.py)；不是单独的旗舰模型 |
| 记忆召回查询改写 | fast | siliconflow / SILICONFLOW_HIGHSPEED_MODEL | [BrainEngine](../../src/brain/engine.py) |
| 长工具结果摘要 | fast | 同上 | [ActionRuntime](../../src/actions/runtime.py) |
| 上下文压缩／摘要 | fast | 同上 | [Activity上下文](../../src/agent_activities/runtime.py)传入selector给上下文构建 |
| 显式推理链 | reasoning | siliconflow / SILICONFLOW_FLAGSHIP_MODEL | [Worker注册](../../src/orchestration/worker.py)；当前没有发现业务主动选择reasoning，不自动切换 |
| 工具语义检索 | embedding | siliconflow / SILICONFLOW_EMBEDDING_MODEL | [Embedding客户端](../../src/resources/embedding.py) |
| 工具候选重排 | rerank | siliconflow / SILICONFLOW_RERANK_MODEL | [Rerank客户端](../../src/resources/reranker.py) |
| 长期记忆提取／反思的生成模型 | Hindsight独立配置 | HINDSIGHT_API_LLM_MODEL | [Compose](../../docker-compose.yaml)的hindsight服务；不走fast链 |
| 长期记忆向量／重排 | Hindsight独立配置 | SILICONFLOW_EMBEDDING_MODEL / SILICONFLOW_RERANK_MODEL | Compose将同一组变量映射到Hindsight自己的参数 |
| 网页搜索、抓取、Office转PDF | 非生成模型服务本身 | SearXNG、HTTP抓取、Gotenberg等 | 研究中的报告生成仍用chat；PDF转换本身不选择LLM |
| 图像生成 | image=[] | 关闭 | 不影响独立的图片上传 |

`FLAGSHIP/STANDARD/HIGHSPEED`只是你给供应商模型目录起的标签，不是模型供应商认可的固定等级，也不等于账户A/B/C权限。三个变量可以填同一个模型ID，依然可按各链的超时、输出长度和请求参数分别运行。

当前三条文本链均已显式选用SiliconFlow对应目录变量，Embedding／Rerank也使用SiliconFlow。MiniMax目录仍可保留，Hindsight LLM按独立配置使用MiniMax。没有增加跨供应商回退。Provider 与模型的 extra_body 以模型配置覆盖合并，序列化时放请求顶层；禁止覆盖 model/messages/tools/stream/max_tokens 等保留字段。当前只校验结构及保留字段，未按模型能力验证每个原生参数，真实兼容性须独立请求验证。

## .env规范写法

按[模板](../../.env.example)的顺序维护：A供应商连接、B模型目录、C独立记忆适配。

```dotenv
# 供应商凭据：这里用格式占位，实际值只写私有.env
MINIMAX_API_KEY='替换为实际密钥'
MINIMAX_API_BASE_URL='替换为对应兼容接口地址'
MINIMAX_FLAGSHIP_MODEL='你的旗舰模型ID'
MINIMAX_STANDARD_MODEL='你的标准模型ID'
MINIMAX_HIGHSPEED_MODEL='你的高速模型ID'

# 放在被引用变量之后；别用单引号包住${...}
HINDSIGHT_API_LLM_PROVIDER=openai
HINDSIGHT_API_LLM_BASE_URL=${MINIMAX_API_BASE_URL}
HINDSIGHT_API_LLM_API_KEY=${MINIMAX_API_KEY}
HINDSIGHT_API_LLM_MODEL=${MINIMAX_STANDARD_MODEL}
```

Compose中，普通值可以用英文单引号；引用表达式用不加引号或双引号。`'${MINIMAX_API_KEY}'`会保留成字面字符串，不能当真实Key使用。不要用`source .env`执行配置。

`HINDSIGHT_API_LLM_PROVIDER=openai`表示使用OpenAI兼容适配器，不表示必须购买OpenAI模型。供应商模型ID、接口可达性、结构化输出支持及账号权限需要单独真实验收，静态检查不能证明这些成立。

Hindsight引用MiniMax变量不代表它会跟随chat链切换。当前chat已经使用SiliconFlow，记忆LLM仍按Hindsight四项配置使用MiniMax，需另行明确是否也切换。

## 切换供应商示例

当前chat已经选用SiliconFlow。若希望主对话、Research报告及HTML成果改回MiniMax标准目录，修改models.yaml：

```yaml
chat:
  - provider: minimax
    model: "${MINIMAX_STANDARD_MODEL}"
    max_tokens: 4096
    timeout: 30.0
```

同步确认私有.env有该变量，并检查该模型/协议支持的原生参数，不能沿用前一供应商的extra_body。此例只是修改教程，不执行切换。

## 不调用模型的安全检查

在Beta目录执行：

```bash
cd /home/hp/deploy/hpagent-beta
python3 scripts/check/model-map.py --env-file .env --beta
```

需要Docker Compose及PyYAML，源码目录也可使用同一入口。脚本将指定.env作为配置依据，清除同名shell变量的覆盖，仅读取Compose配置，不创建容器、连接业务数据库或调用外部模型。实际部署也应避免旧shell变量覆盖私有配置。

输出包含用途、供应商、模型名、模型变量来源、Key/地址是否已填和未选用的文本目录变量。不会输出Key、密码、长度、前后缀或完整展开后的Compose配置。即使Compose报错，也不打印可能含凭据的原始错误正文。退出码0表示静态结构及必需字段通过，1表示发现缺失／占位／未展开引用，2表示无法完成检查。

本机程序需要读取真实密钥才能解析配置；拥有文件权限的维护助手并非技术上无法读取。这里的保护是只将脱敏摘要返回对话，不把真实值传入模型上下文。任何主动输出.env、容器完整环境或完整Compose配置都会打破这个边界，不能依赖文件名自动保密。

现有`models.py`／`models-compose.sh`是实际发请求的测试，会产生模型用量，不能与本页的静态入口混用。

## 如何确认上线后真正使用哪个模型

1. 静态检查先确定“配置打算用什么”；它还不是运行证明。
2. 变更.env／Compose后需要重建对应容器以更新环境；仅restart不会读取新容器环境。挂载的models.yaml变更也需让Worker重新加载。
3. 在安排的真实验收中检查该次Run的模型调用记录／Model Input Snapshot。实际端点才证明该次请求用了哪个模型，回退时尤其如此。
4. Hindsight独立运行，它自己的调用不应仅凭Worker Run记录推断，需单独检查其配置及运行证据。
