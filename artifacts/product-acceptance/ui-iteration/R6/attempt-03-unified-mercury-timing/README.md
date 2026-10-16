# R6：切换对话与“展开阅读区”同速

修复了两入口的基础时间轴系数不同：切换23，阅读区8.5。现在两者共用8.5×0.35和相同hostTempo=2，不改变阅读区原速度、粒子轨迹/颜色或真实加载等待。

入口仍为 http://127.0.0.1:15346/REVIEW.html ，Windows本机回环服务更新到本attempt；真实隔离API预览 http://127.0.0.1:15336/ 继续读取当前production构建。旧attempt-01及其离线包保留，不覆盖。

[合同](INTERACTION_CONTRACT.md) · [本次验证](VALIDATION_RESULTS.md) · [交接](HANDOFF.md)。源码/配置241份起始逐文件与R6 attempt-01一致，before-source/初始Git差异保留。候选快照与4文件增量见source-snapshot、SOURCE_FINGERPRINTS.json、implementation.patch。

R6整体待人审。此包只完成本次速度调整的新增验证，不重复声称原135组/142资源循环为本次新测。未提交、推送或部署。
