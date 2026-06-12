#!/usr/bin/env python3
"""
合并渠道账号 —— 将不同渠道的身份绑定到同一个 account_id。

用法:
  # 按 account_id 追加绑定
  python scripts/merge-account.py --account <account_id> --bind <channel_type>:<channel_user_id>

  # 让两个已有的渠道绑定合并（把 source 的绑定迁到 target）
  python scripts/merge-account.py \
    --source-channel napcat:2109279314 \
    --target-channel official_qq:79E1A6C2416959EA4AD9911A1FD475D1

示例 —— 将 NapCat 老账号的 QQ 号绑定合并到 OfficialQQ 新账号下:
  python scripts/merge-account.py \
    --source-channel napcat:2109279314 \
    --target-channel official_qq:79E1A6C2416959EA4AD9911A1FD475D1
"""
import argparse
import json
import sys
from pathlib import Path


def load(filepath: Path) -> dict:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def save(filepath: Path, data: dict):
    filepath.parent.mkdir(parents=True, exist_ok=True)
    tmp = filepath.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(filepath)
    print(f"  saved → {filepath}")


def find_by_binding(data: dict, ch_type: str, ch_uid: str) -> str | None:
    for account_id, account in data.items():
        bindings = account.get("bindings", {})
        if bindings.get(ch_type) == ch_uid:
            return account_id
    return None


def main():
    parser = argparse.ArgumentParser(description="合并 HpAgent 渠道账号")
    parser.add_argument("--file", default=".data/accounts.json",
                        help="accounts.json 路径 (默认 .data/accounts.json)")
    parser.add_argument("--account", "-a",
                        help="目标的 account_id")
    parser.add_argument("--bind", "-b",
                        help="新增绑定: channel_type:channel_user_id (如 official_qq:ABCD1234)")
    parser.add_argument("--source-channel",
                        help="源渠道绑定: channel_type:channel_user_id (如 napcat:2109279314)")
    parser.add_argument("--target-channel",
                        help="目标渠道绑定: channel_type:channel_user_id (如 official_qq:ABCD1234)")
    args = parser.parse_args()

    filepath = Path(args.file)
    if not filepath.exists():
        print(f"[ERROR] 文件不存在: {filepath}")
        sys.exit(1)

    data = load(filepath)
    print(f"当前账号数: {len(data)}")
    for aid, acct in data.items():
        print(f"  {aid}: {acct.get('bindings', {})}")

    import time

    if args.source_channel and args.target_channel:
        # 合并两个已有绑定
        src_type, src_uid = args.source_channel.split(":", 1)
        tgt_type, tgt_uid = args.target_channel.split(":", 1)

        src_account_id = find_by_binding(data, src_type, src_uid)
        tgt_account_id = find_by_binding(data, tgt_type, tgt_uid)

        if not src_account_id:
            print(f"[ERROR] 源绑定未找到: {src_type}:{src_uid}")
            sys.exit(1)
        if not tgt_account_id:
            print(f"[ERROR] 目标绑定未找到: {tgt_type}:{tgt_uid}")
            sys.exit(1)
        if src_account_id == tgt_account_id:
            print("[INFO] 两个绑定已属于同一账号，无需合并")
            return

        # 把源账号的所有绑定迁到目标账号，删除源账号
        src_account = data.pop(src_account_id)
        dst_account = data[tgt_account_id]

        print(f"\n合并 {src_account_id} → {tgt_account_id}")
        print(f"  源绑定了: {src_account.get('bindings', {})}")
        print(f"  目标已绑定: {dst_account.get('bindings', {})}")

        dst_account.setdefault("bindings", {})
        for ch, uid in src_account.get("bindings", {}).items():
            dst_account["bindings"][ch] = uid
        dst_account["updated_at"] = time.time()

        print(f"  合并后: {dst_account.get('bindings', {})}")
        save(filepath, data)

        print(f"\n合并完成。account_id = {tgt_account_id}")

    elif args.account and args.bind:
        # 为指定账号追加绑定
        ch_type, ch_uid = args.bind.split(":", 1)
        account_id = args.account

        if account_id not in data:
            print(f"[ERROR] 账号不存在: {account_id}")
            sys.exit(1)

        existing = find_by_binding(data, ch_type, ch_uid)
        if existing:
            print(f"[WARN] {ch_type}:{ch_uid} 已绑定到 {existing}")
            sys.exit(1)

        data[account_id].setdefault("bindings", {})[ch_type] = ch_uid
        data[account_id]["updated_at"] = time.time()

        print(f"绑定 {ch_type}:{ch_uid} → {account_id}")
        save(filepath, data)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
