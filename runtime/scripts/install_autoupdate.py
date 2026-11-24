#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""注册每日自动更新（macOS launchd）

  python3 install_autoupdate.py --workspace "<工作区>" --hour 3 --minute 17
  python3 install_autoupdate.py --workspace "<工作区>" --now        # 注册 + 立刻测一次
  python3 install_autoupdate.py --uninstall                          # 取消
  python3 install_autoupdate.py --status                             # 看是否已注册

做的事：
1. 把 autoupdate.py 复制到 <工作区>/.spaceagents/plugins/kb-steward/autoupdate.py（稳定路径，不随版本目录变）
2. 写 ~/Library/LaunchAgents/com.spaceagents.kb-steward.autoupdate.plist
3. launchctl load（已加载则先 unload）
"""
import argparse
import os
import plistlib
import shutil
import subprocess
import sys

LABEL = "com.spaceagents.kb-steward.autoupdate"


def paths(ws):
    base = os.path.join(ws, ".spaceagents", "plugins", "kb-steward")
    return base, os.path.join(base, "autoupdate.py")


def plist_path():
    return os.path.join(os.path.expanduser("~/Library/LaunchAgents"), f"{LABEL}.plist")


def unload():
    p = plist_path()
    if os.path.isfile(p):
        subprocess.run(["launchctl", "unload", p], capture_output=True)


def status():
    p = plist_path()
    if not os.path.isfile(p):
        print("未注册自动更新")
        return 1
    with open(p, "rb") as f:
        d = plistlib.load(f)
    cal = (d.get("StartCalendarInterval") or {})
    print(f"已注册：每天 {cal.get('Hour', '?')}:{str(cal.get('Minute', '?')).zfill(2)}")
    print(f"plist：{p}")
    print(f"脚本：{(d.get('ProgramArguments') or [''])[-1]}")
    log = os.path.join(os.environ.get("KB_STEWARD_HOME") or
                       os.path.join(os.path.expanduser("~"), ".kb-steward"), "autoupdate.log")
    if os.path.isfile(log):
        lines = open(log, encoding="utf-8").read().strip().splitlines()
        print(f"最近日志（{log}）：")
        for line in lines[-6:]:
            print("  " + line)
    r = subprocess.run(["launchctl", "list", LABEL], capture_output=True, text=True)
    print("launchd 状态：", "已加载" if r.returncode == 0 else "未加载")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default="")
    ap.add_argument("--hour", type=int, default=3)
    ap.add_argument("--minute", type=int, default=17)
    ap.add_argument("--now", action="store_true", help="注册后立刻跑一次自检")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        return status()
    if args.uninstall:
        unload()
        p = plist_path()
        if os.path.isfile(p):
            os.remove(p)
        print("已取消自动更新")
        return 0

    if not args.workspace:
        print("需要 --workspace <工作区绝对路径>")
        return 2
    ws = os.path.abspath(args.workspace)
    base, dst = paths(ws)
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "autoupdate.py")
    if not os.path.isfile(src):
        print(f"找不到 {src}")
        return 2
    os.makedirs(base, exist_ok=True)
    shutil.copy2(src, dst)
    os.chmod(dst, 0o755)

    os.makedirs(os.path.dirname(plist_path()), exist_ok=True)
    pl = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, dst, "--workspace", ws, "--quiet"],
        "StartCalendarInterval": {"Hour": args.hour, "Minute": args.minute},
        "RunAtLoad": False,
        "StandardOutPath": os.path.join(os.path.expanduser("~"), ".kb-steward", "launchd.out.log"),
        "StandardErrorPath": os.path.join(os.path.expanduser("~"), ".kb-steward", "launchd.err.log"),
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
    }
    os.makedirs(os.path.join(os.path.expanduser("~"), ".kb-steward"), exist_ok=True)
    unload()
    with open(plist_path(), "wb") as f:
        plistlib.dump(pl, f)
    r = subprocess.run(["launchctl", "load", plist_path()], capture_output=True, text=True)
    if r.returncode != 0:
        print("注册失败：", r.stderr.strip()[:200])
        return 1
    print(f"已注册：每天 {args.hour}:{str(args.minute).zfill(2)} 自动检查更新")
    print(f"  plist：{plist_path()}")
    print(f"  脚本：{dst}")

    if args.now:
        print("\n立刻跑一次：")
        subprocess.run([sys.executable, dst, "--workspace", ws])
    return 0


if __name__ == "__main__":
    sys.exit(main())
