#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由「当前对话的智能体」直接做 AI 判断，不依赖外部 API Key。

工作方式：
  1) --export：把待判文件导出成 jsonl（每行一份，正文截断），供智能体阅读
  2) 智能体按 prompt 判断后写 results.jsonl
  3) --apply：写回 14-AI补齐缓存.json，之后照常 merge_ai → finalize

这样敏感件正文不出本机、不消耗云端额度，用的就是正在跟你对话的模型。
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def load_jsonl(p):
    with open(p, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def cmd_export(args):
    OUT = os.path.abspath(args.out)
    recs = load_jsonl(os.path.join(OUT, "13-全文解析缓存.jsonl"))
    cache_path = os.path.join(OUT, "14-AI补齐缓存.json")
    cache = json.load(open(cache_path, encoding="utf-8")) if os.path.isfile(cache_path) else {}

    todo = [r for r in recs
            if r["source_path"] not in cache
            or (cache.get(r["source_path"]) or {}).get("mode") in ("empty", "sensitive")]

    # 敏感件不导出正文（只给文件名/路径，让智能体判类目，不判内容）
    p09 = os.path.join(OUT, "09-分类结果.csv")
    sens = set()
    if os.path.isfile(p09):
        import csv
        for r in csv.DictReader(open(p09, encoding="utf-8-sig")):
            fl = r.get("flags") or ""
            if "S级" in fl or "A级" in fl:
                sens.add(r["source_path"])

    task_dir = os.path.join(OUT, "agent-tasks")
    os.makedirs(task_dir, exist_ok=True)
    size = args.size
    batches = [todo[i:i + size] for i in range(0, len(todo), size)]
    for idx, batch in enumerate(batches, 1):
        p = os.path.join(task_dir, f"tasks-{idx:02d}.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for r in batch:
                is_sens = r["source_path"] in sens
                f.write(json.dumps({
                    "path": r["source_path"],
                    "name": r["file_name"],
                    "ext": r["ext"],
                    "status": r["status"],
                    "chars": r["chars"],
                    "text": ("" if is_sens else (r.get("text") or "")[:args.text_limit]),
                    "note": "SENSITIVE: 只判类目与主体，不要输出内容细节" if is_sens else "",
                }, ensure_ascii=False) + "\n")
    print(json.dumps({"todo": len(todo), "batches": len(batches),
                      "dir": task_dir, "size": size}, ensure_ascii=False, indent=2))
    return 0


def _mark_no_ingest(OUT, paths, flag="工具验证样例"):
    """把「不入库」判定写进 09 的 flags，finalize 会据此排除。"""
    import csv
    p09 = os.path.join(OUT, "09-分类结果.csv")
    if not os.path.isfile(p09):
        return 0
    rows = list(csv.DictReader(open(p09, encoding="utf-8-sig")))
    fields = list(rows[0].keys()) if rows else []
    n = 0
    for r in rows:
        if r.get("source_path") in paths:
            fl = [x.strip() for x in (r.get("flags") or "").split("/") if x.strip()]
            if flag not in fl:
                fl.append(flag)
            r["flags"] = " / ".join(fl)
            n += 1
    with open(p09, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return n


def cmd_apply(args):
    OUT = os.path.abspath(args.out)
    import categories
    LABEL = categories.LABEL()
    OK_JD = set(LABEL)

    cache_path = os.path.join(OUT, "14-AI补齐缓存.json")
    cache = json.load(open(cache_path, encoding="utf-8")) if os.path.isfile(cache_path) else {}

    n_ok = n_bad = 0
    no_ingest_paths = set()
    for rdir in args.results:
        for line in open(rdir, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                d = json.loads(line)
            except Exception:
                n_bad += 1
                continue
            path = d.get("path") or d.get("source_path")
            if not path:
                n_bad += 1
                continue
            jd = (d.get("jd") or "").strip()
            if jd and jd not in OK_JD:
                n_bad += 1
                continue
            date = (d.get("date") or "").strip()
            if date and not re.fullmatch(r"\d{4}(-\d{2})?(-\d{2})?", date):
                date = ""
            if d.get("no_ingest"):
                no_ingest_paths.add(path)
                cache[path] = {"jd": "", "subject": "", "subject_type": "",
                               "date": "", "confidence": float(d.get("confidence") or 0.9),
                               "evidence": (d.get("evidence") or "")[:80],
                               "mode": "no_ingest"}
                n_ok += 1
                continue
            cache[path] = {
                "jd": jd,
                "subject": (d.get("subject") or "").strip(),
                "subject_type": d.get("subject_type") or "",
                "date": date,
                "confidence": float(d.get("confidence") or 0.6),
                "evidence": (d.get("evidence") or "")[:80],
                "mode": "agent",
            }
            n_ok += 1

    marked = _mark_no_ingest(OUT, no_ingest_paths) if no_ingest_paths else 0
    json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps({"applied": n_ok, "rejected": n_bad, "no_ingest": marked,
                      "covered": len(cache),
                      "cache": cache_path}, ensure_ascii=False, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--results", action="append", default=[],
                    help="智能体写出的结果 jsonl，可多次指定")
    ap.add_argument("--size", type=int, default=36, help="每批份数")
    ap.add_argument("--text-limit", type=int, default=1500, help="每份正文截断字符数")
    args = ap.parse_args()
    if args.export:
        return cmd_export(args)
    if args.apply:
        if not args.results:
            print("--apply 需要 --results <jsonl>")
            return 2
        return cmd_apply(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
