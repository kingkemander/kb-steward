#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""总目录广播：把全公司目录按「人」拆成回流包

输入：30-全公司目录.json（里面每条带 origins_all：谁上报的、他原来是什么）
输出：03-回流/回流-<姓名>.json  每人一份
      - 我的文件在公司目录里的位置（抽屉/新名/主体/日期）
      - 我需要做什么：移抽屉 / 改主体 / 补日期 / 改名 / 不上传 / 与谁重复
      - 我这次没进目录的（被合并/判不入库）
     03-回流/广播清单.csv        谁有几条待办
"""
import argparse
import collections
import csv
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import categories

LABEL = categories.LABEL()


def diff_actions(origin, final):
    """对比「你原来交的」和「公司终版」，列出这个人要做的事。"""
    acts = []
    o, f = origin or {}, final or {}
    if (f.get("action") == "hold_sensitive"):
        return [{"do": "不上传", "why": "敏感件：只登记不出库"}]
    if (f.get("action") == "no_ingest"):
        return [{"do": "可删可留", "why": "工具/中间产物，不进公司目录"}]

    if (o.get("jd") or "") != (f.get("jd") or ""):
        acts.append({"do": "移到抽屉", "from": f'{o.get("jd")} {LABEL.get(o.get("jd"), "")}'.strip(),
                     "to": f'{f.get("jd")} {LABEL.get(f.get("jd"), "")}'.strip()})
    if not (o.get("subject") or "").strip() and (f.get("subject") or "").strip():
        acts.append({"do": "补主体", "to": f.get("subject")})
    elif (o.get("subject") or "") != (f.get("subject") or "") and (f.get("subject") or ""):
        acts.append({"do": "改主体", "from": o.get("subject"), "to": f.get("subject")})
    if not re.fullmatch(r"\d{4}(-\d{2})?(-\d{2})?", (o.get("date") or "")):
        if (f.get("date") or ""):
            acts.append({"do": "补日期", "to": f.get("date")})
    elif (o.get("date") or "") != (f.get("date") or "") and (f.get("date") or ""):
        acts.append({"do": "改日期", "from": o.get("date"), "to": f.get("date")})
    if (o.get("suggested_name") or "") != (f.get("suggested_name") or "") and (f.get("suggested_name") or ""):
        acts.append({"do": "改名为", "to": f.get("suggested_name")})
    if not acts:
        acts.append({"do": "无需改动", "why": "已与公司目录一致"})
    return acts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--catalog", default="", help="默认 <out>/30-全公司目录.json")
    ap.add_argument("--dest", default="", help="回流包输出目录，默认 <out>/03-回流")
    args = ap.parse_args()

    OUT = os.path.abspath(args.out)
    cat_path = args.catalog or os.path.join(OUT, "30-全公司目录.json")
    if not os.path.isfile(cat_path):
        print("找不到 30-全公司目录.json，先跑 merge_company.py")
        return 2
    cat = json.load(open(cat_path, encoding="utf-8"))
    dest = os.path.abspath(args.dest or os.path.join(OUT, "03-回流"))
    os.makedirs(dest, exist_ok=True)

    people = collections.defaultdict(list)
    for it in cat.get("items", []):
        final = it.get("final") or {}
        for o in it.get("origins_all") or []:
            name = (o.get("owner_name") or "").strip() or "未署名"
            people[name].append({
                "source_path": o.get("source_path"),
                "sha256": o.get("sha256"),
                "my_original": {"jd": o.get("jd"), "subject": o.get("subject"),
                                "date": o.get("date"), "suggested_name": o.get("suggested_name")},
                "company_final": {"jd": final.get("jd"), "subject": final.get("subject"),
                                  "date": final.get("date"),
                                  "suggested_name": final.get("suggested_name"),
                                  "action": final.get("action")},
                "folder": f'{final.get("jd")} {LABEL.get(final.get("jd"), "未分类")}'.strip(),
                "actions": diff_actions(o, final),
                "merged_with": [x for x in (it.get("owners") or []) if x and x != name],
                "dept": o.get("dept") or it.get("_dept") or "",
                "manager_revisions": o.get("revisions") or [],
            })

    rows = []
    for name, items in sorted(people.items()):
        todo = [i for i in items if any(a["do"] not in ("无需改动",) for a in i["actions"])]
        pkg = {
            "schema": "kb-steward/person-backlog@1.0",
            "flow": {
                "schema": "kb-steward/flow@1.0", "type": "个人回流包",
                "stage": "公司→个人",
                "from": {"name": (cat.get("flow") or {}).get("from", {}).get("name") or "", "role": "G"},
                "to": {"name": name, "role": "E"},
                "status": "broadcast",
                "history": [{"at": datetime.datetime.now().isoformat(timespec="seconds"),
                             "by": (cat.get("flow") or {}).get("from", {}).get("name") or "总经理",
                             "action": "广播回流", "note": cat.get("version")}],
            },
            "company": cat.get("company"),
            "catalog_version": cat.get("version"),
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "for": {"name": name, "dept": items[0].get("dept") or ""},
            "counts": {"total": len(items), "need_action": len(todo),
                       "no_action": len(items) - len(todo)},
            "items": items,
        }
        fn = os.path.join(dest, f"回流-{name}.json")
        json.dump(pkg, open(fn, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        rows.append({"姓名": name, "部门": pkg["for"]["dept"], "条目": len(items),
                     "待办": len(todo), "回流包": os.path.basename(fn)})

    with open(os.path.join(dest, "广播清单.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["姓名", "部门", "条目", "待办", "回流包"])
        w.writeheader()
        w.writerows(rows)

    try:
        sys.path.insert(0, HERE)
        import flow as flowmod
        ledger_root = flowmod.default_root()
        ld = flowmod.load(ledger_root)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        for it in ld["items"]:
            if it.get("type") == "全公司目录" and it.get("status") != "broadcast":
                it["status"] = "broadcast"
                it.setdefault("history", []).append(
                    {"at": stamp, "by": "总经理", "action": "已广播回流", "note": cat.get("version")})
        for r in rows:
            pid = f"回流包-{r['姓名']}-{cat.get('version')}"
            if not any(x.get("id") == pid for x in ld["items"]):
                ld["items"].append({
                    "id": pid, "type": "个人回流包", "file": r["回流包"], "stage": "公司→个人",
                    "from": {"name": cat.get("flow", {}).get("from", {}).get("name") or "", "dept": "", "role": "G"},
                    "to": {"name": r["姓名"], "role": "E"}, "status": "broadcast",
                    "created_at": stamp,
                    "history": [{"at": stamp, "by": "总经理", "action": "已广播回流",
                                 "note": f"{cat.get('version')}，待办 {r['待办']} 条"}],
                })
        flowmod.save(ledger_root, ld)
    except Exception:
        pass

    msg = (f"【总目录广播】{cat.get('company') or '公司'}｜kb-steward/company-catalog@1.0｜"
           f"版本 {cat.get('version')}｜部门 {'、'.join(cat.get('departments') or [])}｜"
           f"请各人打开自己的回流包，按待办改抽屉/主体/日期/文件名，改完重新上报")
    open(os.path.join(dest, "广播消息.txt"), "w", encoding="utf-8").write(msg + "\n")

    print(json.dumps({"version": cat.get("version"), "people": len(rows),
                      "dest": dest, "message": msg}, ensure_ascii=False, indent=2))
    for r in rows[:8]:
        print(f"  {r['姓名']}（{r['部门']}）{r['条目']} 条，待办 {r['待办']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
