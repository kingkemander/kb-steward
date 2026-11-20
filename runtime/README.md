# 知识库管家 · 运行时资料

版本见仓库根 `VERSION`；安装后路径见 `.spaceagents/plugins/kb-steward/current.json`。

## 七步流水线

```bash
OUT=./kb-整理
python3 scripts/scan_l1.py     --root "$HOME" --out "$OUT"        # 1 第一层：登记+分拣
python3 scripts/make_report.py --out "$OUT" --root "$HOME" --name "姓名" --dept "部门"  # 2 报告
python3 scripts/classify_l2.py --out "$OUT" --root "$HOME" --name "姓名" --dept "部门"  # 3 分类
python3 scripts/fill_gaps.py   --out "$OUT"                      # 4 批量消待补
python3 scripts/parse_all.py   --out "$OUT" --root "$HOME"       # 5 全文解析
python3 scripts/ai_fill.py     --out "$OUT"                      # 6 AI 补齐（需 Key，文本模型读全文）
python3 scripts/parse_vision.py --out "$OUT" --root "$HOME"      # 6.5 视觉解析（需 Key，纯图/扫描/证照看图）
python3 scripts/merge_ai.py    --out "$OUT"                      # 7 合并 AI+视觉 → L3
python3 scripts/finalize.py    --out "$OUT" --root "$HOME"       # 8 定目录+改名映射
```

第 7 步默认 dry-run；要真复制才加 `--apply --dest <目标目录>`（只复制，不移动）。

## 目录

| 路径 | 用途 |
|---|---|
| `SKILL.md` | 完整方法：分层漏斗、白名单、OCR 教训、命名与两层融合、七步流水线 |
| `agent-template.md` | 智能体定义（仓库根，打包时进 zip 根，由 bootstrap 注册到 `.opencode/agents/`） |
| `scripts/scan_l1.py` | 1 只登记 + 前置分拣，不读正文 |
| `scripts/make_report.py` | 2 报告 + 文档哈希/重名 |
| `scripts/classify_l2.py` | 3 读内容分类、抽主体/日期 |
| `scripts/fill_gaps.py` | 4 模板豁免/项目名/目录继承/通用件/日期回退 |
| `scripts/parse_all.py` | 5 全文解析（docx/pdf/pptx/xlsx/csv/doc/xls/dxf） |
| `scripts/ai_fill.py` | 6 大模型定抽屉、补主体/日期（需 `SPACEAGENTS_AUTO_API_KEY`）；S级/A级敏感件跳过不送 |
| `scripts/parse_vision.py` | 6.5 档3 视觉：VLM 看纯图 PPT/扫描 PDF/证照，抽描述+主体/日期/类目 |
| `scripts/merge_ai.py` | 7 合并 AI + 视觉结果 → L3 |
| `scripts/finalize.py` | 8 命名校验、定目录、出改名映射 |
| `scripts/categories.py` | 类目加载器（读根目录 categories.json，各脚本唯一类目数据源） |
| `scripts/export_package.py` | 导出上报包（只元数据）给部门经理 |
| `scripts/merge_dept.py` | 收多份上报包，去重/查冲突/出部门目录 |
| `scripts/make_html.py` | 生成可读 HTML 预览 |
| `templates/naming-rules.md` | 4 段命名、5 条硬约束、日期三级回退 |
| `templates/two-layer-json-format.md` | 个人包/部门包/全公司目录/改名映射字段 |

## 两层交接

```bash
# 员工：把个人结果变成上报包
python3 scripts/export_package.py --out ./kb-整理 --root "$HOME" \
  --name "张三" --dept "交付部" --role 员工 --to "李经理"

# 经理：收多个包，做部门融合
python3 scripts/merge_dept.py --out ./部门融合 --packages-dir ./收到的上报包 \
  --dept "交付部" --manager "李经理"
```

上报包只含元数据（抽屉/主体/日期/建议名/相对路径/哈希），不含正文和绝对路径；敏感件只报数量。

## 产物一览

| 文件 | 来自 | 内容 |
|---|---|---|
| `01-全量文件清单.csv` | 1 | 全部文件 + 判定/通道/理由 |
| `02/03` | 1 | 工作文档候选 / 图片与 CAD |
| `08-工作文档-哈希与重名.csv` | 2 | 哈希、重复组、同名组 |
| `09-分类结果.csv` | 3 | JD 编号、主体、日期、建议名、置信、证据 |
| `10-目录结构建议.md` | 3 | 抽屉统计 + 目录树 |
| `11/12` | 4 | 按目录批量补 / 待补处理说明 |
| `13-全文解析缓存.jsonl` | 5 | 每份文件的全文（截断 6 万字） |
| `14-AI补齐缓存.json` | 6 | 每份文件的 AI 判定（按文件缓存，重跑不计费） |
| `14b-视觉解析缓存.json` | 6.5 | 纯图/扫描/证照的 VLM 视觉结果（描述+主体/日期/类目） |
| `15-最终目录树.md` | 8 | 入库目录树 + 待确认清单 |
| `16-改名映射.csv/json` | 8 | 原路径 → 目标路径 |
| `17-终稿索引包.json` | 8 | 定稿索引 |

## 安全边界

- 全流程只读原件；`--apply` 也只复制。
- 跳过系统目录、隐藏工具目录、依赖包目录、回收站。
- 第 6 步发每份文件全文（长文按 24000 字分段）；只发文本，不发附件、不发图片。
- 中间产物/系统导出/空表模板不送模型。
- S 级敏感件标 `禁上传`，只出清单。

## 常问

**扫出来工作文档很少？** 正常。第一层是漏斗窄口，多数是图片/中间产物/依赖。
**AI 补齐跑不了？** 没有 `SPACEAGENTS_AUTO_API_KEY`。规则层照样能出目录和映射。
**外接盘怎么扫？** 再跑一次 `--root /Volumes/你的盘 --out ./kb-整理-外接盘`。
**改名会动原件吗？** 不会。默认只出映射；`--apply` 也是复制到新目录。
