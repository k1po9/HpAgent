# R6 当前验证结果

2026-10-17（Asia/Shanghai）。全部条目是本attempt新跑；历史通过数不计入。pass只覆盖列明环境，R6整体仍待人审。

|检查|状态与当前证据|
|---|---|
|R5基线|pass，240/240，汇总d767ac3b8d22330f20dbe63eb930f8020b0da56b36e8279c929f5dcf03996d22；baseline-check与before-source|
|完整前端|pass，70文件603测试；logs/vitest-final-603.log。起始594，新增9项保护与竞态回归|
|类型/构建|pass，tsc-b/typecheck-final；vite/build-long-action；既有>500kB chunk警告保留|
|改动范围静态|pass，所有改动TS/TSX ESLint，含CSS的Prettier；logs/eslint、prettier及diff-check-final。不是全仓lint声明|
|Workspace/Tasks/认证边界|pass，21组，最终product-browser-release与product-browser.json；目录草稿取消/放弃、上传选择、改名嵌套Escape、搜索失败重试、空/加载/连接中断/403及恢复、Task创建拒绝/草稿、登录失败/401/换账户、7视口/文字组合|
|长名称操作|pass，390×844+200%文字同数据前后：行高3171px，详情从y2070改为524；可直接点击；long-actions.json与配对截图。完整名称保留|
|审批决策|pass，approve/reject/unknown恢复原决策3组；approval-browser.json。明确浏览器夹具，记录重试原Idempotency-Key；不是Worker或供应商执行|
|R5会话切换|pass，24场景；一次点击两腿、Canvas实际条目区域像素、消息/Composer身份和独立草稿、请求立即、2500ms真实夹具等待、晚回复/快切/失败/附件guard/键盘/目标queued和running优先、7中断和7视口；transit-browser.json|
|基础收纳|pass，22组；browser-r6.json。发送失败/响应未知/停止失败/晚终态、queued/running/cancelling及终态、IME事件模拟、DOM/草稿/附件/滚动锚点/焦点/隐藏、20往返资源、file://操作|
|R5运动/降级|pass，19组mercury-silver + 9组mercury-advanced；反向同seed、几何/减动/modal/宿主/长帧、Canvas/mask/render故障、inert键盘与窄屏。hidden是属性/事件模拟|
|四类Inspector|pass，5组合；four-kind.json，含Task→Artifact→Run→File→Task与返回、同版本iframe/编辑器身份、混合快切晚回、浏览器历史、20完整混合循环|
|收纳×Inspector|pass，5组；fold-inspector.json：iframe/editor、Task/File授权草稿、嵌套dialog/Escape/焦点、390native modal|
|R4实际抵达/标题/关闭|pass，5组motion-regression.json及真实API时序。请求与实际path-tip/门控/标题、途中减动、晚回、重开/关闭、200%文字|
|16子页与7弹层|pass，96张截图、32组几何/选定CSS/Tab比较差异0；visual-matrix.json；7弹层14图dialog-matrix.json。后续唯一CSS是Workspace表格顶部对齐，该类只用于Workspace FileList，不涉及Inspector|
|真实API基础链|pass，5组real-api.json：真实上传/附件/单输入/5轮收纳无额外请求、单次幂等发送/R3 travelling及forming、Run/X/真实cancel202、真实双会话独立草稿与请求、退出bob换账户|
|真实API四类整链|pass，3组real-closure.json：发送→执行→Run→授权File/下载字节相同→生成Artifact/iframe保持→保存真实源码副本→Workspace→Task创建/修订/授权撤销→AI草稿/继续发送；R3 travelling、forming中打开Inspector不重发|
|连续切换资源|pass，见下方实际时长；transit-performance.json|
|连续真实API混合|pass，见下方实际时长；mixed-resources.json|
|最终时序/完整包|pass，timing-browser.json 5组（接受R5对照/最终/慢请求快切/手动反向/file://）；package-smoke.json HTTP与离线实际操作，package-integrity.json ZIP/241源码/240基线/完整截图核对；final-audit.json汇总135组浏览器场景全部pass（不含资源与长名称单独对照）|

实际环境：WSL2 Linux x86_64，Linux Node v22，Chromium headless 151；环境与CPU/容器/文件存储事实见environment、storage-and-runtime。不同脚本按明确尺寸和减动设置运行，不表示真实触屏或其他浏览器。

资源采样：R5接受基线5往返/10切换31.725秒，对照当前40往返/80切换186.782秒；280请求，与基线每次请求比率相同，每目标每次detail/messages各一次。当前listener47、timer0、interval1、Canvas1且backing0×0不增长；RAF1→0源于历史夹具120秒有限采样器结束。8880个装饰采样帧，draw p95约0.3ms、RAF间隔p95约16.7ms，long task0；只计JS回调/Canvas绘制，不是GPU paint/真机60fps。期间同宿主存在其他检查，不作为独占硬件基准。人工42ms负载只验证同key/seed的降档。

真实API混合：1680×1000、reduced-motion、142次“Tasks/TaskInspector→Workspace/FileInspector→AI收纳/展开→两会话切换”，181.263秒。预热后前后RAF0/timer0/interval1/监听器54、Canvas属性300×150、空壳0/dialog0/活动动画0完全一致，activeSSE0、Playwright requestfinished未配对计数1保持；查询从38到1884，是循环中实际读取，没有额外写入。另用mixed-resource-paths.json补做17循环20.756秒，定位该计数为启动/me，ssePaths为空；它不是服务端实时在途连接数量。记录查询/订阅路径；不把有界计数说成全应用资源为零。Canvas属性不是GPU内存测量。脚本跟踪指定全局交互事件和已连接目标；不计React/浏览器全部内部监听器。

隔离真实API：复用前重新核实原容器ID/image，唯一hpagent-r416 internal网络，独立hpagent_r416_final、受限hpagent_api、59迁移checksum一致、repo只读、test与Fake标志；无真实供应商凭证。仅恢复缺失tmpfs测试库/文件，没有覆盖现存库，未接日常产品端口。R6最新测试库/file-store只读备份保存recovery。fixture、真实API+Fake、Worker事实模拟分别标注；本轮未做新的Worker事实模拟。

失败记录：reproduced-boundaries 5项、reproduced-upload-save-tests 2项、reproduced-login-double 1项均先失败后修复。长名称先保留坐标/截图再改。测试脚本的路径、选择器、固定时长与有限采样器假失败见SELF_REVIEW及initial日志；未删除或冒充通过。当前未测项见REMAINING_WORK，不由模拟替代。
