#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第三层：公司汇总（总经理）

输入：N 份 20-部门融合包.json（每份里已含每个人的个人记录 origin）
输出：
  30-全公司目录.json      company-catalog@1.0（带版本号、跨部门冲突、权限域）
  31-全公司目录树.md      带每个文件夹的两句概括
  32-跨部门冲突.csv
  33-总目录签核单.md      只列必须总经理拍板的
  34-变更记录.json        与上一版对比：新增/移动/改名/删除

原则：
- 以各部门包的 final 为准；origin 留着，回流时按人算「你原来是什么、现在要改成什么」
- 同一份文件（同 sha256）跨部门出现 → 合并为一条，归属按出现部门记全
- 同一抽屉里同名不同哈希 → 版本冲突，留日期最新，其余标旧版
- 权限域 = 类目 × 部门/角色，输出授权表
"""
import argparse
import collections
import csv
import datetime
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import categories

LABEL = categories.LABEL()
DOMAINS = categories.DOMAINS()


def load_pkgs(paths, pkg_dir):
    files = list(paths or [])
    if pkg_dir:
        files += sorted(glob.glob(os.path.join(pkg_dir, "20-部门融合包.json")))
        files += sorted(glob.glob(os.path.join(pkg_dir, "*.zip")))
    out = []
    for f in files:
        try:
            if f.endswith(".zip"):
                import zipfile
                with zipfile.ZipFile(f) as z:
                    cand = [n for n in z.namelist() if n.endswith("20-部门融合包.json")]
                    if cand:
                        out.append(json.loads(z.read(cand[0])))
                continue
            out.append(json.load(open(f, encoding="utf-8")))
        except Exception as e:
            print(f"读取失败 {f}: {e}")
    return out


def fmt_range(dates):
    ds = [d for d in dates if re.fullmatch(r"\d{4}(-\d{2})?(-\d{2})?", d or "")]
    return f"{min(ds)} ~ {max(ds)}" if ds else "日期待补"


def folder_meta(items):
    n = len(items)
    if not n:
        return {"count": 0, "summary": "", "ai_summary": ""}
    subj = collections.Counter((i.get("subject") or "通用") for i in items)
    exts = collections.Counter(os.path.splitext(i.get("suggested_name") or "")[1].lstrip(".").upper()
                               for i in items if os.path.splitext(i.get("suggested_name") or "")[1])
    depts = collections.Counter(i.get("owner_dept") or "未填" for i in items)
    dr = fmt_range([i.get("date") for i in items])
    return {
        "count": n,
        "subjects": [k for k, _ in subj.most_common(3)],
        "date_range": dr,
        "exts": dict(exts.most_common(5)),
        "departments": dict(depts),
        "summary": f"{n} 份 · " + "、".join(f"{k} {v}" for k, v in subj.most_common(3))
                   + f" · {dr} · 来自 " + "、".join(f"{k} {v}" for k, v in depts.most_common(3)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--packages", nargs="*", default=[])
    ap.add_argument("--packages-dir", default="")
    ap.add_argument("--company", default="")
    ap.add_argument("--manager", default="", help="总经理姓名")
    ap.add_argument("--version", default="", help="目录版本号，如 v1.0；不给则自动 +0.1")
    ap.add_argument("--prev", default="", help="上一版 30-全公司目录.json，用于出变更记录")
    ap.add_argument("--no-ai", action="store_true")
    args = ap.parse_args()

    OUT = os.path.abspath(args.out)
    os.makedirs(OUT, exist_ok=True)
    pkgs = load_pkgs(args.packages, args.packages_dir)
    if not pkgs:
        print("没有可用的部门包（20-部门融合包.json）")
        return 2

    all_items, dept_names, errors = [], [], []
    for p in pkgs:
        dept = p.get("dept") or ""
        if dept and dept not in dept_names:
            dept_names.append(dept)
        for it in p.get("items", []):
            x = dict(it)
            x["_dept"] = dept
            x["_manager"] = p.get("manager") or ""
            all_items.append(x)

    # 1) 同 sha256 跨部门合并
    by_hash = collections.defaultdict(list)
    for x in all_items:
        k = x.get("sha256") or f'{x.get("source_path")}|{x.get("suggested_name")}'
        by_hash[k].append(x)

    merged, cross_dups = [], 0
    for k, group in by_hash.items():
        if len(group) > 1:
            cross_dups += 1
        # final 为准；同哈希不同 final 时按部门数投票
        jd_votes = collections.Counter((x.get("final") or {}).get("jd") or x.get("jd") for x in group)
        jd = jd_votes.most_common(1)[0][0]
        base = next((x for x in group if ((x.get("final") or {}).get("jd") or x.get("jd")) == jd), group[0])
        item = dict(base)
        item["jd"] = jd
        item["depts"] = sorted({x["_dept"] for x in group if x["_dept"]})
        item["owners"] = sorted({(x.get("owner_name") or "") for x in group if x.get("owner_name")})
        item["_dup_count"] = len(group)
        item["_jd_conflict"] = len(jd_votes) > 1
        item["origins_all"] = []
        for x in group:
            o = dict(x.get("origin") or {})
            o["dept"] = x["_dept"]
            o["final_jd"] = (x.get("final") or {}).get("jd") or x.get("jd")
            o["manager"] = x["_manager"]
            o["revisions"] = x.get("revisions") or []
            item["origins_all"].append(o)
        merged.append(item)

    # 2) 冲突
    conflicts = []
    by_target = collections.defaultdict(list)
    for x in merged:
        if (x.get("final") or {}).get("action") == "no_ingest":
            continue
        by_target[(x["jd"], os.path.basename((x.get("final") or {}).get("suggested_name") or ""))].append(x)
    for (jd, name), group in by_target.items():
        if len(group) > 1:
            group.sort(key=lambda x: ((x.get("final") or {}).get("date") or "", x.get("sha256") or ""))
            for old in group[:-1]:
                conflicts.append({"type": "跨部门版本冲突", "jd": jd, "name": name,
                                  "keep": "/".join(group[-1].get("depts") or []),
                                  "other": "/".join(old.get("depts") or []),
                                  "action": "保留日期最新，另一份标旧版"})
    for x in merged:
        if x.get("_jd_conflict"):
            by_jd = collections.defaultdict(set)
            for o in x.get("origins_all") or []:
                by_jd[o.get("final_jd") or o.get("jd") or "?"].add(o.get("dept") or "未知部门")
            ranked = sorted(by_jd.items(), key=lambda kv: -len(kv[1]))
            keep_jd, keep_depts = ranked[0]
            other = "；".join(f"{j}（{'、'.join(sorted(d))}）" for j, d in ranked[1:])
            conflicts.append({"type": "跨部门类目争议", "jd": x["jd"],
                              "name": (x.get("final") or {}).get("suggested_name") or "",
                              "keep": f"{keep_jd}（{'、'.join(sorted(keep_depts))}）",
                              "other": other or "—",
                              "action": f"按部门票数采纳 {keep_jd}，其余进签核"})

    # 3) 目录 + 抽屉概括
    tree = collections.defaultdict(list)
    members = collections.defaultdict(list)
    for x in merged:
        f = x.get("final") or {}
        if f.get("action") == "no_ingest":
            continue
        folder = f'{x["jd"]} {LABEL.get(x["jd"], "未分类")}'
        tree[folder].append(os.path.basename(f.get("suggested_name") or ""))
        members[folder].append({**x, **f})

    folders_meta = {f: folder_meta(v) for f, v in members.items()}
    if not args.no_ai and os.environ.get("SPACEAGENTS_AUTO_API_KEY"):
        try:
            import llm
            url, key, model, _src = llm.text_endpoint()
            if key:
                import folder_summary as fs
                for folder, meta in folders_meta.items():
                    prompt = fs.PROMPT.format(
                        name=folder, count=meta["count"],
                        subjects="、".join(meta["subjects"]) or "无",
                        date_range=meta["date_range"], exts="见规则行",
                        files="\n".join(tree.get(folder, [])[:8]) or "（无）")
                    meta["ai_summary"] = fs.call_llm(prompt) or meta["summary"]
        except Exception:
            pass
    for f, m in folders_meta.items():
        m.setdefault("ai_summary", m["summary"])

    # 4) 权限域授权表
    domains = collections.defaultdict(lambda: {"items": 0, "depts": collections.Counter()})
    for x in merged:
        f = x.get("final") or {}
        if f.get("action") == "no_ingest":
            continue
        prefix = str(x["jd"])[:2]
        d = domains[prefix]
        d["items"] += 1
        for dp in x.get("depts") or []:
            d["depts"][dp] += 1
    auth_table = [{"类目前缀": p, "权限域": DOMAINS.get(p, "待定"),
                   "份数": d["items"],
                   "涉及部门": "、".join(f"{k}({v})" for k, v in d["depts"].most_common(4))}
                  for p, d in sorted(domains.items())]

    # 5) 版本与变更
    prev = {}
    if args.prev and os.path.isfile(args.prev):
        try:
            prev = json.load(open(args.prev, encoding="utf-8"))
        except Exception:
            prev = {}
    if args.version:
        version = args.version
    else:
        pv = (prev.get("version") or "v0.9").lstrip("v")
        try:
            version = "v%.1f" % (float(pv) + 0.1)
        except Exception:
            version = "v1.0"

    prev_map = {(x.get("sha256") or x.get("suggested_name")): (x.get("final") or {}) for x in prev.get("items", [])}
    cur_map = {(x.get("sha256") or (x.get("final") or {}).get("suggested_name")): (x.get("final") or {}) for x in merged}
    changelog = {"added": 0, "moved": 0, "renamed": 0, "removed": 0, "details": []}
    for k, f in cur_map.items():
        if k not in prev_map:
            changelog["added"] += 1
            continue
        old = prev_map[k]
        if old.get("jd") != f.get("jd"):
            changelog["moved"] += 1
            changelog["details"].append({"type": "移动", "name": f.get("suggested_name"),
                                         "from": old.get("jd"), "to": f.get("jd")})
        if old.get("suggested_name") != f.get("suggested_name"):
            changelog["renamed"] += 1
            changelog["details"].append({"type": "改名", "from": old.get("suggested_name"),
                                         "to": f.get("suggested_name")})
    for k in prev_map:
        if k not in cur_map:
            changelog["removed"] += 1
            changelog["details"].append({"type": "删除", "name": prev_map[k].get("suggested_name")})

    catalog = {
        "schema": "kb-steward/company-catalog@1.0",
        "company": args.company,
        "version": version,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "generated_by": {"manager": args.manager},
        "departments": dept_names,
        "counts": {
            "items": len(merged),
            "in_catalog": sum(1 for x in merged if (x.get("final") or {}).get("action") != "no_ingest"),
            "cross_dept_dups": cross_dups,
            "conflicts_blocking": len([c for c in conflicts if "版本" in c["type"] or "争议" in c["type"]]),
            "sensitive": sum(1 for x in merged if (x.get("final") or {}).get("action") == "hold_sensitive"),
        },
        "folders": folders_meta,
        "domains": auth_table,
        "conflicts": conflicts,
        "changelog": changelog,
        "items": merged,
    }
    cp = os.path.join(OUT, "30-全公司目录.json")
    json.dump(catalog, open(cp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump({"version": version, **(prev.get("changelog") or {}), "this_round": changelog},
              open(os.path.join(OUT, "34-变更记录.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    # 6) 目录树
    lines = [f"# 全公司目录 {version}", "",
             f"总经理：{args.manager or '未填'} ｜ 部门：{'、'.join(dept_names) or '未填'} ｜ "
             f"条目 {catalog['counts']['items']}（入库 {catalog['counts']['in_catalog']}）｜ "
             f"跨部门重复 {cross_dups} 组 ｜ 必签 {catalog['counts']['conflicts_blocking']} 条 ｜ "
             f"敏感 {catalog['counts']['sensitive']} 份", "", "## 目录", ""]
    for folder in sorted(tree):
        m = folders_meta.get(folder) or {}
        lines.append(f"{folder}/")
        if m.get("ai_summary") and m["ai_summary"] != m.get("summary"):
            lines.append(f"    └─ {m['ai_summary']}")
        if m.get("summary"):
            lines.append(f"       {m['summary']}")
        for n in sorted(tree[folder])[:80]:
            lines.append(f"    {n}")
        if len(tree[folder]) > 80:
            lines.append(f"    …… 另有 {len(tree[folder]) - 80} 份")
    lines += ["", "## 权限域", "", "| 类目 | 域 | 份数 | 涉及部门 |", "|---|---|---:|---|"]
    for r in auth_table:
        lines.append(f"| {r['类目前缀']}.x | {r['权限域']} | {r['份数']} | {r['涉及部门']} |")
    open(os.path.join(OUT, "31-全公司目录树.md"), "w", encoding="utf-8").write("\n".join(lines))

    # 7) 冲突 csv + 签核单
    with open(os.path.join(OUT, "32-跨部门冲突.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["type", "jd", "name", "keep", "other", "action"])
        w.writeheader()
        for c in conflicts:
            w.writerow(c)

    sign = [f"# 总目录签核单 {version}", "",
            f"只需处理这 {catalog['counts']['conflicts_blocking']} 条，其余按人回流。", ""]
    for i, c in enumerate([c for c in conflicts if "版本" in c["type"] or "争议" in c["type"]], 1):
        sign += [f"## {i}. {c['type']}", f"- 抽屉：{c['jd']}", f"- 文件：{c['name']}",
                 f"- 保留：{c['keep']}", f"- 另一份：{c['other']}",
                 f"- 建议：{c['action']}", "- 结论：[ ] 通过  [ ] 退回（原因：____）", ""]
    open(os.path.join(OUT, "33-总目录签核单.md"), "w", encoding="utf-8").write("\n".join(sign))

    print(json.dumps({"version": version, **catalog["counts"], "departments": dept_names,
                      "outputs": ["30-全公司目录.json", "31-全公司目录树.md",
                                  "32-跨部门冲突.csv", "33-总目录签核单.md", "34-变更记录.json"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
