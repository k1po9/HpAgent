# 本轮默认静态合同

Paper Flame和S1基础合同沿attempt-05。共享新增--hp-task-lane-surface=#E9EDF1；任务区域用银灰、白卡和少量暖色主动作；状态仍由presentTask / taskActions事实映射决定，成功绿、警告琥珀、普通状态蓝灰。

- 任务屏内容最大1232px。容器>1040px四列，<=1040px两列，<=550px单列；Inspector压缩画布时同样按实际可用宽度回流。分区圆角16、内距14/10、桌面最小高度340；单列空区最低160，不卡固定屏高、无分区内独立滚动。卡角12、内距16/14、类型与时间11px、标题14/22、目标12/20、状态11/18。预算/数据警告保留。任务、类型、计数和操作来自同一真实store，不使用概念数据。
- 新建是主动作；刷新/收件箱低强调；卡片实际primaryAction和More原回调不变。Native disabled保持；紧凑primary节点以稳定key保留，命令导致禁用/短暂失焦后，仅在焦点回到body时恢复原主要按钮，用户主动选择其他控件不会被抢回。忙碌导致的自动blur不释放卡片位置；主动离开再按实际状态移动。终端成功/失败或unknown snapshot不绕过既有守卫。
- bucket URL值/解析与SSE优先级保留；选择bucket定位该区域。默认attention进入时滚动回顶部，避免200%把新建操作卷出视野。排序沿compareTasks；焦点/正在查看的卡片和次序以记录的上下文保护，状态标签和分区计数仍按事实更新。
- 账户A仅以hp-account-settings作用于该Surface；540px或视口减32px宽，角20；height:fit-content、最大100dvh-32px，Native Surface body可滚动。身份白底图标56px、标题23/32；QQ中性图标40px、主绑定按钮38px（手机44）；退出语义红、与QQ主动作分开。手机内距20、身份标题21、按钮44，不铺满整屏。QQ组件结构改进复用在现有绑定入口，API/创建与轮询逻辑保持。

清理旧QQ底边和任务行的失效高优先级排版声明；没有data-r1主题开关、业务页面评审入口或R2新动效。正常production默认生效。DOM锚点.hp-task-title、[data-work-id]、[data-task-lane]、原Surface/Inspector、消息与iframe协议保持；新增任务区域/身份样式为呈现层。
