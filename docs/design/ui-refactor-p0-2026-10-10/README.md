# 前端 UI 创意重构：P0 能力与交互契约审计

本目录保存 2026-10-10 的系统能力审计及下一阶段创意设计输入，按项目惯例归入 `docs/design/`。代码基线为当时检出的 `main@a47d73b06a993d353b4177e282b69ddd28ffdb36`；只检查源码、契约与测试断言，未修改代码、未执行应用或测试。

| 文档 | 用途 |
| --- | --- |
| [HpAgent_UI_Capability_Map.md](HpAgent_UI_Capability_Map.md) | 37 项能力的用户操作、输入输出、API/SSE、约束、支持情况与源码证据。 |
| [HpAgent_Interaction_Contract.md](HpAgent_Interaction_Contract.md) | 状态模型、恢复与重试、安全机制、重构边界及未确认事项。 |
| [HpAgent_Frontend_Integration_Map.md](HpAgent_Frontend_Integration_Map.md) | 18 条实际前后端调用链、更新机制及 88 条显式 HTTP 路由。 |
| [HpAgent_Creative_Design_Brief.md](HpAgent_Creative_Design_Brief.md) | 交给创意设计阶段的产品能力、用户目标与语义约束，不规定导航、布局或视觉形式。 |

建议先读能力地图和交互契约；设计师可直接从创意说明进入，工程适配时结合调用链核对。文档中的 F/I/U 分别表示源码核实、合理推断与未确认，不能视为本轮运行验收通过。

这些是带日期与提交基线的审计材料；后续代码变化需重新核对适用性。持续维护的实现说明见 [Web 前端架构](../../architecture/web-ui.md)、[Durable Work V1](../../architecture/durable-work-v1.md)、[Workspace v4.1](../../architecture/workspace-v4.1.md)。返回 [项目文档索引](../../README.md)。
