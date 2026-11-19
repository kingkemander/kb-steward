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
    ("soffice", "转换 .doc / .xls / .ppt 老格式", "LibreOffice（Windows/Linux 必装）"),
    ("libreoffice", "转换老格式（Linux 别名）", "LibreOffice"),
    ("textutil", "转换 .doc 老格式", "macOS 系统自带"),
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
    has_soffice = any(t["ok"] for t in tools if t["cmd"] in ("soffice", "libreoffice"))
    has_textutil = any(t["ok"] for t in tools if t["cmd"] == "textutil")
    has_ocr = any(t["ok"] for t in tools if t["cmd"] == "tesseract")

    can = []
    if all(m["ok"] for m in mods):
        can.append("新版 docx/xlsx/pptx/pdf 全部可解析")
    else:
        ok_ext = [m["use"].split()[0] for m in mods if m["ok"]]
        if ok_ext:
            can.append("可解析：" + "、".join(ok_ext))
    if has_soffice or has_textutil:
        can.append(".doc/.xls/.ppt 老格式可转换后解析")
    if has_ocr:
        can.append("扫描件可 OCR")

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
        "need_old_format_converter": not (has_soffice or has_textutil),
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
