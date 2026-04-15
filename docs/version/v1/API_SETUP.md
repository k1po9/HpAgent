# MiniMax API 错误排查

## 问题
你的 MiniMax API 密钥不支持 `abab5.5-chat` 或 `abab6.5s-chat` 模型。

错误信息：`your current token plan not support model`

## 可能的原因
1. 你的 API 密钥是免费版/试用版 token
2. 你的账户没有充值或订阅该模型
3. 你的 API 密钥已经过期

## 解决方案

### 方案1：使用 MiniMax 免费模型
MiniMax 可能提供一些免费模型。查看你的 MiniMax 控制台支持哪些模型。

### 方案2：使用 OpenAI API
如果你有 OpenAI API 密钥，可以配置使用 OpenAI：

```yaml
model:
  provider: openai
  model: gpt-3.5-turbo
  api_key: "your-openai-api-key"
  base_url: "https://api.openai.com/v1"
  max_retries: 2
  timeout_seconds: 30

app:
  max_history_turns: 10
  system_prompt: "You are a helpful assistant."
```

### 方案3：申请 MiniMax API Key
1. 访问 [MiniMax 开放平台](https://www.minimaxi.com/)
2. 注册/登录账户
3. 充值或升级你的订阅计划
4. 生成新的 API 密钥

## 验证 API 密钥
使用以下命令测试你的 API 密钥是否有效：

```bash
python test_api.py
```

## 支持的模型（参考）
- MiniMax: `abab5.5-chat`, `abab6.5s-chat`, `abab6.5-chat`
- OpenAI: `gpt-3.5-turbo`, `gpt-4`, `gpt-4-turbo`
