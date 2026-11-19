#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""旧格式（.doc / .ppt）文本抽取：不转换格式，只从 OLE 流里捞可读文本。

为什么要它：本地转换（LibreOffice/antiword）会丢表格与版式，转出来的纯文本
和原文对不上，分类和命名就会偏。这里把流里的原始字符串尽量捞出来，
交给大模型判断「这是什么文件、归哪类、主体和日期是什么」，不再依赖转换工具。

用法：
    python3 parse_legacy.py --file "D:/a.doc"
    python3 parse_legacy.py --file "D:/b.ppt" --json
"""
import argparse
import json
import os
import re
import sys

CJK = re.compile(r"[\u4e00-\u9fa5]")
# 可读片段：中英文、数字、常用标点，长度 ≥4
SEG = re.compile(r"[\u4e00-\u9fa5A-Za-z0-9，。；：、（）《》「」%\-\.\s/]{4,}")
NOISE = re.compile(r"^(Microsoft|Times New Roman|SimSun|Arial|Calibri|Normal|Default|"
                   r"Root Entry|WordDocument|PowerPoint Document|Current User|SummaryInformation)$", re.I)

STREAMS = ("WordDocument", "1Table", "0Table", "PowerPoint Document",
           "DocumentSummaryInformation", "SummaryInformation")


def _decode_all(raw):
    """同一段字节分别按 UTF-16LE 与 GBK 解读，取更像中文的那个。"""
    out = []
    for enc in ("utf-16-le", "gbk"):
        try:
            t = raw.decode(enc, errors="ignore")
        except Exception:
            continue
        score = len(CJK.findall(t))
        out.append((score, t))
    if not out:
        return ""
    out.sort(key=lambda x: -x[0])
    return out[0][1]


def extract(path, max_chars=40000):
    try:
        import olefile
    except ImportError:
        return "", "missing_dep:olefile"
    if not os.path.isfile(path):
        return "", "not_found"
    try:
        if not olefile.isOleFile(path):
            return "", "not_ole"
    except OSError:
        return "", "not_found"
    try:
        ole = olefile.OleFileIO(path)
    except Exception as e:
        return "", f"err:{type(e).__name__}"

    chunks = []
    try:
        names = ole.listdir(streams=True, storages=False)
        for entry in names:
            name = entry[-1]
            if name not in STREAMS and "Table" not in name:
                continue
            try:
                raw = ole.openstream(entry).read()
            except Exception:
                continue
            if len(raw) < 16:
                continue
            text = _decode_all(raw)
            for m in SEG.findall(text):
                s = m.strip()
                if len(s) < 4 or NOISE.match(s):
                    continue
                if len(CJK.findall(s)) == 0 and not re.search(r"[A-Za-z]{4,}", s):
                    continue
                chunks.append(s)
    finally:
        try:
            ole.close()
        except Exception:
            pass

    seen, out = set(), []
    total = 0
    for c in chunks:
        k = c[:60]
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
        total += len(c)
        if total >= max_chars:
            break

    text = "\n".join(out)
    cjk = len(CJK.findall(text))
    quality = round(cjk / max(len(text), 1), 2)
    status = "legacy_text" if cjk >= 30 else "legacy_unreadable"
    return text, status, quality


def _scan_bytes(path, max_chars=60000, block=4 * 1024 * 1024):
    """整文件分块扫描：不依赖 OLE 结构，直接从字节里捞中文。

    旧版 Word/PPT 的正文多以 UTF-16LE 存储，整文件扫描能覆盖
    olefile 漏掉的片段（例如非标准容器、嵌入对象）。
    """
    chunks, total = [], 0
    try:
        with open(path, "rb") as f:
            while total < max_chars:
                raw = f.read(block)
                if not raw:
                    break
                best = ""
                for enc in ("utf-16-le", "gbk"):
                    try:
                        t = raw.decode(enc, errors="ignore")
                    except Exception:
                        continue
                    if len(CJK.findall(t)) > len(CJK.findall(best)):
                        best = t
                for m in SEG.findall(best):
                    x = m.strip()
                    if len(x) >= 6 and not NOISE.match(x):
                        chunks.append(x)
                        total += len(x)
                        if total >= max_chars:
                            break
    except OSError:
        pass
    return "\n".join(chunks)


def _textutil_html(path):
    """macOS：textutil 转 HTML 再剥标签，比纯 txt 保留更多结构。"""
    import shutil as _sh, subprocess, re as _re
    if not _sh.which("textutil"):
        return ""
    try:
        r = subprocess.run(["textutil", "-convert", "html", "-stdout", path],
                           capture_output=True, timeout=90)
        if r.returncode != 0:
            return ""
        h = r.stdout.decode("utf-8", "ignore")
        h = _re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", h)
        h = _re.sub(r"(?s)<[^>]+>", "\n", h)
        return _re.sub(r"\n{2,}", "\n", h)
    except Exception:
        return ""


def extract_deep(path, max_chars=60000):
    """多层兜底：OLE 流 → textutil(HTML) → 整文件字节扫描。

    返回 (text, status, cjk_ratio)。status:
      legacy_text       有足够中文，可直接给模型
      legacy_partial    抽到一些，但偏少，仍交模型（带上下文）
      legacy_unreadable 基本抽不到
    """
    parts = []
    t1, st1 = ("", "")
    if os.path.isfile(path):
        try:
            r = extract(path)
            if len(r) == 3:
                t1, st1, _q = r
            else:
                t1, st1 = r
        except Exception:
            t1, st1 = "", "err"
        if t1:
            parts.append(t1)
        if path.lower().endswith(".doc"):
            h = _textutil_html(path)
            if h.strip():
                parts.append(h)
        b = _scan_bytes(path, max_chars=max_chars)
        if b:
            parts.append(b)

    merged, seen, total = [], set(), 0
    for block in parts:
        for line in block.split("\n"):
            x = line.strip()
            if len(x) < 4:
                continue
            k = x[:50]
            if k in seen:
                continue
            seen.add(k)
            merged.append(x)
            total += len(x)
            if total >= max_chars:
                break
        if total >= max_chars:
            break

    text = "\n".join(merged)
    cjk = len(CJK.findall(text))
    quality = round(cjk / max(len(text), 1), 2)
    if cjk >= 30:
        status = "legacy_text"
    elif cjk >= 8:
        status = "legacy_partial"
    else:
        status = "legacy_unreadable"
    return text, status, quality


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--deep", action="store_true", help="多层兜底（推荐）")
    args = ap.parse_args()

    p = os.path.abspath(args.file)
    if not os.path.isfile(p):
        print(f"文件不存在：{p}")
        return 2
    res = extract_deep(p) if args.deep else extract(p)
    if len(res) == 2:
        text, status = res
        quality = 0
    else:
        text, status, quality = res

    if args.json:
        print(json.dumps({"file": p, "status": status, "chars": len(text),
                          "cjk_ratio": quality, "head": text[:400]},
                         ensure_ascii=False, indent=2))
    else:
        print(f"状态：{status}｜字符：{len(text)}｜中文占比：{quality}")
        print("-" * 40)
        print(text[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
