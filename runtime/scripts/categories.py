#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb-steward 类目加载器（唯一类目数据源 = categories.json）

各脚本 import 本模块拿类目，不再各自硬编码 PLAN / LABEL / WORD / 词表。
改行业、改抽屉、改类别词、改关键词，只改 runtime 根目录的 categories.json，不动任何脚本。

用法：
    import categories
    categories.LABEL()          # {"10.01": "公司资质", ...}
    categories.WORD()           # {"10.01": "资质", ...}   进文件名的类别词
    categories.PLAN()           # [(jd, label, word, keywords), ...]  兼容 classify
    categories.PLAN_TEXT()      # "10.01 公司资质（...）..."  给大模型当抽屉清单
    categories.MUST_SUBJECT_JD()# 必须填主体的抽屉集合
    categories.GENERIC_JD()     # 通用件集合（不必填具体主体）
    categories.DOMAINS()        # {"10": "全员", ...}  权限域映射
"""
import json
import os


def _data_path():
    here = os.path.dirname(os.path.abspath(__file__))
    # categories.json 在 runtime 根（scripts 的上一级）；兼容放 scripts 同级的旧布局
    candidates = [
        os.path.join(os.path.dirname(here), "categories.json"),
        os.path.join(here, "categories.json"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    # 兜底：向上找（安装到 .spaceagents/plugins/kb-steward/runtime-*/scripts/ 时）
    cur = here
    for _ in range(4):
        p = os.path.join(cur, "categories.json")
        if os.path.isfile(p):
            return p
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    raise FileNotFoundError(
        "找不到 categories.json。它应位于 runtime 根目录（SKILL.md 同级）。")


_DATA = None


def load():
    global _DATA
    if _DATA is None:
        with open(_data_path(), encoding="utf-8") as f:
            _DATA = json.load(f)
    return _DATA


def categories():
    return load()["categories"]


def LABEL():
    return {c["jd"]: c["label"] for c in categories()}


def WORD():
    return {c["jd"]: c["word"] for c in categories()}


def PLAN():
    """兼容 classify_l2 的 (jd, label, word, keywords) 元组列表。"""
    return [(c["jd"], c["label"], c["word"], c.get("keywords") or [])
            for c in categories()]


def PLAN_TEXT():
    """给大模型的抽屉清单文本：一行一个「编号 抽屉名（描述）」。"""
    lines = []
    for c in categories():
        if c.get("desc"):
            lines.append(f'{c["jd"]} {c["label"]}（{c["desc"]}）')
        else:
            lines.append(f'{c["jd"]} {c["label"]}')
    return "\n".join(lines)


def MUST_SUBJECT_JD():
    return set(load().get("must_subject_jd", []))


def GENERIC_JD():
    return set(load().get("generic_jd", []))


def DOMAINS():
    return load().get("domains", {})


def domain_for(jd):
    """按 JD 前两位取权限域。"""
    prefix = jd.split(".")[0] if jd else ""
    return DOMAINS().get(prefix, "")


if __name__ == "__main__":
    print("类目数：", len(categories()))
    print("示例 LABEL：", LABEL().get("10.01"), LABEL().get("20.01"))
    print("PLAN_TEXT 前 3 行：")
    for line in PLAN_TEXT().splitlines()[:3]:
        print("  ", line)