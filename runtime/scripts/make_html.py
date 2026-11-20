#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 L2 索引生成可读的 HTML 预览"""
import argparse
import collections
import html
import json
import os
import categories

JD_LABEL = categories.LABEL()


def load_items(OUT):
    """优先读终稿（17），其次 L3/L2。终稿带最终抽屉、改名结果与规则覆盖。"""
    p17 = os.path.join(OUT, "17-终稿索引包.json")
    if os.path.isfile(p17):
        d = json.load(open(p17, encoding="utf-8"))
        items = []
        for x in d.get("items", []):
            jd = x.get("jd", "00.03")
            src = x.get("from", "")
            conf = x.get("confidence")
            items.append({
                "jd_code": jd, "category": f"{jd} {JD_LABEL.get(jd, '未分类')}",
                "subject": x.get("subject") or "", "subject_source": "",
                "date": x.get("date") or "",
                "suggested_name": os.path.basename(x.get("to") or ""),
                "confidence": float(conf) if conf is not None else 0.0,
                "flags": [f for f in (x.get("flags") or "").split(";") if f],
                "upload": "禁上传" if x.get("action") == "hold_sensitive" else
                          ("未上传" if x.get("action") == "no_ingest" else "已上传"),
                "evidence": x.get("name_check") or "",
                "file_name": os.path.basename(src), "source_path": src,
                "action": x.get("action", ""),
                "sha256": x.get("sha256") or "", "size": x.get("size") or "",
            })
        return items, d.get("owner") or {}
    p3 = os.path.join(OUT, "个人索引包-L3.json")
    p2 = os.path.join(OUT, "个人索引包-L2.json")
    pkg = json.load(open(p3 if os.path.isfile(p3) else p2, encoding="utf-8"))
    return pkg["items"], pkg.get("owner", {})


CHIP = {"正文": "", "文件名/路径": "", "文件名-项目名": "项目", "正文-项目名称": "项目",
        "目录继承": "继承", "别名表": "别名", "无特定主体": "通用",
        "内部知识库": "通用", "模板/演示": "模板", "模板豁免": "模板"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="分类结果")
    args = ap.parse_args()
    OUT = os.path.abspath(args.out)
    items, owner = load_items(OUT)
    p17 = os.path.join(OUT, "17-终稿索引包.json")
    meta = json.load(open(p17, encoding="utf-8")) if os.path.isfile(p17) else {}
    gen_at = (meta.get("generated_at") or "")[:10]

    groups = collections.defaultdict(list)
    for it in items:
        groups[it["jd_code"]].append(it)
    label = {it["jd_code"]: it["category"].split(" ", 1)[1] for it in items}

    EXCL = ("生成中间产物", "系统导出数据", "工具验证样例", "空表模板", "模板/演示件")

    def cls(it):
        if it["upload"] == "禁上传":
            return "sens"
        act = it.get("action") or ""
        if act == "no_ingest":
            return "inter"
        if act in ("rename", "keep_name"):
            return "low" if float(it["confidence"]) < 0.7 else "ok"
        if any(f in EXCL for f in it["flags"]):
            return "inter"
        if float(it["confidence"]) < 0.7:
            return "low"
        return "ok"

    cards = []
    for code in sorted(groups):
        g = sorted(groups[code], key=lambda x: (-float(x["confidence"]), x["file_name"]))
        cnt = collections.Counter(cls(x) for x in g)
        badges = ""
        if cnt["sens"]:
            badges += f'<span class="badge s">敏感 {cnt["sens"]}</span>'
        if cnt["low"]:
            badges += f'<span class="badge l">待核 {cnt["low"]}</span>'
        if cnt["inter"]:
            badges += f'<span class="badge i">不入库 {cnt["inter"]}</span>'
        trs = []
        for it in g:
            c = cls(it)
            chip = CHIP.get(it.get("subject_source", ""), "")
            subj = html.escape(it["subject"] or "—")
            if chip:
                subj += f' <span class="chip">{chip}</span>'
            trs.append(
                f'<tr class="{c}" data-k="{html.escape((it["file_name"]+" "+it["source_path"]+" "+it["subject"]).lower())}">'
                f'<td>{html.escape(it["file_name"])}</td><td>{subj}</td>'
                f'<td>{html.escape(it["date"])}</td>'
                f'<td class="nm">{html.escape(it["suggested_name"] or "—")}</td>'
                f'<td class="hs">{html.escape((it.get("sha256") or "")[:12])}</td>'
                f'<td class="c">{html.escape(str(it.get("size") or ""))}</td>'
                f'<td class="c">{it["confidence"]}</td>'
                f'<td class="ev">{html.escape(it["evidence"][:60])}</td>'
                f'<td class="fl">{html.escape(" / ".join(it["flags"]))}</td></tr>')
        cards.append(
            f'<section class="card"><h2><span class="jd">{code}</span> {html.escape(label.get(code, code))}'
            f'<span class="cnt">{len(g)} 份</span>{badges}</h2><table><thead><tr>'
            '<th>原文件名</th><th>主体</th><th>日期</th><th>建议新名</th><th>哈希(前12)</th><th class="c">大小</th><th>置信</th><th>证据</th><th>标记</th>'
            f'</tr></thead><tbody>{"".join(trs)}</tbody></table></section>')

    tot = len(items)
    n_low = sum(1 for i in items if cls(i) == "low")
    n_sens = sum(1 for i in items if cls(i) == "sens")
    n_inter = sum(1 for i in items if cls(i) == "inter")
    n_named = sum(1 for i in items
                  if i.get("action") in ("rename", "keep_name")) or sum(
        1 for i in items if i["suggested_name"])

    page = f"""<!doctype html><html lang="zh"><meta charset="utf-8"><title>{html.escape(args.title)}</title>
<style>
body{{font:13.5px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;margin:0;background:#f5f5f7;color:#1b1b1f}}
header{{background:#fff;border-bottom:1px solid #e5e5e8;padding:18px 26px;position:sticky;top:0;z-index:2}}
h1{{font-size:19px;margin:0 0 5px}} .sub{{color:#666;font-size:12.5px}}
.k{{display:flex;gap:10px;margin-top:11px;flex-wrap:wrap}}
.k div{{background:#f7f7f9;border:1px solid #e8e8ec;border-radius:10px;padding:7px 13px;min-width:96px}}
.k b{{display:block;font-size:18px}} .k span{{font-size:11px;color:#777}}
input{{margin-top:11px;padding:7px 11px;width:320px;border:1px solid #d9d9de;border-radius:8px;font-size:13px}}
main{{padding:18px 26px 60px}} .card{{background:#fff;border:1px solid #e5e5e8;border-radius:12px;margin-bottom:14px;overflow:hidden}}
h2{{font-size:14.5px;margin:0;padding:12px 15px;background:#fafafc;border-bottom:1px solid #eee;display:flex;align-items:center;gap:9px}}
.jd{{font-family:ui-monospace,monospace;background:#eef2ff;color:#3346b3;padding:2px 7px;border-radius:6px;font-size:11.5px}}
.cnt{{color:#777;font-size:12px;font-weight:400}}
.badge{{font-size:11px;padding:2px 7px;border-radius:999px;font-weight:500}}
.badge.s{{background:#ffe8e8;color:#b3261e}} .badge.l{{background:#fff4d6;color:#8a6100}} .badge.i{{background:#eee;color:#666}}
table{{width:100%;border-collapse:collapse}} th,td{{padding:6px 9px;border-bottom:1px solid #f2f2f4;text-align:left;vertical-align:top}}
th{{font-size:11px;color:#666;background:#fcfcfd}} td.nm{{font-size:11.5px;color:#2f5d3f}}
td.c{{text-align:right;font-family:ui-monospace,monospace;color:#555}}
td.ev{{color:#777;font-size:11px;max-width:240px}} td.fl{{color:#777;font-size:11px}}
td.hs{{font-family:ui-monospace,monospace;font-size:11px;color:#888;letter-spacing:.2px}}
.chip{{font-size:10px;padding:1px 5px;border-radius:4px;background:#eef2ff;color:#3346b3;margin-left:4px}}
tr.low{{background:#fffdf3}} tr.sens{{background:#fff4f4}} tr.inter{{opacity:.6}}
tr:hover{{background:#f8f8fb}}
</style>
<header><h1>{html.escape(args.title)}</h1>
<div class="sub">{html.escape(owner.get('name',''))} · {html.escape(owner.get('dept',''))} · {html.escape({'E':'员工','M':'部门经理','G':'总经理'}.get(owner.get('role',''), ''))} ｜ 生成 {gen_at} ｜ {tot} 份 ｜ {len(groups)} 个抽屉 ｜ {n_named} 份已有建议名 ｜ 哈希为 sha256 前 12 位，完整值见 16/17</div>
<div class="k"><div><b>{tot}</b><span>已分类</span></div><div><b>{n_named}</b><span>可直接改名</span></div>
<div><b>{n_low}</b><span>分类待核</span></div><div><b>{n_sens}</b><span>敏感禁上传</span></div>
<div><b>{n_inter}</b><span>不入库</span></div></div>
<input id="q" placeholder="搜文件名 / 主体 / 路径…" oninput="doF()"></header>
<main>{''.join(cards)}</main>
<script>function doF(){{const q=document.getElementById('q').value.toLowerCase();document.querySelectorAll('tbody tr').forEach(tr=>{{tr.style.display=tr.dataset.k.includes(q)?'':'none'}})}}</script>
</html>"""
    p = os.path.join(OUT, "分类结果-预览.html")
    open(p, "w", encoding="utf-8").write(page)
    print(p)


if __name__ == "__main__":
    main()
