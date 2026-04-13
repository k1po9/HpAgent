# HpAgent - OpenClaw 风格自动回复框架

基于 OpenClaw 自动回复框架的核心设计模式，使用 Python 3.11+ 实现的对话回复引擎。

## ✨ 特性

- 🏗️ **分层架构**：清晰分离入口、上下文构建、执行、回复处理层
- 💉 **依赖注入**：所有外部依赖通过参数传入，便于测试和替换
- 🧪 **完整测试**：包含 23 个单元测试，覆盖核心功能
- 🔧 **灵活配置**：使用 YAML 配置文件管理所有设置
- 🔄 **重试机制**：模型调用失败时自动重试
- 💬 **会话记忆**：支持多会话管理和历史记录

---

## 🚀 快速开始

### 方式1：一键启动（推荐）

```powershell
.\run.ps1
```

脚本会自动：
- ✅ 检测或创建虚拟环境
- ✅ 安装所有依赖
- ✅ 验证配置文件
- ✅ 启动应用

### 方式2：手动启动

```powershell
# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境
.\.venv\Scripts\Activate.ps1

# 安装依赖
pip install -r requirements.txt

# 运行应用
python -m src.main

# 运行测试
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

---

## ⚙️ 配置

### 1. 复制配置模板

```bash
copy config.yaml.example config.yaml
```

### 2. 编辑 config.yaml

```yaml
model:
  provider: minimax  # 或 openai
  model: MiniMax-M2.7
  api_key: "your-api-key-here"
  base_url: "https://api.minimaxi.com/v1"
  max_retries: 2
  timeout_seconds: 30

app:
  max_history_turns: 10
  system_prompt: "You are a helpful assistant."
```

### 3. API 配置示例

**MiniMax:**
```yaml
model:
  provider: minimax
  model: MiniMax-M2.7
  api_key: "your-minimax-key"
  base_url: "https://api.minimaxi.com/v1"
```

**OpenAI:**
```yaml
model:
  provider: openai
  model: gpt-3.5-turbo
  api_key: "your-openai-key"
  base_url: "https://api.openai.com/v1"
```

---

## 📁 项目结构

```
HpAgent/
├── src/
│   ├── core/              # 核心类型定义
│   │   ├── types.py       # TemplateContext, ReplyPayload
│   │   └── config.py      # 配置类
│   ├── context/           # 上下文管理
│   │   ├── session_store.py      # 会话存储（内存）
│   │   └── context_builder.py    # 构建上下文
│   ├── execution/         # 执行层
│   │   ├── llm_executor.py       # LLM 调用
│   │   └── agent_runner.py       # 核心编排器
│   ├── response/          # 回复处理
│   │   └── payload_builder.py    # 回复构建
│   ├── channels/         # 渠道层
│   │   └── console_channel.py    # 控制台渠道
│   └── main.py           # 入口
├── tests/                # 单元测试
├── .venv/               # 虚拟环境
├── config.yaml          # 配置文件
├── requirements.txt     # 依赖列表
└── run.ps1             # 启动脚本
```

---

## 🧪 测试

```powershell
# 运行所有测试
.\.venv\Scripts\python.exe -m pytest tests/ -v

# 运行特定测试文件
.\.venv\Scripts\python.exe -m pytest tests/test_agent_runner.py -v

# 运行单个测试
.\.venv\Scripts\python.exe -m pytest tests/test_agent_runner.py::TestAgentRunner::test_successful_reply_flow -v
```

---

## 🔧 常用命令

| 命令 | 说明 |
|------|------|
| `.\run.ps1` | 一键启动（推荐） |
| `.\.venv\Scripts\python.exe -m src.main` | 直接运行 |
| `.\.venv\Scripts\python.exe -m pytest tests/ -v` | 运行测试 |
| `.\.venv\Scripts\pip list` | 查看已安装包 |
| `.\.venv\Scripts\pip install -U httpx pyyaml` | 升级依赖 |

---

## ❓ 常见问题

### Q: 如何获取 API 密钥？
- **MiniMax**: [MiniMax 开放平台](https://www.minimaxi.com/)
- **OpenAI**: [OpenAI API](https://platform.openai.com/)

### Q: 虚拟环境在哪？
项目根目录的 `.venv` 文件夹。

### Q: 如何更新依赖？
```powershell
.\.venv\Scripts\pip install -U httpx pyyaml pytest
```

### Q: 如何重新创建虚拟环境？
```powershell
Remove-Item -Recurse -Force .venv
.\run.ps1
```

---

## 🎯 扩展点

项目设计预留了以下扩展点：

- **多渠道支持**：通过 `TemplateContext.provider` 标识来源
- **流式输出**：修改 `LLMExecutor.generate` 返回生成器
- **命令系统**：在 `context_builder` 中识别 `/` 开头消息
- **模型回退**：实现 `run_with_fallback` 支持多模型
- **持久化存储**：实现 `RedisSessionStore`、`FileSessionStore`
- **工具调用**：扩展 `LLMExecutor` 支持 function calling
- **记忆压缩**：在 `context_builder` 中插入压缩钩子

---

## 📚 技术栈

- Python 3.11+
- httpx - HTTP 客户端
- PyYAML - 配置管理
- pytest - 测试框架
- dataclass - 数据结构

---

## 📄 许可证

MIT License
