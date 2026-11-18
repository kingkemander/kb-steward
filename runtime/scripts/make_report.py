#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把第一层扫描结果整理成可读报告 + 文档哈希/重名表"""
import argparse
import csv
import hashlib
import json
import os
import time
from collections import Counter, defaultdict

_ap = argparse.ArgumentParser()
_ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "work_out"))
_ap.add_argument("--root", default=os.path.expanduser("~"))
_ap.add_argument("--name", default="未填写")
_ap.add_argument("--dept", default="未填写")
_ap.add_argument("--role", default="待确认")
_args = _ap.parse_args()
OUT = os.path.abspath(_args.out)
ROOT = os.path.abspath(_args.root)
OWNER = {"name": _args.name, "dept": _args.dept, "role": _args.role}


def load(name):
    with open(os.path.join(OUT, name), encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def sha256(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def main():
    summary = json.load(open(os.path.join(OUT, "00-扫描汇总.json"), encoding="utf-8"))
    rows = load("01-全量文件清单.csv")
    docs = load("02-工作文档候选.csv")
    ocr = load("03-图片与CAD候选.csv")

    # 哈希 + 重名
    recs = []
    for r in docs + [x for x in ocr if x["通道"] == "CAD"]:
        full = os.path.join(ROOT, r["相对路径"])
        recs.append({
            "相对路径": r["相对路径"], "文件名": r["文件名"], "扩展名": r["扩展名"],
            "大小": r["大小"], "通道": r["通道"],
            "sha256": sha256(full),
        })
    by_hash = defaultdict(list)
    by_name = defaultdict(list)
    for x in recs:
        if x["sha256"]:
            by_hash[x["sha256"]].append(x["相对路径"])
        by_name[x["文件名"]].append(x["相对路径"])
    dup_hash = {k: v for k, v in by_hash.items() if len(v) > 1}
    dup_name = {k: v for k, v in by_name.items() if len(v) > 1}

    with open(os.path.join(OUT, "08-工作文档-哈希与重名.csv"), "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["相对路径", "文件名", "扩展名", "大小", "通道", "sha256",
                    "同哈希组", "同名组"])
        for x in recs:
            hg = len(by_hash.get(x["sha256"], []))
            ng = len(by_name.get(x["文件名"], []))
            w.writerow([x["相对路径"], x["文件名"], x["扩展名"], x["大小"], x["通道"],
                        x["sha256"][:16], hg if hg > 1 else "", ng if ng > 1 else ""])

    # 分布
    doc_dir = Counter("(根目录)" if "/" not in r["相对路径"] else r["相对路径"].rsplit("/", 1)[0] for r in docs)
    doc_ext = Counter(r["扩展名"] for r in docs)
    vc = summary["verdict_counts"]
    total = summary["total_files"]
    inter = load("07-图片-疑似中间产物.csv")
    real_ocr = [r for r in ocr if r["通道"] == "OCR"]

    def pct(n):
        return f"{n / total * 100:.1f}%"

    lines = []
    A = lines.append
    A("# 知识库第一层检查报告（磁盘检索 + 前置分拣）")
    A("")
    A(f"- 归属人：**{OWNER['name']}**（{OWNER['dept']}，职位待确认）")
    A(f"- 扫描根目录：`{ROOT}`（外接盘 `/Volumes/S7` 未扫）")
    A(f"- 扫描时间：{summary['scanned_at']}，耗时 {summary['elapsed_sec']} 秒")
    A(f"- 文件总数：**{total:,}** 个，合计 **{summary['total_size']}**")
    A(f"- 已跳过目录：{summary['skipped_dirs_count']} 个（系统库、隐藏工具目录、依赖包、回收站）")
    A(f"- 读取错误：{summary['error_count']} 个（权限/占用，不影响结论）")
    A("")
    A("> 这一层只做「看清楚」：不读正文、不调 AI、不改名、不移动、不上传。")
    A("")
    A("## 一、第一层分拣结果")
    A("")
    A("| 判定 | 数量 | 占比 | 说明 |")
    A("|---|---:|---:|---|")
    explain = {
        "工作文档": "docx/doc/pdf/ppt/pptx/xlsx/xls/csv，进第二层打分",
        "图片": "png/jpg 等，走 OCR 通道（证照多在这里）",
        "CAD": "dxf/dwg，走本地 CAD 预解析",
        "非最终产物": "md/json/txt/log/yaml 等中间态，不入库",
        "素材媒体": "音视频，只登记不概括",
        "归档包": "zip/rar/dmg 等，只登记",
        "垃圾": "临时文件、0 字节、.DS_Store",
        "开发不晋升": "代码/依赖/构建产物/虚拟环境",
        "未识别": "扩展名不在白名单，待在第二层按真实格式判断",
        "其他排除": "字体/图标/系统资源等",
    }
    order = ["工作文档", "图片", "CAD", "非最终产物", "素材媒体", "归档包",
             "未识别", "垃圾", "开发不晋升", "其他排除"]
    for k in order:
        A(f"| {k} | {vc.get(k,0):,} | {pct(vc.get(k,0))} | {explain.get(k,'')} |")
    A("")
    A("## 二、真正要往下走的只有这两堆")
    A("")
    A(f"**1）工作文档候选：{len(docs)} 份**（第二层按内容打分）")
    A("")
    A("| 格式 | 份数 |")
    A("|---|---:|")
    for k, v in doc_ext.most_common():
        A(f"| {k} | {v} |")
    A("")
    A("按目录分布（前 15）：")
    A("")
    A("| 目录 | 份数 |")
    A("|---|---:|")
    for k, v in doc_dir.most_common(15):
        A(f"| `{k}` | {v} |")
    A("")
    A(f"**2）图片/图纸候选：{len(real_ocr)} 张图片 + {len([x for x in ocr if x['通道']=='CAD'])} 份 CAD**")
    A("")
    A(f"- 另有 **{len(inter)} 张**图片落在 `pass*/page_*/_verify/artifacts` 这类目录，判定为**中间产物**，不算证照候选。")
    A("  （这一步很关键：不排掉中间产物，候选数会虚高一个数量级。）")
    A("")
    A("## 三、去重情况（仅对工作文档 + CAD）")
    A("")
    A(f"- 参与哈希：{len(recs)} 份")
    A(f"- 同内容重复组：**{len(dup_hash)} 组**，涉及 {sum(len(v) for v in dup_hash.values())} 份")
    A(f"- 同名不同内容组：**{len(dup_name)} 组**")
    A("")
    if dup_hash:
        A("同内容重复（可直接合并、只留一份）：")
        A("")
        for i, (h, paths) in enumerate(list(dup_hash.items())[:10], 1):
            A(f"{i}. `{h[:12]}` — " + "；".join(f"`{p}`" for p in paths))
        A("")
    if dup_name:
        A("同名文件组（同哈希=重复可直接合并；不同哈希=必须打开比对，不能按名字合并）：")
        A("")
        for i, (n, paths) in enumerate(list(dup_name.items())[:10], 1):
            A(f"{i}. `{n}` — " + "；".join(f"`{p}`" for p in paths))
        A("")
    A("## 四、这一层没做、也不能做的")
    A("")
    A("- 没读正文，所以还不能判断「对公司有用 / 是个人物品」——那是第二层（L2 打分）和第三层（L3 硬证据）。")
    A("- 没 OCR，所以图片里是不是营业执照/职称证还不知道。")
    A("- 没扫敏感凭据（apiKey/账号密码表/身份证）。**这是入库前必须过的一关**，放在 AI 读内容那一层做。")
    A("- 没改名、没移动、没上传：原件一个都没动。")
    A("")
    A("## 五、下一层建议（按性价比排序）")
    A("")
    A(f"1. **文档打分（{len(docs)} 份，免费）**：文件名 + 路径 + 邻居投票先判工作/个人，把中间的挑出来。")
    A("2. **图片 OCR（先挑 100 张试跑）**：从下载/桌面里的截图开始，验证识别质量和敏感信息召回。")
    A("3. **敏感件扫描（必须先于上传）**：账号密码表、apiKey、身份证照片。")
    A("4. **再谈改名和上传**：都等你确认，不自动做。")
    A("")
    A("## 六、交付文件")
    A("")
    A("| 文件 | 内容 |")
    A("|---|---|")
    A("| `01-全量文件清单.csv` | 全部文件，含判定/通道/理由 |")
    A("| `02-工作文档候选.csv` | 343 份，第二层输入 |")
    A("| `03-图片与CAD候选.csv` | 图片 + CAD |")
    A("| `04-已排除-垃圾与开发.csv` | 垃圾/代码/依赖 |")
    A("| `05-素材媒体与归档-只登记.csv` | 音视频/压缩包 |")
    A("| `06-非最终产物与未识别.csv` | 中间态/未知格式 |")
    A("| `07-图片-疑似中间产物.csv` | 不算证照候选的那批 |")
    A("| `08-工作文档-哈希与重名.csv` | 哈希 + 重名/重复组 |")
    A("| `00-扫描汇总.json` | 机器可读汇总 |")
    A("")

    rep = os.path.join(OUT, "第一层-磁盘检查报告.md")
    with open(rep, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(json.dumps({
        "report": rep,
        "docs": len(docs),
        "images": len(real_ocr),
        "intermediate": len(inter),
        "cad": len([x for x in ocr if x["通道"] == "CAD"]),
        "dup_hash_groups": len(dup_hash),
        "dup_name_groups": len(dup_name),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
