#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CAD 后解析：从图纸里抽图签文字（单位/工程名/图名/图号/日期/比例）

为什么不放在第一批：CAD 解析慢（DWG 要先转 DXF），且不影响其余文件的目录。
用法：
  python3 parse_cad.py --out <整理目录> --root <扫描根>            # 解析全部 CAD
  python3 parse_cad.py --out ... --root ... --limit 20             # 先试 20 份
  python3 parse_cad.py --out ... --root ... --only <关键词>         # 只解析路径含关键词的

结果写回 14-AI补齐缓存.json（mode=cad），之后跑 merge_ai + finalize 就会用上。
"""
import argparse
import collections
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import categories

# 图签常见字段
KEY_PROJECT = ("工程名称", "项目名称", "工程编号", "建设项目")
KEY_ORG = ("建设单位", "设计单位", "施工单位", "监理单位", "勘察单位", "业主")
KEY_TITLE = ("图名", "图纸名称", "图纸内容", "子项名称")
KEY_NO = ("图号", "图纸编号", "图别")
KEY_DATE = ("日期", "出图日期", "设计日期", "制图日期")
KEY_SCALE = ("比例",)

DATE_RE = re.compile(r"(20\d{2})[-./年](\d{1,2})[-./月]?(\d{1,2})?")
ORG_RE = re.compile(r"[\u4e00-\u9fff（）()]{2,40}?(?:有限公司|有限责任公司|设计院|研究院|集团|事务所|大学|学院)")
CJK = re.compile(r"[\u4e00-\u9fff]")


def dwg_to_dxf(path, tmpdir):
    """DWG → DXF（LibreDWG dwg2dxf）。没有工具就返回 None。"""
    exe = shutil.which("dwg2dxf")
    if not exe:
        return None
    out = os.path.join(tmpdir, os.path.splitext(os.path.basename(path))[0] + ".dxf")
    try:
        r = subprocess.run([exe, "-o", out, path], capture_output=True, timeout=90)
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            return out
    except Exception:
        pass
    return None


def collect_texts(dxf_path):
    """抽所有文字：TEXT / MTEXT / 块属性（图签基本都是块属性）。"""
    import ezdxf
    texts = []
    doc = ezdxf.readfile(dxf_path)
    spaces = [doc.modelspace()]
    for name in doc.layouts.names():
        try:
            spaces.append(doc.layouts.get(name))
        except Exception:
            pass
    for sp in spaces:
        try:
            ents = list(sp)
        except Exception:
            continue
        for e in ents:
            t = e.dxftype()
            try:
                if t == "TEXT":
                    texts.append(e.dxf.text or "")
                elif t == "MTEXT":
                    texts.append(e.plain_text() or "")
                elif t == "INSERT":
                    for a in e.attribs:
                        tag, val = a.dxf.tag or "", a.dxf.text or ""
                        texts.append(f"{tag}:{val}" if tag else val)
            except Exception:
                continue
    return [x.strip() for x in texts if x and x.strip()]


def pick(texts):
    """从文字里挑图签字段。返回 dict。"""
    joined = "\n".join(texts)
    out = {"project": "", "orgs": [], "title": "", "drawing_no": "", "date": "", "scale": ""}

    def find_after(keys, maxlen=40):
        for k in keys:
            for t in texts:
                if k in t:
                    v = re.split(r"[:：]", t, 1)
                    if len(v) == 2 and v[1].strip():
                        return v[1].strip()[:maxlen]
                    tail = t.split(k, 1)[1].strip(" ：:")
                    if tail:
                        return tail[:maxlen]
        return ""

    out["project"] = find_after(KEY_PROJECT, 50)
    out["title"] = find_after(KEY_TITLE, 40)
    out["drawing_no"] = find_after(KEY_NO, 30)
    out["scale"] = find_after(KEY_SCALE, 12)

    for k in KEY_ORG:
        v = find_after((k,), 50)
        if v and CJK.search(v) and v not in out["orgs"]:
            out["orgs"].append(v)
    if not out["orgs"]:
        for m in ORG_RE.findall(joined):
            m = m.strip()
            if len(m) >= 6 and m not in out["orgs"]:
                out["orgs"].append(m)
            if len(out["orgs"]) >= 3:
                break

    dm = DATE_RE.search(joined)
    if dm:
        y, mo, d = dm.group(1), dm.group(2), dm.group(3)
        if d:
            out["date"] = f"{y}-{int(mo):02d}-{int(d):02d}"
        else:
            out["date"] = f"{y}-{int(mo):02d}"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", default=os.path.expanduser("~"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="", help="只处理路径含该关键词的")
    ap.add_argument("--redo", action="store_true", help="已解析过的也重做")
    ap.add_argument("--skip-dwg", action="store_true", help="只解析 DXF，DWG 留到后面批量转")
    ap.add_argument("--only-dwg", action="store_true", help="只解析 DWG")
    args = ap.parse_args()

    OUT = os.path.abspath(args.out)
    ROOT = os.path.abspath(args.root)
    cache_path = os.path.join(OUT, "14-AI补齐缓存.json")
    cache = json.load(open(cache_path, encoding="utf-8")) if os.path.isfile(cache_path) else {}

    rows = []
    p3 = os.path.join(OUT, "03-图片与CAD候选.csv")
    if os.path.isfile(p3):
        import csv
        for r in csv.DictReader(open(p3, encoding="utf-8-sig")):
            if r.get("通道") == "CAD":
                rows.append(r["相对路径"])
    # 工具中间产物不算公司图纸：oda/libredwg 转出来的、tmp 目录里的
    SKIP_MARKS = ("oda_out", "libredwg_out", "/_tmp_in/", "/tmp/", "cad-helper-v1.5-audit",
                  "envcad-validation-output", "凹凸CAD助手1.5")
    rows = [x for x in rows if not any(m in x for m in SKIP_MARKS)]
    if args.only:
        rows = [x for x in rows if args.only in x]
    if not args.redo:
        rows = [x for x in rows if (cache.get(x) or {}).get("mode") != "cad"]
    if args.skip_dwg:
        rows = [x for x in rows if not x.lower().endswith(".dwg")]
    if args.only_dwg:
        rows = [x for x in rows if x.lower().endswith(".dwg")]
    if args.limit:
        rows = rows[:args.limit]

    print(f"待解析 CAD {len(rows)} 份", flush=True)
    stats = collections.Counter()
    tmpdir = tempfile.mkdtemp(prefix="cad_")
    try:
        for i, rel in enumerate(rows, 1):
            print(f"[{i}/{len(rows)}] {rel[-70:]}", flush=True)
            full = os.path.join(ROOT, rel)
            if not os.path.isfile(full):
                stats["missing"] += 1
                continue
            try:
                mb = os.path.getsize(full) / 1024 / 1024
            except OSError:
                mb = 0
            if mb > 30:
                stats["too_big"] += 1
                cache[rel] = {"jd": "50.04", "subject": "", "subject_type": "", "date": "",
                              "confidence": 0.3, "evidence": f"CAD 文件 {mb:.0f}MB 过大，跳过解析",
                              "mode": "cad"}
                continue
            ext = os.path.splitext(full)[1].lower()
            dxf = full
            if ext == ".dwg":
                dxf = dwg_to_dxf(full, tmpdir)
                if not dxf:
                    stats["dwg_no_converter"] += 1
                    continue
            try:
                texts = collect_texts(dxf)
            except Exception as e:
                stats["read_error"] += 1
                continue

            info = pick(texts) if texts else {}
            cjk = len(CJK.findall("\n".join(texts)))
            if cjk < 4:
                stats["no_text"] += 1
                cache[rel] = {"jd": "50.04", "subject": info.get("project") or "",
                              "subject_type": "project" if info.get("project") else "",
                              "date": info.get("date") or "", "confidence": 0.3,
                              "evidence": "CAD 无图签文字", "mode": "cad"}
                continue

            subject = info.get("project") or (info["orgs"][0] if info["orgs"] else "")
            stype = "project" if info.get("project") else ("org" if info["orgs"] else "")
            ev = "；".join(filter(None, [
                f"工程名:{info['project']}" if info.get("project") else "",
                f"单位:{'、'.join(info['orgs'][:2])}" if info["orgs"] else "",
                f"图名:{info['title']}" if info.get("title") else "",
                f"图号:{info['drawing_no']}" if info.get("drawing_no") else "",
            ]))[:80]
            cache[rel] = {
                "jd": "50.04", "subject": subject, "subject_type": stype,
                "date": info.get("date") or "",
                "confidence": 0.85 if subject else 0.5,
                "evidence": ev or f"CAD 文字 {cjk} 字",
                "mode": "cad",
            }
            stats["ok"] += 1
            if i % 10 == 0:
                json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps({"files": len(rows), "stats": dict(stats), "cache": cache_path},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
