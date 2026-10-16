# 当前时序调整验证

本次新增运行，不能把R6 attempt-01历史结果计作本次。

- before时序复现：fail，切换每段约为对应阅读动作的36%；evidence/before.json。
- after时序：pass，收束2317.8ms对阅读2324ms；展开2634.5ms对阅读2647.2ms。同一生产React、1680×1000、Chromium headless；往返Composer DOM/草稿保持；evidence/after.json。
- 完整Vitest：70文件603测试通过；logs/vitest-final.log。
- tsc-b、4文件ESLint/Prettier、production构建通过。原chunk>500kB警告保留；logs/typecheck、eslint-final、prettier-final、build-final。
- 24切换与5收纳/Inspector组合由当前新构建重新执行，最终结果见transit-browser.json和fold-inspector.json。
- 实际隔离API+Fake的5组链路重新执行，见real-api.json；不连接供应商或外部投递。
- 完整离线、源码ZIP与Windows实际入口资产核验保存在package-smoke/package-integrity/windows-entry证据。

新速度下允许正确的更长运动完成时间；浏览器等待依据终态事件，不用旧5秒总切换闹钟。真实目标detail/messages仍立即开始，未压缩服务端等待，快切/异常仍优先当前真实目标。

本次没有重复16子页截图矩阵、长时资源/堆检查、真机IME/软键盘/读屏、其他浏览器、OS冻结、真实供应商/生产Worker。没有相关产品差异，不借用历史通过数冒充。当前只提交工程候选供人审，无Git提交/推送/部署。
