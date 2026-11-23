#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出「上报包」：把个人终稿索引变成可交接的 zip（只含元数据，不含正文）

产物：
  上报包-<姓名>-<日期>.zip
    ├── handover.json   个人索引（元数据）
    ├── README.md       收件人看这一页
    └── manifest.json   sha256 校验

同时在终端打印一段可直接复制转发的标准消息。
"""
import argparse
import csv
import datetime
import hashlib
import json
import os
import re
import sys
import zipfile

SCHEMA = "kb-steward/handover@1.1"


def rel_no_user(path, root):
    p = path.replace(os.sep, "/")
    root = root.replace(os.sep, "/").rstrip("/")
    if p.startswith(root):
        p = p[len(root):].lstrip("/")
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="本次整理的目录（含 17-终稿索引包.json）")
    ap.add_argument("--root", default=os.path.expanduser("~"))
    ap.add_argument("--name", required=True)
    ap.add_argument("--dept", required=True)
    ap.add_argument("--role", default="员工")
    ap.add_argument("--to", default="", help="收件人（部门经理姓名），可为空")
    ap.add_argument("--dest", default="", help="zip 输出目录，默认同 --out")
    args = ap.parse_args()

    OUT = os.path.abspath(args.out)
    dest = os.path.abspath(args.dest or OUT)
    p17 = os.path.join(OUT, "17-终稿索引包.json")
    if not os.path.isfile(p17):
        print("找不到 17-终稿索引包.json，请先跑 finalize.py")
        return 2

    final = json.load(open(p17, encoding="utf-8"))
    # 哈希表：17 里已有完整 sha256；老产物回退到 08
    hashes, sizes = {}, {}
    p8 = os.path.join(OUT, "08-工作文档-哈希与重名.csv")
    if os.path.isfile(p8):
        for r in csv.DictReader(open(p8, encoding="utf-8-sig")):
            hashes[r["相对路径"]] = r["sha256"]
            sizes[r["相对路径"]] = r.get("大小", "")

    items = []
    sensitive = 0
    for m in final["items"]:
        flags = [f for f in (m.get("flags") or "").split(";") if f]
        if m["action"] == "hold_sensitive":
            sensitive += 1
            continue  # 敏感件不出现在上报包里
        items.append({
            "key": m.get("sha256") or hashes.get(m["from"], ""),
            "sha256": m.get("sha256") or hashes.get(m["from"], ""),
            "size": m.get("size") or sizes.get(m["from"], ""),
            "owner_name": m.get("owner_name") or args.name,
            "owner_dept": m.get("owner_dept") or args.dept,
            "owner_role": m.get("owner_role") or args.role,
            "source_path": rel_no_user(m["from"], args.root),
            "jd": m["jd"],
            "subject": m.get("subject", ""),
            "subject_type": m.get("subject_type", ""),
            "date": m.get("date", ""),
            "suggested_name": os.path.basename(m["to"]),
            "target_path": m["to"],
            "action": m["action"],
            "confidence": m.get("confidence", 0),
            "flags": flags,
        })

    handover = {
        "schema": SCHEMA,
        "from": {"name": args.name, "dept": args.dept, "role": args.role},
        "tool_version": (final.get("tool") or ""),
        "source_total": len(final.get("items", [])),
        "to": args.to,
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "items": items,
        "folders": final.get("folders") or {},
        "counts": {
            "total": len(items),
            "rename": sum(1 for x in items if x["action"] == "rename"),
            "keep_name": sum(1 for x in items if x["action"] == "keep_name"),
            "no_ingest": sum(1 for x in items if x["action"] == "no_ingest"),
            "sensitive_excluded": sensitive,
        },
        "note": "只含元数据，不含文件正文、不含绝对路径。敏感件已排除，仅报数量。",
    }

    payload = json.dumps(handover, ensure_ascii=False, indent=2).encode()
    sha = hashlib.sha256(payload).hexdigest()
    day = datetime.date.today().strftime("%Y%m%d")
    zip_name = f"上报包-{args.name}-{day}.zip"
    zip_path = os.path.join(dest, zip_name)

    readme = f"""# 上报包 · {args.name}（{args.dept} / {args.role}）

生成时间：{handover['generated_at']}
收件人：{args.to or '部门经理'}

## 里面是什么

- 共 {len(items)} 条文件索引（元数据：抽屉、主体、日期、建议名、相对路径）
- 不含文件正文、不含本机绝对路径
- 敏感件 {sensitive} 份已排除，只报数量

## 收件人怎么用

1. 打开「知识库管家」智能体
2. 说：**融合这个上报包**（把 zip 拖进来或给路径）
3. 它会去重、出冲突清单、生成部门目录

## 校验

handover.json 的 sha256：`{sha}`
"""
    manifest = {
        "schema": SCHEMA,
        "file": "handover.json",
        "sha256": sha,
        "size": len(payload),
        "generated_at": handover["generated_at"],
    }

    # 流转单：随包走，谁拿到都能看到这包从谁到谁、走过哪些步
    flow = {
        "schema": "kb-steward/flow@1.0",
        "package": zip_name,
        "type": "上报包",
        "stage": "个人→部门",
        "from": {"name": args.name, "dept": args.dept, "role": args.role},
        "to": {"name": args.to or "部门经理", "role": "M"},
        "status": "created",
        "created_at": handover["generated_at"],
        "history": [{"at": handover["generated_at"], "by": args.name, "action": "生成上报包", "note": ""}],
    }

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("handover.json", payload)
        z.writestr("README.md", readme)
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        z.writestr("flow.json", json.dumps(flow, ensure_ascii=False, indent=2))

    # 本地台账登记（你说一句就推进）
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import flow as flowmod
        ledger_root = flowmod.default_root()
        d = flowmod.load(ledger_root)
        fid = f"上报包-{args.name}-{day}"
        if not any(x.get("id") == fid for x in d["items"]):
            d["items"].append({
                "id": fid, "type": "上报包", "file": zip_name, "stage": "个人→部门",
                "from": flow["from"], "to": flow["to"], "status": "created",
                "created_at": flow["created_at"], "history": flow["history"],
            })
            flowmod.save(ledger_root, d)
    except Exception:
        pass

    msg = (f"【目录上行】{args.name}（{args.dept}/{args.role}）｜{SCHEMA}｜"
           f"载荷：{zip_name}｜需{args.to or '部门经理'}做部门融合")
    print(json.dumps({
        "zip": zip_path, "items": len(items), "sensitive_excluded": sensitive,
        "sha256": sha, "message": msg,
    }, ensure_ascii=False, indent=2))
    print("\n把下面这段和 zip 一起发给对方：\n")
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
