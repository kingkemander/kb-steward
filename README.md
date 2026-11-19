# 知识库管家（kb-steward）

把散在每个人电脑里的文件，登记成一套公司能用的文件系统：**一套编号、一本总目录、两层融合、权限域视图**。

- 不是「把文件夹搬整齐」，是「给每份公司文件登记户口」。
- 先看清楚 → 再确认 → 才动手；原件永远保留。
- 默认先做**第一层**：检查你指定的目录 + 前置分拣 + 清单 + 报告，**只读**：不读正文、不改名、不移动、不上传。
- 用户说「继续」就往下走：分类 → 批量补待补 → 全文解析 → AI 补齐 → 定目录 + 改名映射 → 上报包。

## 同事怎么装（一句话）

> 帮我装知识库管家，地址是 `github.com/kingkemander/kb-steward`

装完**新建会话**，智能体下拉选「知识库管家」，然后说：

> 我叫 XXX，XX 部门，职位 XX。帮我检查我的电脑目录文件，出第一层。

## 六步流程（用户视角）

| 步 | 做什么 | 动不动文件 |
|---|---|---|
| 1 | 问清姓名 / 部门 / 职位 / 能看哪些盘与文件夹（`--list-roots` 让用户勾选） | 只读 |
| 2 | 环境体检：缺哪些解析库、装到哪个盘（不默认系统盘） | 只读 |
| 3 | 一键检索目录 + 按既有规则解析 | 只读 |
| 4 | 出建议结构：归类后的文件结构、新文件名（原名括注在括号里） | 只出文件 |
| 5 | 用户要看就看（目录索引 / 可搜索预览页） | 只读 |
| 6 | 不看就直接导出索引包，交第二层融合 | 只写新文件 |

第 4 步默认只出映射，不复制。用户明确说「执行」才加 `--apply --dest`，且只复制、不移动。

## 它能干什么

| 你说 | 它做 |
|---|---|
| 帮我检查电脑目录文件 / 第一层 | 按你指定的范围清点 + 分拣：工作文档、图片、CAD、垃圾、依赖分开列 |
| 这些文件哪些有用 | L2/L3 打分与硬证据判定（工作 / 个人） |
| 帮我分类 | 对照公司文件计划给 JD 编号、证据、置信度 |
| 有没有重复 | 内容哈希 + 同名不同内容比对 |
| 有没有敏感文件 | 账号密码表、apiKey、邀请码、身份证照片 |
| 给我改名 | 第 7 步出映射，不自动改 |
| 往下做 / 继续 | 按八步顺序推进 |
| 上报 / 交给经理 | 出只含元数据的上报包（zip），附标准消息 |
| 融合 / 合并部门 | 收多份上报包，去重、查冲突、出部门目录与签核单 |
| 部门 / 全公司目录 | 出部门融合包 / 全公司目录 JSON |

## 第一层的边界

只回答「我电脑上有什么、哪些值得往下看」。

- 不读正文、不 OCR、不判公司/个人、不碰敏感件
- 不改名、不移动、不删除、不上传
- 跳过系统库、隐藏工具目录、依赖包、回收站
- 外接盘不默认扫，要扫需明确指定

## 三类产物

| 产物 | 给谁 | 内容 |
|---|---|---|
| `目录索引.md` | 人 | 按抽屉分组，一行一份：新文件名 —— 一句话内容 |
| `改名对照.md` | 人 | 原名 → 新名，两列，无哈希 |
| `17-终稿索引包.json` | 智能体 / 第二层 | 完整字段（抽屉/主体/日期/哈希/权限域） |

## 依赖体检

```bash
python3 runtime/scripts/doctor.py                                  # 看缺什么
python3 runtime/scripts/install_deps.py --target "<盘位>/kb-deps"  # 装到指定盘
```

| 格式 | 最小依赖 | 是否需 LibreOffice |
|---|---|---|
| `.docx` / `.xlsx` / `.pptx` / `.pdf` | python-docx / openpyxl / python-pptx / pymupdf | 否 |
| `.xls` | `xlrd`（1 MB） | **否**，直读 |
| `.doc` | macOS `textutil`（自带）／Windows `antiword`（1–2 MB） | 一般不需要 |
| `.ppt` | `olefile`（200 KB） | 一般不需要 |
| 扫描件 | `tesseract` | — |

**不默认装系统盘**。

## 两层交接

```
员工 → 上报包（只元数据） → 部门经理 → 部门融合包 + 签核单 → 总经理 → 全公司目录 + 权限域
```

上报包不含正文、不含本机绝对路径、不含敏感件；部门融合自动去重、判类目争议、判版本冲突，经理只签核 `blocking`，`review` 按人退回。

## 仓库结构

```
kb-steward/
├── bootstrap.py                    # 安装器（下载、校验、解压、注册智能体）
├── agent-template.md               # 智能体模板（mode: all）
├── make_release.py                 # 发版工具（打包 + 回填 SHA + 打印命令）
├── VERSION                         # 当前版本
├── skills/kb-steward/SKILL.md      # 入口技能（安装引导）
├── runtime/                        # 运行时源码
│   ├── SKILL.md                    # 方法与规则全文（14 节）
│   ├── README.md                   # 运行时说明
│   ├── scripts/                    # 11 个脚本
│   └── templates/                  # 命名规则、两层 JSON 格式
└── runtime-vX.Y.Z.zip              # 发布资产（release 附件）
```

脚本：`scan_l1` · `make_report` · `classify_l2` · `fill_gaps` · `parse_all` · `ai_fill` · `merge_ai` · `finalize` · `make_html` · `export_package` · `merge_dept`

## 手动安装（不用入口技能时）

```bash
python3 bootstrap.py --workspace "<工作区>"
```

离线/自测：

```bash
python3 bootstrap.py --workspace "<工作区>" --package ./runtime-v1.3.7.zip
```

## 换到 GitLab 或镜像站

仓库里已提交 `runtime-v*.zip`，不依赖 GitHub Release 附件。

```bash
export KB_STEWARD_DIST_URL="https://gitlab.com/<组>/kb-steward/-/raw/{version}/{asset}"
python3 bootstrap.py --workspace "<工作区>"
```

占位符：`{version}` = `v1.3.9`，`{asset}` = `runtime-v1.3.9.zip`。SHA-256 校验照常执行，镜像内容必须与仓库一致。

## 环境变量

| 变量 | 用途 | 必需 |
|---|---|---|
| `SPACEAGENTS_AUTO_API_KEY` | 第 6 步 AI 补齐 | 到第 6 步才需要 |
| `SPACEAGENTS_AUTO_API_URL` | 覆盖接口地址（换通道/代理） | 否 |
| `KB_STEWARD_DEPS` | 依赖目录（等价于 `--deps`） | 否 |
| `KB_STEWARD_DIST_URL` | 自定义分发地址（GitLab / 镜像） | 否 |

密钥只从环境变量读，不写进任何文件、不进日志。

## 验证

```bash
python3 ".spaceagents/plugins/kb-steward/runtime-v1.3.7/scripts/scan_l1.py" \
  --root "$HOME" --out ./kb-第一层 --max-files 500
```

看到 JSON 汇总（total_files / verdict_counts）即通过。

## 卸载

```bash
rm -rf "<工作区>/.spaceagents/plugins/kb-steward"
rm -f  "<工作区>/.opencode/agents/知识库管家.md"
```

扫描结果与索引包不在安装目录，不会被删。

## 设计依据

Johnny.Decimal（编号 / 中央索引 / ID 不复用）· PARA（个人工作区）· ISO 15489（职能分类 / 登记 / 受控词表）· 开源文件整理 Agent 的安全机制（预览-确认-执行、白名单根目录、复制不移动、哈希去重、可回滚）。

## 版本

- **1.4.0** — 六步流程 + 一键 `run_all.py`；依赖体检 `doctor.py` / `install_deps.py`（必须指定盘位）；三类产物（人读目录索引 / 改名对照 / 机器索引包）；新名带「（原：…）」括注。
- 1.3.10 — 对外表述改为「检查电脑目录文件」，强调只读与范围可控。
- 1.3.9 — zip 入库（GitLab 可直取）；bootstrap 支持 `KB_STEWARD_DIST_URL`。
- 1.3.8 — 标准包结构：`agent-template.md` 移到仓库根。
- 1.3.7 — 新增 `make_release.py`（打包 + SHA 回填 + 发布命令）。
- 1.3.6 — 私有仓库下载走 GitHub API（`GITHUB_TOKEN`/`GH_TOKEN`）。
- 1.3.0 — 两层交接：上报包导出、部门融合。
- 1.2.3 — 全文 AI 补齐整合与终稿输出。
- 1.1.0 — 七步流水线齐全。
- 1.0.0 — 首个可分发版本。
