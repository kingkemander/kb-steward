#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""改完 runtime 代码后跑一次：重新打包、回填校验值、打印发布命令。

用法：
    python3 make_release.py            # 按 VERSION 里的版本号打包
    python3 make_release.py --bump 1.3.8   # 先改版本号再打包

它做四件事（顺序不能反）：
1. 打包 runtime/ → runtime-v<版本>.zip（排除 __pycache__、.DS_Store）
2. 计算 zip 的 SHA-256，回填 bootstrap.py 的 VERSION / ASSET / SHA256
3. 计算 bootstrap.py 的 SHA-256，回填 skills/kb-steward/SKILL.md 的校验值与下载地址
4. 打印 git 与 release 命令

为什么必须跑它：安装器会校验 zip 的 SHA-256、入口技能会校验 bootstrap.py 的 SHA-256。
只改代码不重算，同事安装会直接失败。
"""
import argparse
import hashlib
import os
import pathlib
import re
import subprocess
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
BOOTSTRAP = ROOT / "bootstrap.py"
SKILL = ROOT / "skills" / "kb-steward" / "SKILL.md"
REPO_DEFAULT = "kingkemander/kb-steward"


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def pack(version):
    zip_path = ROOT / f"runtime-v{version}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(RUNTIME):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".DS_Store")]
            for fn in filenames:
                if fn in (".DS_Store",) or fn.endswith(".pyc"):
                    continue
                full = pathlib.Path(dirpath) / fn
                z.write(full, full.relative_to(RUNTIME))
        # 模板在仓库根（标准包结构），打包时放进 zip 根，供 bootstrap 注册智能体
        tpl = ROOT / "agent-template.md"
        if tpl.is_file():
            z.write(tpl, "agent-template.md")
    return zip_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bump", default="", help="新版本号，如 1.3.8；不给则用 VERSION 现值")
    args = ap.parse_args()

    if args.bump:
        (ROOT / "VERSION").write_text(args.bump.strip() + "\n")

    version = (ROOT / "VERSION").read_text().strip()
    asset = f"runtime-v{version}.zip"

    zip_path = pack(version)
    zip_sha = sha256(zip_path)

    s = BOOTSTRAP.read_text()
    repo = re.search(r'REPO = "([^"]+)"', s)
    repo = repo.group(1) if repo else REPO_DEFAULT
    s = re.sub(r'VERSION = "v[0-9.]+"', f'VERSION = "v{version}"', s)
    s = re.sub(r'ASSET = "runtime-v[0-9.]+\.zip"', f'ASSET = "{asset}"', s)
    s = re.sub(r'SHA256 = "[0-9a-f]{64}"', f'SHA256 = "{zip_sha}"', s)
    BOOTSTRAP.write_text(s)
    boot_sha = sha256(BOOTSTRAP)

    t = SKILL.read_text()
    t = re.sub(r'releases/download/v[0-9.]+/bootstrap\.py',
               f'releases/download/v{version}/bootstrap.py', t)
    t = re.sub(r'releases/tags/v[0-9.]+', f'releases/tags/v{version}', t)
    t = re.sub(r"h ?== ?'[0-9a-f]{64}'", f"h == '{boot_sha}'", t)
    t = re.sub(r'assert h == "[0-9a-f]{64}"', f'assert h == "{boot_sha}"', t)
    t = re.sub(r'h == "[0-9a-f]{64}"', f'h == "{boot_sha}"', t)
    t = t.replace("__SHA__", boot_sha)
    SKILL.write_text(t)

    print(f"版本      : v{version}")
    print(f"zip       : {zip_path.name}  ({zip_path.stat().st_size} bytes)")
    print(f"zip sha   : {zip_sha}")
    print(f"boot sha  : {boot_sha}")
    print(f"仓库      : {repo}")
    print("\n下一步（在仓库根目录）：")
    print(f'  git add -A && git commit -m "v{version}: <一句话说明>" && git push origin main')
    print(f'  gh release create v{version} --title "v{version}" --notes "<说明>" bootstrap.py {asset}')
    print("\n发布后自检：")
    print(f'  export GITHUB_TOKEN=$(gh auth token)')
    print(f'  python3 bootstrap.py --workspace /tmp/ws-check   # 干净目录实测一次')
    return 0


if __name__ == "__main__":
    sys.exit(main())
