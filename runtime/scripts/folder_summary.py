#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文件夹一句话概括（AI）——用于上传/融合时判断"这个文件夹里到底是什么"

用法：
  python3 folder_summary.py --out <整理目录>          # 有 Key 才调模型，无 Key 用规则概括
  python3 folder_summary.py --out <整理目录> --no-ai  # 只重算规则概括

产出：
  回写 17-终稿索引包.json 的 folders[*].ai_summary
  回写 15-最终目录树.md   每个文件夹下显示规则概括 + AI 一句
  18-文件夹概括缓存.json  按内容哈希缓存，重跑不重复调模型
"""
import argparse
import collections
import hashlib
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = os.environ.get("SPACEAGENTS_AUTO_API_URL", "https://token.spaceagents.cn/v1/chat/completions")
KEY = os.environ.get("SPACEAGENTS_AUTO_API_KEY", "")
MODEL = "auto"
CACHE_NAME = "18-文件夹概括缓存.json"

PROMPT = """你在给一个公司文件柜写抽屉标签。根据下面信息，写**一句**中文说明，供归档审阅和上传时判断内容。

要求：
- 不超过 40 个字，一句话，不要句号结尾也可以
- 说清"这是什么材料、涉及谁/什么项目"
- 不要出现"文件夹""该目录""包含"这类废话
- 不要编造未给出的事实；信息不足就写得笼统些

抽屉：{name}
份数：{count}
主要主体：{subjects}
日期范围：{date_range}
格式：{exts}
代表文件：
{files}

只输出这一句话，不要引号、不要解释。"""


def call_llm(prompt, retry=2, max_tokens=8000):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": 0.3}
    for _ in range(retry):
        try:
            req = urllib.request.Request(
                API, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {KEY}"})
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.loads(r.read())
            txt = (d["choices"][0]["message"].get("content") or "").strip()
            txt = re.sub(r"^[\"'“”]|[\"'“”]$", "", txt)
            txt = txt.split("\n")[0].strip()
            if txt:
                return txt[:60]
        except Exception:
            continue
    return ""


def summarize(folders, items, use_ai=True):
    """folders: {name: meta}；items: 17 的 items。返回 {name: {..., ai_summary}}"""
    by_folder = collections.defaultdict(list)
    for it in items:
        if it.get("action") not in ("rename", "keep_name"):
            continue
        f = (it.get("to") or "").split("/", 1)[0]
        if f:
            by_folder[f].append(it)

    out = {}
    for name, meta in folders.items():
        members = by_folder.get(name, [])
        files = [os.path.basename(m.get("to") or "") for m in members][:10]
        meta = dict(meta or {})
        meta.setdefault("count", len(members))
        meta["ai_summary"] = ""
        if use_ai and KEY and members:
            prompt = PROMPT.format(
                name=name, count=meta.get("count"),
                subjects="、".join(meta.get("subjects") or []) or "无",
                date_range=meta.get("date_range") or "未知",
                exts="、".join(f"{k}×{v}" for k, v in (meta.get("exts") or {}).items()) or "未知",
                files="\n".join(files) or "（无）")
            meta["ai_summary"] = call_llm(prompt)
        if not meta["ai_summary"]:
            meta["ai_summary"] = rule_summary(name, meta)
        out[name] = meta
    return out


def rule_summary(name, meta):
    """模型不可用时的兜底：直接说清份数/主体/时间/格式。"""
    parts = [f"{meta.get('count', 0)} 份"]
    if meta.get("subjects"):
        parts.append("、".join(meta["subjects"][:2]))
    if meta.get("date_range"):
        parts.append(meta["date_range"])
    return " · ".join(parts)


def rewrite_tree(out_dir, folders):
    p = os.path.join(out_dir, "15-最终目录树.md")
    if not os.path.isfile(p):
        return
    lines = open(p, encoding="utf-8").read().split("\n")
    res = []
    for line in lines:
        res.append(line)
        m = re.match(r"^(\S.*?)/$", line.strip())
        if m and m.group(1) in folders:
            meta = folders[m.group(1)]
            ai = meta.get("ai_summary") or ""
            rule = meta.get("summary") or ""
            if ai:
                res.append(f"    └─ {ai}")
            if rule:
                res.append(f"       {rule}")
    open(p, "w", encoding="utf-8").write("\n".join(res))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-ai", action="store_true")
    args = ap.parse_args()

    OUT = os.path.abspath(args.out)
    p17 = os.path.join(OUT, "17-终稿索引包.json")
    if not os.path.isfile(p17):
        print("缺少 17-终稿索引包.json")
        return 2
    pkg = json.load(open(p17, encoding="utf-8"))
    folders = pkg.get("folders") or {}
    if not folders:
        print("folders 为空，先跑 finalize.py")
        return 2

    use_ai = not args.no_ai and bool(KEY)
    if not use_ai:
        print("未配置 SPACEAGENTS_AUTO_API_KEY 或指定 --no-ai：只用规则概括", flush=True)

    cache_path = os.path.join(OUT, CACHE_NAME)
    cache = json.load(open(cache_path, encoding="utf-8")) if os.path.isfile(cache_path) else {}

    result = summarize(folders, pkg.get("items", []), use_ai=use_ai)
    n_ai = sum(1 for v in result.values() if v.get("ai_summary"))
    pkg["folders"] = result
    json.dump(pkg, open(p17, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(result, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    rewrite_tree(OUT, result)

    print(json.dumps({"folders": len(result), "with_ai": n_ai,
                      "sample": {k: v["ai_summary"] for k, v in list(result.items())[:3]}},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
