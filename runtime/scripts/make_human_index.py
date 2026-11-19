#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成两份给人看的文件——每份都带哈希，抬头带姓名/部门/职位：

1. 目录索引.md   —— 按抽屉分组，一行一份：新文件名 —— 内容，下挂 sha256 前 12 位与大小
2. 改名对照.md   —— 原文件名 → 新文件名 → 哈希（前 12 位）→ 大小

完整 64 位 sha256 在 16-改名映射.csv/.json 与 17-终稿索引包.json。
"""
import argparse
import json
import os
import re
import sys

JD_LABEL = {
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


def load_heads(out_dir):
    """从 13-全文解析缓存.jsonl 取正文开头，用于一句话概要。"""
    heads = {}
    p = os.path.join(out_dir, "13-全文解析缓存.jsonl")
    if not os.path.isfile(p):
        return heads
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            heads[r.get("source_path", "")] = r.get("head") or ""
    return heads


def brief(head, limit=46):
    if not head:
        return ""
    t = re.sub(r"\[[^\]]*\]", " ", head)          # 去掉 [sheet] 之类标记
    t = re.sub(r"[\t\r\n|]+", " ", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    t = re.sub(r"^[#>*\-\s]+", "", t)
    m = re.search(r"[。！？；]", t[:120])
    s = t[:m.start() + 1] if m else t
    s = s.strip()
    return (s[:limit] + "…") if len(s) > limit else s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--dept", default="")
    args = ap.parse_args()

    OUT = os.path.abspath(args.out)
    p17 = os.path.join(OUT, "17-终稿索引包.json")
    if not os.path.isfile(p17):
        print("缺少 17-终稿索引包.json，先跑 finalize.py")
        return 2
    pkg = json.load(open(p17, encoding="utf-8"))
    items = pkg.get("items", [])
    heads = load_heads(OUT)

    keep = [x for x in items if x.get("action") in ("rename", "keep_name")]
    skip = [x for x in items if x.get("action") == "no_ingest"]
    sens = [x for x in items if x.get("action") == "hold_sensitive"]

    groups = {}
    for x in keep:
        groups.setdefault(x["jd"], []).append(x)

    owner = pkg.get("owner") or {}
    name = args.name or owner.get("name", "")
    dept = args.dept or owner.get("dept", "")
    role = {"E": "员工", "M": "部门经理", "G": "总经理"}.get(owner.get("role", ""), "")
    gen = (pkg.get("generated_at") or "")[:10].replace("-", ".")
    tool = pkg.get("tool", "")
    who = " · ".join(filter(None, [name, dept, role]))

    lines = ["# 目录索引", ""]
    meta = [x for x in (who, f"生成 {gen}" if gen else "", tool) if x]
    if meta:
        lines.append("　".join(meta))
        lines.append("")
    lines += [
        f"共 {len(items)} 份文件：建议入库 **{len(keep)}** 份，"
        f"另有 {len(skip)} 份不入库（中间产物/系统导出/测试件）"
        + (f"，{len(sens)} 份敏感件不出现在目录里" if sens else "") + "。",
        "",
        "> 每行一份：**新文件名** —— 大概是什么内容；下面一行是文件指纹。",
        "> 哈希为 sha256 前 12 位，完整 64 位见 `16-改名映射.csv` 或 `17-终稿索引包.json`。",
        "",
    ]

    for jd in sorted(groups):
        g = sorted(groups[jd], key=lambda x: os.path.basename(x.get("to", "")))
        title = f"{jd} {JD_LABEL.get(jd, '未分类')}"
        lines.append(f"## {title}（{len(g)} 份）")
        lines.append("")
        for x in g:
            new_name = os.path.basename(x.get("to", ""))
            src = x.get("from", "")
            b = brief(heads.get(src, ""))
            if not b:
                b = (x.get("subject") or "").strip()
            tail = f" —— {b}" if b else ""
            lines.append(f"- **{new_name}**{tail}")
            sha = (x.get("sha256") or "")[:12]
            size = x.get("size") or ""
            print_line = "　".join(filter(None, [f"`{sha}`" if sha else "", size]))
            if print_line:
                lines.append(f"  {print_line}")
        lines.append("")

    if skip:
        lines.append(f"## 未入库（{len(skip)} 份，只登记不动）")
        lines.append("")
        lines.append("中间产物、系统导出数据、工具测试件，明细见 `01-全量文件清单.csv`。")
        lines.append("")

    open(os.path.join(OUT, "目录索引.md"), "w", encoding="utf-8").write("\n".join(lines))

    rows = ["# 改名对照", ""]
    meta = [x for x in (who, f"生成 {gen}" if gen else "", tool) if x]
    if meta:
        rows.append("　".join(meta))
        rows.append("")
    rows += [
            f"共 {len(items)} 份：改名 {sum(1 for x in items if x.get('action') == 'rename')}，"
            f"保留原名 {sum(1 for x in items if x.get('action') == 'keep_name')}，"
            f"不入库 {len(skip)}。哈希为 sha256 前 12 位。", "",
            "| 原文件名 | 新文件名 | 哈希(前12) | 大小 |", "|---|---|---|---|"]
    for x in sorted(items, key=lambda x: (x["jd"], os.path.basename(x.get("to", "")))):
        if x.get("action") not in ("rename", "keep_name"):
            continue
        old = os.path.basename(x.get("from", ""))
        new = os.path.basename(x.get("to", ""))
        rows.append(f"| {old} | {new} | {(x.get('sha256') or '')[:12]} | {x.get('size') or ''} |")
    open(os.path.join(OUT, "改名对照.md"), "w", encoding="utf-8").write("\n".join(rows))

    print(json.dumps({"目录索引": len(keep), "改名对照": len(keep), "未入库": len(skip),
                      "文件": ["目录索引.md", "改名对照.md"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
