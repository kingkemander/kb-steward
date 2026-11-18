#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb-steward 第一层扫描器（纯标准库，不读内容、不联网、不动文件）

只做三件事：
1) 盘位与大目录盘点
2) 全量文件登记（路径/大小/扩展名/真实格式线索）
3) L1 前置分拣 + L1.5 格式白名单（顺序铁律：路径排除先于格式白名单）

用法:
  python3 scan_l1.py --root /Users/tianhao --out ./out
"""
import argparse
import csv
import json
import os
import stat
import sys
import time
from collections import Counter, defaultdict

# ---------- L1 路径排除（必须跑在格式白名单之前） ----------
DEV_PATH_MARKS = [
    "site-packages", "/venv/", "/.venv/", "node_modules", "/.git/",
    "/dist/", "/build/", "__pycache__", "/.tox/", "/.mypy_cache/",
    "/.pytest_cache/", "/target/debug", "/target/release",
    "/.deps/", "/tests/data/", "/runtime-v0", "/runtime-v1",
]

# 系统目录与个人隐私目录：不进入（顶层判断）
SKIP_TOP = {
    "Library", ".Trash", ".cache", ".npm", ".cargo", ".rustup", ".gradle",
    ".m2", ".pyenv", ".nvm", ".oracle_jre_usage", ".Trash-*", ".local",
    ".CFUserTextEncoding", ".DS_Store", "Parallels", ".docker", ".colima",
    ".ollama", ".lmstudio", ".cursor", ".vscode", ".config",
}

# 中间产物目录标记（图片/OCR 类，SKILL 实测教训：不能算进证照候选）
INTERMEDIATE_MARKS = [
    "/pass", "/page_", "_verify", "/artifacts/", "extracted_images",
    "/t3-t6/", "/canvas-image", "/whiteboard",
]

GARBAGE_PREFIX = (".~", "~\u0024", "~$")
GARBAGE_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".localized"}

CODE_EXT = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".sh", ".bash", ".zsh",
    ".rb", ".php", ".c", ".h", ".cpp", ".hpp", ".cs", ".rs", ".kt", ".swift",
    ".sql", ".pl", ".lua", ".r", ".m", ".mm", ".scala", ".vue", ".css", ".scss",
    ".less", ".ipynb", ".ps1", ".bat", ".cmd", ".groovy", ".gradle", ".toml",
}

DOC_EXT = {".docx", ".doc", ".pdf", ".ppt", ".pptx", ".xlsx", ".xls", ".csv"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".heic", ".tif", ".tiff"}
CAD_EXT = {".dxf", ".dwg"}
NONFINAL_EXT = {".md", ".json", ".txt", ".log", ".yaml", ".yml", ".ini", ".conf", ".rtf"}
MEDIA_EXT = {
    ".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".webm", ".m4v", ".mpg", ".mpeg",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aiff",
}
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".dmg", ".iso", ".pkg"}
OTHER_SKIP_EXT = {
    ".exe", ".dll", ".so", ".dylib", ".bin", ".app", ".jar", ".class", ".o", ".a",
    ".ttf", ".otf", ".woff", ".woff2", ".eot", ".icns", ".svg", ".plist",
}


def is_under_dev(path_lower):
    for m in DEV_PATH_MARKS:
        if m in path_lower:
            return True
    return False


def classify(path, name, size, ext):
    """返回 (层级, 判定, 通道, 理由)"""
    pl = path.lower()
    nl = name.lower()

    # L1-A 路径排除（第一优先）
    if is_under_dev(pl):
        return ("L1", "开发不晋升", "-", "路径命中开发目录")

    # L1-B 垃圾
    if name.startswith(GARBAGE_PREFIX) or name in GARBAGE_NAMES:
        return ("L1", "垃圾", "-", "临时/系统垃圾文件")
    if size == 0:
        return ("L1", "垃圾", "-", "0 字节")
    if nl in {"fonttest", "t", "test"}:
        return ("L1", "垃圾", "-", "测试件")

    # L1-C 代码
    if ext in CODE_EXT:
        return ("L1", "开发不晋升", "-", "代码扩展名")

    # L1.5 格式白名单（正向放行）
    if ext in DOC_EXT:
        return ("L1.5", "工作文档", "文档", "白名单格式")
    if ext in IMG_EXT:
        if any(m in pl for m in INTERMEDIATE_MARKS):
            return ("L1.5", "图片", "OCR-疑似中间产物", "路径命中中间产物目录")
        return ("L1.5", "图片", "OCR", "白名单格式")
    if ext in CAD_EXT:
        return ("L1.5", "CAD", "CAD", "白名单格式")
    if ext in NONFINAL_EXT:
        return ("L1.5", "非最终产物", "-", "中间产物/笔记/配置")
    if ext in MEDIA_EXT:
        return ("L1.5", "素材媒体", "只登记", "音视频素材")
    if ext in ARCHIVE_EXT:
        return ("L1.5", "归档包", "只登记", "压缩包/镜像")
    if ext in OTHER_SKIP_EXT:
        return ("L1.5", "其他排除", "-", "非交付格式")

    return ("L1.5", "未识别", "待定", "扩展名不在白名单")


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}"
        n /= 1024.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-files", type=int, default=0, help="0=不限")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    t0 = time.time()
    rows = []
    skipped_dirs = []
    errors = []
    top_dirs = Counter()
    count = 0

    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                entries = list(it)
        except PermissionError as e:
            errors.append((cur, str(e)))
            continue
        except OSError as e:
            errors.append((cur, str(e)))
            continue

        for e in entries:
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            rel = os.path.relpath(e.path, root)
            if is_dir:
                base = e.name
                # 顶层跳过系统/隐私目录
                if os.sep not in rel and (base in SKIP_TOP or base.startswith(".Trash")):
                    skipped_dirs.append(rel)
                    continue
                # 顶层点目录：AI 客户端/工具缓存（.openclaw/.qoder/.codex 等），非公司资产
                if os.sep not in rel and base.startswith("."):
                    skipped_dirs.append(rel + " (隐藏工具目录)")
                    continue
                # 开发/依赖目录：整目录跳过（只记一次）
                low = e.path.lower() + os.sep
                if is_under_dev(low):
                    skipped_dirs.append(rel)
                    continue
                stack.append(e.path)
                continue

            try:
                st = e.stat(follow_symlinks=False)
            except OSError as ex:
                errors.append((e.path, str(ex)))
                continue
            if not stat.S_ISREG(st.st_mode):
                continue

            name = e.name
            ext = os.path.splitext(name)[1].lower()
            path = e.path
            level, verdict, channel, reason = classify(path, name, st.st_size, ext)
            depth = rel.count(os.sep)
            top = rel.split(os.sep)[0] if os.sep in rel else "(root)"
            top_dirs[top] += 1

            rows.append([
                rel, name, ext, st.st_size, human_size(st.st_size),
                int(st.st_mtime), level, verdict, channel, reason, depth,
            ])
            count += 1
            if args.max_files and count >= args.max_files:
                stack.clear()
                break

    # 写全量清单
    all_csv = os.path.join(out, "01-全量文件清单.csv")
    with open(all_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["相对路径", "文件名", "扩展名", "字节", "大小", "修改时间",
                    "判定层级", "判定", "通道", "理由", "深度"])
        for r in rows:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(r[5]))
            w.writerow(r[:5] + [ts] + r[6:])

    # 分组导出
    groups = defaultdict(list)
    for r in rows:
        groups[(r[7], r[8])].append(r)

    def dump(fname, pred):
        p = os.path.join(out, fname)
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["相对路径", "文件名", "扩展名", "字节", "大小", "修改时间",
                        "判定层级", "判定", "通道", "理由"])
            for r in rows:
                if pred(r):
                    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(r[5]))
                    w.writerow(r[:5] + [ts] + r[6:])
        return p

    p_doc = dump("02-工作文档候选.csv", lambda r: r[8] == "文档")
    p_ocr = dump("03-图片与CAD候选.csv", lambda r: r[8] in ("OCR", "CAD"))
    p_skip = dump("04-已排除-垃圾与开发.csv", lambda r: r[7] in ("垃圾", "开发不晋升"))
    p_media = dump("05-素材媒体与归档-只登记.csv", lambda r: r[8] in ("只登记",))
    p_other = dump("06-非最终产物与未识别.csv",
                   lambda r: r[7] in ("非最终产物", "未识别", "其他排除"))
    p_inter = dump("07-图片-疑似中间产物.csv",
                   lambda r: r[8] == "OCR-疑似中间产物")

    # 统计
    verdict_cnt = Counter(r[7] for r in rows)
    ext_cnt = Counter(r[2] or "(无扩展名)" for r in rows)
    channel_cnt = Counter(r[8] for r in rows)
    doc_ext = Counter(r[2] for r in rows if r[8] == "文档")
    size_by_verdict = defaultdict(int)
    for r in rows:
        size_by_verdict[r[7]] += r[3]

    summary = {
        "root": root,
        "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_sec": round(time.time() - t0, 1),
        "total_files": len(rows),
        "total_size": human_size(sum(r[3] for r in rows)),
        "verdict_counts": dict(verdict_cnt),
        "channel_counts": dict(channel_cnt),
        "doc_ext_counts": dict(doc_ext.most_common()),
        "size_by_verdict": {k: human_size(v) for k, v in size_by_verdict.items()},
        "top_dirs": dict(top_dirs.most_common(40)),
        "skipped_dirs_count": len(skipped_dirs),
        "skipped_dirs_sample": skipped_dirs[:80],
        "error_count": len(errors),
        "errors_sample": errors[:20],
        "outputs": [all_csv, p_doc, p_ocr, p_skip, p_media, p_other],
    }
    with open(os.path.join(out, "00-扫描汇总.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps({k: summary[k] for k in
                      ("root", "elapsed_sec", "total_files", "total_size",
                       "verdict_counts", "channel_counts", "doc_ext_counts",
                       "skipped_dirs_count", "error_count")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
