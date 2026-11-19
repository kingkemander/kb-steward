#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 补齐（全文模式）

默认：把每份文件的**全文**交给模型；超过单次上下文时按段落切分，逐段抽取后再聚合。
只做三件事：选抽屉、抄主体/项目名、抄日期。找不到就留空，禁止编造。
"""
import argparse
import collections
import csv
import json
import os
import re
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# 接口地址优先取环境变量，便于换通道/自建代理；不在此文件写任何密钥。
API = os.environ.get("SPACEAGENTS_AUTO_API_URL", "https://token.spaceagents.cn/v1/chat/completions")
KEY = os.environ.get("SPACEAGENTS_AUTO_API_KEY", "")
MODEL = "auto"

SEG_LIMIT = 24000      # 单次请求喂给模型的正文上限（字符）
SEG_OVERLAP = 800
CACHE_NAME = "14-AI补齐缓存.json"

PLAN_TEXT = """00.01 规则与命名
00.03 待复核
10.01 公司资质（营业执照/许可证/体系认证）
10.02 荣誉专利
20.01 合同业绩（合同/中标/业绩）
20.02 投标文件（投标/招标/响应文件/标书）
20.03 验收评价
20.04 客户清单
30.01 人员证件（职称/毕业证/社保/职业资格）
30.02 劳动合同
30.03 体检培训
30.04 组织岗位
40.01 制度
40.02 审批权限
40.03 SOP/操作手册
40.04 报价规则
50.01 产品资料
50.02 检测报告
50.03 TDS/MSDS
50.04 图纸CAD
50.05 技术方案/环评/可研/设计方案
60.01 投标模板
60.02 方案模板/演示模板
60.03 表单（确认单/台账/签到表/空表）
60.04 品牌资产（logo/企业简介/宣传册）
60.05 营销内容（文案/脚本/分镜/账号分析/对标）
70.01 发票票据
70.02 法务文件
70.03 审计报告
70.04 税务申报
80.01 项目档案-进行中（项目进度/周报/会议纪要）
80.02 项目档案-已归档
"""

FULL_PROMPT = """你是公司档案分类员。读这份文件的全文，只做三件事：选抽屉、抄主体、抄日期。

抽屉只能从下面选一个编号：
{plan}

规则：
1. subject：全文里**逐字出现**的完整机构名（含"有限公司/设计院/研究院/大学/学院/集团/事务所/协会"）或**项目名**（含"项目/工程"）。不得拼接、改写、推测；不确定就写 ""。
2. subject_type：机构 "org"，项目名 "project"，没有 ""。
3. date：只抄文件里明确的日期（签署日/编制日/成文日优先），格式 YYYY-MM-DD 或 YYYY；没有写 ""。不得用今天。
4. confidence：0~1；证据不足给 0.5 以下。
5. evidence：一句原文（≤40字），证明为什么归这个抽屉。
6. 空表/模板（大量 __ 或 ××× 或"模拟演示"）归 60.03 或 60.02，subject 写 "通用"。

只输出一行 JSON，不要解释、不要代码块：
{{"jd":"20.02","subject":"","subject_type":"","date":"","confidence":0.8,"evidence":""}}

文件名：{name}
路径：{path}
全文（{chars} 字）：
{text}
"""

SEG_PROMPT = """从下面这段文件内容里**逐字抄**出四类信息，不要推测、不要改写。找不到就给空数组。

1. orgs：完整机构名（必须含"有限公司/设计院/研究院/大学/学院/集团/事务所/协会"）
2. projects：项目名（必须含"项目/工程"）
3. dates：明确日期（YYYY-MM-DD 或 YYYY）
4. jd：这段内容最像下面哪个抽屉编号，只填一个
{plan}

只输出一行 JSON：
{{"orgs":[],"projects":[],"dates":[],"jd":"","ev":""}}

文件名：{name}
第 {idx}/{total} 段：
{text}
"""

CONTEXT_PROMPT = """你在给一份**读不出正文**的文件做归档判断。只能凭下面线索推断，不要编造。

抽屉只能从下面选一个编号：
{plan}

线索：
- 文件名：{name}
- 所在目录：{path}
- 大小：{size}
- 同目录其他文件（最多 12 个）：{siblings}
- 抽到的文字片段（可能为空或乱码）：
{snippet}

规则：
1. jd 只能选上面清单里的编号；完全没把握就写 ""，confidence 给 0.3 以下。
2. subject 只写线索里**明确出现**的机构名或项目名；没有写 ""，不许猜。
3. date 只写线索里明确的年份/日期；没有写 ""，不许用今天。
4. evidence 写你依据的线索（≤40 字），如"同目录全是投标文件"。
5. 宁可低置信，也不要编。

只输出一行 JSON：
{{"jd":"","subject":"","subject_type":"","date":"","confidence":0.0,"evidence":""}}
"""

RETRY_PROMPT = """上一轮判断证据不足。请只根据文件名、目录、同目录文件这三类线索，再判断一次。

文件：{name}
目录：{path}
同目录文件：{siblings}
上一轮结果：{prev}

要求：jd 必须是清单里的编号；若仍无法判断，jd 写 "00.03"、confidence 给 0.2，evidence 写"线索不足"。
只输出一行 JSON：{{"jd":"","subject":"","subject_type":"","date":"","confidence":0.0,"evidence":""}}
"""

ORG_SUFFIX = ("有限公司", "有限责任公司", "股份有限公司", "设计院", "研究院", "大学",
              "学院", "集团", "事务所", "协会", "促进会")
ORG_BAD = ["的", "了", "和", "与", "对", "为", "在", "是", "把", "被", "给", "向", "让", "从",
           "参与", "采用", "委托", "统一", "关于", "解决", "包括", "通过", "根据", "按照",
           "属于", "位于", "成立", "更名", "称为", "简称", "我公司", "本公司", "该公司",
           "一直", "有个", "还有", "要是", "就是", "到", "凭", "可用", "办理", "找", "咱"]
CENTER_BAD = ("个人", "创作", "研发", "服务", "管理", "技术", "数据", "客服", "运营",
              "体验", "学习", "培训", "会议", "展示", "结算", "监控", "调度", "指挥")
JD_OK = {line.split(" ")[0] for line in PLAN_TEXT.strip().splitlines()}


def call_llm(prompt, retry=2, max_tokens=1200):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": 0.1}
    for attempt in range(retry):
        try:
            req = urllib.request.Request(
                API, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {KEY}"})
            data = json.loads(urllib.request.urlopen(req, timeout=300).read().decode())
            msg = data["choices"][0]["message"]
            content = msg.get("content") or msg.get("reasoning_content") or ""
            m = re.search(r"\{[^{}]*\}", content)
            if m:
                return json.loads(m.group(0))
        except Exception:
            time.sleep(2 * (attempt + 1))
    return {}


def ok_org(s):
    if not s or len(s) < 6 or len(s) > 40:
        return False
    if any(b in s for b in ORG_BAD):
        return False
    if not s.endswith(ORG_SUFFIX + ("中心",)):
        return False
    if s.endswith("中心") and (len(s) > 16 or any(b in s for b in CENTER_BAD)):
        return False
    return True


def ok_project(s):
    if not s or len(s) < 6 or len(s) > 50:
        return False
    return not any(b in s for b in ORG_BAD)


def split_text(text, limit=SEG_LIMIT, overlap=SEG_OVERLAP, max_segs=15):
    if len(text) <= limit:
        return [text]
    segs, start = [], 0
    while start < len(text) and len(segs) < max_segs:
        segs.append(text[start:start + limit])
        start += limit - overlap
    return segs


def process_one(rec, siblings=None):
    text = rec.get("text", "")
    if rec.get("status") == "legacy_text":
        text = ("【说明】以下内容由旧格式文件（.doc/.ppt）直接抽取，可能含碎片或乱码。"
                "只写能从片段中确认的字段；不确定就留空，不要猜。\n" + text)
    name, path = rec["file_name"], rec["source_path"]
    st = rec.get("status") or ""
    no_text_status = st in ("legacy_unreadable", "legacy_partial", "待OCR", "待转换", "cad_no_text") \
        or st.startswith("need_converter") or st.startswith("missing_dep")
    if not text.strip() or no_text_status:
        # 抽不到正文：直接交模型，用文件名/目录/邻居/碎片推断，避免反复重跑
        sib = "；".join((siblings or [])[:12]) or "（无）"
        snippet = (text or "")[:600] or "（无）"
        prompt = CONTEXT_PROMPT.format(plan=PLAN_TEXT, name=name, path=path,
                                       size=rec.get("size", ""), siblings=sib,
                                       snippet=snippet)
        out = call_llm(prompt, max_tokens=8000)
        jd = out.get("jd", "") if out.get("jd") in JD_OK else ""
        conf = out.get("confidence", 0.3)
        mode = "context"
        if not jd or float(conf or 0) < 0.6:
            out2 = call_llm(RETRY_PROMPT.format(
                name=name, path=path, siblings=sib,
                prev=json.dumps(out, ensure_ascii=False)[:300]), max_tokens=8000)
            if out2.get("jd") in JD_OK:
                out, mode = out2, "context_retry"
        return {"jd": jd if mode == "context" else (out.get("jd") if out.get("jd") in JD_OK else jd),
                "subject": (out.get("subject") or "").strip(),
                "subject_type": out.get("subject_type") or "",
                "date": out.get("date", "") if re.fullmatch(r"\d{4}(-\d{2}-\d{2})?", out.get("date", "") or "") else "",
                "confidence": out.get("confidence", 0.3),
                "evidence": (out.get("evidence") or "")[:80],
                "mode": mode}

    if len(text) <= SEG_LIMIT:
        out = call_llm(FULL_PROMPT.format(plan=PLAN_TEXT, name=name, path=path,
                                          chars=len(text), text=text), max_tokens=4000)
        jd = out.get("jd", "") if out.get("jd") in JD_OK else ""
        subj = (out.get("subject") or "").strip()
        typ = out.get("subject_type") or ""
        if subj and subj != "通用" and subj not in text:
            subj, typ = "", ""
        if subj == "通用":
            typ = "generic"
        return {"jd": jd, "subject": subj, "subject_type": typ,
                "date": out.get("date", "") if re.fullmatch(r"\d{4}(-\d{2}-\d{2})?", out.get("date", "") or "") else "",
                "confidence": out.get("confidence", 0.5),
                "evidence": (out.get("evidence") or "")[:80],
                "mode": "full"}

    # 长文件：分段抽取 + 聚合
    segs = split_text(text)
    orgs, projects, dates, jds, evs = collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter(), []
    for i, seg in enumerate(segs, 1):
        o = call_llm(SEG_PROMPT.format(plan=PLAN_TEXT, name=name, idx=i, total=len(segs), text=seg),
                     max_tokens=8000)
        for x in (o.get("orgs") or [])[:8]:
            if ok_org(x) and x in text:
                orgs[x] += 1
        for x in (o.get("projects") or [])[:8]:
            if ok_project(x) and x in text:
                projects[x] += 1
        for x in (o.get("dates") or [])[:8]:
            if re.fullmatch(r"\d{4}(-\d{2}-\d{2})?", str(x)):
                dates[x] += 1
        if o.get("jd") in JD_OK:
            jds[o["jd"]] += 1
        if o.get("ev"):
            evs.append(str(o["ev"])[:40])

    subj, typ = "", ""
    if projects and (not orgs or projects.most_common(1)[0][1] >= orgs.most_common(1)[0][1]):
        subj, typ = projects.most_common(1)[0][0], "project"
    elif orgs:
        subj, typ = orgs.most_common(1)[0][0], "org"
    date = dates.most_common(1)[0][0] if dates else ""
    jd = jds.most_common(1)[0][0] if jds else ""
    conf = min(0.9, 0.5 + 0.1 * len(segs) * (1 if subj else 0.4)) if jd else 0.4
    return {"jd": jd, "subject": subj, "subject_type": typ, "date": date,
            "confidence": round(conf, 2), "evidence": "；".join(evs[:2]),
            "mode": f"segmented({len(segs)})"}


def main():
    if not KEY:
        print("缺少环境变量 SPACEAGENTS_AUTO_API_KEY，无法调用大模型。")
        print("处理方式（任选一）：")
        print("  1) 让管理员在 SpaceAgents Cloud 导入组织托管模型，工作区会自动注入该变量；")
        print("  2) 本机临时设置：export SPACEAGENTS_AUTO_API_KEY=<你的 Key> 后重跑。")
        print("不配也能用：跳过本步，前面 1~5 步的结果仍可出目录和改名映射。")
        return 3

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    OUT = os.path.abspath(args.out)

    recs = [json.loads(l) for l in open(os.path.join(OUT, "13-全文解析缓存.jsonl"), encoding="utf-8")]

    # 不送模型的：中间产物/系统导出/工具样例/空表模板
    skip = set()
    p09 = os.path.join(OUT, "09-分类结果.csv")
    if os.path.isfile(p09):
        for r in csv.DictReader(open(p09, encoding="utf-8-sig")):
            if any(f in (r.get("flags") or "") for f in
                   ("生成中间产物", "系统导出数据", "工具验证样例", "空表模板", "模板/演示件")):
                skip.add(r["source_path"])
    if args.limit:
        recs = recs[:args.limit]

    cache_path = os.path.join(OUT, CACHE_NAME)
    cache = {}
    if os.path.isfile(cache_path):
        cache = json.load(open(cache_path, encoding="utf-8"))

    dir_map = {}
    for r in recs:
        d = os.path.dirname(r["source_path"])
        dir_map.setdefault(d, []).append(r["file_name"])

    # 之前靠线索猜的（无正文），现在 OCR/深抽拿到正文了 → 作废旧结果，重判
    rec_map = {r["source_path"]: r for r in recs}
    stale = [k for k, v in cache.items()
             if (v.get("mode") in ("context", "context_retry")
                 and len((rec_map.get(k) or {}).get("text", "")) > 100)
             or v.get("mode") == "empty"]
    for k in stale:
        cache.pop(k, None)
    if stale:
        print(f"需要重判 {len(stale)} 条（正文已补全 / 上次没出结果）", flush=True)

    lock = threading.Lock()
    done = [0]

    def save():
        tmp = cache_path + ".tmp"
        json.dump(cache, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, cache_path)

    def work(rec):
        if rec["source_path"] in skip:
            with lock:
                cache[rec["source_path"]] = {"jd": "00.03", "subject": "", "subject_type": "",
                                             "date": "", "confidence": 0.2, "evidence": "",
                                             "mode": "skipped"}
                done[0] += 1
            return
        sib = [x for x in dir_map.get(os.path.dirname(rec["source_path"]), [])
               if x != rec["file_name"]]
        res = process_one(rec, sib)
        with lock:
            cache[rec["source_path"]] = res
            done[0] += 1
            if done[0] % 20 == 0:
                save()
                print(f"  {done[0]}/{len(recs)}  mode={res.get('mode')}", flush=True)

    todo = [r for r in recs if r["source_path"] not in cache]
    print(f"待补 {len(todo)} / 共 {len(recs)}；全文模式", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, todo))
    save()

    missing = [r for r in recs if r["source_path"] not in cache]
    if missing:
        print(f"重试 {len(missing)} 份", flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(work, missing))
        save()

    modes = collections.Counter(v.get("mode") for v in cache.values())
    print(json.dumps({"total": len(recs), "covered": len(cache),
                      "modes": dict(modes),
                      "missing": len([r for r in recs if r["source_path"] not in cache]),
                      "cache": cache_path}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
