# UI-4 验收证据（2026-10-07）

> 后续自检：[自检证据](self-review/README.md)及[问题记录](../../../docs/implementation/ui-4-self-review.md)。本轮已有相关测试 78 项通过，4 项新增反例均复现缺陷；原实施日志和计数保持不变，不能视为这些边界已通过。

源码与测试变更清单见 [changed-files.txt](changed-files.txt)。对应 [实施报告](../../../docs/implementation/ui-4-workspace-permissions-report.md) 和 [实施计划](../../../docs/implementation/ui-4-workspace-permissions-plan.md)。基线 HEAD 为 `40a318a5600a3123f3149e2788ddafaf28d59e9c`，本批随 UI-4 实现及自检修复交付；不改写 UI-2/UI-3 的历史证据。

修复后的独立结果见 [自检修复证据](self-review/README.md)。下表244/37/30为原实施阶段结果，未累加为本次修复的验证数量。UI-4截图随修复后浏览器复验重新生成。

## 原实施命令与结果

| 验证 | 实际命令/范围 | 结果与日志 |
| --- | --- | --- |
| TypeScript | `cd web && npm run typecheck` | 通过，[typecheck.log](logs/typecheck.log) |
| ESLint/Prettier | `cd web && npm run lint` | 通过，[lint.log](logs/lint.log) |
| 生产构建 | `cd web && npm run build` | 通过，[build.log](logs/build.log)，保留 >500 kB chunk 提示 |
| 完整前端单测 | `cd web && npx vitest run --maxWorkers=1 --testTimeout=15000` | 35 文件、244 项通过，100.64s，[unit-final.log](logs/unit-final.log) |
| 撤权与规模实测 | `npx vitest run src/components/workspace/workspaceOperations.test.ts src/components/workspace/WorkspaceScale.test.tsx --maxWorkers=1 --testTimeout=15000 --reporter=verbose` | 12 项通过，[operations-scale.log](logs/operations-scale.log) |
| 浏览器矩阵 | 下方 11 个 spec，workers=1，真实 API/PG/Redis，Fake Executor | 37 项通过，13.8min，[e2e-final.log](logs/e2e-final.log) |
| 后端既有契约 | 下方 9 个 API/persistence 文件；`.venv/bin/python` | 30 项通过，1 项弃用提示，788.02s，[backend.log](logs/backend.log) |

浏览器完整命令（先配置隔离测试环境）：

```bash
cd web
npm run test:e2e -- e2e/ui-4-workspace.spec.ts e2e/workspace-direct-upload.spec.ts e2e/workspace-p1.spec.ts e2e/workspace-p3.spec.ts e2e/manual-repair.spec.ts e2e/ui-2-ai.spec.ts e2e/ui-3-execution.spec.ts e2e/artifact.spec.ts e2e/auth.spec.ts e2e/multi-tab.spec.ts e2e/a11y.spec.ts
```

后端完整命令（仓库根目录，先配置隔离环境）：

```bash
PYTHONPATH=src .venv/bin/python -m pytest test/web_api/test_workspace_direct_upload.py test/web_api/test_workspace_v41_p1.py test/web_api/test_workspace_v41_p2.py test/web_api/test_workspace_v41_p3.py test/web_api/test_work_foundation.py test/web_persistence/test_workspace_v41_p1.py test/web_persistence/test_workspace_v41_p2.py test/web_persistence/test_workspace_v41_p3.py test/web_persistence/test_resource_account_integrity.py -q
```

使用专用可丢弃 PostgreSQL `hpagent_ui4_test_20261007` / `hpagent_ui4_e2e_20261007`（前三轮）/ `hpagent_ui4_e2e_final_20261007`（最终全新库），migration/API/worker 三角色同库，Redis DB12，API8184/Vite5277，文件目录 `/tmp/hpagent-ui4-files-20261007`。未清理业务数据库，未安装新依赖。凭据不收录；浏览器 trace 含测试登录值及测试正文，只留本地 Playwright 临时目录，不纳入此证据目录。

## 失败记录与复验

- [unit.log](logs/unit.log)：首次完整单测 232 通过、3 项 5 秒超时；该轮本机同时运行隔离基础设施。随后 [unit-second.log](logs/unit-second.log) 242 全部通过。最后新增撤权快照在途去重回归，再运行完整门禁。
- [e2e-initial.log](logs/e2e-initial.log)：30 通过、6 失败。目录深链由再点击当前 Rail 重置目录导致，已修复；旧资料入口断言迁移；其他超时/DOM detached 与该轮源代码热更新及资源竞争同批发生，保留原记录，不直接当作通过。
- [e2e-second.log](logs/e2e-second.log)：35 通过、2 失败。Run Inspector 关闭后焦点被文件新增逻辑移到 Canvas，修复为仅 File 使用触发行回退。跨账户 trace 显示 Alice/Bob 隔离及两次运行断言都已完成，60 秒期限在 context teardown 前耗尽；该双账户用例单独 `test.slow()`，保留全部断言。最终稳定代码再运行完整矩阵。

- [e2e-third.log](logs/e2e-third.log)：36 通过、1 失败；此前两轮通过的 Artifact 用例在复用测试库一直得到 queued 快照，15 秒内未产生 iframe，失败发生在保存入口前。未改写 Artifact store 或后端执行器；最终改用全新可丢弃库，37 项完整回归全部通过。旧库只读检查还存在 queued reminder/research Run，不能用该轮结果宣称 Artifact 流程通过。

## 三条操作轨迹

### 成功

UI-4 目录用例在根目录新建子目录 → 进入后上传文本 → 保存完成显示“未自动授权” → 刷新保留目录 URL → 文件只在直接子项出现 → 打开唯一 Inspector 读取真实文本 → 浏览器返回关闭 Inspector → 返回根目录看不到该子文件 → 全部文件能找到它。独立上传用例及活动对话用例通过 API 读回证明默认无 grant。

### 失败恢复

浏览器将真实上传 PUT 的响应丢弃（`route.fetch()` 先使服务端收到内容，再 abort 响应）→ 显示错误并保留 initialized file_id/key → 点击继续 → 先查 metadata ready → 保存。断言 PUT=1、save POST=1。另一用例在保存后阻断首次 grant → 入口仍可见 → 关闭/重开上传记录 → 继续原操作仅补授权，PUT/save 各仍为1，权威读回恰有两条读取规则。新对话授权失败重试只创建一次，对 `/messages` 的 POST=0。

### 并发/迟到

查询测试固定旧搜索页请求，应用新条件后才完成旧页：结果不混合；正文 A→B→A 第一次 A 迟到不能覆盖新 A；published-files 换 Run 后旧 Run 响应被拒绝；旧源保存完成不能关闭新源弹窗。账户 reset 后迟到保存不回填缓存或恢复指针。撤权快照测试同时通知重复 Run ID，GET 在途只发一次；页面隐藏不发 snapshot；5 个 Run 上限3路；切账户后旧3个 snapshot 完成不解锁或写入新账户状态；只有 cancelled snapshot 才显示“执行已结束”。

## 规模记录

`WorkspaceScale` 使用根节点、100个目录与1000/10000个文件的合成树。数据来自单次本机 jsdom 验证，不能视为持续压力测试，也未渲染“全部文件”10000行来证明无卡顿。

| 文件数/总节点 | 索引耗时 | 初始导航及当前目录渲染 | 当前文件行 | tree GET | metadata GET |
| --- | --- | --- | --- | --- | --- |
| 1000 / 1101 | 20.38 ms | 210.25 ms | 10 | 1 | 0 |
| 10000 / 10101 | 162.35 ms | 367.63 ms | 100 | 1 | 0 |

索引按同一树对象缓存复用，没有逐行 metadata。后端仍返回全量树，服务端分页/懒加载保留 BE-W3；[index-scale.log](logs/index-scale.log) 是更早的纯索引冷启动测量，不用其大小顺序反推性能曲线。

## 截图

列表与 Inspector 开关各覆盖 360×800、390×844、768×1024、1024×768、1280×800、1440×900、1920×1080，共14张。另有减少动效、200%文本大小、桌面/手机空目录及不可用文件，共20张。

- [桌面空间](workspace-1440x900.png)、[桌面 Inspector](inspector-1440x900.png)
- [手机空间](workspace-390x844.png)、[手机 Inspector](inspector-390x844.png)
- [200%文本](inspector-text-200-percent.png)、[reduced-motion](inspector-reduced-motion.png)
- [空目录桌面](workspace-empty-desktop.png)、[空目录手机](workspace-empty-mobile.png)
- [不可用对象桌面](file-unavailable-desktop.png)、[不可用对象手机](file-unavailable-mobile.png)

Browser 用例检查横向溢出、Tabs 键盘、权限草稿放弃确认、关闭焦点与 Esc；查看截图确认长标题换行和移动面板。仍未覆盖人工读屏/完整辅助技术、真实手机软键盘、真实模型/渠道、独立 Temporal 撤权执行。Fake Executor 与截图不构成这些环境的通过证明。
