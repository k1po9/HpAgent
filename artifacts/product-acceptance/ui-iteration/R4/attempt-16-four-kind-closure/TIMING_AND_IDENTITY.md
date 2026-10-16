# 请求、抵达与对象身份

真实API来源：`evidence/real-access.json`；汇总：`evidence/timing-summary.json`。同一Chromium1680×1000、正常动效，毫秒相对来源click测量。网络在页面request事件记录，标题/正文为逐RAF采样，约一帧精度，不能当作高精度服务延迟。

| 来源 | 当前目标请求（相对click） | 实际光头到达 | 首帧标题 | 首帧正文 | tip / path |
| --- | --- | --- | --- | --- | --- |
| Run卡 | +47ms（源页先有-204ms读取） | 771.5ms | 788ms | 1007ms | 432.756 / 432.756 |
| Artifact卡 | +51ms | 745.4ms | 762ms | 964ms | 373.267 / 373.267 |
| Workspace File | metadata +51ms、content +63ms；tree已先读 | 751.2ms | 768ms | 968ms | 718.191 / 718.191 |
| Task卡 | +44ms；列表控制器先有-231/-216ms预取 | 740.4ms | 758ms | 957ms | 894.223 / 894.223 |

各次均有actual-tip事件，tip等于真实路径长度。抵达前没有暴露业务标题/正文；标题先显约200ms后再开放正文。这些是实际测量，不把固定时长timer当作抵达。来源身份、token、hash、逐帧phase、请求路径全部保留在原JSON。Task与File明确区分列表/tree缓存、coalesced查询及新请求；不要求同对象重复点击额外发HTTP。

真实API完整链在`real-complete-chain.json`保留request/response、idempotency key、R3与Inspector逐帧事件。`real-r3-interrupt-travelling.json`与`forming.json`证明Inspector打开取消A且无再次发送。`real-versions.json`记录实际POST版本202接受/完成、新旧versionId、同版本iframe保持、切版本iframe替换以及真实停止202/cancelled。

四类父子导航与原引用版本：`four-kind-task-artifact-run-file-task-history.json`记录9步hash；Task outputs→原Artifact v1→Run→File→Task，再四次返回依次恢复File details、Run resources、Artifact details v1、Task outputs。`four-kind-version-dom-draft-identity.json`直接比较iframe/textarea DOM节点；`real-complete-chain.json`记录真实iframe交互状态跨Tab/展开保留；内部Tab/版本变化无重复跨区接力。

焦点、native dialog、草稿guard和纯壳关闭：对应`dialog-matrix.json`、Task/File逐例JSON、556组件/状态测试。source不存在/卸载/窄屏/减动会安全直接呈现；这类直接呈现不伪称发生实际来源飞行。关闭获准立即删除业务内容，只留空inert/aria-hidden壳；来源页和同版本业务DOM不会为动画复制。
