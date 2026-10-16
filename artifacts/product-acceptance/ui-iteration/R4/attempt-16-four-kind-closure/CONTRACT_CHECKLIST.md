# R4-ACCEPTANCE I01–I20 逐项核查

“pass”表示本轮列明环境的工程检查通过；人审仍为pending。没有待实施的本轮生产补丁；所有项均重新执行或通过本轮组件测试核查，不以历史报告替代。浏览器失败/降级/生命周期极端场景多为确定性夹具，真实API证据单独列明。

| ID | 当前状态 | 本轮核查结论 | evidence下的证据（另注明源码测试/log） |
| --- | --- | --- | --- |
| I01 | pass；人审pending | 四类导航/查询在receiving期间启动；可见列表已有预取不重复请求 | real-access.json、timing-summary.json、browser.json、file-regression.json |
| I02 | pass；人审pending | 真实path-tip抵达前中性hidden/inert；标题先于正文 | real-access.json、timing-summary.json、actual-task-arrival-0.json、actual-arrival-0.json |
| I03 | pass；人审pending | 快/慢/403/404/错误均仅显示当前对象；真实异账户404 | browser.json、file-regression.json、real-access.json |
| I04 | pass；人审pending | 四类交错/旧网络晚回/关闭重开最终目标正确 | four-kind-mixed-late-replies.json、rapid-late-reopen-0.json、shell与Arrival单测 |
| I05 | pass；人审pending | 来源真实DOM/身份/键盘；离屏/缺来源安全降级 | keyboard-and-dom-retention-0.json、directory-keyboard-0.json、interrupt-missing-source-0.json |
| I06 | pass；人审pending | X关闭与父层返回区分；Escape最高层 | four-kind-task-artifact-run-file-task-history.json、dialog-matrix.json、Surface单测 |
| I07 | pass；人审pending | 要求/授权草稿、打开与Tab/返回guard保护历史；取消/放弃 | task-editor-cancel-and-discard-0.json、task-grant-cancel-and-discard-0.json、shell.test、TaskArrival.test |
| I08 | pass；人审pending | 同帧删除业务DOM、释放dialog，仅空inert壳并移除 | real-access.json、twenty-close-cycles-0.json、关闭组件测试 |
| I09 | pass；人审pending | 关闭中重开/旧timer不隐藏新对象，无布局残留 | close-during-travel-reopen-0.json、rapid-late-reopen-0.json、repeat-close motion |
| I10 | pass；人审pending | 四类Tab/展开保DOM/草稿；Artifact iframe实际身份与状态 | four-kind-version-dom-draft-identity.json、real-complete-chain.json、real-versions.json |
| I11 | pass；人审pending | 默认版本归一化/原版本/新版本不串线，不重播 | real-versions.json、four-kind-version-dom-draft-identity.json、ArtifactArrival与导航单测 |
| I12 | pass；人审pending | Task→Artifact→Run→File→Task及四层返回；刷新/浏览器历史 | four-kind.json、four-kind-browser-history-refresh.json |
| I13 | pass；人审pending | 账户权限/来源卸载/resize/scroll/长帧安全收敛 | real-access.json、interrupt-*.json、会话隔离及shell单测 |
| I14 | pass；人审pending | 减动/hidden中断不补播；静态业务可用 | motion-regression.json、interrupt-reduce/hidden-0.json、motion偏好单测 |
| I15 | pass；人审pending | 390/780/1279/1280/1680/短屏/200%文本/native dialog | viewport-*.json、short-screen*.json、motion-zoom-200.json |
| I16 | pass；人审pending | 嵌套7弹层/草稿/顶层Escape/焦点及任务操作语义 | dialog-matrix.json、browser.json、real-controls.json、Surface/TaskSelfReview测试 |
| I17 | pass；人审pending | 真实R3 travelling/forming开Inspector取消A，无重发；停止终态 | real-closure.json、real-r3-interrupt-*.json、real-versions.json、R3组件测试 |
| I18 | pass；人审pending | 真实输入→执行→File/Artifact/Task→关闭→继续发送 | real-closure.json、real-complete-chain.json、real-controls.json |
| I19 | pass；人审pending | 20次混合循环资源计数；长帧降级 | four-kind-twenty-mixed-normal-cycles.json、long-frame-0.json |
| I20 | pass；人审pending | R1源码/构建同一性、R2/R3/账户/任务焦点及回归 | final-parity.json、canonical-e2e-aggregate.json、vitest.log、typecheck/build/lint |

I13/I14的后台/hidden以事件模拟验证，不等同真实OS冻结；I15没有原生OS缩放/软键盘；I17 quick-thinking/queued/停止失败等稀有组合由组件/状态夹具覆盖，真实API只声称travelling、forming、实际停止及完整链；I19不覆盖堆/低端设备；I20不声称全部仓库E2E或所有真实供应商运行。具体not-run见REMAINING_WORK。

API、幂等、权限与版本规则没有生产差异；真实受限角色、checksum迁移、186测试与控制确认验证其继续有效。查看、生成、保存、下载、授权、验收、投递与完成仍独立。R1–R3视觉/布局/特效保持源和产物一致，并有本轮实际操作回归。
