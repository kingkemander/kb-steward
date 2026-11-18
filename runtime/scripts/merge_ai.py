#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合并 AI 补齐结果 → L3 终稿索引

规则：
- subject：AI 抄到的机构名/项目名优先（已做原文校验），否则保留规则层结果
- date：AI 明确日期优先，否则保留
- jd：AI 置信 ≥0.6 才覆盖
- 只补不猜：AI 为空不动原值
"""
import argparse
import json
import os
import re

LABEL = {
    "00.01": "规则与命名", "00.03": "待复核",
    "10.01": "公司资质", "10.02": "荣誉专利",
    "20.01": "合同业绩", "20.02": "投标文件", "20.03": "验收评价", "20.04": "客户清单",
    "30.01": "人员证件", "30.02": "劳动合同", "30.03": "体检培训", "30.04": "组织岗位",
    "40.01": "制度", "40.02": "审批权限", "40.03": "SOP", "40.04": "报价规则",
    "50.01": "产品资料", "50.02": "检测报告", "50.03": "TDS-MSDS", "50.04": "图纸CAD",
    "50.05": "技术方案",
    "60.01": "投标模板", "60.02": "方案模板", "60.03": "表单", "60.04": "品牌资产", "60.05": "营销内容",
    "70.01": "发票票据", "70.02": "法务文件", "70.03": "审计报告", "70.04": "税务申报",
    "80.01": "项目档案-进行中", "80.02": "项目档案-已归档",
}
WORD = {"00.01": "规则", "00.03": "待复核", "10.01": "资质", "10.02": "荣誉",
        "20.01": "合同", "20.02": "投标", "20.03": "验收", "20.04": "客户",
        "30.01": "证件", "30.02": "劳动", "30.03": "培训", "30.04": "组织",
        "40.01": "制度", "40.02": "审批", "40.03": "SOP", "40.04": "报价",
        "50.01": "产品", "50.02": "检测", "50.03": "TDS", "50.04": "图纸", "50.05": "方案",
        "60.01": "投标模板", "60.02": "方案模板", "60.03": "表单", "60.04": "品牌", "60.05": "营销",
        "70.01": "发票", "70.02": "法务", "70.03": "审计", "70.04": "税务",
        "80.01": "项目", "80.02": "归档"}


def clean(s, n):
    s = re.sub(r"[_/\\:*?\"<>|]+", "-", s or "")
    return re.sub(r"\s+", "", s)[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    OUT = os.path.abspath(args.out)

    pkg = json.load(open(os.path.join(OUT, "个人索引包-L2.json"), encoding="utf-8"))
    items = pkg["items"]
    cache_path = os.path.join(OUT, "14-AI补齐缓存.json")
    cache = json.load(open(cache_path, encoding="utf-8")) if os.path.isfile(cache_path) else {}

    changed = {"subject": 0, "date": 0, "jd": 0}
    for it in items:
        ai = cache.get(it["source_path"]) or {}
        if ai.get("mode") == "skipped":
            continue
        if ai.get("subject") and not it["subject"]:
            it["subject"] = ai["subject"]
            it["subject_source"] = "AI-全文"
            it["subject_type"] = ai.get("subject_type") or ""
            changed["subject"] += 1
        if ai.get("date") and (it["date"] in ("", "日期待补") or len(str(ai["date"])) > len(str(it["date"]))):
            it["date"] = ai["date"]
            it["date_source"] = "AI-全文"
            changed["date"] += 1
        try:
            conf = float(ai.get("confidence") or 0)
        except Exception:
            conf = 0
        if ai.get("jd") and ai["jd"] in LABEL and conf >= 0.6 and ai["jd"] != it["jd_code"]:
            it["jd_code"] = ai["jd"]
            it["category"] = f'{ai["jd"]} {LABEL[ai["jd"]]}'
            it["category_word"] = WORD[ai["jd"]]
            it["ai_confidence"] = conf
            changed["jd"] += 1

        if it["jd_code"] == "00.03":
            it["suggested_name"] = ""
            continue
        stem = os.path.splitext(it["file_name"])[0]
        c = clean(it["category_word"], 12) or "文件"
        s = clean(it["subject"], 30) if it["subject"] else "主体待补"
        n = clean(stem, 40)
        d = it["date"] if it["date"] not in ("", "日期待补") else "日期待补"
        name = f"{c}_{s}_{n}_{d}{it['ext']}"
        if len(name) > 100:
            n = n[:max(6, len(n) - (len(name) - 100))]
            name = f"{c}_{s}_{n}_{d}{it['ext']}"
        it["suggested_name"] = name

    pkg["layer"] = "L3"
    pkg["summary"].update({
        "ai_covered": len(cache),
        "ai_changed_subject": changed["subject"],
        "ai_changed_date": changed["date"],
        "ai_changed_jd": changed["jd"],
        "no_subject_final": sum(1 for i in items if not i["subject"]),
        "named_final": sum(1 for i in items if i["suggested_name"]),
    })
    json.dump(pkg, open(os.path.join(OUT, "个人索引包-L3.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(json.dumps(pkg["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
