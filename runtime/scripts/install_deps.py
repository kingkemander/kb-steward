#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把解析依赖装到用户指定的目录（不碰系统 Python、不默认装 C 盘）。

用法：
    python3 install_deps.py --target "D:/kb-deps"            # 装全部缺失的
    python3 install_deps.py --target "D:/kb-deps" --only xlrd python-pptx
    python3 install_deps.py --target "D:/kb-deps" --dry-run   # 只打印命令

安装后用：
    python3 parse_all.py --out ./kb-整理 --deps "D:/kb-deps"
或设环境变量：
    export KB_STEWARD_DEPS="D:/kb-deps"
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

DEFAULT_PKGS = ["xlrd", "python-pptx"]
ALL_PKGS = ["python-docx", "openpyxl", "xlrd", "python-pptx", "pymupdf"]
# 约 30 MB，留 200 MB 余量
NEED_FREE_MB = 200


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="依赖安装目录（必须由用户指定盘位）")
    ap.add_argument("--only", nargs="*", default=[], help="只装这些包")
    ap.add_argument("--all", action="store_true", help="装全部解析库（含 docx/openpyxl/pymupdf）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不执行")
    args = ap.parse_args()

    target = os.path.abspath(args.target)
    pkgs = args.only or (ALL_PKGS if args.all else DEFAULT_PKGS)

    # 目标盘位检查
    probe = target
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    if not os.path.exists(probe):
        print(f"目标路径不存在：{target}")
        return 2
    try:
        u = shutil.disk_usage(probe)
    except OSError as e:
        print(f"无法读取磁盘信息：{e}")
        return 2
    free_mb = u.free / 2**20
    if free_mb < NEED_FREE_MB:
        print(f"空间不足：{probe} 只剩 {free_mb:.0f} MB，至少需要 {NEED_FREE_MB} MB")
        return 2

    os.makedirs(target, exist_ok=True)
    cmd = [sys.executable, "-m", "pip", "install", "--target", target,
           "--upgrade", "--no-warn-script-location"] + pkgs

    print(json.dumps({"target": target, "free_mb": round(free_mb),
                      "packages": pkgs, "command": " ".join(cmd)}, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("\n[dry-run] 未执行。确认后去掉 --dry-run 再跑一次。")
        return 0

    r = subprocess.run(cmd)
    if r.returncode != 0:
        print(f"\n安装失败（退出码 {r.returncode}）。常见原因：网络不通 / 需要镜像源。")
        print('可试：pip install -i https://pypi.tuna.tsinghua.edu.cn/simple ...')
        return r.returncode

    print(f"\n安装完成：{target}")
    print("下一步：")
    print(f'  python3 parse_all.py --out ./kb-整理 --root "<范围>" --deps "{target}"')
    print("  或一次性：export KB_STEWARD_DEPS=" + f'"{target}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
