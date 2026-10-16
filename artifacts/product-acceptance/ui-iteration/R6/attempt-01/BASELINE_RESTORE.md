# 基线与恢复边界

起始240/240与R5冻结清单一致，无新增基线差异。before-source按文件保留真实工作区；BEFORE_FINGERPRINTS.json为R5接受清单；diffs/worktree.patch和staged.patch包含开始时已有未提交成果，绝不能把其整体撤销。

R6恢复只针对implementation.patch列出的文件。先备份当前文件/新修改，并逐份核对SOURCE_FINGERPRINTS.json；如之后有其他修改，手工合并或只反向应用确认属于R6的hunk。不要git reset、git clean、checkout HEAD或整份回放起始Git补丁。before-source不包含新增WorkspaceBoundary.test.tsx，删除它也需先保存后续修改。

SOURCE_FINGERPRINT.txt为候选快照汇总，不是接受决定。R5冻结清单/原快照未改写。新字段仅前端状态；无DB迁移或后端恢复步骤。

隔离恢复仅针对已核实原hpagent-r416三容器；scripts/restart-isolation.py核对原ID/image/internal/test标志，restore-missing-test-data.py拒绝覆盖任何已存在测试库。来源为已指纹R5.0专用recovery，不得替换成日常产品库。详见HANDOFF。
