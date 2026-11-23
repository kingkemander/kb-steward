#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""档3 视觉解析（VLM）：纯图 PPT / 扫描 PDF / 证照图片 → 描述 + 抽主体/日期/类目。

作用：补上「无正文模式」看不到内容的一块——规则读不出文字、文本模型也读不出的文件，
用视觉模型直接「看图」，得到描述、主体、日期、类目。

输入：
  - 13-全文解析缓存.jsonl（找「无正文 / 有效中文极少」的 pdf/pptx/ppt）
  - 03-图片与CAD候选.csv（通道==OCR 的图片，即证照等真实图片候选）

输出：
  - 14b-视觉解析缓存.json   { source_path: {desc,subject,subject_type,date,jd,confidence,evidence,mode} }

端点：复用 SPACEAGENTS_AUTO_API_URL / SPACEAGENTS_AUTO_API_KEY（OpenAI 多模态 image_url 格式）。
敏感红线：文件名/路径命中 身份证/密码/账号/密钥 的图片跳过，全文（图）不外发。
"""
import argparse
import base64
import collections
import csv
import json
import os
import re
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import llm

API, KEY, MODEL, LLM_SOURCE = llm.vision_endpoint()

CACHE_NAME = "14b-视觉解析缓存.json"
MAX_IMAGE_MB = 18          # 单张 base64 图超过则跳过（端点通常吃不下）
RENDER_DPI = 120           # PDF 渲染分辨率，够认字又不大
PDF_MAX_PAGES = 3
PPT_MEDIA_MAX = 5          # 纯图 PPT 取前几个最大 media
MIN_CJK = 30               # 有效中文 < 此阈值视为「无实质文字」，走视觉

import categories
PLAN_TEXT = categories.PLAN_TEXT()
JD_OK = {line.split(" ")[0] for line in PLAN_TEXT.strip().splitlines()}

SENS_FILE_KW = ("身份证", "公民身份", "账号", "密码", "password", "口令", "密钥",
                "apiKey", "secret", "邀请码", "token", "登陆", "登录")

VISION_PROMPT = """你是公司档案的视觉解析员，正在看一份文件扫描图/证件图/纯图幻灯片。只做四件事，找不到就写空字符串，绝不编造。

1. desc：一句话客观描述画面里是什么（证件类型 / 图表内容 / 文档性质），≤40 字
2. subject：图里「逐字出现」的完整机构名（结尾含 有限公司/设计院/研究院/大学/学院/集团/事务所/协会/促进会），或证件上的人名（只对毕业证/职称证/身份证这类证件填人名）；没有写 ""
3. subject_type：org（机构）/ person（人名）/ project（项目名）/ ""
4. date：图里明确的时间，格式 YYYY-MM 或 YYYY（签署日/有效期/发证日优先）；没有写 ""
5. jd：这份东西最像下面哪个抽屉编号，只填一个；完全没把握写 ""
6. confidence：0~1，证据不足给 0.5 以下
7. evidence：一句依据（≤30 字）

抽屉只能从下面选：
{plan}

只输出一行 JSON，不要解释、不要代码块：
{{"desc":"","subject":"","subject_type":"","date":"","jd":"","confidence":0.5,"evidence":""}}
"""

SENS_VISION_PROMPT = """你是公司档案的视觉解析员。请判断这张图是不是「身份证原件 / 账号密码表 / 密钥凭证 / 邀请码清单」这类应禁止上传的敏感件。

只输出一行 JSON：
{{"sensitive": true/false, "kind": "身份证/账号密码/密钥/邀请码/其他/无", "desc": "一句话描述(≤40字)"}}
"""


def cjk_count(s):
    return len(re.findall(r"[\u4e00-\u9fff]", s or ""))


def call_llm(messages, max_tokens=1200, retry=2):
    body = {"model": MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": 0.1}
    for attempt in range(retry):
        try:
            req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": f"Bearer {KEY}"})
            data = json.loads(urllib.request.urlopen(req, timeout=300).read().decode())
            msg = data["choices"][0]["message"]
            content = msg.get("content") or msg.get("reasoning_content") or ""
            m = re.search(r"\{[^{}]*\}", content)
            if m:
                return json.loads(m.group(0))
        except Exception:
            time.sleep(2 * (attempt + 1))
    return {}


def pick_subject_vl(jd, subject, subject_type):
    """视觉抽到的主体按类目做方向校验（证件类该是人名，合同类该是机构）。"""
    if not subject:
        return "", ""
    if jd == "30.01":  # 人员证件：主体该是人名
        return subject, "person"
    return subject, subject_type or "org"


def render_images(path, ext):
    """按文件类型把「能看到的内容」转成若干张 base64 图。返回 (images, note)。"""
    imgs = []
    try:
        if ext in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"):
            with open(path, "rb") as f:
                raw = f.read()
            if len(raw) > MAX_IMAGE_MB * 1024 * 1024:
                return [], "图片过大跳过"
            imgs.append(base64.b64encode(raw).decode())
            return imgs, ""

        if ext == ".pdf":
            try:
                import fitz
            except Exception:
                return [], "缺 pymupdf"
            doc = fitz.open(path)
            n = min(doc.page_count, PDF_MAX_PAGES)
            for i in range(n):
                pix = doc[i].get_pixmap(dpi=RENDER_DPI)
                imgs.append(base64.b64encode(pix.tobytes("png")).decode())
            doc.close()
            return imgs, "" if imgs else "渲染失败"

        if ext in (".pptx", ".ppt"):
            # 纯图 PPT：media 里的整图就是页面（实测 slide:media≈1:1）；取前几个最大的
            try:
                import zipfile
                z = zipfile.ZipFile(path)
                cands = []
                for n in z.namelist():
                    if n.lower().split(".")[-1] in ("png", "jpg", "jpeg"):
                        try:
                            info = z.getinfo(n)
                            cands.append((info.file_size, n))
                        except Exception:
                            continue
                cands.sort(reverse=True)
                for _, n in cands[:PPT_MEDIA_MAX]:
                    raw = z.read(n)
                    if len(raw) > MAX_IMAGE_MB * 1024 * 1024:
                        continue
                    imgs.append(base64.b64encode(raw).decode())
                z.close()
            except Exception:
                return [], "ppt 抽 media 失败"
            return imgs, "" if imgs else "无 media"

    except Exception as e:
        return [], f"渲染异常:{type(e).__name__}"
    return [], "不支持格式"


def build_messages(prompt, images, name, path):
    content = [{"type": "text", "text": prompt.format(plan=PLAN_TEXT)}]
    content.append({"type": "text", "text": f"文件名：{name}\n路径：{path}"})
    for b64 in images:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    return [{"role": "user", "content": content}]


def sensitive_name(name, path):
    low = (name + " " + path).lower()
    return any(k.lower() in low for k in SENS_FILE_KW)


def process_one(rec):
    ext = (rec.get("ext") or "").lower()
    name = rec.get("file_name") or os.path.basename(rec.get("source_path", ""))
    path = rec.get("source_path", "")
    imgs, note = render_images(rec["full_path"], ext)
    if not imgs:
        return {"mode": "no_image", "desc": note, "evidence": note, "confidence": 0.0,
                "subject": "", "subject_type": "", "date": "", "jd": ""}

    # 先做敏感判识（发一次，避免把身份证/账号密码图直接当正文解析外发两次）
    sens = call_llm([{"role": "user", "content":
                      [{"type": "text", "text": SENS_VISION_PROMPT}] +
                      [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{imgs[0]}"}}]}])
    if isinstance(sens, dict) and sens.get("sensitive"):
        return {"mode": "sensitive_img", "desc": sens.get("desc", "")[:40],
                "evidence": "视觉判敏感:" + str(sens.get("kind", "")),
                "confidence": 0.0, "subject": "", "subject_type": "", "date": "", "jd": "",
                "sensitive": True}

    out = call_llm(build_messages(VISION_PROMPT, imgs, name, path))
    jd = out.get("jd", "") if out.get("jd") in JD_OK else ""
    subj = (out.get("subject") or "").strip()
    typ = out.get("subject_type") or ""
    subj, typ = pick_subject_vl(jd, subj, typ)
    date = out.get("date", "") if re.fullmatch(r"\d{4}(-\d{2})?", out.get("date", "") or "") else ""
    return {"mode": "vision", "desc": (out.get("desc") or "")[:60], "subject": subj,
            "subject_type": typ, "date": date, "jd": jd,
            "confidence": out.get("confidence", 0.5),
            "evidence": (out.get("evidence") or "")[:40], "sensitive": False}


def main():
    if not KEY:
        print("缺少 SPACEAGENTS_AUTO_API_KEY，跳过视觉解析（跟 ai_fill 同一把 Key）。")
        return 3

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", action="append", default=None, help="文件根目录，默认主目录")
    ap.add_argument("--deps", default="", help="依赖目录")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    deps = args.deps or os.environ.get("KB_STEWARD_DEPS", "")
    if deps and os.path.isdir(deps):
        sys.path.insert(0, deps)
    roots = [os.path.abspath(r) for r in (args.root or [os.path.expanduser("~")])]
    OUT = os.path.abspath(args.out)

    recs = []
    # 1) 无正文 / 有效中文极少的 pdf/pptx/ppt（来自 13 缓存）
    p13 = os.path.join(OUT, "13-全文解析缓存.jsonl")
    if os.path.isfile(p13):
        for line in open(p13, encoding="utf-8"):
            r = json.loads(line)
            ext = (r.get("ext") or "").lower()
            if ext not in (".pdf", ".pptx", ".ppt"):
                continue
            text = r.get("text") or ""
            st = r.get("status") or ""
            no_text = st in ("cad_no_text", "legacy_unreadable", "legacy_partial", "待OCR", "待转换")
            # 纯图 PPT 的 text 往往只剩 "[slide]N" 占位，有效中文几乎为 0
            if no_text or cjk_count(text) < MIN_CJK:
                recs.append({"source_path": r["source_path"], "file_name": r["file_name"],
                             "ext": ext, "full_path": _resolve(r["source_path"], roots)})

    # 2) 证照图片（来自 03 图片候选，通道==OCR）
    p03 = os.path.join(OUT, "03-图片与CAD候选.csv")
    if os.path.isfile(p03):
        for r in csv.DictReader(open(p03, encoding="utf-8-sig")):
            if r.get("通道") != "OCR":
                continue
            ext = (r.get("扩展名") or "").lower()
            recs.append({"source_path": r["相对路径"], "file_name": r["文件名"], "ext": ext,
                         "full_path": _resolve(r["相对路径"], roots)})

    # 去重 + 敏感文件名粗筛（身份证/账号密码等，命中的名字直接跳过，不送图）
    seen = set()
    todo = []
    for r in recs:
        if r["source_path"] in seen or not os.path.isfile(r["full_path"]):
            continue
        seen.add(r["source_path"])
        if sensitive_name(r["file_name"], r["source_path"]):
            continue
        todo.append(r)

    if args.limit:
        todo = todo[:args.limit]

    cache_path = os.path.join(OUT, CACHE_NAME)
    cache = {}
    if os.path.isfile(cache_path):
        cache = json.load(open(cache_path, encoding="utf-8"))

    notdone = [r for r in todo if r["source_path"] not in cache]
    print(f"视觉候选 {len(todo)}，待解析 {len(notdone)}（证件/纯图PPT/扫描PDF）", flush=True)

    lock = threading.Lock()

    def work(r):
        res = process_one(r)
        with lock:
            cache[r["source_path"]] = res

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, notdone))

    json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    modes = collections.Counter(v.get("mode") for v in cache.values())
    with_subj = sum(1 for v in cache.values() if v.get("subject"))
    sens = sum(1 for v in cache.values() if v.get("sensitive"))
    print(json.dumps({"total": len(cache), "modes": dict(modes),
                      "with_subject": with_subj, "sensitive_img": sens,
                      "cache": cache_path}, ensure_ascii=False, indent=2))
    return 0


def _resolve(rel, roots):
    for rt in roots:
        cand = os.path.join(rt, rel)
        if os.path.isfile(cand):
            return cand
        parts = rel.split("/", 1)
        if len(parts) == 2:
            cand2 = os.path.join(rt, parts[1])
            if os.path.isfile(cand2):
                return cand2
    return os.path.join(roots[0], rel)


if __name__ == "__main__":
    sys.exit(main())