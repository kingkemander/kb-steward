#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""流转台账：每个包到谁手上、走到哪一步

不靠人记，靠三件事：
1. 包一生成就自动登记（created）
2. 你说一句「给经理了」→ mark sent；经理回包 → 自动把上游标 merged
3. 随时 status 查全链路

台账放在工作区根：流转台账.json（不含正文、不含绝对路径以外的敏感信息）
"""
import argparse
import datetime
import json
import os
import sys

LEDGER = "流转台账.json"
STAGES = {"上报包": "个人→部门", "部门融合包": "部门→公司", "全公司目录": "公司→全员"}


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def default_root():
    """台账全局唯一：~/.kb-steward/，所有整理任务共用，查状态不用记路径。"""
    return os.environ.get("KB_STEWARD_HOME") or os.path.join(os.path.expanduser("~"), ".kb-steward")


def load(root):
    os.makedirs(root, exist_ok=True)
    p = os.path.join(root, LEDGER)
    if os.path.isfile(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {"schema": "kb-steward/flow@1.0", "items": []}


def save(root, d):
    p = os.path.join(root, LEDGER)
    tmp = p + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def find(root, key):
    d = load(root)
    for it in d["items"]:
        if key in (it.get("id"), it.get("file")) or key in os.path.basename(it.get("file", "")):
            return d, it
    return d, None


def cmd_register(args):
    root = os.path.abspath(args.root)
    d = load(root)
    fid = args.id or f"{args.type}-{os.path.basename(args.file)}"
    if any(x.get("id") == fid for x in d["items"]):
        print(f"已登记：{fid}")
        return 0
    d["items"].append({
        "id": fid, "type": args.type, "file": args.file,
        "stage": STAGES.get(args.type, ""),
        "from": {"name": args.frm, "dept": args.dept, "role": args.role},
        "to": {"name": args.to, "role": args.to_role},
        "status": "created",
        "created_at": now(),
        "history": [{"at": now(), "by": args.frm or "系统", "action": "生成", "note": ""}],
    })
    save(root, d)
    print(f"已登记：{fid}（{args.type}）→ 等你说「已发给{args.to or '对方'}」")
    return 0


def cmd_mark(args):
    root = os.path.abspath(args.root)
    d, it = find(root, args.package)
    if not it:
        print(f"台账里没有：{args.package}\n用 flow.py list 看已登记的有哪些")
        return 2
    it["status"] = args.action
    it.setdefault("history", []).append(
        {"at": now(), "by": args.by or "我", "action": args.action, "note": args.note or ""})
    if args.action == "sent" and args.by:
        it["sent_by"] = args.by
    save(root, d)
    print(f"{it['id']}：{args.action}（{args.by or '我'}）")
    return 0


def cmd_status(args):
    root = os.path.abspath(args.root)
    d = load(root)
    items = d.get("items") or []
    if not items:
        print("台账是空的。第一次生成上报包/部门包时会自动登记。")
        return 0
    order = {"created": 1, "sent": 2, "received": 3, "merged": 4, "signed": 5, "broadcast": 6}
    label = {"created": "已生成（未发出）", "sent": "已发出（等对方）", "received": "对方已收",
             "merged": "已融合", "signed": "已签核", "broadcast": "已广播回流"}
    lines = []
    for it in sorted(items, key=lambda x: (order.get(x.get("status"), 9), x.get("created_at", ""))):
        st = it.get("status", "created")
        lines.append({
            "包": it.get("id"), "环节": it.get("stage"), "状态": label.get(st, st),
            "从": (it.get("from") or {}).get("name") or "",
            "给": (it.get("to") or {}).get("name") or "",
            "时间": (it.get("created_at") or "")[:16],
        })
    if args.json:
        print(json.dumps(lines, ensure_ascii=False, indent=2))
        return 0
    print(f"{'包':38} {'环节':10} {'状态':16} {'从':6} {'给':6}")
    for r in lines:
        print(f"{r['包'][:36]:38} {r['环节']:10} {r['状态']:16} {r['从']:6} {r['给']:6}")
    # 一眼看出卡在哪
    waiting = [r for r in lines if r["状态"] == "已生成（未发出）"]
    if waiting:
        print(f"\n卡住 {len(waiting)} 个：生成了但还没发。你说「XX 已发给 YY」我就推进。")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="", help="台账目录，默认 ~/.kb-steward")
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("register")
    r.add_argument("--type", required=True, choices=list(STAGES))
    r.add_argument("--file", required=True)
    r.add_argument("--id", default="")
    r.add_argument("--from", dest="frm", default="")
    r.add_argument("--dept", default="")
    r.add_argument("--role", default="")
    r.add_argument("--to", default="")
    r.add_argument("--to-role", default="")

    m = sub.add_parser("mark")
    m.add_argument("--package", required=True)
    m.add_argument("--action", required=True,
                   choices=["sent", "received", "merged", "signed", "broadcast"])
    m.add_argument("--by", default="")
    m.add_argument("--note", default="")

    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")

    args = ap.parse_args()
    args.root = args.root or default_root()
    if args.cmd == "register":
        return cmd_register(args)
    if args.cmd == "mark":
        return cmd_mark(args)
    return cmd_status(args)


if __name__ == "__main__":
    sys.exit(main())
