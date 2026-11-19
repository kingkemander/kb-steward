#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键跑完：体检 → 检查目录 → 报告 → 分类 → 补待补 → 全文解析 → AI 补齐 → 定目录 → 人读索引。

用法：
    python3 run_all.py --list-roots                     # 先看能选哪些盘/目录
    python3 run_all.py --out ./kb-整理 --name 张三 --dept 交付部 --role 员工 \
        --scope ~/Desktop --scope ~/Documents

说明：
- 只读。第 7 步默认只出映射，不复制文件。
- 缺解析库会停下，提示先 install_deps.py --target "<盘位>"。
- 没有算力 Key 会自动跳过 AI 补齐，其余照跑。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def script(name):
    return os.path.join(HERE, name)


def run(title, args, allow_fail=False):
    print(f"\n=== {title}", flush=True)
    t0 = time.time()
    r = subprocess.run([sys.executable] + args)
    dt = time.time() - t0
    if r.returncode != 0:
        print(f"[失败] {title}（退出码 {r.returncode}，{dt:.1f}s）")
        if not allow_fail:
            sys.exit(r.returncode)
    else:
        print(f"[完成] {title}（{dt:.1f}s）")
    return r.returncode


def doctor(deps):
    r = subprocess.run([sys.executable, script("doctor.py"), "--deps", deps or ""],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout.split("\n\n需要补齐")[0])
    except Exception:
        return {}


def list_roots():
    """列出可选的盘位与常用目录，附浅层文件数。"""
    home = os.path.expanduser("~")
    cands = []
    for name in ("Desktop", "Documents", "Downloads", "Pictures", "Movies", "Music"):
        p = os.path.join(home, name)
        if os.path.isdir(p):
            cands.append(p)
    try:
        for n in sorted(os.listdir(home)):
            p = os.path.join(home, n)
            if os.path.isdir(p) and not n.startswith(".") and p not in cands:
                if n in ("Library", "Applications", "node_modules"):
                    continue
                cands.append(p)
    except OSError:
        pass
    if os.path.isdir("/Volumes"):
        for n in sorted(os.listdir("/Volumes")):
            p = os.path.join("/Volumes", n)
            if os.path.isdir(p):
                cands.append(p)
    if os.name == "nt":
        for letter in "DEFGH":
            p = f"{letter}:\\"
            if os.path.exists(p):
                cands.append(p)

    rows = []
    for p in cands:
        n = 0
        try:
            with os.scandir(p) as it:
                for e in it:
                    if e.is_file(follow_symlinks=False):
                        n += 1
        except OSError:
            n = -1
        try:
            free = round(shutil.disk_usage(p).free / 2**30, 1)
        except OSError:
            free = None
        rows.append({"path": p, "top_files": n, "free_gb": free})
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    print("\n把要检查的路径用 --scope 传进来，可多个：")
    print('  --scope "~/Desktop" --scope "~/Documents"')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="./kb-整理")
    ap.add_argument("--name", default="")
    ap.add_argument("--dept", default="")
    ap.add_argument("--role", default="员工")
    ap.add_argument("--scope", action="append", default=[], help="要检查的目录/盘，可重复")
    ap.add_argument("--deps", default="", help="依赖目录（install_deps.py 装到的地方）")
    ap.add_argument("--max-files", type=int, default=0)
    ap.add_argument("--skip-ai", action="store_true")
    ap.add_argument("--allow-missing-deps", action="store_true")
    ap.add_argument("--list-roots", action="store_true")
    args = ap.parse_args()

    if args.list_roots:
        return list_roots()

    if not args.scope:
        print("还没指定范围。先跑 --list-roots 看看能选什么，再用 --scope 指定。")
        return 2

    OUT = os.path.abspath(args.out)
    os.makedirs(OUT, exist_ok=True)
    scopes = [os.path.expanduser(s) for s in args.scope]
    missing = [s for s in scopes if not os.path.isdir(s)]
    if missing:
        print("这些路径不存在：" + "、".join(missing))
        return 2

    # [0] 体检
    print("=== [0/10] 环境体检", flush=True)
    rep = doctor(args.deps)
    if rep:
        miss = rep.get("missing") or []
        print("可解析：" + ("；".join(rep.get("can_parse") or []) or "无"))
        if miss:
            print("缺少解析库：" + "、".join(miss))
        for n in (rep.get("notes") or []):
            print("提示：" + n)
        if (miss or rep.get("need_old_format_converter")) and not args.allow_missing_deps:
            print("\n请先装依赖（必须指定盘位，不要装系统盘）：")
            print('  python3 ' + script("install_deps.py") + ' --target "<盘位>/kb-deps"')
            print("装完重跑本命令并加上 --deps \"<盘位>/kb-deps\"；")
            print("或加 --allow-missing-deps 先跑，缺库的文件会标 missing_dep。")
            return 3

    scope_args = []
    for s in scopes:
        scope_args += ["--root", s]

    run("[1/10] 检查目录", [script("scan_l1.py")] + scope_args
        + ["--out", OUT, "--max-files", str(args.max_files)])
    run("[2/10] 报告与去重", [script("make_report.py"), "--out", OUT, "--root", scopes[0],
                          "--name", args.name, "--dept", args.dept])
    run("[3/10] 分类", [script("classify_l2.py"), "--out", OUT, "--root", scopes[0],
                     "--name", args.name, "--dept", args.dept])
    run("[4/10] 批量补待补", [script("fill_gaps.py"), "--out", OUT])
    run("[5/10] 全文解析", [script("parse_all.py"), "--out", OUT] + scope_args
        + (["--deps", args.deps] if args.deps else []))

    key = os.environ.get("SPACEAGENTS_AUTO_API_KEY", "")
    if args.skip_ai or not key:
        print("\n[6/10] 跳过 AI 补齐" + ("（--skip-ai）" if args.skip_ai else "（未配置 SPACEAGENTS_AUTO_API_KEY）"))
        print("[7/10] 跳过合并（无 AI 结果）")
        if not os.path.isfile(os.path.join(OUT, "个人索引包-L3.json")):
            print("\n没有 AI 结果，后续定目录会退回规则结果。")
    else:
        run("[6/10] AI 补齐主体与日期", [script("ai_fill.py"), "--out", OUT], allow_fail=True)
        # 每次都重跑合并：上游分类/抽取变了，旧 L3 会和新结果打架
        run("[7/10] 合并 AI 结果", [script("merge_ai.py"), "--out", OUT], allow_fail=True)

    run("[8/10] 定目录与改名映射", [script("finalize.py"), "--out", OUT,
                              "--root", scopes[0], "--keep-orig",
                              "--name", args.name, "--dept", args.dept,
                              "--role", getattr(args, "role", "") or ""])
    run("[9/10] 文件夹一句话概括", [script("folder_summary.py"), "--out", OUT], allow_fail=True)
    run("[10/10] 生成人读索引", [script("make_human_index.py"), "--out", OUT,
                             "--name", args.name, "--dept", args.dept])

    print("\n全部完成。产出目录：" + OUT)
    print("  给人看：目录索引.md、改名对照.md")
    print("  给智能体看：17-终稿索引包.json")
    print("  要真复制才加：finalize.py --apply --dest <目标>（只复制，不动原件）")
    print("  要上报：export_package.py --name <姓名> --dept <部门>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
