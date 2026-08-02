# HpAgent 架构文档

本目录只维护 HpAgent 的架构事实、对应的人工视觉表达，以及可追溯的历史决策材料。

## 事实优先级

架构信息发生冲突时，统一按以下优先级判断：

```text
代码 > Markdown > Mermaid > Excalidraw
```

- **代码是实现事实**：它描述系统当前实际如何运行。
- **Markdown 是设计事实源**：`single_agent/` 下的 Markdown 描述系统应当如何设计，也是架构评审和变更的入口。
- **Mermaid 是 Markdown 的辅助表达**：不得引入 Markdown 正文没有定义的新事实。
- **Excalidraw 是人工维护的视觉表达**：用于沟通与展示，不作为反向生成或覆盖 Markdown 的依据。

代码与 Markdown 不一致时，必须明确暴露为 **architecture drift**。不得静默修改图、改写措辞或选择其中一方来掩盖差异；应记录不一致的位置、代码现状、设计预期和后续处置。若实现变更是有意的，应同步更新 Markdown；若实现偏离设计，应修正代码。下游 Mermaid 与 Excalidraw 仅在设计事实确认后同步。

## Excalidraw 人工维护红线

`.excalidraw` 文件的内容只能由人工维护。对 AI、Agent、脚本及其他自动化工具作如下强制约束：

1. **禁止修改内容**：不得创建、重绘、改写、格式化、修复、优化或删除任何 Excalidraw 图元及文件内容。
2. **禁止自动生成或转换**：不得从 Markdown、Mermaid、代码、图片或其他来源生成、转换或嵌入 Excalidraw。
3. **只允许只读检查**：自动化只能读取文件、检查是否存在、验证 JSON 是否可解析，以及报告它与 Markdown 之间的 visual drift。
4. **不得自动同步**：Markdown 或代码发生变化时，只能在对应 Markdown 中显式记录 visual drift 并提醒人工更新，严禁顺手修改 Excalidraw。
5. **文件整理的唯一例外**：仅在用户明确要求整理目录时，允许对 Excalidraw 做字节不变的复制、移动或重命名；操作前后内容哈希必须一致。

除非用户明确撤销本红线并专门授权修改 Excalidraw 内容，否则任何“更新架构”“同步文档”“修复 drift”等指令都不构成修改 Excalidraw 的授权。

## Scope

当前架构文档的范围是 **HpAgent 单 Agent 运行形态**：

- 覆盖外部参与者与系统边界、运行时容器、进程内组件，以及关键交互时序。
- 覆盖当前单 Agent 主链路涉及的渠道接入、会话编排、模型推理、工具执行、记忆、回复和持久化关系。
- 不把未来的多 Agent 协作方案当作当前事实；相关目录在真正开始设计时再建立。
- 不承担 API 参考、部署操作手册、故障排查记录或版本发布说明的职责。
- 历史方案、重构过程和旧版设计材料只放在 `decisions/old/`，不作为当前事实源。

四份当前文档按抽象层级划分：

| 设计事实源 | 负责回答 | 人工视觉文件 |
|---|---|---|
| [`01_system_context.md`](single_agent/01_system_context.md) | 系统服务谁、依赖谁、边界在哪里 | [`01_system_context.excalidraw`](single_agent/diagrams/01_system_context.excalidraw) |
| [`02_container.md`](single_agent/02_container.md) | 系统由哪些可运行/部署单元组成、如何通信 | [`02_container.excalidraw`](single_agent/diagrams/02_container.excalidraw) |
| [`03_component.md`](single_agent/03_component.md) | 单 Agent 内部组件如何分工与依赖 | [`03_component.excalidraw`](single_agent/diagrams/03_component.excalidraw) |
| [`04_sequence.md`](single_agent/04_sequence.md) | 关键场景按什么顺序协作、数据如何流动 | [`04_sequence.excalidraw`](single_agent/diagrams/04_sequence.excalidraw) |

## 维护约束

1. `single_agent/` 中只保留上述四份 Markdown；`diagrams/` 中只保留与其同序号、同主题的四份 Excalidraw，严格 1:1。
2. 架构变更只修改对应 Markdown 和 Mermaid；Excalidraw 始终由人工另行同步，自动化不得触碰其内容。
3. 一个事实只在最合适的层级定义；其他文档通过链接引用，避免复制后产生多份真相。
4. 发现 drift 时，在相关 Markdown 中显式标注并提醒人工处理；不得通过自动修改 Excalidraw 来消除 drift，在 drift 消除前不得宣称文档与视觉表达一致。
