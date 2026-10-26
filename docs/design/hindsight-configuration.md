# Hindsight 后续质量评测与历史数据处理

当前已实现的配置文件、参数、调用边界和同步操作见[质量配置](../reference/configuration.md#hindsight-110-质量参数)与[切换流程](../operations/deployment.md#记忆质量配置切换)，本页只保留尚需决定的输入。

- 真实 Provider 的中文质量评测：固定样本集与成本范围后，分别比较提取遗漏、角色归属、时间冲突、一次性决定与稳定偏好、同义召回和结果重复。mock 合约通过不提供这些结论；需先明确使用的真实模型与费用授权。
- 历史误分类与错误泛化：先按来源只读定位，决定逐来源重提取或事实/Observation 维护策略；不批量把 experience 改成 world，不绕过来源 generation、manual_curation、删除与撤权约束。
- Embedding 更换：变更模型/维度前确定历史向量、索引与回退方案，不能只修改配置名称。
- 新来源与新 Provider：当前保留纯文本 Chat 来源，生成/向量用 openai 兼容协议、重排用 siliconflow；内联附件、可信工具执行经历、其他 Provider 映射须按实际输入和消费边界另行设计。
