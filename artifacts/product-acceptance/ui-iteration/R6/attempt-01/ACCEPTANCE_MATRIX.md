# 页面与状态验收矩阵

pass仅指本轮工程检查，人审统一pending；not-run独立列出，不由历史结果替代。

|ID|范围|状态|本轮证据|
|---|---|---|---|
|B01|240冻结源码/备份/工作区暂存差异|pass|baseline-check、BEFORE_FINGERPRINTS、before-source、diffs|
|W01|Workspace目录/全部/搜索、空/加载/失败/重试/权限|pass|product-browser、WorkspaceBoundary、真实complete-chain|
|W02|上传/新建/改名/移动/授权及dirty保护|pass|复现5+2项后修复；product-browser、real-closure、WorkspaceCommands/Migration测试|
|T01|四分区/筛选/同步、创建/修订/操作/关联|pass|21边界、7Task弹层、真实Task创建修订授权撤销、完整Task相关测试|
|A01|登录失败/成功/过期/退出/换账户/旧请求|pass|reproduced→fixed、603全量、product-browser登录链、real-api换账户|
|A02|连接中断/恢复/权限变化/重试|pass|Workspace/Tasks error/denied后恢复、四类晚回/403/404、API/Store代际测试|
|K01|Tab/Enter/Space/Escape、焦点、native嵌套、hidden/inert|pass|product-browser、fold-inspector、mercury-advanced、Surface/Arrival组件|
|V01|390/780/1279/1280/1680/短屏/200%文字/减动|pass|7组合主页面、R5/Inspector矩阵；长名称坐标2070→524与可点击|
|C01|R3/R4四类/16子页/身份/历史/快切晚回|pass|four-kind、motion-regression、visual/dialog-matrix、real-closure|
|C02|R5跨栏/双腿/最终时序/草稿附件输入身份|pass|transit-browser24、mercury19+advanced9、fold22；Mercury所有源码与R5逐文件一致，timing-browser收口记录|
|C03|隔离真实API整链+Fake|pass|核实ID/internal/DB/受限角色/存储；real-api5与real-closure3。真实Worker/供应商不在通过范围|
|C04|执行等待审批/未知/终态、失败/重试|pass|Run/Vitality/Workbench当前测试、22收纳场景、审批3浏览器夹具；不等同真实审批Worker执行|
|P01|明确时长连续使用与资源|pass|80切换186.782秒；142混合循环181.263秒；无增长，计数范围和限制见VALIDATION_RESULTS|
|Q01|完整Vitest/tsc-b/build/局部静态|pass|70文件603测试；typecheck-final、build-long-action、eslint/prettier/diff-check-final|
|D01|完整离线/可操作入口/最终快照/恢复交接|pass|241源码、implementation.patch、REVIEW、offline、HANDOFF；完整包最后smoke/integrity另记|
|N01|真机IME/软键盘/读屏/OS冻结/其他浏览器/长期堆|not-run|无对应设备/系统，详见REMAINING_WORK|
|N02|真实供应商/生产Worker/QQ/Temporal/完整后端E2E|not-run|不接生产库/真实外发；本轮无后端差异，不借用R4的186项|
