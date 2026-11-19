#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""知识库管家（kb-steward）引导器。

用法：
    python3 bootstrap.py --workspace "<工作区绝对路径>"
    python3 bootstrap.py --workspace "<工作区绝对路径>" --package ./runtime-v1.0.0.zip

依赖：只用 Python 标准库。
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import time
import urllib.request
import zipfile

# ============ 需要修改 ============
REPO = "kingkemander/kb-steward"                 # GitHub 仓库 owner/repo
VERSION = "v1.4.3"
ASSET = "runtime-v1.4.3.zip"
SHA256 = "df84d32f8edb4d01ec566f5d5eebcdddb776ab2be5be715528503cec89d9d13c"                          # Release zip 的 SHA-256，打包后回填
PLUGIN_ID = "kb-steward"                 # 落点：.spaceagents/plugins/<PLUGIN_ID>
AGENT_NAME = "知识库管家"
# ==================================


def fetch_asset(browser_url, token):
    """下载顺序：自定义分发地址 → GitHub API（有 Token）→ GitHub 公开地址。

    KB_STEWARD_DIST_URL 可指向任意镜像，用 {version} / {asset} 占位：
      https://gitlab.com/<组>/kb-steward/-/raw/{version}/{asset}
    """
    tmpl = os.environ.get("KB_STEWARD_DIST_URL", "")
    if tmpl:
        u = tmpl.format(version=VERSION, asset=ASSET)
        return urllib.request.urlopen(
            urllib.request.Request(u, headers={"User-Agent": "kb-steward-bootstrap"}),
            timeout=180).read()

    if token:
        api = f"https://api.github.com/repos/{REPO}/releases/tags/{VERSION}"
        req = urllib.request.Request(api, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "kb-steward-bootstrap",
        })
        rel = json.load(urllib.request.urlopen(req, timeout=60))
        asset = next((a for a in rel.get("assets", []) if a.get("name") == ASSET), None)
        if not asset:
            raise RuntimeError(f"release {VERSION} 里没有 {ASSET}")
        req2 = urllib.request.Request(asset["url"], headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/octet-stream",
            "User-Agent": "kb-steward-bootstrap",
        })
        return urllib.request.urlopen(req2, timeout=300).read()
    req = urllib.request.Request(browser_url, headers={
        "Accept": "application/octet-stream",
        "User-Agent": "kb-steward-bootstrap",
    })
    return urllib.request.urlopen(req, timeout=180).read()


def log(msg):
    print(msg, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True, help="当前工作区绝对路径")
    ap.add_argument("--package", help="本地 zip 路径（离线/自测用）；不给则从 GitHub 下载")
    args = ap.parse_args()

    ws = os.path.abspath(args.workspace)
    base = os.path.join(ws, ".spaceagents", "plugins", PLUGIN_ID)
    runtime = os.path.join(base, f"runtime-{VERSION}")
    pointer = os.path.join(base, "current.json")
    os.makedirs(base, exist_ok=True)

    if os.path.isfile(pointer) and os.path.isdir(runtime):
        try:
            with open(pointer, encoding="utf-8") as f:
                info = json.load(f)
            if info.get("version") == VERSION:
                log(f"[跳过] 已安装 {info['version']}：{info['runtime_root']}")
                register(ws, info["runtime_root"])
                return 0
        except Exception:
            pass

    if args.package:
        log(f"[1/4] 读本地包 {args.package}")
        try:
            with open(args.package, "rb") as f:
                data = f.read()
        except OSError as e:
            log(f"读取失败：{e}")
            return 2
        source = os.path.abspath(args.package)
    else:
        url = f"https://github.com/{REPO}/releases/download/{VERSION}/{ASSET}"
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        log(f"[1/4] 下载 {url}" + ("（私有仓库：走 API + Token）" if token else ""))
        try:
            data = fetch_asset(url, token)
        except Exception as e:
            log(f"下载失败：{e}")
            if not token:
                log("该仓库可能是私有：先 `gh auth login`，再 `export GITHUB_TOKEN=$(gh auth token)` 重试；")
                log("或让有权限的人把 zip 发你，用 --package <本地 zip> 安装。")
            log("其他排查：Release 是否已 Publish？版本号是否对？")
            return 2
        source = url

    log("[2/4] 校验 SHA-256")
    got = hashlib.sha256(data).hexdigest()
    if SHA256 and SHA256 != "TODO":
        if got.lower() != SHA256.lower():
            log("校验失败，已停止（文件可能不完整或被替换）")
            log(f"  期望：{SHA256}")
            log(f"  实际：{got}")
            return 3
    else:
        log(f"[警告] 引导器未内置 SHA-256，本次仅记录实际值：{got}")

    log("[3/4] 解压")
    if os.path.isdir(runtime):
        shutil.rmtree(runtime)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(runtime)

    log("[4/4] 写指针")
    info = {
        "version": VERSION,
        "runtime_root": runtime,
        "cli": os.path.join(runtime, "scripts", "scan_l1.py"),
        "installed_at": int(time.time()),
        "source": source,
        "sha256": got,
    }
    with open(pointer, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

    register(ws, runtime)
    log("")
    log(f"安装完成：{AGENT_NAME} {VERSION}")
    log(f"运行时：{runtime}")
    log("下一步：新建一个会话，在智能体下拉里选择「知识库管家」")
    log(f'第一层自测：python3 "{runtime}/scripts/scan_l1.py" --root "$HOME" --out ./kb-第一层 --max-files 500')
    return 0


def register(ws, runtime):
    tpl = os.path.join(runtime, "agent-template.md")
    if not os.path.isfile(tpl):
        log("[提示] 运行时里没有 agent-template.md，跳过智能体注册")
        return
    agents_dir = os.path.join(ws, ".opencode", "agents")
    os.makedirs(agents_dir, exist_ok=True)
    dst = os.path.join(agents_dir, f"{AGENT_NAME}.md")
    if os.path.exists(dst):
        log(f"[提示] 智能体已存在，未覆盖：{dst}")
        return
    with open(tpl, encoding="utf-8") as f:
        body = f.read().replace("{{RUNTIME_ROOT}}", runtime)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(body)
    log(f"[完成] 智能体已注册：{dst}")


if __name__ == "__main__":
    sys.exit(main())
