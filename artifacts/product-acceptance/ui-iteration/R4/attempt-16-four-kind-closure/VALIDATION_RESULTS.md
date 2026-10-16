# 验证结果与边界

环境：AMD Ryzen 7 9700X、WSL2 Linux 6.18.54.1、Node22.23.3、Chromium151.0.7922.34。常规1680×1000，单一活跃Inspector，无CPU节流。其余视口390/780/1279/1280/1680和短屏见独立JSON。生产React/JS/CSS与Task接受版一致。

| 检查 | 结果 | 本轮证据 |
| --- | --- | --- |
| 前端组件/状态 | pass：66文件556项 | logs/vitest.log |
| TypeScript / Vite生产构建 | pass；已有大chunk警告 | logs/typecheck.log、build.log |
| 局部source与新验证脚本lint / diff-check | pass | logs/source-eslint.log、new-scripts-eslint-final.log、final-static.log |
| PostgreSQL/API领域测试 | pass：186；Temporal 6 deselected | logs/backend-tests.log、evidence/backend-results.xml |
| Task回归 | pass：33组 | evidence/browser.json |
| File/目录回归 | pass：23组 | evidence/file-regression.json |
| Artifact共享motion | pass：5组 | evidence/motion-regression.json |
| 四类组合 | pass：5组，含20次混合开关 | evidence/four-kind.json |
| 同数据16子页默认/展开/深层 | pass：96截图；32组布局/样式差异0 | evidence/visual-matrix.json、screenshots |
| 7个关联弹层默认/展开 | pass：14图 | evidence/dialog-matrix.json |
| 评审入口与待人审控制数据 | pass：55链接、2个pending对象，无业务写入 | evidence/landing-smoke.json |
| HTTP / 完整离线包 | pass；写入409拒绝、外部请求/页面错误0 | evidence/package-smoke.json |
| 原浏览器场景 | pass：29个独立场景最终通过 | evidence/canonical-e2e-aggregate.json |
| 真实API完整链与R3 travelling/forming中断 | pass：3组；页面错误0 | evidence/real-closure.json、real-*.json |
| 真实四类来源抵达、跨账户拒绝与退出 | pass：4来源actual-tip、4个404、退出清空 | evidence/real-access.json、timing-summary.json |
| 真实预算、版本冲突、成果验收/幂等、投递确认 | pass | evidence/real-controls.json |
| 真实版本生成、版本DOM与停止 | pass：202接受、完成、cancelled | evidence/real-versions.json |
| 隔离迁移/角色 | pass：4库各59迁移校验和一致；API/Worker无超级用户等权限 | evidence/isolation-final.json |

原E2E不是单次29全绿：校正定位后首轮26通过3失败；手动修复与标题改名复测2通过；UI8在新final测试库通过。aggregate按唯一场景取最后执行结果，保留各次失败/trace。部分UI3为明确route夹具，UI2注入传输故障，其余走真实API；不能把29一概称为纯真实端到端。

真实整链：实际上传Markdown→实际授权→UI发送202/R3→运行中的Run→File预览/下载并比较字节→返回→完成→HTML生成→iframe交互和跨Tab状态→明确保存源码副本→File→未来Task修订、授予2项/撤销1项→关闭→AI草稿仍在→第二次UI发送完成。Task为未来计划；FakeRun不执行Work，不声称Task调度已实跑。

真实控制组：actual HTTP/PG/鉴权/幂等/版本检查；前置生成与不确定投递由canonical Worker服务显式模拟，无真实供应商/QQ。预算200；旧If-Match验收409；指定原版本验收200后Work完成；同key重放200且Idempotency-Replayed；确认投递200产生explicitly_confirmed，而其他条件未满足的任务仍active。新的人审pending pair见control-review-seed.json，自动测试原样本保持在control-seed.json。

性能：20次正常混合开关49,543ms、3105帧间隔采样，p50约16.7ms、p95约16.8ms、最大33.4ms；监听器34→34、timer0→0、overlay0→0、animations0→0。RAF1→2包含测量器自身的一个采样RAF，结束标志令其下一帧停止。只证明所选资源计数不累积，不是堆泄漏证明或稳定60fps保证。长帧降级另有确定性夹具通过；没有低端实机测量。

hidden/后台中断、IME组合键等采用浏览器事件模拟；200%为根字体/CSS zoom；没有声称真实OS休眠、真实IME或原生系统缩放通过。其他not-run见REMAINING_WORK。
