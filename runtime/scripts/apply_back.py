#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个人回流落地：把「回流-我.json」变成我本机可执行的清单

输出（默认写进 --out，即个人整理目录）：
  40-我的待办.md          按优先顺序：移抽屉/补主体/补日期/改名/不上传
  41-公司目录位次.csv      我的每份文件 → 公司目录哪个抽屉、最终叫什么
  42-回流改名映射.csv      只含需要改名的（原名 → 公司终名）
  43-回流对账.csv          我本地的 vs 公司目录的差异（公司有我没有 / 我有公司没收）

不自动改名、不自动移动——只出清单，用户说「执行」才动。
"""
import argparse
import csv
import json
import os
import sys


def load_person(path):
    d = json.load(open(path, encoding="utf-8"))
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="个人整理目录（写产物的地方）")
    ap.add_argument("--backlog", required=True, help="回流-<姓名>.json")
    ap.add_argument("--mine", default="", help="我本地的 17-终稿索引包.json，用于对账")
    args = ap.parse_args()

    OUT = os.path.abspath(args.out)
    os.makedirs(OUT, exist_ok=True)
    pkg = load_person(args.backlog)
    who = pkg.get("for") or {}
    items = pkg.get("items") or []

    order = {"移到抽屉": 1, "改主体": 2, "补主体": 2, "改日期": 3, "补日期": 3,
             "改名为": 4, "不上传": 5, "可删可留": 8, "无需改动": 9}
    todo = []
    for it in items:
        for a in it.get("actions") or []:
            if a["do"] in ("无需改动",):
                continue
            todo.append((order.get(a["do"], 5), a["do"], it, a))
    todo.sort(key=lambda x: x[0])

    lines = [f"# 我的待办 · {who.get('name','')}（{who.get('dept','')}）", "",
             f"公司目录版本 {pkg.get('catalog_version')} ｜ 条目 {pkg.get('counts',{}).get('total')}"
             f" ｜ 需处理 {pkg.get('counts',{}).get('need_action')}", "",
             "> 只列清单，不动文件。确认后再说「执行改名」。", ""]
    cur = None
    for _, do, it, a in todo:
        if do != cur:
            cur = do
            lines += [f"## {do}", ""]
        name = os.path.basename(it.get("source_path") or "")
        detail = ""
        if a.get("from") or a.get("to"):
            detail = f"（{a.get('from') or '空'} → {a.get('to') or '空'}）"
        extra = ""
        if it.get("merged_with"):
            extra = f"　与 {'、'.join(it['merged_with'])} 重复，保留一份"
        if it.get("manager_revisions"):
            r = it["manager_revisions"][0]
            extra += f"　经理改过：{r.get('field')} {r.get('from')}→{r.get('to')}（{r.get('reason','')}）"
        lines.append(f"- `{name}` {detail}{extra}")
    if not todo:
        lines.append("- 无需改动，已与公司目录一致。")
    open(os.path.join(OUT, "40-我的待办.md"), "w", encoding="utf-8").write("\n".join(lines))

    with open(os.path.join(OUT, "41-公司目录位次.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["我的文件", "sha256", "公司抽屉", "公司终名", "公司主体", "公司日期", "状态"])
        for it in items:
            cf = it.get("company_final") or {}
            w.writerow([os.path.basename(it.get("source_path") or ""), it.get("sha256"),
                        it.get("folder"), cf.get("suggested_name"), cf.get("subject"),
                        cf.get("date"), cf.get("action")])

    with open(os.path.join(OUT, "42-回流改名映射.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["原文件名", "公司终名", "sha256"])
        n = 0
        for it in items:
            cf = it.get("company_final") or {}
            if cf.get("action") not in ("rename", "keep_name"):
                continue
            old = os.path.basename(it.get("source_path") or "")
            new = cf.get("suggested_name") or ""
            if new and new != old:
                w.writerow([old, new, it.get("sha256")])
                n += 1

    # 对账
    diff = {"company_only": [], "mine_only": []}
    if args.mine and os.path.isfile(args.mine):
        mine = json.load(open(args.mine, encoding="utf-8"))
        my_hashes = {i.get("sha256") for i in mine.get("items", []) if i.get("sha256")}
        co_hashes = {it.get("sha256") for it in items if it.get("sha256")}
        diff["company_only"] = sorted(co_hashes - my_hashes)
        diff["mine_only"] = sorted(my_hashes - co_hashes)
    with open(os.path.join(OUT, "43-回流对账.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["类型", "sha256", "说明"])
        for h in diff["company_only"]:
            w.writerow(["公司有我没有", h, "可能是别人的同名件，或我这版删了"])
        for h in diff["mine_only"]:
            w.writerow(["我有公司没收", h, "可能被判不入库/敏感，或新加还没上报"])

    print(json.dumps({"for": who, "total": len(items), "todo": len(todo),
                      "renames": n,
                      "outputs": ["40-我的待办.md", "41-公司目录位次.csv",
                                  "42-回流改名映射.csv", "43-回流对账.csv"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
