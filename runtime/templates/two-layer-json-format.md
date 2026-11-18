# 两层融合规则与 JSON 格式（v1.0）

> 两层 = **个人层**（每人自己电脑的索引）+ **职位判定层**（员工 / 部门经理 / 总经理各自能做什么）。
> 融合 ≠ 把几套文件夹树拼起来；融合 = 一份文件只存一次，用「JD 编号 + 权限域」生成不同视图。
> 本文件与实际脚本产出一致：`handover@1.0` 由 `export_package.py` 生成，`dept-catalog@1.0` 由 `merge_dept.py` 生成。

## 一、四种产物

| 产物 | 谁产出 | 谁看 | 脚本 | 说明 |
|---|---|---|---|---|
| A 上报包 | 员工 | 直属经理 | `export_package.py` | 我电脑上有什么、建议归哪类 |
| B 部门融合包 | 部门经理 | 总经理 | `merge_dept.py` | 本部门去重后的索引，**只带元数据** |
| C 全公司目录 | 总经理 | 全员 | 暂用 `merge_dept.py` 汇总 | 带版本号的唯一权威目录 |
| D 改名映射 | 全员（可延后） | 本人 | `finalize.py` | 原名 → 新名，改不改自己定 |

## 二、A 上报包（`上报包-姓名-YYYYMMDD.zip`）

```
上报包-张三-20261118.zip
├── handover.json
├── README.md
└── manifest.json
```

**目录不存正文、不存绝对路径、不含敏感件。**

```json
{
  "schema": "kb-steward/handover@1.0",
  "from": { "name": "张三", "dept": "交付部", "role": "员工" },
  "to": "李经理",
  "generated_at": "2026-11-18T18:15:31+08:00",
  "items": [
    {
      "key": "ea98323351edb8b8",
      "source_path": "Downloads/发票与Excel逐项对照表.xlsx",
      "jd": "70.01",
      "subject": "",
      "subject_type": "",
      "date": "2026",
      "suggested_name": "发票_主体待补_发票与Excel逐项对照表_2026.xlsx",
      "target_path": "70.01 发票票据/发票_主体待补_发票与Excel逐项对照表_2026.xlsx",
      "action": "rename",
      "confidence": 0.88,
      "flags": ["主体待补", "合并1份重复", "日期待核"]
    }
  ],
  "counts": {
    "total": 395, "rename": 309, "keep_name": 14,
    "no_ingest": 72, "sensitive_excluded": 1
  },
  "note": "只含元数据，不含文件正文、不含绝对路径。敏感件已排除，仅报数量。"
}
```

`manifest.json` 校验 handover.json：

```json
{ "schema": "kb-steward/handover@1.0", "file": "handover.json",
  "sha256": "a96a31f1…", "size": 246074, "generated_at": "…" }
```

字段纪律：

- `key` = 文件内容哈希（跨人同文件靠它识别重复）；缺失时由接收端用 `source_path|suggested_name` 兜底。
- `source_path` / `target_path` 一律相对家目录，不带用户名、不带盘符。
- `action` 只有 `rename` / `keep_name` / `no_ingest`；`hold_sensitive` 的条目**不进 items**，只进 `counts.sensitive_excluded`。
- `confidence` < 0.7 或 `flags` 含 `主体待补` / `继承待核` / `日期待核` → 接收端列入待复核，不进经理签核单。
- `flags` 取值固定：`个人` / `过期` / `重复` / `待OCR` / `待转换` / `待复核` / `主体待补` / `继承待核` / `日期待核` / `合并N份重复` / `敏感`。

## 三、B 部门融合包（`20-部门融合包.json`）

```json
{
  "schema": "kb-steward/dept-catalog@1.0",
  "dept": "交付部",
  "manager": "李经理",
  "members": ["张三", "王五"],
  "packages": [ { "file": "上报包-张三-20261118.zip", "from": { "…": "同 A 的 from" }, "count": 395 } ],
  "stats": {
    "input_items": 790, "merged_items": 353, "duplicates_merged": 353,
    "conflicts": 204, "blocking": 9, "review": 195,
    "domains": { "全员": 0, "交付部+管理层": 61, "…": 0 }
  },
  "items": [
    { "…": "同 A 的条目", "sources": ["张三", "王五"], "dup_count": 2, "domain": "交付部+管理层" }
  ],
  "errors": []
}
```

同目录另出三份人看的文件：`21-部门目录树.md`、`22-冲突清单.csv`、`23-签核单.md`。

**自动判定（不需要人逐条看）**

| 输入情况 | 判定 type | 处理 | 进签核单 |
|---|---|---|---|
| 同 `key` 多人都有 | 重复 | 合并为一条，记 `sources` | 否 |
| 同 `key` 但 `jd` 不同 | 类目争议 | 取票数多的 `jd` | 是 |
| 同 `jd` + 同文件名、`key` 不同 | 版本冲突 | 留日期最新，其余标「旧版」 | 是 |
| `confidence`<0.7 或 待补类 flags | 待复核 | 按人汇总，退回原上报人 | 否 |

`blocking` = 版本冲突 + 类目争议；`review` = 待复核。经理只看 blocking，通常 5–15 条。

**M 模式不得把文件正文往上送**，只送元数据与冲突清单。

## 四、C 全公司目录（`company-catalog@1.0`，暂未出脚本）

结构 = B 再加 `version` 与域授权表：

```json
{
  "schema": "kb-steward/company-catalog@1.0",
  "version": "2026.11.18-1",
  "generated_at": "…",
  "departments": [ { "dept": "交付部", "manager": "李经理", "items": 353 } ],
  "items": [ { "…": "同 B 的条目", "owner_dept": "交付部", "live_path": "…", "version": "v1" } ],
  "grants": [ { "jd_prefix": "20", "domains": ["交付部", "管理层"], "mode": "read" } ]
}
```

权限域默认映射：10 资质=全员｜20 合同投标=交付部+管理层｜30 人员=行政+管理层｜40 制度=全员｜50 技术=交付部｜60 模板=全员｜70 财务=财务+管理层｜80 项目=交付部。父域覆盖子域；跨部门共享走域授权，**不复制文件**。

## 五、D 改名映射

```json
{ "schema": "kb-steward/rename-map@1.0", "items": [
  { "from": "Downloads/a.docx", "to": "20.01 合同业绩/合同_甲方_名称_2026.docx",
    "action": "rename", "confidence": 0.92, "flags": [] }
] }
```

`finalize.py` 默认 dry-run，`--apply` 也只复制不改原文件。改名是本人决定，可延后；不改也能入库（目录用新名索引）。

## 六、跨角色消息（接口未接通时）

```
【类型】发送者（部门/角色）｜schema 版本｜载荷：文件名或路径｜需对方动作
类型 = 目录上行 / 部门上行 / 总目录广播 / 异议上行 / 命令 / 结果上行 / 签核 / 退回 / 上传完成
```

实例：

```
【目录上行】张三（交付部/员工）｜kb-steward/handover@1.0｜载荷：上报包-张三-20261118.zip｜需李经理做部门融合
```

一次只发一件事，不合并类型；退回必须写原因；不代发、不代上传。
