#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部门融合：收多个「上报包」，去重、查冲突、出部门目录与签核单。

用法：
  python3 merge_dept.py --out ./部门融合 --packages 上报包-张三.zip 上报包-李四.zip
  python3 merge_dept.py --out ./部门融合 --packages-dir ./收到的上报包

判定规则（自动，不需要人逐条看）：
1. 同一份文件（同哈希）多人都有 → 合并为一条，记录来源人
2. 同哈希但抽屉不同 → 类目争议（取票数多的，记冲突）
3. 同目录同名但哈希不同 → 版本冲突（保留修改时间最新，另一份标“旧版”）
4. 低置信 <0.7 / 主体待补 / 继承待核 / 日期待核 → 进复核队列
5. 敏感件不在上报包里，只统计数量（发送端已排除）

产物：
  20-部门融合包.json
  21-部门目录树.md
  22-冲突清单.csv
  23-签核单.md
"""
import argparse
import collections
import csv
import glob
import hashlib
import json
import os
import re
import sys
import zipfile

DOMAIN = [
    ("10", "全员"), ("20", "交付部+管理层"), ("30", "行政+管理层"),
    ("40", "全员"), ("50", "交付部"), ("60", "全员"),
    ("70", "财务+管理层"), ("80", "交付部"), ("00", "管理员"),
]
LABEL = {
    "00.01": "规则与命名", "00.03": "待复核",
    "10.01": "公司资质", "10.02": "荣誉专利",
    "20.01": "合同业绩", "20.02": "投标文件", "20.03": "验收评价", "20.04": "客户清单",
    "30.01": "人员证件", "30.02": "劳动合同", "30.03": "体检培训", "30.04": "组织岗位",
    "40.01": "制度", "40.02": "审批权限", "40.03": "SOP", "40.04": "报价规则",
    "50.01": "产品资料", "50.02": "检测报告", "50.03": "TDS-MSDS", "50.04": "图纸CAD",
    "50.05": "技术方案",
    "60.01": "投标模板", "60.02": "方案模板", "60.03": "表单", "60.04": "品牌资产", "60.05": "营销内容",
    "70.01": "发票票据", "70.02": "法务文件", "70.03": "审计报告", "70.04": "税务申报",
    "80.01": "项目档案-进行中", "80.02": "项目档案-已归档",
}


def load_package(path):
    """返回 (handover, 来源文件)"""
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            data = z.read("handover.json")
            manifest = json.loads(z.read("manifest.json")) if "manifest.json" in z.namelist() else {}
        sha = hashlib.sha256(data).hexdigest()
        if manifest.get("sha256") and manifest["sha256"] != sha:
            raise ValueError(f"{path} 校验失败：清单 {manifest['sha256'][:12]} ≠ 实际 {sha[:12]}")
        return json.loads(data), os.path.basename(path)
    return json.load(open(path, encoding="utf-8")), os.path.basename(path)


def domain_of(jd):
    for prefix, dom in DOMAIN:
        if jd.startswith(prefix):
            return dom
    return "待定"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--packages", nargs="*", default=[])
    ap.add_argument("--packages-dir", default="")
    ap.add_argument("--dept", default="")
    ap.add_argument("--manager", default="")
    ap.add_argument("--no-ai", action="store_true", help="只用规则概括，不调模型")
    args = ap.parse_args()

    paths = list(args.packages)
    if args.packages_dir:
        paths += sorted(glob.glob(os.path.join(args.packages_dir, "*.zip")))
        paths += sorted(glob.glob(os.path.join(args.packages_dir, "handover*.json")))
    if not paths:
        print("没有输入包。用 --packages 或 --packages-dir 指定。")
        return 2

    OUT = os.path.abspath(args.out)
    os.makedirs(OUT, exist_ok=True)

    packages, errors = [], []
    for p in paths:
        try:
            h, src = load_package(p)
            packages.append((h, src))
        except Exception as e:
            errors.append({"file": p, "error": str(e)})

    # 汇总所有条目
    all_items = []
    for h, src in packages:
        for it in h.get("items", []):
            x = dict(it)
            x["_from"] = h.get("from", {}).get("name", "未知")
            x["_dept"] = h.get("from", {}).get("dept", "")
            x["_pkg"] = src
            all_items.append(x)

    # 1) 同哈希合并
    by_key = collections.defaultdict(list)
    for x in all_items:
        key = x.get("key") or f'{x["source_path"]}|{x["suggested_name"]}'
        by_key[key].append(x)

    merged, dups = [], 0
    for key, group in by_key.items():
        if len(group) > 1:
            dups += 1
        # 抽屉取票数最多
        jd_votes = collections.Counter(x["jd"] for x in group)
        jd = jd_votes.most_common(1)[0][0]
        base = next((x for x in group if x["jd"] == jd), group[0])
        item = dict(base)
        item["jd"] = jd
        item["_sources"] = sorted({x["_from"] for x in group})
        item["_dup_count"] = len(group)
        item["_jd_conflict"] = len(jd_votes) > 1
        merged.append(item)

    # 2) 同名同目录不同哈希 → 版本冲突
    by_target = collections.defaultdict(list)
    for x in merged:
        by_target[(x["jd"], os.path.basename(x["suggested_name"]))].append(x)

    conflicts = []
    for (jd, name), group in by_target.items():
        if len(group) > 1:
            group.sort(key=lambda x: (x.get("date") or "", x["_from"]))
            for old in group[:-1]:
                conflicts.append({
                    "type": "版本冲突", "jd": jd, "name": name,
                    "keep": group[-1]["_from"], "other": old["_from"],
                    "action": "保留日期最新，另一份标「旧版」",
                })

    for x in merged:
        if x.get("_jd_conflict"):
            conflicts.append({"type": "类目争议", "jd": x["jd"], "name": x["suggested_name"],
                              "keep": x["_from"], "other": "/".join(x["_sources"]),
                              "action": "按票数采纳，另一类目记录待总管理确认"})
        flags = x.get("flags") or []
        if float(x.get("confidence") or 0) < 0.7 or any(
                f in ("主体待补", "继承待核", "日期待核") for f in flags):
            conflicts.append({"type": "待复核", "jd": x["jd"], "name": x["suggested_name"],
                              "keep": x["_from"], "other": "",
                              "action": "打开确认主体/日期"})

    # 3) 部门目录（每个文件夹带一句概括，融合/签核时用来判断里面是什么）
    tree = collections.defaultdict(list)
    folder_members = collections.defaultdict(list)
    for x in merged:
        if x["action"] == "no_ingest":
            continue
        folder = f'{x["jd"]} {LABEL.get(x["jd"], "未分类")}'
        tree[folder].append(os.path.basename(x["suggested_name"]))
        folder_members[folder].append(x)

    def folder_meta(members):
        n = len(members)
        if not n:
            return {"count": 0, "summary": ""}
        subj = collections.Counter((m.get("subject") or "通用") for m in members)
        s_str = "、".join(f"{k} {v}" for k, v in subj.most_common(3))
        dates = [str(m.get("date") or "") for m in members
                 if re.fullmatch(r"\d{4}(-\d{2})?(-\d{2})?", str(m.get("date") or ""))]
        dr = f"{min(dates)} ~ {max(dates)}" if dates else "日期待补"
        who = collections.Counter(m.get("_from") or m.get("owner_name") or "?" for m in members)
        w_str = "、".join(f"{k} {v}" for k, v in who.most_common(3))
        return {"count": n, "subjects": [k for k, _ in subj.most_common(3)],
                "date_range": dr, "members": dict(who),
                "summary": f"{n} 份 · {s_str} · {dr} · 来自 {w_str}"}

    folders_meta = {f: folder_meta(v) for f, v in folder_members.items()}

    # AI 一句话：融合后的文件夹也必须有自然语言描述（上传前判断用）
    if not args.no_ai and os.environ.get("SPACEAGENTS_AUTO_API_KEY"):
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import folder_summary as fs
            for folder, meta in folders_meta.items():
                files = tree.get(folder, [])[:10]
                prompt = fs.PROMPT.format(
                    name=folder, count=meta["count"],
                    subjects="、".join(meta.get("subjects") or []) or "无",
                    date_range=meta.get("date_range") or "未知",
                    exts="未知",
                    files="\n".join(files) or "（无）")
                meta["ai_summary"] = fs.call_llm(prompt)
        except Exception:
            pass
    for folder, meta in folders_meta.items():
        meta.setdefault("ai_summary", "")

    dept_pkg = {
        "schema": "kb-steward/dept-catalog@1.0",
        "dept": args.dept or (packages[0][0].get("from", {}).get("dept", "") if packages else ""),
        "manager": args.manager,
        "members": sorted({h.get("from", {}).get("name", "") for h, _ in packages}),
        "packages": [{"file": src, "from": h.get("from", {}), "count": len(h.get("items", []))}
                     for h, src in packages],
        "stats": {
            "input_items": len(all_items),
            "merged_items": len(merged),
            "duplicates_merged": dups,
            "conflicts": len(conflicts),
        "blocking": len([c for c in conflicts if c["type"] in ("版本冲突", "类目争议")]),
        "review": len([c for c in conflicts if c["type"] == "待复核"]),
            "domains": {d: sum(1 for x in merged
                               if x.get("action") != "no_ingest" and domain_of(x["jd"]) == d)
                        for _, d in DOMAIN},
        },
        "items": [{k: v for k, v in x.items() if not k.startswith("_")} | {
            "sources": x.get("_sources", []),
            "dup_count": x.get("_dup_count", 1),
            "domain": domain_of(x["jd"]),
        } for x in merged],
        "folders": folders_meta,
        "errors": errors,
    }
    json.dump(dept_pkg, open(os.path.join(OUT, "20-部门融合包.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    lines = ["# 部门融合结果", "",
             f"- 部门：{dept_pkg['dept'] or '未填写'}｜经理：{args.manager or '未填写'}",
             f"- 收到上报包：{len(packages)} 份（{', '.join(dept_pkg['members'])}）",
             f"- 条目：{len(all_items)} → 合并后 {len(merged)}（跨人重复 {dups} 组）",
             f"- 冲突/待复核：{len(conflicts)} 条", "",
             "## 部门目录", "", "```"]
    for folder in sorted(tree):
        lines.append(f"{folder}/")
        meta = folders_meta.get(folder) or {}
        if meta.get("ai_summary"):
            lines.append(f"    └─ {meta['ai_summary']}")
        if meta.get("summary"):
            lines.append(f"       {meta['summary']}")
        for n in sorted(tree[folder]):
            lines.append(f"    {n}")
    lines += ["```", "", "## 权限域建议", "", "| 类目 | 域 | 份数 |", "|---|---|---:|"]
    for prefix, dom in DOMAIN:
        n = sum(1 for x in merged if x["jd"].startswith(prefix) and x.get("action") != "no_ingest")
        if n:
            lines.append(f"| {prefix}.x | {dom} | {n} |")
    if errors:
        lines += ["", "## 读取失败", ""] + [f"- {e['file']}：{e['error']}" for e in errors]
    open(os.path.join(OUT, "21-部门目录树.md"), "w", encoding="utf-8").write("\n".join(lines))

    with open(os.path.join(OUT, "22-冲突清单.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["type", "jd", "name", "keep", "other", "action"])
        w.writeheader()
        for c in conflicts:
            w.writerow(c)

    blocking = [c for c in conflicts if c["type"] in ("版本冲突", "类目争议")]
    review = [c for c in conflicts if c["type"] == "待复核"]
    by_person = collections.Counter()
    for x in merged:
        if float(x.get("confidence") or 0) < 0.7 or any(
                f in ("主体待补", "继承待核", "日期待核") for f in (x.get("flags") or [])):
            by_person[x["_from"]] += 1

    sign = ["# 部门签核单", "",
            f"部门：{dept_pkg['dept'] or '未填写'}　经理：{args.manager or '未填写'}", "",
            f"## 必须签核（{len(blocking)} 条）", "",
            "| # | 类型 | 文件 | 建议 | 决定（通过/退回） |", "|---|---|---|---|---|"]
    for i, c in enumerate(blocking[:50], 1):
        sign.append(f"| {i} | {c['type']} | {c['name']} | {c['action']} |  |")
    if not blocking:
        sign.append("| — | 无 | — | 可直接通过 |  |")
    sign += ["", f"## 待原上报人复核（{len(review)} 条，不需经理逐条看）", ""]
    for person, n in by_person.most_common():
        sign.append(f"- {person}：{n} 条（主体/日期待补或分类置信低）")
    sign += ["", "签核后：通过 → 生成部门包；退回 → 写明原因，由原上报人重做。"]
    open(os.path.join(OUT, "23-签核单.md"), "w", encoding="utf-8").write("\n".join(sign))

    print(json.dumps({
        "packages": len(packages), "members": dept_pkg["members"],
        "input_items": len(all_items), "merged": len(merged), "dups": dups,
        "conflicts": len(conflicts),
        "blocking": len([c for c in conflicts if c["type"] in ("版本冲突", "类目争议")]),
        "review": len([c for c in conflicts if c["type"] == "待复核"]), "errors": len(errors),
        "outputs": ["20-部门融合包.json", "21-部门目录树.md", "22-冲突清单.csv", "23-签核单.md"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
