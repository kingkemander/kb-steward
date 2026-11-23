#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全量解析：把 396 份工作文档/CAD 的正文完整读出来，缓存成 JSONL。

与 L2 的 extract 区别：L2 只读开头做分类；这里读全文，供 AI 定目录与补字段。
"""
import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

MAX_CHARS = 300000


def _soffice_convert(path, pathlib_suffix=".txt"):
    """用 LibreOffice 把老格式转成 txt（找不到 soffice 就返回空）。"""
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        return ""
    tmp = tempfile.mkdtemp(prefix="kbconv-")
    try:
        r = subprocess.run([exe, "--headless", "--convert-to", "txt:Text",
                            "--outdir", tmp, path],
                           capture_output=True, timeout=180)
        if r.returncode != 0:
            return ""
        base = os.path.splitext(os.path.basename(path))[0] + pathlib_suffix
        out = os.path.join(tmp, base)
        if os.path.isfile(out):
            with open(out, encoding="utf-8", errors="ignore") as f:
                return f.read()
    except Exception:
        return ""
    return ""


def ocr_pdf(path, pages=3, dpi=200):
    """扫描版 PDF：渲染前几页交给 tesseract 中文识别。

    有文字层就不走这里；走这里说明 PDF 是图片，OCR 比让模型猜准得多。
    """
    import shutil as _sh, subprocess as _sp, tempfile, os as _os
    if not _sh.which("tesseract"):
        return ""
    try:
        import fitz
    except Exception:
        return ""
    tmp = tempfile.mkdtemp(prefix="ocr_")
    texts = []
    try:
        doc = fitz.open(path)
        n = min(doc.page_count, pages)
        for i in range(n):
            pix = doc[i].get_pixmap(dpi=dpi)
            png = _os.path.join(tmp, f"p{i}.png")
            pix.save(png)
            r = _sp.run(["tesseract", png, "stdout", "-l", "chi_sim+eng"],
                        capture_output=True, timeout=180)
            t = r.stdout.decode("utf-8", "ignore").strip()
            if t:
                texts.append(t)
        doc.close()
    except Exception:
        pass
    finally:
        import shutil as _sh2
        _sh2.rmtree(tmp, ignore_errors=True)
    txt = _ocr_clean("\n".join(texts))
    cjk = len(re.findall(r"[\u4e00-\u9fff]", txt))
    # 有效行占比太低说明是图纸/CAD 字体，OCR 只会产出噪声，交给模型按线索判断更准
    lines = [x for x in txt.split("\n") if len(x) >= 4]
    good = [x for x in lines
            if len(re.findall(r"[\u4e00-\u9fff0-9]", x)) / max(len(x), 1) >= 0.3]
    ratio = len(good) / max(len(lines), 1) if lines else 0
    if cjk < 30 or ratio < 0.35:
        return ""
    return txt


def _ocr_clean(txt):
    """丢掉 OCR 的纯字母噪声行（CAD 字体识别失败的典型产物）。"""
    keep = []
    for line in txt.split("\n"):
        x = line.strip()
        if len(x) < 4:
            continue
        cjk = len(re.findall(r"[\u4e00-\u9fff]", x))
        digits = len(re.findall(r"[0-9]", x))
        if cjk == 0 and digits < 3:
            continue
        if cjk / max(len(x), 1) < 0.15 and digits < 3:
            continue
        keep.append(x)
    return "\n".join(keep)


def extract_full(path, ext):
    try:
        if ext == ".docx":
            import docx
            d = docx.Document(path)
            parts = [p.text for p in d.paragraphs if p.text.strip()]
            for t in d.tables:
                for row in t.rows:
                    parts.append(" | ".join(c.text.strip() for c in row.cells if c.text.strip()))
            return "\n".join(parts), "ok"
        if ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            parts = []
            for ws in wb.worksheets:
                parts.append(f"[sheet]{ws.title}")
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    if i > 3000:
                        break
                    vals = [str(v) for v in row if v is not None]
                    if vals:
                        parts.append(" | ".join(vals))
            wb.close()
            return "\n".join(parts), "ok"
        if ext == ".pptx":
            from pptx import Presentation
            prs = Presentation(path)
            parts = []
            for idx, s in enumerate(prs.slides, 1):
                parts.append(f"[slide]{idx}")
                for sh in s.shapes:
                    if sh.has_text_frame and sh.text_frame.text.strip():
                        parts.append(sh.text_frame.text)
                    if getattr(sh, "has_table", False):
                        for row in sh.table.rows:
                            parts.append(" | ".join(c.text for c in row.cells))
            return "\n".join(parts), "ok"
        if ext == ".pdf":
            import fitz
            doc = fitz.open(path)
            n = min(doc.page_count, 1000)
            parts = [doc[i].get_text() for i in range(n)]
            txt = "\n".join(parts)
            if txt.strip():
                doc.close()
                return txt, "ok"
            # 无图片 + 无文字 = 矢量图纸（CAD 导出），OCR 只会出噪声，交模型按线索判断
            try:
                p0 = doc[0]
                imgs = len(p0.get_images(full=True))
                draws = len(p0.get_drawings())
            except Exception:
                imgs, draws = 1, 0
            doc.close()
            if draws > 20000:
                return "", "cad_no_text"
            ocr = ocr_pdf(path)
            return (ocr, "ok-ocr") if ocr else ("", "cad_no_text")
        if ext == ".csv":
            for enc in ("utf-8-sig", "gbk", "utf-8"):
                try:
                    with open(path, encoding=enc, errors="ignore") as f:
                        return "".join([next(f, "") for _ in range(20000)]), "ok"
                except UnicodeDecodeError:
                    continue
            return "", "待转换"
        if ext == ".doc":
            # macOS 自带 textutil 质量最好；没有就走 OLE 抽原文，交给大模型判断
            if shutil.which("textutil"):
                r = subprocess.run(["textutil", "-convert", "txt", "-stdout", path],
                                   capture_output=True, timeout=60)
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout.decode("utf-8", "ignore"), "ok"
            try:
                import parse_legacy
                t, st2, _q = parse_legacy.extract_deep(path)
            except Exception:
                t, st2 = "", "legacy_unreadable"
            if st2 in ("legacy_text", "legacy_partial"):
                return t, st2
            return "", "legacy_unreadable"
        if ext == ".xls":
            import xlrd
            wb = xlrd.open_workbook(path)
            parts = []
            for sh in wb.sheets():
                parts.append(f"[sheet]{sh.name}")
                for r in range(min(sh.nrows, 3000)):
                    vals = [str(sh.cell_value(r, c)) for c in range(min(sh.ncols, 30))]
                    vals = [v for v in vals if v.strip()]
                    if vals:
                        parts.append(" | ".join(vals))
            return "\n".join(parts), "ok"
        if ext == ".ppt":
            try:
                import parse_legacy
                t, st2, _q = parse_legacy.extract_deep(path)
            except Exception:
                t, st2 = "", "legacy_unreadable"
            if st2 in ("legacy_text", "legacy_partial"):
                return t, st2
            return "", "legacy_unreadable"
        if ext == ".dxf":
            txt = []
            with open(path, "r", errors="ignore") as f:
                lines = f.readlines()
            for i in range(len(lines) - 1):
                if lines[i].strip() in ("1", "3") and lines[i + 1].strip():
                    v = lines[i + 1].strip()
                    if not re.fullmatch(r"[\d.\-]+", v):
                        txt.append(v)
            return "\n".join(txt[:20000]), "ok" if txt else "cad_no_text"
        if ext == ".dwg":
            return "", "cad_no_text"
    except ImportError as e:
        return "", f"missing_dep:{getattr(e, 'name', '')}"
    except Exception as e:
        # 损坏/伪造文件：归一为 unreadable，别把异常类名当状态抛给人看
        return "", "unreadable"
    return "", "skip"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", action="append", default=None,
                    help="文件所在根目录，可重复；默认用户主目录")
    ap.add_argument("--deps", default="", help="自定义依赖目录（install_deps.py 装到的地方）")
    args = ap.parse_args()

    deps = args.deps or os.environ.get("KB_STEWARD_DEPS", "")
    if deps and os.path.isdir(deps):
        sys.path.insert(0, deps)
        print(f"依赖目录：{deps}", flush=True)

    roots = [os.path.abspath(r) for r in (args.root or [os.path.expanduser("~")])]
    OUT = os.path.abspath(args.out)

    rows = list(csv.DictReader(open(os.path.join(OUT, "09-分类结果.csv"), encoding="utf-8-sig")))
    out_path = os.path.join(OUT, "13-全文解析缓存.jsonl")
    stats = {}
    done = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            full = None
            for rt in roots:
                cand = os.path.join(rt, r["source_path"])
                if os.path.isfile(cand):
                    full = cand
                    break
                # 多范围扫描时相对路径带前缀，尝试去掉第一段
                parts = r["source_path"].split("/", 1)
                if len(parts) == 2:
                    cand2 = os.path.join(rt, parts[1])
                    if os.path.isfile(cand2):
                        full = cand2
                        break
            if full is None:
                full = os.path.join(roots[0], r["source_path"])
            ext = r["ext"].lower()
            text, st = extract_full(full, ext)
            text = text[:MAX_CHARS]
            rec = {
                "source_path": r["source_path"],
                "file_name": r["file_name"],
                "ext": ext,
                "status": st,
                "chars": len(text),
                "head": text[:2500],
                "text": text,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            stats[st] = stats.get(st, 0) + 1
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(rows)}", flush=True)
    missing = sorted({k.split(":", 1)[1] for k in stats if k.startswith("missing_dep:")})
    if missing:
        print("缺少解析库：" + "、".join(missing) + "（先跑 doctor.py 体检，再按提示指定盘位安装）")
    print(json.dumps({"done": done, "stats": stats, "missing_deps": missing, "cache": out_path,
                      "total_chars": sum(1 for _ in open(out_path, encoding='utf-8'))},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
