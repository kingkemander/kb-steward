#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日自检更新：发现新版就装，装失败保持原样。

由 launchd 每天定时调用；也可以手动跑。
不删旧版本、不改用户数据；只有「下载 + SHA 校验 + 解压」全成功才切换指针。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = "kingkemander/kb-steward"
PROXIES = ("http://127.0.0.1:7897", "http://127.0.0.1:7890",
           "http://127.0.0.1:1087", "http://127.0.0.1:6152")


def home():
    return os.environ.get("KB_STEWARD_HOME") or os.path.join(os.path.expanduser("~"), ".kb-steward")


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(home(), exist_ok=True)
        with open(os.path.join(home(), "autoupdate.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def opener():
    """直连优先；失败再试本机常见代理（launchd 环境没有 http_proxy）。"""
    explicit = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    candidates = [explicit] if explicit else []
    candidates += [None] + list(PROXIES)
    for p in candidates:
        if p:
            op = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": p, "https": p}))
        else:
            op = urllib.request.build_opener()
        yield p, op


def http_get(url, timeout=90, binary=False):
    last = None
    for proxy, op in opener():
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "kb-steward-autoupdate",
                "Accept": "application/octet-stream" if binary else "application/vnd.github+json",
            })
            with op.open(req, timeout=timeout) as r:
                data = r.read()
            return data if binary else json.loads(data)
        except Exception as e:
            last = e
            continue
    raise RuntimeError(f"所有通道都失败：{last}")


def main():
    ap_ws = None
    args = sys.argv[1:]
    ws = None
    quiet = "--quiet" in args
    for i, a in enumerate(args):
        if a == "--workspace" and i + 1 < len(args):
            ws = os.path.abspath(args[i + 1])
    ws = ws or os.environ.get("KB_STEWARD_WORKSPACE") or os.getcwd()

    base = os.path.join(ws, ".spaceagents", "plugins", "kb-steward")
    pointer = os.path.join(base, "current.json")
    if not os.path.isfile(pointer):
        log(f"未安装（找不到 {pointer}），跳过")
        return 0
    cur = json.load(open(pointer, encoding="utf-8"))
    installed = (cur.get("version") or "").lstrip("v")
    if not quiet:
        log(f"当前 {installed}｜检查更新…")

    try:
        rel = http_get(f"https://api.github.com/repos/{REPO}/releases/latest")
    except Exception as e:
        log(f"检查失败（网络）：{e}")
        return 1

    latest = (rel.get("tag_name") or "").lstrip("v")
    if not latest:
        log("拿不到版本号，跳过")
        return 1
    if latest == installed:
        if not quiet:
            log("已是最新")
        return 0

    log(f"发现新版 {latest}（当前 {installed}）→ 开始升级")
    boot = next((a for a in rel.get("assets", []) if a.get("name") == "bootstrap.py"), None)
    if not boot:
        log("该 release 没有 bootstrap.py，跳过（避免装到无校验的包）")
        return 1
    try:
        data = http_get(boot["browser_download_url"], binary=True)
    except Exception as e:
        log(f"下载 bootstrap 失败：{e}")
        return 1

    tmpdir = os.path.join(home(), "tmp-update")
    os.makedirs(tmpdir, exist_ok=True)
    boot_path = os.path.join(tmpdir, f"bootstrap-{latest}.py")
    with open(boot_path, "wb") as f:
        f.write(data)

    # 用新版 bootstrap 装（它自带 SHA 校验；失败不会动 current.json）
    cmd = [sys.executable, boot_path, "--workspace", ws]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=1800)
        out = (r.stdout or b"").decode("utf-8", "ignore")
        err = (r.stderr or b"").decode("utf-8", "ignore")
        if r.returncode == 0:
            log(f"升级成功：{installed} → {latest}")
            for line in out.strip().splitlines()[-3:]:
                log("  " + line)
            return 0
        log(f"升级失败（退出码 {r.returncode}），保持原版本 {installed}")
        for line in (out + "\n" + err).strip().splitlines()[-6:]:
            log("  " + line)
        return 1
    except subprocess.TimeoutExpired:
        log("升级超时，保持原版本")
        return 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
