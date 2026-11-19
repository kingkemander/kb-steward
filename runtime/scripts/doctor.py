#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境体检：本机能不能解析这些文件，缺什么，装到哪个盘。

只检查，不安装。安装用 install_deps.py，且必须由用户指定目标目录。

用法：
    python3 doctor.py
    python3 doctor.py --deps "D:/kb-deps"     # 同时检查自定义依赖目录
"""
import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys

# (模块名, 用途, pip 包名, 大约体积)
REQUIRED = [
    ("docx", ".docx 新版 Word", "python-docx", "2 MB"),
    ("openpyxl", ".xlsx 新版 Excel", "openpyxl", "3 MB"),
    ("xlrd", ".xls 老式 Excel", "xlrd", "1 MB"),
    ("pptx", ".pptx 新版 PPT", "python-pptx", "5 MB"),
    ("fitz", ".pdf 文字层", "pymupdf", "20 MB"),
]

# (命令名, 用途, 说明)
TOOLS = [
    ("textutil", "转换 .doc（免安装）", "macOS 系统自带"),
    ("antiword", "转换 .doc（免安装，~1MB）", "Windows/Linux 可选"),
    ("catdoc", "转换 .doc（免安装）", "catdoc 套件"),
    ("soffice", "转换 .doc/.ppt（全能，~300MB）", "LibreOffice，可最后再装"),
    ("libreoffice", "转换老格式（Linux 别名）", "LibreOffice"),
    ("tesseract", "扫描件 OCR", "可选；不装则标「待OCR」"),
]


def has_module(name):
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def has_cmd(name):
    return shutil.which(name) is not None


def volumes():
    """列出可用的盘位与剩余空间。"""
    out = []
    if platform.system() == "Windows":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            root = f"{letter}:\\"
            if os.path.exists(root):
                try:
                    u = shutil.disk_usage(root)
                    out.append({"path": root, "free_gb": round(u.free / 2**30, 1)})
                except OSError:
                    pass
    else:
        cands = ["/"]
        vd = "/Volumes"
        if os.path.isdir(vd):
            for n in sorted(os.listdir(vd)):
                p = os.path.join(vd, n)
                if os.path.isdir(p) and not os.path.islink(p):
                    cands.append(p)
        if platform.system() == "Linux":
            for m in ("/mnt", "/media"):
                if os.path.isdir(m):
                    for n in sorted(os.listdir(m)):
                        p = os.path.join(m, n)
                        if os.path.isdir(p):
                            cands.append(p)
        for p in cands:
            try:
                u = shutil.disk_usage(p)
                out.append({"path": p, "free_gb": round(u.free / 2**30, 1)})
            except OSError:
                pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deps", default="", help="自定义依赖目录（pip --target 装到的地方）")
    args = ap.parse_args()

    if args.deps:
        d = os.path.abspath(args.deps)
        if os.path.isdir(d):
            sys.path.insert(0, d)

    mods = []
    for name, use, pkg, size in REQUIRED:
        mods.append({"module": name, "use": use, "pip": pkg, "size": size,
                     "ok": has_module(name)})

    tools = [{"cmd": c, "use": u, "note": n, "ok": has_cmd(c)} for c, u, n in TOOLS]

    missing_mods = [m for m in mods if not m["ok"]]
    ok = {t["cmd"]: t["ok"] for t in tools}
    has_soffice = ok.get("soffice") or ok.get("libreoffice")
    has_doc_reader = ok.get("textutil") or ok.get("antiword") or ok.get("catdoc") or has_soffice
    has_ppt_reader = has_module("olefile") or has_soffice
    has_ocr = ok.get("tesseract")

    can = []
    if all(m["ok"] for m in mods):
        can.append("新版 docx/xlsx/pptx/pdf 全部可解析")
    else:
        ok_ext = [m["use"].split()[0] for m in mods if m["ok"]]
        if ok_ext:
            can.append("可解析：" + "、".join(ok_ext))
    if has_module("xlrd"):
        can.append(".xls 直读（无需转换）")
    if has_doc_reader:
        can.append(".doc 可转换后解析")
    if has_ppt_reader:
        can.append(".ppt 可抽文本" + ("（olefile）" if has_module("olefile") else "（LibreOffice）"))
    if has_ocr:
        can.append("扫描件可 OCR")

    notes = []
    if not has_module("xlrd"):
        notes.append(".xls：装 xlrd（约 1 MB）即可直读，不需要装 LibreOffice。")
    if not has_doc_reader:
        if platform.system() == "Darwin":
            notes.append(".doc：macOS 自带 textutil，本机没有，检查系统版本。")
        else:
            notes.append(".doc：装 antiword（约 1–2 MB）即可读文本；需要保表格/样式再考虑 LibreOffice。")
    elif not has_soffice and not ok.get("textutil"):
        notes.append(".doc：用 antiword 读文本，表格样式会有损失。")
    if not has_module("olefile") and not has_soffice:
        notes.append(".ppt：装 olefile（约 200 KB）可先抽文本；复杂版式需 LibreOffice。")
    if not has_ocr:
        notes.append("扫描件：不装 tesseract 会标「待OCR」，不影响其他格式。")

    report = {
        "platform": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "python_ok": sys.version_info >= (3, 9),
        "modules": mods,
        "tools": tools,
        "deps_dir": os.path.abspath(args.deps) if args.deps else "",
        "volumes": volumes(),
        "can_parse": can,
        "missing": [m["pip"] for m in missing_mods],
        "need_old_format_converter": not has_doc_reader,
        "doc_reader": ("textutil" if ok.get("textutil") else
                       "antiword" if ok.get("antiword") else
                       "catdoc" if ok.get("catdoc") else
                       "libreoffice" if has_soffice else ""),
        "ppt_reader": ("olefile" if has_module("olefile") else
                       "libreoffice" if has_soffice else ""),
        "notes": notes,
        "ocr": has_ocr,
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))

    # 人读建议（给用户看的那几句）
    if missing_mods or report["need_old_format_converter"]:
        lines = ["", "需要补齐："]
        if missing_mods:
            total = sum(1 for _ in missing_mods)
            lines.append(f"- 解析库 {total} 个：" + "、".join(m["pip"] for m in missing_mods))
        if report["need_old_format_converter"]:
            lines.append("- 老格式转换：装 LibreOffice（.doc/.xls/.ppt）")
        lines.append("")
        lines.append("安装时请指定目标盘位，不要装到系统盘。示例：")
        lines.append('  python3 install_deps.py --target "<你选的盘位>/kb-deps"')
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
