# amlibmerge

[English](README.md) | **简体中文**

> **Apple Music 资料库分裂专辑扫描、备份与合并 CLI / TUI 工具**  
> 一款跨平台 Python 命令行与交互式终端工具，用于自动扫描 Apple Music 云端资料库中的「分裂专辑」、安全备份自建歌单与评分、执行删旧整专重导与歌单恢复，并输出「自动合并成功 / 需手动操作合并」分类报告。

> [!IMPORTANT]
> **诚邀测试与共建**：当前的合并逻辑尚无法自动处理部分分裂情况（例如混入多套不同录音/版本、含本区已下架曲目、或 Apple Music 官方曲库自身元数据存在分裂的专辑）。欢迎大家使用自己的曲库测试（建议先从只读 `scan` 和 Dry-Run 预览开始），并通过 Issue / PR 反馈遇到的分裂样本，共同完善合并方法！

---

## 背景与核心能力

Apple Music 资料库中经常会出现同一张专辑被分裂成多张残缺专辑（例如 14 首 + 1 首，或 35 首 + 9 首）的现象。由于 Apple 官方公开 API 未提供从资料库删除专辑或修改元数据的接口，手动逐一整理既繁琐，又容易因删除专辑导致自建歌单里的对应曲目一并被清空。

`amlibmerge` 基于 Apple Music 网页端接口（`amp-api.music.apple.com`）实现了安全、受控的自动化流程：

- **免开发者账号 · 弹窗一键登录**：自动唤起本机 Edge / Chrome / Brave 浏览器打开 `music.apple.com`，完整支持 Apple ID 与双重认证（2FA），登录后通过本地 CDP 自动捕获 `media-user-token` 并关闭窗口，同时自动提取公开 Web Developer JWT（无需 $99/年 Apple Developer 账号）。
- **两级分裂检测 + 强安全护栏**：
  - **Tier 1 高置信度**：多个资料库专辑指向同一个 Apple Music 官方 Catalog Album ID。
  - **Tier 2 中置信度**：规范化后同名同艺人、但跨 Catalog ID 的疑似分裂专辑。
  - **内置拦截保护**：自动识别并拦截**纯本地上传 / 本区已下架曲目**（无 Catalog 映射）、**混入多套不同录音/版本（常见于古典音乐）**以及**当前区服已下架的专辑**，防止误删不可恢复的内容。
- **执行前自动备份、WAL 日志与幂等恢复**：
  - 执行删除前强制自动快照所有可编辑自建歌单与喜爱/评分状态（`backups/`）。
  - 每步写入写前日志（WAL：`journals/merge_*.jsonl`），状态机全程可追溯。
  - 重导完成后自动补回受影响的自建歌单曲目，并提供独立的 `restore` 幂等恢复命令。
- **中英双语界面（自动适配 + 随时切换）**：默认英文，在中文系统下自动显示简体中文；支持通过 `--lang en|zh|auto`、`amlibmerge lang <值>`、`AMLIBMERGE_LANG` 环境变量或在 TUI 内按 `G` 实时切换语言。
- **交互式终端界面（TUI）**：运行 `amlibmerge --tui`（或 `amlibmerge tui`）即可打开支持键盘与鼠标操作的交互面板，一站式完成登录、扫描、双栏对比查看每个分裂壳下的曲目与拦截原因、勾选分组、Dry-Run 预览、二次确认合并执行与歌单恢复。
- **智能兜底与结果分类报告**：
  - 专辑级 `DELETE` 若遇云端残留空壳，自动降级为整组曲目级逐首清理。
  - 支持 `--all` 一键批处理，结束后输出终端分表与 JSON 报告（`reports/merge_report_<timestamp>.json`），清晰列出 **✅ 自动合并成功** 与 **⚠️ 需要手动操作合并** 的专辑清单。

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

### 交互式 TUI 模式（推荐新手 / 测试用户直接使用）

```bash
# 启动交互式 TUI（自动加载本地最新扫描报告与歌单备份，不立即发起网络请求）
amlibmerge --tui
# 等价子命令（也支持指定预加载某份扫描报告）：
amlibmerge tui --report reports/scan_20261124_154538.json
```

TUI 常用快捷键：`L` 登录 · `S` 扫描资料库 · `B` 备份歌单 · `Space` 勾选/取消 · `A` 全选安全组 · `N` 清空勾选 · `D` Dry-Run 预览 · `M` 确认执行合并 · `R` 歌单差异比对与恢复 · `G` 切换中英文 · `Q` 退出。

### 显示语言设置

`amlibmerge` 默认显示**英文**，在中文操作系统下自动显示**简体中文**，也支持随时手动调整：

```bash
# 查看当前生效语言及来源
amlibmerge lang

# 持久化设置语言偏好（en | zh | auto）
amlibmerge lang en
amlibmerge lang zh
amlibmerge lang auto

# 单次命令覆盖或通过环境变量指定
amlibmerge --lang en scan
AMLIBMERGE_LANG=en amlibmerge --tui
```

### 1. 登录 Apple Music

```bash
# 弹出本机浏览器窗口登录，成功后自动抓取 Token 并关闭窗口
amlibmerge auth login

# 如需切换 Apple ID，使用全新临时浏览器配置启动
amlibmerge auth login --fresh

# 无图形界面环境：手动粘贴 Cookie 中的 media-user-token
amlibmerge auth login --manual

# 检查当前鉴权状态与区服
amlibmerge auth status --verify
```

> 凭证保存在用户目录 `~/.amlibmerge/credentials.json`（也可通过环境变量 `AM_MEDIA_USER_TOKEN` 覆盖），不会提交到代码仓库。

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

## 手动合并指南（当 CLI 无法自动处理时）

对于部分触发安全护栏被自动跳过的专辑（如混入多套不同录音版本、含本区已下架/本地自传曲目），或因异常未能由 CLI 自动完成的分裂专辑，可以按照以下步骤在 Apple Music 客户端中手动完成合并：

1. **查看报告并确认歌单备份**
   - 查看 `amlibmerge merge --all` 末尾输出的「⚠️ 需要手动操作合并」清单，记下专辑名称、艺人以及对应的目标 Catalog ID。
   - 确认已有一份最新备份快照（可先执行 `amlibmerge backup`），避免删除专辑后忘记原自建歌单里包含哪些曲目。
   - *（若组内混入了多套不同版本/年份录音，请先确认要保留哪套版本再操作。）*
2. **在资料库中找到所有分裂专辑**
   - 打开 Apple Music 客户端（macOS、Windows、iOS/iPadOS 或网页端 `https://music.apple.com`），进入 **「资料库 → 专辑」**，搜索该专辑名称，定位出该专辑对应的**所有分裂条目**。
3. **删除所有分裂专辑**
   - 对找到的**每一个**分裂专辑条目，依次点击 `…` → 选择 **「从资料库中删除」（Delete from Library）**，将所有分裂出来的残缺条目**全部删干净**。
   - 等待数秒，确保云端资料库同步完成、不再残留旧的分裂条目。
4. **重新把完整专辑加入资料库**
   - 切换到 Apple Music 官方曲库搜索该专辑（或直接根据扫描报告中的 Catalog ID 打开专辑链接 `https://music.apple.com/<区服>/album/<catalog_id>`），点击 **`+ 添加`** 将完整专辑重新加入资料库，即可恢复为单张完整专辑。
5. **自动补回受影响的歌单曲目（如适用）**
   - 由于从资料库删除专辑会同步移出自建歌单中对应的曲目，重新加入专辑后，运行以下命令即可自动、幂等地将缺失曲目补回原歌单：
     ```bash
     amlibmerge restore --execute
     ```

> **补充提示：如果手动删除并重新添加后依然分裂怎么办？**  
> 极少数专辑在 Apple Music 当前官方曲库自身的元数据中就存在分裂（CLI 会将这类专辑标记为 `重导后仍分裂`）。针对此类情况，请在桌面端 Apple Music 应用中同时选中该专辑的所有曲目 → 右键打开 **「显示简介」（Get Info）**，手动将 **「专辑」** 和 **「专辑艺人」** 统一为完全一致的内容（可先临时加一个后缀保存，再改回原名保存，以强制触发重新归组）。

---

## 已知边界与注意事项

1. **播放次数与原始添加日期**：删除并重导专辑会重置受影响曲目的播放次数（Play Count）和原始添加日期（Date Added）；已下载的离线文件需重新下载。
2. **歌单曲目顺序**：受 Apple Music 接口限制，歌单补回采用追加模式，无法保证恢复到原歌单中的精确行号。
3. **官方曲库自身元数据异常**：上游官方曲库本身元数据损坏的专辑无法仅靠删后重导合并，工具会自动识别并在报告中标注。

---

## 项目结构

```text
src/amlibmerge/
├── cli.py           # Typer + Rich CLI 入口（auth / scan / backup / merge / restore / tui / lang）
├── tui.py           # Textual 交互式 TUI 面板（扫描、右侧曲目检视、二次确认弹窗、分类报告与歌单恢复）
├── i18n.py          # 中英双语词典、系统语言检测与运行时切换
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

---

## 开源协议

本项目基于 [MIT License](LICENSE) 开源。
