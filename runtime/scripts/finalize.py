#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定稿：定目录 + 按命名规则生成改名映射（默认 dry-run，不动任何文件）

输入：09-分类结果.csv（或 个人索引包-L3.json）
输出：
  15-最终目录树.md
  16-改名映射.csv / 16-改名映射.json
  17-终稿索引包.json

命名规则校验：类别_主体_名称_日期.扩展名 —— 4 段、段内无下划线、扩展名只一次、≤100 字符。
`--apply` 时才把文件**复制**到 `--dest`（不移动、不删原件）。
"""
import argparse
import collections
import csv
import json
import os
import re
import hashlib
import shutil
import sys
import categories

JD_LABEL = categories.LABEL()


def folder_meta(items):
    """每个抽屉的一句话概括：份数 · 主体分布 · 日期范围 · 格式分布。

    融合时用它判断「这个文件夹到底是什么」，比只看目录名可靠。
    """
    n = len(items)
    if not n:
        return {"count": 0, "summary": "", "subjects": [], "date_range": "", "exts": {}}
    subj = collections.Counter((i.get("subject") or "通用") for i in items)
    top = subj.most_common(3)
    s_str = "、".join(f"{k} {v}" for k, v in top)
    dates = [str(i.get("date") or "") for i in items
             if re.fullmatch(r"\d{4}(-\d{2})?(-\d{2})?", str(i.get("date") or ""))]
    dr = f"{min(dates)} ~ {max(dates)}" if dates else "日期待补"
    exts = collections.Counter(os.path.splitext(i.get("to") or "")[1].lstrip(".").upper()
                               for i in items if os.path.splitext(i.get("to") or "")[1])
    e_str = "、".join(f"{k}×{v}" for k, v in exts.most_common(3))
    return {"count": n, "subjects": [k for k, _ in top], "date_range": dr,
            "exts": dict(exts), "summary": f"{n} 份 · {s_str} · {dr} · {e_str}"}


# 文件名强规则：文件名本身足够明确时，覆盖 AI 判定的抽屉。
# 顺序即优先级。返回 (jd, action, 理由, 名称前缀)；jd 为 None 表示不入库。
NAME_RULES = [
    (r"^(t|tmp|temp|test|preview|\u9884\u89c8)[\s\-_.]?.*\.(docx?|pdf|xlsx?)$", None, "no_ingest", "测试/预览文件", None),
    (r"(pytest|\u6837\u4f8b|sample|demo)", None, "no_ingest", "测试样例", None),
    (r"(\u5728\u804c|\u79bb\u804c|\u6536\u5165|\u5de5\u4f5c)\u8bc1\u660e", "30.01", None, "人员证明", "证件"),
    (r"(\u5ba1\u6838|\u5ba1\u67e5|\u8bc4\u5ba1|\u4fee\u6539)\u610f\u89c1|\u95ed\u73af\u6e05\u5355", "80.01", None, "项目审核/修改意见", "项目"),
    (r"GB\s*/?T?\s*\d{3,}", "50.01", None, "标准规范", "资料"),
    (r"(\u7edf\u8ba1|\u5730\u7c7b)(?!.*\u56fe)", "80.01", None, "项目统计数据", "项目"),
    (r"(flange|hex_bolt|bolt|gear|^T\d[_\-])", "50.04", None, "零件图/CAD", "图纸"),
]
NAME_RULE_RX = [(re.compile(pat, re.I), jd, act, why, pre) for pat, jd, act, why, pre in NAME_RULES]


def match_rule(raw_name):
    for rx, jd, act, why, pre in NAME_RULE_RX:
        if rx.search(raw_name or ""):
            return jd, act, why, pre
    return None


def rebuild_name(prefix, subject, stem, date, ext):
    stem = re.sub(r"[\u3010\u3011\[\]]", "", stem).strip() or "未命名"
    stem = re.sub(r"[\\/:*?\"<>|#]", "", stem)
    stem = stem.replace("_", "-")[:40]
    subj = subject or "待补"
    d = date or "待补"
    n = f"{prefix}_{subj}_{stem}_{d}{ext}"
    if len(n) > 100:
        n = f"{prefix}_{subj}_{stem[:24]}_{d}{ext}"
    return n


def check_name(name):
    """返回 (ok, reasons)"""
    reasons = []
    if not name:
        return False, ["空名"]
    stem, ext = os.path.splitext(name)
    core = re.sub(r"（原：[^）]*）", "", stem)   # 括注里的原名不参与分段校验
    parts = core.split("_")
    if len(parts) != 4:
        reasons.append(f"段数={len(parts)}")
    if any("_" in p for p in parts):
        reasons.append("段内下划线")
    if re.search(r"\.(docx?|pdf|pptx?|xlsx?|csv|dwg|dxf)$", stem, re.I):
        reasons.append("名称段残留扩展名")
    if len(name) > 150:
        reasons.append(f"超长{len(name)}")
    if re.search(r"【(重复|待OCR|待核|副本|最终|新建|作废|旧)】", name):
        reasons.append("状态标记进名字")
    if ext.lower() not in (".docx", ".doc", ".pdf", ".ppt", ".pptx", ".xlsx", ".xls",
                           ".csv", ".dwg", ".dxf",
                           ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".heic",
                           ".tif", ".tiff"):
        reasons.append("扩展名不在白名单")
    return not reasons, reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", default=os.path.expanduser("~"))
    ap.add_argument("--dest", default="", help="apply 时的目标根目录")
    ap.add_argument("--apply", action="store_true", help="真正复制文件（默认只出映射）")
    ap.add_argument("--keep-orig", dest="keep_orig", action="store_true", default=True,
                    help="新文件名末尾括注原文件名（默认开）")
    ap.add_argument("--name", default="", help="归档人姓名（写进索引）")
    ap.add_argument("--dept", default="", help="部门（写进索引）")
    ap.add_argument("--role", default="", help="职位：员工/部门经理/总经理 或 E/M/G")
    ap.add_argument("--no-keep-orig", dest="keep_orig", action="store_false",
                    help="新文件名不带原文件名括注")
    args = ap.parse_args()
    OUT = os.path.abspath(args.out)

    pkg3 = os.path.join(OUT, "个人索引包-L3.json")
    if os.path.isfile(pkg3):
        items = json.load(open(pkg3, encoding="utf-8"))["items"]
    else:
        rows = list(csv.DictReader(open(os.path.join(OUT, "09-分类结果.csv"), encoding="utf-8-sig")))
        items = []
        for r in rows:
            items.append({
                "source_path": r["source_path"], "file_name": r["file_name"],
                "ext": r["ext"], "size": int(r["size"] or 0),
                "jd_code": r["jd_code"], "subject": r["subject"], "date": r["date"],
                "suggested_name": r["suggested_name"], "confidence": float(r["confidence"] or 0),
                "flags": [f.strip() for f in r["flags"].split("/") if f.strip()],
                "upload": r["upload"], "subject_type": "project" if "项目" in (r.get("subject") or "") else "",
            })

    # 图片/证照通道（OCR + 视觉）也要进终稿，否则营业执照/身份证这类资质资产会丢
    p3 = os.path.join(OUT, "03-图片与CAD候选.csv")
    p14b = os.path.join(OUT, "14b-视觉解析缓存.json")
    vision = json.load(open(p14b, encoding="utf-8")) if os.path.isfile(p14b) else {}
    texts = {}
    p13 = os.path.join(OUT, "13-全文解析缓存.jsonl")
    if os.path.isfile(p13):
        for line in open(p13, encoding="utf-8"):
            try:
                r13 = json.loads(line)
                texts[r13["source_path"]] = r13
            except Exception:
                pass
    if os.path.isfile(p3):
        have = {it["source_path"] for it in items}
        words = categories.WORD()
        for r in csv.DictReader(open(p3, encoding="utf-8-sig")):
            if r.get("通道") == "CAD":
                continue
            sp = r["相对路径"]
            if sp in have:
                continue
            v = vision.get(sp) or {}
            t = texts.get(sp) or {}
            flags, upload = [], "未上传"
            if v.get("sensitive"):
                flags += ["S级-视觉判敏感"]
                upload = "禁上传"
            if v:
                flags.append("视觉")
            elif not (t.get("text") or "").strip():
                flags.append("待OCR")
            jd = v.get("jd") or "00.03"
            stem = os.path.splitext(r["文件名"])[0]
            _c = (words.get(jd, "文件") or "文件")[:12]
            _s = (v.get("subject") or "主体待补")[:30]
            _n = stem[:40]
            _d = v.get("date") or "日期待补"
            suggested = "" if v.get("sensitive") else f"{_c}_{_s}_{_n}_{_d}{r['扩展名']}"
            items.append({
                "source_path": sp, "file_name": r["文件名"], "ext": r["扩展名"],
                "size": int(r["字节"] or 0), "jd_code": jd,
                "subject": v.get("subject") or "", "date": v.get("date") or "",
                "suggested_name": suggested, "confidence": float(v.get("confidence") or 0),
                "flags": flags, "upload": upload,
                "subject_type": v.get("subject_type") or "",
                "category": f"{jd} {categories.LABEL().get(jd, '未分类')}",
                "category_word": words.get(jd, "文件"),
            })

    EXCL = ("生成中间产物", "系统导出数据", "工具验证样例", "空表模板")

    mapping = []
    used = collections.Counter()
    tree = collections.defaultdict(list)
    # 哈希与大小：索引必须可校验，每份文件都带完整 sha256 与字节数
    sha_map, size_map = {}, {}
    p8 = os.path.join(OUT, "08-工作文档-哈希与重名.csv")
    if os.path.isfile(p8):
        for r in csv.DictReader(open(p8, encoding="utf-8-sig")):
            sha_map[r["相对路径"]] = r.get("sha256", "")
            size_map[r["相对路径"]] = r.get("大小", "")
    p1 = os.path.join(OUT, "01-全量文件清单.csv")
    if os.path.isfile(p1):
        for r in csv.DictReader(open(p1, encoding="utf-8-sig")):
            k = r.get("相对路径") or r.get("path") or ""
            if k and k not in size_map:
                size_map[k] = r.get("大小") or r.get("size") or ""

    ROLE_MAP = {"员工": "E", "部门经理": "M", "总经理": "G", "E": "E", "M": "M", "G": "G", "": ""}
    def file_sha(path):
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            return h.hexdigest()
        except OSError:
            return ""

    def human(n):
        for u in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.1f}{u}" if u != "B" else f"{n}B"
            n /= 1024
        return f"{n:.1f}TB"

    def sha_of(it):
        v = sha_map.get(it["source_path"], "")
        if v:
            return v
        p = os.path.join(args.root, it["source_path"])
        v = file_sha(p) if os.path.isfile(p) else ""
        if v:
            sha_map[it["source_path"]] = v
        return v

    def size_of(it):
        v = size_map.get(it["source_path"], "")
        if v:
            return v
        p = os.path.join(args.root, it["source_path"])
        try:
            return human(os.path.getsize(p))
        except OSError:
            return ""

    # AI 直判阶段标为 skipped 的（工具测试产物等）直接不入库
    skipped = set()
    p14 = os.path.join(OUT, "14-AI补齐缓存.json")
    if os.path.isfile(p14):
        try:
            for k, v in json.load(open(p14, encoding="utf-8")).items():
                if (v or {}).get("mode") == "no_ingest":
                    skipped.add(k)
        except Exception:
            pass

    owner = {"name": args.name, "dept": args.dept,
             "role": ROLE_MAP.get((args.role or "").strip(), "")}
    override_count = []
    for it in items:
        jd = it["jd_code"]
        name = it.get("suggested_name") or ""
        action = "rename"
        reasons = []

        if it["source_path"] in skipped:
            action = "no_ingest"
            reasons.append("AI判定:不入库")

        # 文件名强规则优先于 AI 判定
        raw_name = os.path.basename(it.get("source_path") or it.get("file_name") or "")
        hit = match_rule(raw_name)
        if hit:
            r_jd, r_act, r_why, r_pre = hit
            it["flags"].append(f"规则覆盖:{r_why}")
            override_count.append((raw_name, it["jd_code"], r_jd or r_act, r_why))
            if r_act == "no_ingest":
                action = "no_ingest"
            elif r_jd:
                jd = r_jd
                it["jd_code"] = r_jd
                stem, ext = os.path.splitext(raw_name)
                name = rebuild_name(r_pre, it.get("subject", ""), stem,
                                    it.get("date", ""), ext)

        folder = f"{jd} {JD_LABEL.get(jd, '未分类')}"

        if not name:
            name = it["file_name"]
            reasons = ["无建议名"]
            if action == "rename":
                action = "keep_name"
        else:
            if args.keep_orig:
                stem_o, ext_o = os.path.splitext(name)
                orig = os.path.splitext(os.path.basename(it.get("source_path") or ""))[0]
                orig = re.sub(r"[（）()]", "", orig).strip()
                room = 150 - len(stem_o) - len(ext_o) - 5
                if room >= 4 and orig and orig not in stem_o:
                    name = f"{stem_o}（原：{orig[:room]}）{ext_o}"
            ok, reasons = check_name(name)
            if not ok:
                action = "hold"

        if any(f in EXCL for f in it["flags"]):
            action = "no_ingest"
        if it.get("upload") == "禁上传":
            action = "hold_sensitive"

        key = (folder, name)
        used[key] += 1
        if used[key] > 1:
            stem, ext = os.path.splitext(name)
            name = f"{stem}-{used[key]}{ext}"
            reasons.append("同名加序号")

        target = f"{folder}/{name}"
        mapping.append({
            "from": it["source_path"],
            "to": target,
            "sha256": sha_of(it),
            "size": size_of(it),
            "owner_name": owner["name"],
            "owner_dept": owner["dept"],
            "owner_role": owner["role"],
            "jd": jd,
            "subject": it.get("subject", ""),
            "subject_type": it.get("subject_type", ""),
            "date": it.get("date", ""),
            "confidence": it.get("confidence", 0),
            "flags": ";".join(it["flags"]),
            "action": action,
            "name_check": ";".join(reasons),
        })
        if action in ("rename", "keep_name"):
            tree[folder].append(name)

    # 目录树
    folders_meta = {}
    for folder, names in tree.items():
        members = [m for m in mapping
                   if m["to"].split("/", 1)[0] == folder and m["action"] in ("rename", "keep_name")]
        folders_meta[folder] = folder_meta(members)

    lines = ["# 最终目录树与改名映射（dry-run）", "",
             f"文件数：{len(mapping)} ｜ 入库抽屉：{len(tree)} ｜ 改名：{sum(1 for m in mapping if m['action']=='rename')}"
             f" ｜ 保持原名：{sum(1 for m in mapping if m['action']=='keep_name')}"
             f" ｜ 不入库：{sum(1 for m in mapping if m['action']=='no_ingest')}"
             f" ｜ 待确认：{sum(1 for m in mapping if m['action']=='hold')}"
             f" ｜ 敏感：{sum(1 for m in mapping if m['action']=='hold_sensitive')}",
             "", "## 入库目录树", "", "```"]
    for folder in sorted(tree):
        lines.append(f"{folder}/")
        meta = folders_meta.get(folder) or {}
        if meta.get("summary"):
            lines.append(f"    └─ {meta['summary']}")
        for n in sorted(tree[folder]):
            lines.append(f"    {n}")
    lines += ["```", "",
              "## 需要人工确认的", ""]
    hold = [m for m in mapping if m["action"] == "hold"]
    sens = [m for m in mapping if m["action"] == "hold_sensitive"]
    clean = [m for m in mapping if m["action"] == "no_ingest"]
    lines.append(f"- 命名校验不过（hold）：{len(hold)} 份")
    for m in hold[:20]:
        lines.append(f"  - `{m['from']}` → {m['name_check']}")
    lines.append(f"- 敏感件（不上传）：{len(sens)} 份")
    for m in sens[:10]:
        lines.append(f"  - `{m['from']}`")
    lines.append(f"- 不入库（中间产物/工具件）：{len(clean)} 份")
    with open(os.path.join(OUT, "15-最终目录树.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # 映射 CSV/JSON
    fields = ["from", "to", "sha256", "size", "owner_name", "owner_dept", "owner_role",
              "jd", "subject", "subject_type", "date", "confidence", "action", "name_check", "flags"]
    with open(os.path.join(OUT, "16-改名映射.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for m in mapping:
            w.writerow({k: m.get(k, "") for k in fields})
    json.dump({"schema": "kb-steward/rename-map@0.3", "mode": "apply" if args.apply else "dry-run",
               "items": mapping},
              open(os.path.join(OUT, "16-改名映射.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    # 终稿索引
    try:
        ver = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "VERSION"), encoding="utf-8").read().strip()
    except Exception:
        ver = ""
    json.dump({"schema": "kb-steward/final-index@2.0",
               "owner": owner,
               "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
               "tool": f"kb-steward {ver}".strip(),
               "source_root": args.root,
               "summary": {"files": len(mapping),
                           "rename": sum(1 for m in mapping if m["action"] == "rename"),
                           "keep_name": sum(1 for m in mapping if m["action"] == "keep_name"),
                           "no_ingest": sum(1 for m in mapping if m["action"] == "no_ingest"),
                           "hold_sensitive": sum(1 for m in mapping if m["action"] == "hold_sensitive"),
                           "folders": len(tree)},
               "items": mapping,
               "folders": folders_meta,
               "folders_count": {k: len(v) for k, v in tree.items()}},
              open(os.path.join(OUT, "17-终稿索引包.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    copied = 0
    if args.apply:
        dest = os.path.abspath(args.dest or os.path.join(OUT, "KB"))
        for m in mapping:
            if m["action"] != "rename":
                continue
            src = os.path.join(args.root, m["from"])
            dst = os.path.join(dest, m["to"])
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.isfile(src):
                shutil.copy2(src, dst)
                copied += 1

    print(json.dumps({
        "files": len(mapping), "folders": len(tree),
        "rename": sum(1 for m in mapping if m["action"] == "rename"),
        "keep_name": sum(1 for m in mapping if m["action"] == "keep_name"),
        "hold": sum(1 for m in mapping if m["action"] == "hold"),
        "sensitive": sum(1 for m in mapping if m["action"] == "hold_sensitive"),
        "no_ingest": sum(1 for m in mapping if m["action"] == "no_ingest"),
        "copied": copied,
        "outputs": ["15-最终目录树.md", "16-改名映射.csv", "16-改名映射.json", "17-终稿索引包.json"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
