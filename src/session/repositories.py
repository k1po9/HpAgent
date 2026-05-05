"""
旧存储实现已迁移到 src/storage/。

============================================================================
历史说明
============================================================================

  此文件原包含约 416 行的存储实现代码，包括:
    - FileSessionRepository:  文件系统会话存储
    - FileEventRepository:    文件系统事件存储
    - PostgresSessionRepository:  PostgreSQL 会话存储
    - PostgresEventRepository:    PostgreSQL 事件存储
    - PostgresAccountRepository:  PostgreSQL 账户存储

  重构后这些实现已提取到独立的 storage/ 层:
    - 文件存储   → src/storage/file.py     (AioFileStore)
    - PostgreSQL → src/storage/postgres.py  (SqlKeyValueStore)
    - 内存存储   → src/storage/_memory.py   (InMemoryKVStore)

============================================================================
为什么保留此文件？
============================================================================

  1. 引用保护: 其他模块可能仍然 import 此文件（会触发 ModuleNotFoundError
     而非神秘的 AttributeError）。
  2. 迁移指引: 帮助开发者找到新的存储位置。
  3. 向后兼容: 如果未来需要，可在此重新导出 storage 的接口。

============================================================================
迁移指南
============================================================================

  旧代码:
    from session.repositories import FileSessionRepository
    repo = FileSessionRepository(base_dir="./data")

  新代码:
    from storage.file import AioFileStore
    store = AioFileStore(base_dir="./data")
    # 或使用 DI 容器
    from storage.container import InfraContainer
    container = InfraContainer.build(config)
    store = container.kv  # 自动选择后端
"""
