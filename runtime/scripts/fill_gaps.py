#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""待补项批量补全（L2.1）

思路：待补项不逐份补，按「目录 / 项目」批量补。
1) 模板豁免：政府空表/申报表模板不是公司资产，改判表单模板，主体不要求
2) 目录继承：同一目录内已确认主体占多数 → 其余继承（标「继承待核」）
3) 日期回退：文件名 > 父目录 > 正文 > 文件修改时间（只取年份，标「日期待核」）
4) 剩余按目录聚类，生成「一次填一个目录」的批量补全表
"""
import argparse
import collections
import csv
import json
import os
import re

PROJECT_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9（）()·\-]{3,32}?(?:项目|工程))")
PROJECT_BAD = ("项目档案", "项目管理", "项目启动", "项目总结", "项目计划", "工程项目管理",
               "职称", "评审", "申报", "系列", "示范区", "考评", "花名", "登记", "培训")
TOOL_DIRS = ("/oda-install/", "/envcad-validation", "/kb-cad-parse/out/", "/cad-helper/")
GENERIC_JD = {"00.01", "30.03", "40.01", "40.02", "40.03", "40.04",
              "60.01", "60.02", "60.03", "60.04", "60.05"}
MUST_SUBJECT_JD = {"10.01", "10.02", "20.01", "20.02", "20.03", "30.01", "30.02", "70.01", "70.02", "70.03", "70.04"}

FORM_TMPL = re.compile(r"(表格|模板|格式|填报|说明|标准条件|申报表|花名表|考评表|登记表|推荐函|承诺书|审批表)")
FILLED_HINT = re.compile(r"(有限公司|有限责任公司|设计院|研究院|大学|学院)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    OUT = os.path.abspath(args.out)
    pkg = json.load(open(os.path.join(OUT, "个人索引包-L2.json"), encoding="utf-8"))
    items = pkg["items"]

    # ---------- 1) 模板豁免 ----------
    exempt = 0
    for it in items:
        if it["jd_code"] != "30.01":
            continue
        if not FORM_TMPL.search(it["file_name"]):
            continue
        # 已填过的实例（正文出现具体人名/公司）不豁免
        if FILLED_HINT.search(it.get("evidence", "") + it["file_name"]):
            if "有限公司" in it["file_name"]:
                continue
        it["jd_code"] = "60.03"
        it["category"] = "60.03 表单"
        it["category_word"] = "表单"
        it["subject"] = ""
        it["subject_source"] = "模板豁免"
        it["flags"] = [f for f in it["flags"] if f != "主体待补"]
        it["flags"].append("空表模板")
        it["confidence"] = max(float(it["confidence"]), 0.8)
        it["suggested_name"] = ""  # 模板不改名
        exempt += 1

    # ---------- 1.5) 工具验证样例：不当作公司文件 ----------
    tools = 0
    for it in items:
        low = "/" + it["source_path"].lower() + "/"
        if any(d in low for d in TOOL_DIRS):
            it["jd_code"] = "00.03"
            it["category"] = "00.03 待复核"
            it["category_word"] = "待复核"
            it["flags"] = [f for f in it["flags"] if f != "主体待补"] + ["工具验证样例"]
            it["confidence"] = 0.2
            it["suggested_name"] = ""
            tools += 1

    # ---------- 1.6) 从文件名抽项目名（项目类允许用项目名当主体） ----------
    proj = 0
    for it in items:
        if it["subject"] or it["jd_code"] == "00.03":
            continue
        stem = os.path.splitext(it["file_name"])[0]
        cands = [c for c in PROJECT_RE.findall(stem)
                 if not any(b in c for b in PROJECT_BAD) and len(c) >= 6]
        if cands:
            best = max(cands, key=len)
            it["subject"] = best
            it["subject_source"] = "文件名-项目名"
            it["subject_type"] = "project"
            it["flags"] = [f for f in it["flags"] if f != "主体待补"]
            it["flags"].append("项目主体")
            proj += 1

    # ---------- 2) 目录继承 ----------
    groups = collections.defaultdict(list)
    for it in items:
        parent = it["source_path"].rsplit("/", 1)[0] if "/" in it["source_path"] else ""
        groups[parent].append(it)

    inherited = 0
    for parent, g in groups.items():
        known = collections.Counter(it["subject"] for it in g
                                    if it["subject"] and it["subject_source"] not in ("模板豁免", ""))
        if not known:
            continue
        top, cnt = known.most_common(1)[0]
        total_known = sum(known.values())
        if cnt / total_known < 0.6:
            continue
        for it in g:
            if it["subject"]:
                continue
            it["subject"] = top
            it["subject_source"] = "目录继承"
            it["flags"] = [f for f in it["flags"] if f != "主体待补"]
            it["flags"].append("继承待核")
            inherited += 1

    # ---------- 2.5) 通用件：模板/内部营销/制度等，本就不对应某家公司 ----------
    generic = 0
    for it in items:
        if it["subject"] or it["jd_code"] == "00.03":
            continue
        if it["jd_code"] in GENERIC_JD:
            it["subject"] = "通用"
            it["subject_source"] = "无特定主体"
            it["subject_type"] = "generic"
            it["flags"] = [f for f in it["flags"] if f != "主体待补"]
            it["flags"].append("通用件")
            generic += 1

    # ---------- 3) 日期回退 ----------
    dated = 0
    for it in items:
        if it["date"] != "日期待补":
            continue
        m = re.match(r"(\d{4})", str(it.get("mtime", "")))
        if m:
            it["date"] = m.group(1)
            it["date_source"] = "文件时间"
            it["flags"] = [f for f in it["flags"] if f != "日期待补"]
            it["flags"].append("日期待核")
            dated += 1

    # ---------- 4) 重算建议名 ----------
    def clean(s, n):
        s = re.sub(r"[_/\\:*?\"<>|]+", "-", s or "")
        s = re.sub(r"\s+", "", s)
        return s[:n]

    for it in items:
        if it.get("subject_source") == "模板豁免" or it["jd_code"] == "00.03":
            continue
        stem = os.path.splitext(it["file_name"])[0]
        c = clean(it["category_word"], 12) or "文件"
        s = clean(it["subject"], 30) if it["subject"] else "主体待补"
        n = clean(stem, 40)
        d = it["date"] if it["date"] != "日期待补" else "日期待补"
        name = f"{c}_{s}_{n}_{d}{it['ext']}"
        if len(name) > 100:
            n = n[:max(6, len(n) - (len(name) - 100))]
            name = f"{c}_{s}_{n}_{d}{it['ext']}"
        it["suggested_name"] = name

    # ---------- 5) 剩余按目录聚类 ----------
    EXCLUDE_FLAGS = ("生成中间产物", "系统导出数据", "工具验证样例", "空表模板")

    # 内部知识库/手册：不对应外部主体
    INTERNAL_DOC = re.compile(r"(知识库|一问一答|操作手册|科普手册|学员手册|内部培训|操作白皮书)")
    internal = 0
    for it in items:
        if it["subject"] or it["jd_code"] == "00.03":
            continue
        if INTERNAL_DOC.search(it["file_name"]):
            it["subject"] = "通用"
            it["subject_source"] = "内部知识库"
            it["subject_type"] = "generic"
            it["flags"] = [f for f in it["flags"] if f != "主体待补"]
            it["flags"].append("内部知识库")
            internal += 1

    # 兜底：仍无主体且不属于「必须主体」的抽屉 → 通用件，不阻塞命名
    fallback = 0
    for it in items:
        if it["subject"] or it["jd_code"] == "00.03":
            continue
        if any(f in EXCLUDE_FLAGS for f in it["flags"]):
            continue
        if it["jd_code"] not in MUST_SUBJECT_JD:
            it["subject"] = "通用"
            it["subject_source"] = "无特定主体"
            it["subject_type"] = "generic"
            it["flags"] = [f for f in it["flags"] if f != "主体待补"]
            it["flags"].append("通用件")
            fallback += 1

    missing = [it for it in items if not it["subject"]
               and not any(f in EXCLUDE_FLAGS for f in it["flags"])]
    by_parent = collections.defaultdict(list)
    for it in missing:
        parent = it["source_path"].rsplit("/", 1)[0] if "/" in it["source_path"] else "(根目录)"
        by_parent[parent].append(it)

    rows = []
    for parent, g in sorted(by_parent.items(), key=lambda x: -len(x[1])):
        known = collections.Counter(it["subject"] for it in groups.get(parent, []) if it["subject"])
        cand = " / ".join(k for k, _ in known.most_common(3))
        # 目录名里的主体线索
        hint = ""
        segs = parent.split("/")
        for seg in reversed(segs):
            if re.search(r"(公司|集团|煤矿|设计院|研究院|大学|学院|项目|工厂|园区|热电|化工|煤业)", seg):
                hint = seg
                break
        action = "整目录一次填主体" if len(g) >= 5 else "逐份确认"
        if all(it["ext"] in (".dwg", ".dxf") for it in g):
            action = "按项目定主体（图纸）"
        rows.append({
            "目录": parent, "待补份数": len(g), "建议动作": action,
            "组内已知主体": cand, "目录线索": hint,
            "示例文件": g[0]["file_name"],
        })

    for it in items:
        if it["subject"] and it["jd_code"] != "00.03" and it.get("subject_source") != "模板豁免":
            stem = os.path.splitext(it["file_name"])[0]
            c = clean(it["category_word"], 12) or "文件"
            s_ = clean(it["subject"], 30)
            n = clean(stem, 40)
            d = it["date"] if it["date"] != "日期待补" else "日期待补"
            name = f"{c}_{s_}_{n}_{d}{it['ext']}"
            if len(name) > 100:
                n = n[:max(6, len(n) - (len(name) - 100))]
                name = f"{c}_{s_}_{n}_{d}{it['ext']}"
            it["suggested_name"] = name

    p_batch = os.path.join(OUT, "11-待补项-按目录批量补.csv")
    with open(p_batch, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["目录", "待补份数", "建议动作", "组内已知主体", "目录线索", "示例文件"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # ---------- 6) 写回 ----------
    low = [it for it in items if float(it["confidence"]) < 0.7
           and not any(f in ("生成中间产物", "系统导出数据") for f in it["flags"])]
    pkg["layer"] = "L2.1"
    pkg["summary"].update({
        "tool_samples": tools,
        "project_named": proj,
        "generic_no_subject": generic,
        "generic_fallback": fallback,
        "internal_doc": internal,
        "exempt_forms": exempt,
        "inherited_subject": inherited,
        "date_from_mtime": dated,
        "no_subject_after": len(missing),
        "low_confidence_after": len(low),
        "parent_groups_to_fill": len(rows),
    })
    with open(os.path.join(OUT, "个人索引包-L2.json"), "w", encoding="utf-8") as f:
        json.dump(pkg, f, ensure_ascii=False, indent=2)

    # CSV 重写
    fields = ["source_path", "file_name", "ext", "size", "mtime", "jd_code", "category",
              "category_word", "subject", "date", "suggested_name", "confidence", "score",
              "score_margin", "evidence", "flags", "personal_suspect", "extract_status", "upload"]
    with open(os.path.join(OUT, "09-分类结果.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for it in items:
            w.writerow([" / ".join(it[k]) if isinstance(it[k], list) else it[k] for k in fields])

    print(json.dumps({
        "tools": tools, "project_named": proj, "generic": generic, "fallback": fallback,
        "exempt_forms": exempt, "inherited": inherited, "date_from_mtime": dated,
        "no_subject_after": len(missing), "low_after": len(low),
        "parent_groups": len(rows),
        "top_groups": [{"dir": r["目录"], "n": r["待补份数"]} for r in rows[:8]],
        "output": p_batch,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
