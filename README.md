# amlibmerge

> **Apple Music Library Split-Album Scanner, Backup & Merger CLI**  
> 一款用于自动扫描、安全备份并合并 Apple Music 云端资料库中「分裂专辑」的跨平台 Python 命令行工具。

---

## 背景与核心能力

Apple Music 资料库中经常会出现同一张专辑被分裂成多张残缺专辑（例如 14 首 + 1 首，或 35 首 + 9 首）的问题。由于 Apple 官方公开 API 未提供从资料库删除专辑或修改元数据的接口，手动逐一处理既耗时又容易误删歌单曲目。

`amlibmerge` 基于 Apple Music 网页端接口（`amp-api.music.apple.com`）实现了安全、受控的自动化工作流：

- **免开发者账号 · 弹窗一键登录**：自动唤起本机 Edge / Chrome / Brave 浏览器打开 `music.apple.com`，完成 Apple ID 登录（支持 2FA）后通过本地 CDP 自动捕获 `media-user-token` 并关闭窗口，同时自动提取公开 Web Developer JWT（无需 $99/年 Apple Developer 账号）。
- **两级分裂检测 + 强安全护栏**：
  - **Tier 1 高置信度**：同一 Apple Music Catalog Album ID 对应的多份分裂资料库专辑。
  - **Tier 2 中置信度**：规范化后同名同艺人但跨 Catalog ID 的疑似分裂专辑。
  - **自动拦截保护**：自动识别并拦截**纯本地上传/本区已下架曲目**、**混入多套不同录音/版本（如古典音乐不同年份录音）**以及**当前区服已下架的专辑**，防止误删不可恢复的内容。
- **删除前自动备份 & 歌单幂等恢复**：
  - 删除专辑前强制自动快照所有可编辑自建歌单与喜爱/评分状态（`backups/`）。
  - 整专重导后自动补回因删除而从自建歌单中移除的曲目，并提供独立的 `restore` 幂等恢复命令。
  - 每次执行落盘写前日志（WAL：`journals/merge_*.jsonl`），中断可追溯。
- **智能兜底与结果分类报告**：
  - 专辑级 `DELETE` 遇到云端缓存残留时，自动降级为整组曲目级逐首清理。
  - 支持 `--all` 一键批处理，结束后输出清晰的分类报告（终端表格 + JSON 报告）：**✅ 自动合并成功** vs **⚠️ 需要手动操作合并**（含具体原因与桌面端手动处理建议）。

---

## 环境要求

- Python **3.11+**
- 有效的 Apple Music 订阅账号
- 本机已安装 Chromium 内核浏览器（Edge / Chrome / Brave，用于弹窗登录；无图形界面环境可使用 `--manual` 手动输入模式）

---

## 安装

```bash
git clone git@github.com:ye-rm/amlibmerge.git
cd amlibmerge

python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
# source .venv/bin/activate

pip install -e ".[dev]"
```

---

## 快速上手

### 1. 登录 Apple Music

```bash
# 弹出本机浏览器窗口登录，成功后自动抓取 Token 并关闭窗口
amlibmerge auth login

# 如需切换 Apple ID，使用全新临时配置启动浏览器
amlibmerge auth login --fresh

# 无图形界面环境：手动粘贴 Cookie 中的 media-user-token
amlibmerge auth login --manual

# 检查当前鉴权状态与区服
amlibmerge auth status --verify
```

> 凭证保存在用户目录 `~/.amlibmerge/credentials.json`（也可通过环境变量 `AM_MEDIA_USER_TOKEN` 覆盖），绝不提交到代码仓库。

### 2. 只读扫描分裂专辑

```bash
# 扫描全量资料库并校验官方曲库在架状态，报告保存至 reports/scan_<timestamp>.json
amlibmerge scan

# 仅检测高置信度（同 Catalog ID）分裂
amlibmerge scan --no-weak
```

### 3. 备份自建歌单与评分（可选，执行合并前也会自动触发）

```bash
amlibmerge backup
```

### 4. 预览并合并分裂专辑

`merge` 命令**默认处于 Dry-Run 预览模式**，不会对云端资料库做任何修改；确认无误后需显式传入 `--execute`。

```bash
# 预览单个分裂组
amlibmerge merge --group G004

# 执行单个分裂组合并
amlibmerge merge --group G004 --execute

# 一键预览所有分裂组（展示哪些可自动合并、哪些将被拦截进入手动清单）
amlibmerge merge --all

# 一键执行全部安全分裂组合并，并在结束后输出「成功 / 需手动合并」完整报告
amlibmerge merge --all --execute

# 对受拦截的分组，在人工确认目标版本后可显式强制合并（仍强制校验目标专辑在架）
amlibmerge merge --group G006 --catalog-id 1440743071 --force --execute
```

### 5. 独立幂等歌单恢复

```bash
# 预览备份快照与当前线上歌单的差异
amlibmerge restore

# 补回所有缺失曲目（幂等去重，不会重复添加）
amlibmerge restore --execute
```

---

## 已知边界与注意事项

1. **播放次数与原始添加日期**：Apple Music 删除并重导专辑会重置受影响曲目的播放次数（Play Count）和原始添加日期（Date Added）；已下载的离线文件需重新下载。
2. **歌单曲目顺序**：受 Apple Music 接口限制，歌单补回采用追加模式，无法保证恢复到原歌单中的精确行号。
3. **官方曲库自身元数据异常**：若某张专辑在 Apple Music 当前官方曲库自身的元数据中就存在分裂，全新重导后仍可能保持分裂。工具会自动识别该情况并在报告中标记为 **`重导后仍分裂`**，建议此类专辑在桌面端 Apple Music 应用中全选曲目 →「显示简介」手动统一「专辑」和「专辑艺人」。

---

## 项目结构

```text
src/amlibmerge/
├── cli.py           # Typer + Rich CLI 入口（auth / scan / backup / merge / restore）
├── browser_auth.py  # 零依赖本地 CDP WebSocket 客户端与浏览器弹窗自动登录
├── client.py        # amp-api.music.apple.com HTTP/2 客户端（自动提取 Web JWT、限流退避）
├── config.py        # 凭证存储、环境变量覆盖与 JWT 有效期解析
├── models.py        # Pydantic 强类型数据模型（专辑、分裂组、备份、WAL 日志、合并报告）
├── detector.py      # Tier 1 / Tier 2 分裂检测引擎与安全护栏判定
├── scanner.py       # 分页扫描资料库、拉取候选曲目与校验区服在架状态
├── backup.py        # 自建歌单与喜爱评分快照导出与加载
└── merger.py        # 删除（含逐首降级）→ 轮询 → 整专重导 → 歌单恢复状态机与报告生成
```

---

## 开发与测试

```bash
pytest -v
```

---

## 免责声明

本项目仅供个人整理与维护自己的 Apple Music 资料库使用，依赖 Apple Music 网页端未公开接口（`amp-api.music.apple.com`），与 Apple Inc. 无任何官方关联。接口行为可能随 Apple 服务端变更而调整，执行写入操作前请务必做好备份并先使用 Dry-Run 预览。
