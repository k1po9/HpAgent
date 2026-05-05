"""
Account 层 —— 渠道身份到统一账号的解析。

在 v5 架构中，Account 层是"手脑分离"模型的身份统一层：
  - 同一用户可能通过 QQ (NapCat)、Web 页面、CLI 终端等多种渠道与系统交互
  - AccountService 将不同渠道的 sender_id 映射到统一的 account_id
  - 使得 QQ 和 Web 端的消息能路由到同一个 Temporal Workflow，实现跨客户端记忆共享

数据模型：
  - Account: 统一账号实体，包含 account_id 和渠道绑定映射

服务：
  - AccountService: 渠道解析、账号创建、渠道绑定
"""
from .models import Account
from .account_service import AccountService

__all__ = ["Account", "AccountService"]
