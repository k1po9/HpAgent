# W2-E closure fix

Baseline：`d4a8ef493a4e7120eeca32cf6949c9470cae266d`。仅修复用户指定的四项 finding；未执行 W3 删除、W4 cleanup、package move/rename。冻结 phase2_2 不修改，原有 phase2_1 用户工作区修改不纳入提交。

## Findings / root cause / fix / regression

| Finding | Root cause | Fix | Regression |
| --- | --- | --- | --- |
| Official QQ inbound dedup | `_handle_dispatch` 在 callback 前将 provider ID 放进 `_sent_msg_ids`；callback 异常或 DB 未提交后，同 ID 被吞掉；且与 outgoing cache 共用语义 | 删除 inbound cache 读写，每次合法消息均进入 callback；出站成功缓存保留并明确仅 outgoing 使用，入站重复结果交由 PG receipts | 参数化两种首次失败：callback 抛错、真实不可用 DB；第二和第三次同 ID 均进入 callback，最终恰好一个 Run、一个 start_run Outbox、一个 ingress receipt |
| NapCat media provenance | parser 仅写 `_images` / `_image_count`，canonical origin whitelist 不接收这些私有字段 | parser 同时写现有 `image_urls`，经既有 whitelist 进入 Message.origin；保存 URL 或 provider file reference，不下载，不接 File Domain | 真实 NapCat normalize → ingress → PG，断言 `origin.metadata.image_urls` 同时包含 URL 与无 URL 的 provider reference |
| delivery last_error | 成功 chunk 后把 delivery state 转回 pending，再用 state 推导 error，误把正常等待当失败 | 在推进 delivery 状态前从发送结果生成 error：成功为 NULL，明确发送失败为 send_failed，不确定为 uncertain；state/next_part 规则不变 | 多 chunk 首段成功后 `(next_part,state,last_error)=(1,pending,NULL)`，清除历史 error；失败返回 pending/send_failed，保留重试及完成回归 |
| W2 evidence count | 将主执行 184 个 collection 用例与后续 15 项 targeted Outbox 运行次数相加，未经 node ID 去重便宣称 199 distinct | 撤回历史 199 distinct 声明，不累加 targeted rerun；本轮保存当前脚本真实 collection 与完整运行日志 | 当前 collection 为 202 unique node IDs，见机器可核查清单；以本轮完整执行结果验 Gate |

## Count reconciliation

- 历史主执行日志是 183 passed + 1 failed，后续修正单项复验通过，主 collection 为 **184 distinct**。15 项 Outbox targeted 输出不再加入历史 distinct 总数。
- 当前已提交的 `scripts/verify_w2_contracts.py` 默认列表包含 `test_outbox_and_lifecycle.py` 的 15 个 node IDs。此前报告对应主执行与后来提交的脚本范围不同，不能拿当前 collection 反推历史执行总数。
- 本轮未改变 verifier scope，实际 collection 为 **202 unique node IDs**：当前基线 199 + 新增 3（Official 首次失败两种参数 + NapCat media）。已有 chunk 用例增加断言，不新增 node ID。
- [collection 原始输出](W2_E_collection.txt)、[唯一 node ID 清单与计数](W2_E_collection.json)。`collected=distinct=202`，无重复 node ID。完整执行和 targeted 重复用例不再累加。

## Validation

Targeted：

```text
.venv/bin/python scripts/verify_w2_contracts.py test/web_persistence/test_qq_canonical_ingress.py test/web_persistence/test_qq_delivery.py test/test_qq_delivery_adapter.py
```

完整：`.venv/bin/python scripts/verify_w2_contracts.py`。

Targeted：**16 passed，179.57 秒，无 skip**，见 [targeted](W2_E_targeted_validation.txt)。完整重跑：**202 passed，951.05 秒，无失败/skip**，见 [full](W2_E_validation.txt)。进程退出码 0；唯一 warning 为既有 Starlette/httpx 弃用提示。202 与 collection 的唯一 node ID 数一致；不累加 targeted 16 项。

修改的 5 个 Python 文件使用项目 Ruff 默认 F/I 规则检查，通过；`git diff --check` 通过，见 [static validation](W2_E_static_validation.txt)。真实 PG/Redis/Temporal 隔离 fixture，QQ 外网与 Hindsight/模型使用受控替身；不扩大为外网验收。

首次完整执行被环境中断：测试 PG 返回 `AdminShutdown`，随后 PG/Redis 测试容器消失，Temporal crash 用例亦失败。原输出为 127 passed / 2 failed / 73 errors，保留在 [interrupted validation](W2_E_interrupted_validation.txt)，不计为通过证据。环境恢复后使用新隔离容器完整重跑，未为环境失败修改产品代码。

## Gates

**G06 PASS / W2 EXIT PASS / W3 MAY START。** 本轮四项 finding 已关闭，targeted、完整 202 项、Ruff F/I 和 diff 检查通过。W3 MAY START 仅表示下一 Session 的前置条件满足；本次未执行 W3/W4。
