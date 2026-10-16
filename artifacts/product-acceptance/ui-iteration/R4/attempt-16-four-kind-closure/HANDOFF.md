# R4 四类组合收口交接 · attempt-16

当前停 **R4 整体待人审**。用户“通过，下一轮”接受Task attempt-15，并授权本轮四类组合/R1–R3回归/真实隔离API。File与Task分别已接受；R4整轮尚未接受；R5/R6未启动。不得自动提交、推送、部署。

## 当前源码和证据

工作目录`/home/hp/workspace/HpAgent`，HEAD `9d47e55`。生产源码本轮无新增差异；209份源码/配置指纹`3f157303d1526c5357cfb899fe445035e01bc3c0d78926dd4240a80d573b5758`，与已接受Task及生产JS/CSS/index完全一致。当前Git dirty是已接受File/Task成果，不能reset或用HEAD覆盖。

before-source为226份修改前备份，source-snapshot为209份当前源码/配置，inputs保留原始输入，diffs包含开始/最终工作区差异和空本轮implementation.patch。见BASELINE_RESTORE。根目录SOURCE_FINGERPRINT和两份manifest不可用HEAD替代。

本轮556前端测试、186真实隔离后端测试、29独立原浏览器场景最终通过；Task33/File23/Artifact5/组合5组、96子页截图和14弹层截图、真实API整链/实际抵达/权限/控制确认/版本停止均通过。范围、失败与分层复测见VALIDATION_RESULTS、SELF_REVIEW；I01–I20见CONTRACT_CHECKLIST。不能把这些结果当作用户接受。

## 直接评审

- 总入口：<http://127.0.0.1:5333/REVIEW.html>。
- 5333：当前production React + 确定性只读夹具；写入409或unknown场景；无DB/供应商连接。before/after同数据16页对照、慢/晚/失败、完整离线HTML均在入口。
- 5336：实际production Vite preview→`hpagent-r416-final`；alice / e2e-password。新发送、执行、生成、修改版本、停止。FakeRun/FakeArtifact明确启用。
- 5334：实际整链原数据；r416_closure / r416-review-password。已上传/授权File、保存成果副本与计划Task。混合旧库中Reminder/Research不由Fake执行，可能占容量；新增生成请使用5336，不改业务容量。
- 5335：实际控制API；r416_controls / r416-review-password。Fake关闭，canonical Worker服务显式准备成果/丢失回执事实，验证预算、指定成果验收、投递确认。人审新pending pair见`evidence/control-review-seed.json`，自动测试原样本见`control-seed.json`。确认回执不等于满足任务的其他验收条件。
- 完整离线：`offline/AFTER.React.Standalone.html`，直接文件打开；同一生产产物、完整源快照及只读评审桥。可在URL hash选择AI/tasks/workspace。

建议顺序：正常桌面来源点击看光头→标题→正文；Task成果→原Artifact→来源Run→File→Task，再四层返回；同版本iframe交互后Tab/展开保状态；X后确认旧内容已清、壳收束；授权/要求草稿尝试Tab/返回/X并取消；窄屏native dialog/最高层Escape/减动；再在5336发送→执行→生成/版本→关闭→继续发送及停止。技术正确与体验判断分别记录。

## 已核实服务与隔离

只使用本轮新建`hpagent-r416` **internal**网络。全部运行容器只在这一网络；没有真实供应商/QQ密钥或外网出口。日常8080/5434服务检查后未复用、未修改。宿主Docker内部网络可达；内部网络忽略发布端口，Vite代理使用核实的容器IP。

容器当前IP：pg .2、redis .3、runner .4、api .5:8196、controls .6:8195、final .7:8194（前缀172.19.0）。**每次重建重新inspect，不能凭这里的IP直接连接。** `evidence/isolation-final.json`有镜像ID、挂载、网络、DB名、角色与001–059迁移checksum。

四个独立DB：`hpagent_r416`仅供会截表的backend tests；`hpagent_r416_browser`、`hpagent_r416_controls`、`hpagent_r416_final`用于各自浏览器服务。API和Worker使用真实独立受限角色；migration角色只在一次性PG内有超级权限。专用Redis无持久化，不与日常Redis共用DB。

PGDATA及api/final文件库为tmpfs（controls文件库在其容器可写层），停止相关容器会失去tmpfs数据；已保存三个浏览器库pg_dump与三个file-store tar于recovery/。不应为结束本轮停止这些正在评审的服务。所有凭证为本轮一次性测试值，见scripts/*.env；无需也不得读取产品环境密钥。

## 恢复静态入口与preview

从仓库根运行；5330/5331/5332是旧评审服务，不关闭或复用。先`ss -ltnp`核实端口占用，再仅启动缺少的服务：

```bash
python3 artifacts/product-acceptance/ui-iteration/R4/attempt-16-four-kind-closure/serve-review.py --port 5333
```

真实preview先核实容器`docker inspect hpagent-r416-final`、网络internal、env数据库名和只读挂载（不能仅凭进程名）。确认仍为本轮环境后：

```bash
cd /home/hp/workspace/HpAgent/web
PATH=/home/hp/.local/share/hpagent-review:$PATH HPAGENT_API_TARGET=http://172.19.0.7:8194 ./node_modules/.bin/vite preview --host 127.0.0.1 --port 5336 --strictPort
```

5334/5335同理替换为核实后的api .5:8196 / controls .6:8195。不要不设HPAGENT_API_TARGET而落到Vite默认8000。当前生产dist已构建并指纹一致，不需要重新构建才能评审。

## 若一次性环境已丢失

1. 不对日常库操作。`scripts/start-isolation.sh`只供**本轮同名网络/容器均不存在**时新建，非幂等；先检查Docker名字和网络。它创建全新PG/Redis与runner，安装pytest时runner先在bridge且未加载DB配置/执行仓库代码，然后切到internal；不要给API接外网。
2. 在新PG内通过canonical角色初始化后，分别创建`hpagent_r416_browser`、`hpagent_r416_controls`、`hpagent_r416_final`（owner hpagent_migrate）。使用`recovery/*.pgdump`仅还原到这些新空库，例如`cat recovery/hpagent_r416_final.pgdump | docker exec -i hpagent-r416-pg pg_restore -U hpagent_migrate -d hpagent_r416_final --exit-on-error`。不要对已有库使用clean或覆盖。
3. 使用本轮scripts/final.env、browser.env、controls.env创建各自API容器；repo只读挂载到/repo，file-store可写tmpfs，工作目录/repo，只连hpagent-r416。浏览器/final启动命令为`bash web/scripts/e2e-backend.sh`，会checksum迁移并确保alice/bob；controls为`bash -c 'APP_DATABASE_URL="$MIGRATION_DATABASE_URL" python -m persistence.migrate && python -m web_api'`，避免恢复后再自动seed。
4. 示例final创建（仓库根，确认同名容器不存在后）：

```bash
repo=/home/hp/workspace/HpAgent
review_dir="$repo/artifacts/product-acceptance/ui-iteration/R4/attempt-16-four-kind-closure"
docker run -d --name hpagent-r416-final --network hpagent-r416 --workdir /repo --env-file "$review_dir/scripts/final.env" --tmpfs /tmp/r416-file-store:rw -v "$repo:/repo:ro" --entrypoint bash hpagent/hpagent-api:offline web/scripts/e2e-backend.sh
cat "$review_dir/recovery/final-files.tar" | docker exec -i hpagent-r416-final tar -C /tmp/r416-file-store -xf -
```

用browser.env时容器名api；controls.env时容器名controls且须加入`-v "$review_dir:/evidence"`、改为第3步无seed命令。新建容器后按inspect确认internal/角色/DB/只读挂载，再启动preview。
5. 如需补充pending控制样本，显式运行`docker exec -e CONTROL_EVIDENCE_FILE=/evidence/evidence/control-review-new.json hpagent-r416-controls python /evidence/scripts/seed-controls.py`；这是Worker事实模拟，不是供应商执行。不要覆盖原test seed或旧人审记录。
6. `scripts/capture-isolation.py`可重新核实/备份**同名本轮容器**；应先比较容器身份，且把原isolation/备份另存，避免覆盖历史。Redis仅供新测试环境缓存，dump恢复不声称保留旧session，重新登录即可。

## 定向复验命令

从repo根执行浏览器脚本，使用Node22路径。脚本结果会覆盖同名报告；如复测先复制原证据到新attempt。

```bash
PATH=/home/hp/.local/share/hpagent-review:$PATH node web/scripts/r42-four-kind-check.mjs
PATH=/home/hp/.local/share/hpagent-review:$PATH node web/scripts/r42-real-versions.mjs
PATH=/home/hp/.local/share/hpagent-review:$PATH node web/scripts/r42-closure-package-smoke.mjs
```

真实版本脚本基于5336 alice最新会话中的已生成Artifact；全新空数据需要先完成发送/生成。real-controls依赖测试seed且会实际验收，不要重复对人审pending样本运行。backend测试仅`scripts/run-backend-tests.sh`中hpagent_r416，**会清测试表，绝不能改成浏览器库或日常库**。

前端检查在web：直接`./node_modules/.bin/vitest run`、`tsc -b`、`vite build`，给同一Node PATH。系统npm可能落到Windows环境，不使用。原29场景的配置为web/playwright.r416.config.ts；复测不宜在同一混合库不断创建非Fake支持Work。所有脚本快照在scripts。

## 后续授权边界

等待用户整体人审；当前无已知本轮生产回归，未测环境明确列在REMAINING_WORK。若用户指出缺陷，保留当前证据后新建attempt修复；若明确接受R4，再按验收合同记录决定并冻结。不得由本报告自动接受、启动R5/R6、提交或部署。
