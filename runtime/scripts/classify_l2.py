#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb-steward 第二层：本地自动分类 + 合并同类项（不调外部 AI）

输入：scan_l1 的第一层产物（02 工作文档、03 图片与CAD）
输出：09-分类结果.csv / 10-目录结构建议.md / 个人索引包-L2.json

判定链：文件名(±3) + 路径(±2) + 正文(±4) + 邻居投票(±2)
主体：全文公司全称正则，频次最高；抽不到写「主体待补」
日期：文件名 > 父目录年份 > 正文，冲突留痕
敏感：账号密码成对表 / apiKey / 身份证号(过 GB11643 才认)
"""
import argparse
import collections
import csv
import json
import os
import re
import subprocess
import sys

# ---------------- 公司文件计划（JD 编号 → 抽屉名 + 类别词 + 关键词） ----------------
PLAN = [
    ("00.01", "规则与命名", "规则", ["命名规则", "文件计划", "目录规范", "编码规则", "资料总索引", "总索引"]),
    ("00.03", "待复核", "待复核", []),
    ("10.01", "公司资质", "资质", ["营业执照", "资质证书", "许可证", "安全生产许可", "体系认证", "ISO9001", "资质等级", "证书编号"]),
    ("10.02", "荣誉专利", "荣誉", ["荣誉证书", "专利", "著作权", "获奖", "表彰", "科技成果"]),
    ("20.01", "合同业绩", "合同", ["合同", "协议书", "中标通知书", "业绩", "合同金额", "甲方", "乙方"]),
    ("20.02", "投标文件", "投标", ["投标", "招标", "响应文件", "标书", "开标", "评标", "投标人", "采购文件"]),
    ("20.03", "验收评价", "验收", ["验收", "评价报告", "竣工", "合格证明", "业主评价"]),
    ("20.04", "客户清单", "客户", ["客户清单", "客户名录", "拜访记录", "商机"]),
    ("30.01", "人员证件", "证件", ["职称", "毕业证", "学位证", "社保", "职业资格", "注册证", "学历证书", "参保", "考核登记表", "专业技术", "评审公示", "助理工程师"]),
    ("30.02", "劳动合同", "劳动", ["劳动合同", "入职", "离职", "转正"]),
    ("30.03", "体检培训", "培训", ["体检", "培训", "签到", "考试", "合格证"]),
    ("30.04", "组织岗位", "组织", ["组织架构", "岗位职责", "任命", "人员编制"]),
    ("40.01", "制度", "制度", ["管理制度", "办法", "规定", "细则", "章程"]),
    ("40.02", "审批权限", "审批", ["审批", "权限", "授权", "签批"]),
    ("40.03", "SOP", "SOP", ["SOP", "作业指导", "操作规程", "工作流程"]),
    ("40.04", "报价规则", "报价", ["报价", "价格表", "取费", "费率", "成本核算"]),
    ("50.01", "产品资料", "产品", ["产品说明", "产品手册", "白皮书", "选型对比", "产品参数"]),
    ("50.02", "检测报告", "检测", ["检测报告", "监测报告", "检验", "CMA", "测试报告"]),
    ("50.03", "TDS-MSDS", "TDS", ["TDS", "MSDS", "化学品安全", "技术数据表"]),
    ("50.04", "图纸CAD", "图纸", ["图纸", "施工图", "总图", "平面图", "剖面图", "大样图", "CAD", "dwg", "dxf"]),
    ("50.05", "技术方案", "方案", ["技术方案", "实施方案", "环评", "环境影响", "可研", "可行性研究", "设计说明", "工艺", "治理方案", "风险评估"]),
    ("60.01", "投标模板", "投标模板", ["投标模板", "标书模板", "投标文件格式"]),
    ("60.02", "方案模板", "方案模板", ["方案模板", "PPT模板", "汇报模板"]),
    ("60.03", "表单", "表单", ["表单", "确认单", "记录表", "台账", "检查表", "签到表"]),
    ("60.04", "品牌资产", "品牌", ["logo", "LOGO", "品牌", "宣传册", "企业简介", "宣讲", "视觉识别", "企业宣传", "宣传PPT", "推广"]),
    ("60.05", "营销内容", "营销", ["文案", "脚本", "分镜", "口播", "抖音", "视频号", "小红书", "封面", "选题", "对标", "获客", "直播", "投放", "矩阵", "引流", "转化", "标题", "账号分析"]),
    ("70.01", "发票票据", "发票", ["发票", "票据", "报销", "付款", "对账单", "开票"]),
    ("70.02", "法务文件", "法务", ["法务", "律师", "诉讼", "仲裁", "保密协议", "承诺书"]),
    ("70.03", "审计报告", "审计", ["审计报告", "财务审计", "验资"]),
    ("70.04", "税务申报", "税务", ["企业所得税", "纳税申报", "增值税", "申报表", "税务", "完税"]),
    ("80.01", "项目档案-进行中", "项目", ["项目", "进度", "周报", "月报", "会议纪要", "启动会", "录音转写", "会议录音", "访谈记录"]),
    ("80.02", "项目档案-已归档", "归档", ["竣工资料", "归档", "结项"]),
]
PERSONAL_KW = ["简历", "求职", "备考", "小升初", "个人总结", "银行卡", "户口", "购房", "租房", "旅游", "照片备份"]
ORG_SUFFIX = ("有限公司", "有限责任公司", "股份有限公司", "设计院", "研究院", "设计研究院",
              "大学", "学院", "集团", "事务所", "协会", "促进会")
ORG_RE = re.compile(r"([\u4e00-\u9fa5]{2,24}(?:" + "|".join(ORG_SUFFIX) + r"))")
ORG_CENTER_RE = re.compile(r"([\u4e00-\u9fa5]{5,16}中心)")
ORG_GENERIC_HEAD = ("工程咨询", "工程", "咨询", "科技", "环保", "设计", "监理", "建设", "项目管理")
ORG_BAD = ["月在本", "在本", "参加", "具有", "相应", "该同志", "公司学校", "专业证书", "评审培训",
           "的", "了", "和", "与", "对", "为", "在", "是", "把", "被", "给", "向", "让", "从",
           "参与", "采用", "委托", "统一", "关于", "解决", "建有", "包括", "通过", "根据",
           "按照", "属于", "位于", "成立", "发展", "更名", "称为", "简称",
           "我公司", "本公司", "该公司", "贵公司", "我司", "贵司",
           "到", "凭", "可用", "办理", "找", "咱", "像", "这", "那", "他们", "你们", "我们",
           "一直", "有个", "还有", "要是", "就是"]
CENTER_BAD = ("个人", "创作", "研发", "服务", "管理", "技术", "数据", "客服", "运营",
              "体验", "学习", "培训", "会议", "展示", "结算", "监控", "调度", "指挥", "设计")
SENSITIVE_KW = ["apiKey", "api_key", "AccessKey", "SecretKey", "sk-", "邀请码", "invite code",
                "password", "pwd", "admin123", "账号", "用户名", "登录名"]
ID_RE = re.compile(r"\b(\d{17}[\dXx])\b")


def gb11643_ok(s):
    if len(s) != 18:
        return False
    try:
        y, m, d = int(s[6:10]), int(s[10:12]), int(s[12:14])
    except ValueError:
        return False
    if not (1900 <= y <= 2015 and 1 <= m <= 12 and 1 <= d <= 31):
        return False
    w = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    chk = "10X98765432"
    try:
        tot = sum(int(s[i]) * w[i] for i in range(17))
    except ValueError:
        return False
    return chk[tot % 11] == s[17].upper()


# ---------------- 文本提取 ----------------
def extract(path, ext):
    """返回 (text, status)"""
    try:
        if ext == ".docx":
            import docx
            d = docx.Document(path)
            parts = [p.text for p in d.paragraphs[:400]]
            for t in d.tables[:20]:
                for row in t.rows[:40]:
                    parts.append(" ".join(c.text.strip() for c in row.cells))
            return "\n".join(p for p in parts if p.strip()), "ok"
        if ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            parts = []
            for ws in wb.worksheets[:4]:
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    if i > 60:
                        break
                    vals = [str(v) for v in row if v is not None]
                    if vals:
                        parts.append(" | ".join(vals))
            wb.close()
            return "\n".join(parts), "ok"
        if ext == ".pptx":
            from pptx import Presentation
            prs = Presentation(path)
            parts = []
            for s in list(prs.slides)[:12]:
                for sh in s.shapes:
                    if sh.has_text_frame:
                        parts.append(sh.text_frame.text)
            return "\n".join(parts), "ok"
        if ext == ".pdf":
            import fitz
            doc = fitz.open(path)
            txt = "\n".join(doc[i].get_text() for i in range(min(3, doc.page_count)))
            doc.close()
            if txt.strip():
                return txt, "ok"
            try:
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                import parse_all
                ocr = parse_all.ocr_pdf(path)
            except Exception:
                ocr = ""
            return (ocr, "ok-ocr") if ocr else ("", "待OCR")
        if ext == ".csv":
            for enc in ("utf-8-sig", "gbk", "utf-8"):
                try:
                    with open(path, encoding=enc) as f:
                        return "".join([next(f, "") for _ in range(40)]), "ok"
                except UnicodeDecodeError:
                    continue
            return "", "待转换"
        if ext in (".doc",):
            r = subprocess.run(["textutil", "-convert", "txt", "-stdout", path],
                               capture_output=True, timeout=30)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.decode("utf-8", "ignore"), "ok"
            return "", "待转换"
        if ext == ".xls":
            import xlrd
            wb = xlrd.open_workbook(path)
            parts = []
            for sh in wb.sheets()[:4]:
                for r in range(min(sh.nrows, 60)):
                    vals = [str(sh.cell_value(r, c)) for c in range(min(sh.ncols, 20))]
                    vals = [v for v in vals if v.strip()]
                    if vals:
                        parts.append(" | ".join(vals))
            return "\n".join(parts), "ok"
    except Exception:
        return "", "待转换"
    return "", "skip"


# ---------------- 分类 ----------------
def _kw_hit(k, hay, is_name=False):
    """英文短词用词边界，中文用包含；返回命中次数（上限 6）"""
    if re.fullmatch(r"[A-Za-z0-9\-]{2,8}", k):
        pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(k) + r"(?![A-Za-z0-9])", re.I)
        return len(pat.findall(hay))
    return hay.count(k)


def score_category(name, path, text):
    scores = collections.Counter()
    hit_terms = collections.defaultdict(list)
    nl, pl, tl = name.lower(), path.lower(), text[:8000].lower()
    for code, label, cat, kws in PLAN:
        if not kws:
            continue
        for kw in kws:
            n_name = _kw_hit(kw, nl, True)
            n_text = _kw_hit(kw, tl)
            n_path = _kw_hit(kw, pl)
            s = 0
            if n_name:
                s += 7
                hit_terms[code].append(f"文件名:{kw}")
            if n_text:
                s += 2 + min(2, max(0, n_text - 1) // 2)
                hit_terms[code].append(f"正文:{kw}x{n_text}")
            if n_path and not n_name:
                s += 2
            if s:
                scores[code] += s
    return scores, hit_terms


def _org_ok(s):
    if len(s) < 6:
        return False
    if any(b in s for b in ORG_BAD):
        return False
    if len(s) < 10 and s.startswith(ORG_GENERIC_HEAD):
        return False
    return True


def pick_subject(text, name, path):
    cand = collections.Counter()
    for m in ORG_RE.finditer(text[:4000]):
        s = m.group(1).strip("（）() 、，。")
        if _org_ok(s):
            cand[s] += 1
    if cand:
        # 优先「有限公司」类强后缀，其次频次
        best = sorted(cand.items(),
                      key=lambda x: (-(1 if ("有限公司" in x[0] or "有限责任公司" in x[0]) else 0),
                                     -x[1], -len(x[0])))[0][0]
        return best, "正文"
    # 「中心」类单独严格判定：排除个人中心/创作者中心这类功能名
    for m in ORG_CENTER_RE.finditer(text[:4000]):
        s = m.group(1).strip("（）() 、，。")
        if len(s) <= 12 and _org_ok(s) and not any(b in s for b in CENTER_BAD):
            return s, "正文"
    for m in ORG_RE.finditer(name + " " + path):
        s = m.group(1).strip("（）() 、，。")
        if _org_ok(s) and len(s) >= 8:
            return s, "文件名/路径"
    return "", ""


# 主体别名表：文件名里的简称 → 全称。
# 按本公司实际填写，例如 {"某某": "某某（山西）设计院有限公司"}。
# 留空则不做简称扩展，主体只能从正文/目录名抽取。
ALIASES = {}

DATE_PATTERNS = [
    re.compile(r"(20\d{2})(\d{2})(\d{2})(?!\d)"),            # 20250702
    re.compile(r"(20\d{2})[-_.年](\d{1,2})[-_.月](\d{1,2})"),  # 2025-07-02
    re.compile(r"(20\d{2})[-_.年](\d{1,2})月?"),                # 2025-07 / 2025年7月
    re.compile(r"(20\d{2})"),                                   # 只到年
]


def pick_date(name, path, text):
    for pat in DATE_PATTERNS:
        m = pat.search(name)
        if m:
            g = m.groups()
            y = g[0]
            if len(g) == 1:
                return y, "文件名"          # 只到年就写年，不造 01-01
            mo = g[1]
            if len(g) == 2 or not g[2]:
                return f"{y}-{int(mo):02d}", "文件名"
            return f"{y}-{int(mo):02d}-{int(g[2]):02d}", "文件名"
    # 父目录（只到年，不造月日）
    for seg in reversed(path.split("/")[:-1]):
        m = re.search(r"(20\d{2})", seg)
        if m:
            return m.group(1), "父目录"
    # 正文
    m = re.search(r"(20\d{2})[-年./](\d{1,2})[-月./](\d{1,2})", text[:3000])
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", "正文"
    return "日期待补", ""


def clean_seg(s, maxlen=40):
    s = re.sub(r"[_/\\:*?\"<>|]+", "-", s)
    s = re.sub(r"\s+", "", s)
    return s[:maxlen]


def build_name(cat_word, subject, stem, date, ext):
    c = clean_seg(cat_word, 12) or "文件"
    s = clean_seg(subject, 30) if subject else "主体待补"
    n = clean_seg(stem, 40)
    d = date if date != "日期待补" else "日期待补"
    name = f"{c}_{s}_{n}_{d}{ext}"
    if len(name) > 100:
        over = len(name) - 100
        n = n[:max(6, len(n) - over)]
        name = f"{c}_{s}_{n}_{d}{ext}"
    return name


SAFE_CONTEXT = ("操作手册", "验收清单", "怎么开账号", "重置密码", "忘记密码",
                "分配权限", "使用说明", "帮助文档", "检查清单")


def sensitive_flags(text):
    """只在看到「真实值形态」时才判敏感，避免把操作手册当成凭据表。"""
    t = text[:30000]
    low = t.lower()
    flags = []
    safe = any(k in t for k in SAFE_CONTEXT)

    # S1 同一行出现账号+密码，且后面有值形态
    for ln in t.splitlines():
        if not re.search(r"(账号|账户|用户名|登录名)", ln):
            continue
        if not re.search(r"(密码|password|pwd|口令)", ln, re.I):
            continue
        if re.search(r"[:：\t|]\s*\S{3,}", ln) and not any(k in ln for k in SAFE_CONTEXT):
            flags.append("S级-账号密码同表")
            break

    # S2 密钥明文 / 默认口令
    if re.search(r"(api[_-]?key|access[_-]?key|secret[_-]?key)\s*[:=]\s*[A-Za-z0-9_\-]{16,}", t, re.I) \
            or re.search(r"sk-[A-Za-z0-9]{16,}", t) \
            or re.search(r"admin\s*[/:：]\s*admin\d{2,}", t, re.I):
        flags.append("S级-密钥明文")

    # S3 邀请码后面跟真实码
    if re.search(r"邀请码\s*[:：]?\s*[A-Za-z0-9]{8,}", t) or re.search(r"invite\s*code\s*[:：]?\s*[A-Za-z0-9]{8,}", t, re.I):
        flags.append("S级-邀请码")

    # A 级：身份证号必须过校验位；读不全但有证件关键词也算 A
    ids = [m.group(1) for m in ID_RE.finditer(t)]
    if any(gb11643_ok(i) for i in ids):
        flags.append("A级-身份证号")
    elif any(k in t for k in ["居民身份证", "公民身份号码", "签发机关"]) and not safe:
        flags.append("A级-身份证件")

    return flags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--root", default=os.path.expanduser("~"))
    ap.add_argument("--name", default="未填写")
    ap.add_argument("--dept", default="未填写")
    ap.add_argument("--role", default="待确认")
    args = ap.parse_args()
    OUT = os.path.abspath(args.out)
    ROOT = os.path.abspath(args.root)

    docs = list(csv.DictReader(open(os.path.join(OUT, "02-工作文档候选.csv"), encoding="utf-8-sig")))
    ocr = [r for r in csv.DictReader(open(os.path.join(OUT, "03-图片与CAD候选.csv"), encoding="utf-8-sig"))
           if r["通道"] == "CAD"]

    items = []
    staging = []
    for i, r in enumerate(docs + ocr):
        rel, name, ext = r["相对路径"], r["文件名"], r["扩展名"]
        full = os.path.join(ROOT, rel)
        text, st = ("", "skip") if ext.lower() in (".dwg", ".dxf") else extract(full, ext.lower())
        scores, hit_terms = score_category(name, rel, text)
        staging.append((r, rel, name, ext, text, st, scores, hit_terms))

    # 邻居投票：同父目录的多数类目 +2（只看有分的）
    parent_top = {}
    by_parent = collections.defaultdict(collections.Counter)
    for r, rel, name, ext, text, st, scores, hit in staging:
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if scores:
            top = scores.most_common(1)[0][0]
            by_parent[parent][top] += 1
    for parent, cnt in by_parent.items():
        parent_top[parent] = cnt.most_common(1)[0][0]

    for i, (r, rel, name, ext, text, st, scores, hit_terms) in enumerate(staging):
        stem = os.path.splitext(name)[0]
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        neigh = parent_top.get(parent)
        if neigh and scores:
            scores = collections.Counter(scores)
            scores[neigh] += 2

        pos = [(c, s) for c, s in scores.items() if s > 0]
        pos.sort(key=lambda x: (-x[1], x[0]))
        if pos:
            code, sc = pos[0]
            second = pos[1][1] if len(pos) > 1 else 0
            entry = next(p for p in PLAN if p[0] == code)
            cat_word = entry[2]
            margin = sc - second
            if sc >= 9 and margin >= 3:
                conf = 0.88
            elif sc >= 5:
                conf = 0.75
            else:
                conf = 0.55
        else:
            code, cat_word, conf = "00.03", "待复核", 0.3
            sc = 0
            margin = 0

        # 模板 / 演示件：正文本身是占位符，主体应为「通用」
        tmpl_hits = sum(1 for pat in (r"模拟演示", r"×××", r"××", r"待核验",
                                      r"_{4,}", r"＿{3,}", r"公司全称_", r"统一社会信用代码_", r"模板/待补")
                        if re.search(pat, text[:3000]))
        if tmpl_hits >= 2 or "模拟演示" in text[:3000]:
            cat_word = "方案模板" if code in ("20.01", "20.02", "50.05", "80.01") else "表单"
            code = "60.02" if cat_word == "方案模板" else "60.03"
            conf = 0.8
            is_template = True
        else:
            is_template = False

        # 中间产物 / 测试件 / 系统导出数据：不进正式抽屉
        low_name = name.lower()
        low_rel = rel.lower()
        stem_low = os.path.splitext(low_name)[0]
        is_intermediate = (
            any(seg in low_rel for seg in ("/t3/", "/t4/", "/t5/", "/t6/"))
            or stem_low in {"base", "base1", "base2", "pass", "pass1", "pass2",
                            "final", "final1", "final2", "output", "tmp"}
        )
        is_export = (
            re.match(r"^(tasks|api-calls|api_calls|export|logs?)[-_]?\d", stem_low) is not None
            or "task_id" in text.lower()[:3000] and "token" in text.lower()[:3000]
        )
        if is_intermediate:
            code, cat_word, conf, sc, margin = "00.03", "待复核", 0.2, 0, 0
        if "fonttest" in low_name or "测试中文" in low_name or is_export:
            code, cat_word, conf, sc, margin = "00.03", "待复核", 0.2, 0, 0

        label = next(p[1] for p in PLAN if p[0] == code)
        subject, subj_src = pick_subject(text, name, rel)
        if is_template:
            subject, subj_src = "通用", "模板/演示"
        if not subject:
            m = re.search(r"项目名称[:：]\s*([^\n，。；＿_]{4,40})", text[:5000])
            if m:
                cand = m.group(1).strip(" 　")
                if len(cand) >= 6:
                    subject, subj_src = cand, "正文-项目名称"
        if not subject:
            for alias, full in ALIASES.items():
                if alias in rel or alias in name:
                    subject, subj_src = full, "别名表"
                    break
        date, date_src = pick_date(name, rel, text)
        flags = []
        if is_template:
            flags.append("模板/演示件")
        if is_intermediate:
            flags.append("生成中间产物")
        if is_export:
            flags.append("系统导出数据")
        if st in ("待OCR", "待转换"):
            flags.append(st)
        if not subject:
            flags.append("主体待补")
        if date == "日期待补":
            flags.append("日期待补")
        if conf < 0.7:
            flags.append("待复核")
        flags += sensitive_flags(text)
        personal = any(k in (name + text[:3000]) for k in PERSONAL_KW) and not subject
        suggested = "" if code == "00.03" else build_name(cat_word, subject, stem, date, ext)
        evidence = "；".join(hit_terms.get(code, [])[:6]) or ("无关键词命中" if sc == 0 else "")

        items.append({
            "source_path": rel,
            "file_name": name,
            "ext": ext,
            "size": int(r["字节"]),
            "mtime": r["修改时间"],
            "sha256": "",
            "layer1_verdict": r["判定"],
            "channel": r["通道"],
            "jd_code": code,
            "category": f"{code} {label}",
            "category_word": cat_word,
            "subject": subject,
            "subject_source": subj_src,
            "subject_type": "project" if "项目" in subj_src else "",
            "date": date,
            "date_source": date_src,
            "suggested_name": suggested,
            "owner_suggest": args.dept,
            "domain": args.dept,
            "confidence": conf,
            "score": sc,
            "score_margin": margin,
            "evidence": evidence,
            "flags": flags,
            "personal_suspect": personal,
            "extract_status": st,
            "upload": "禁上传" if any(f.startswith("S级") for f in flags) else "未上传",
        })
        if (i + 1) % 50 == 0:
            print(f"  ... {i+1}/{len(docs)+len(ocr)}", flush=True)

    # 合并同类项：同哈希去重（哈希从第一层 08 表补）
    hash_map = {}
    p8 = os.path.join(OUT, "08-工作文档-哈希与重名.csv")
    if os.path.isfile(p8):
        for r in csv.DictReader(open(p8, encoding="utf-8-sig")):
            hash_map[r["相对路径"]] = r["sha256"]
    for it in items:
        it["sha256"] = hash_map.get(it["source_path"], "")
    by_hash = collections.defaultdict(list)
    for it in items:
        if it["sha256"]:
            by_hash[it["sha256"]].append(it)
    merged = []
    kept_paths = set()
    for h, group in by_hash.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda x: (len(x["source_path"]), x["source_path"]))
        keep = group[0]
        keep["merged_from"] = [g["source_path"] for g in group[1:]]
        keep["flags"].append(f"合并{len(group)-1}份重复")
        for g in group:
            kept_paths.add(g["source_path"])
    for it in items:
        it.setdefault("merged_from", [])

    # 写 CSV
    fields = ["source_path", "file_name", "ext", "size", "mtime", "jd_code", "category",
              "category_word", "subject", "date", "suggested_name", "confidence", "score",
              "score_margin", "evidence", "flags", "personal_suspect", "extract_status", "upload"]
    p_csv = os.path.join(OUT, "09-分类结果.csv")
    with open(p_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for it in items:
            row = []
            for k in fields:
                v = it[k]
                row.append(" / ".join(v) if isinstance(v, list) else v)
            w.writerow(row)

    # 目录结构建议
    groups = collections.defaultdict(list)
    for it in items:
        groups[it["jd_code"]].append(it)
    p_tree = os.path.join(OUT, "10-目录结构建议.md")
    lines = ["# 分类结果与目录结构建议（L2 自动分类）", "",
             f"分类对象：{len(items)} 份（工作文档 {len(docs)} + CAD {len(ocr)}）", ""]
    lines += ["| JD | 抽屉 | 份数 | 其中低置信 | 其中敏感 |", "|---|---|---:|---:|---:|"]
    for code in sorted(groups):
        g = groups[code]
        label = next(p[1] for p in PLAN if p[0] == code)
        low = sum(1 for x in g if x["confidence"] < 0.7)
        sens = sum(1 for x in g if x["upload"] == "禁上传")
        lines.append(f"| {code} | {label} | {len(g)} | {low} | {sens} |")
    lines += ["", "## 建议目录树", "", "```"]
    for code in sorted(groups):
        label = next(p[1] for p in PLAN if p[0] == code)
        lines.append(f"{code} {label}/")
        shown = sorted(groups[code], key=lambda x: -x["confidence"])[:8]
        for x in shown:
            lines.append(f"    {x['suggested_name'] or ('（不改名）' + x['file_name'])}")
        if len(groups[code]) > len(shown):
            lines.append(f"    …… 另有 {len(groups[code])-len(shown)} 份")
    lines += ["```", "", "## 需要人工看的", ""]
    low = [x for x in items if x["confidence"] < 0.7
           and not any(f in ("生成中间产物", "系统导出数据") for f in x["flags"])]
    inter = [x for x in items if "生成中间产物" in x["flags"] or "系统导出数据" in x["flags"]]
    lines.append(f"- 中间产物/系统导出（明确不入库）：{len(inter)} 份")
    lines.append(f"- 低置信/待复核：{len(low)} 份")
    for x in low[:20]:
        lines.append(f"  - `{x['source_path']}` → {x['jd_code']}（{x['evidence'] or '无关键词'}）")
    sensitive = [x for x in items if x["upload"] == "禁上传"]
    lines.append(f"- 敏感禁上传：{len(sensitive)} 份")
    for x in sensitive[:20]:
        lines.append(f"  - `{x['source_path']}`（{'；'.join(f for f in x['flags'] if f.startswith(('S级','A级'))) }）")
    nosub = [x for x in items if not x["subject"]]
    lines.append(f"- 主体待补：{len(nosub)} 份")
    with open(p_tree, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # JSON
    pkg = {
        "schema": "kb-steward/personal-index@0.3",
        "owner": {"name": args.name, "dept": args.dept, "role": args.role},
        "layer": "L2",
        "scan": json.load(open(os.path.join(OUT, "00-扫描汇总.json"), encoding="utf-8")),
        "items": items,
        "summary": {
            "items": len(items),
            "by_category": {k: len(v) for k, v in sorted(groups.items())},
            "low_confidence": len(low),
            "intermediate": len(inter),
            "sensitive": len(sensitive),
            "no_subject": len(nosub),
        },
    }
    with open(os.path.join(OUT, "个人索引包-L2.json"), "w", encoding="utf-8") as f:
        json.dump(pkg, f, ensure_ascii=False, indent=2)

    print(json.dumps({"items": len(items), "categories": len(groups), "low": len(low), "inter": len(inter),
                      "sensitive": len(sensitive), "no_subject": len(nosub),
                      "outputs": [p_csv, p_tree]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
