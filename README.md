# ZhiTan: mcp for boss

本地运行的 BOSS 直聘 MCP 服务。通过浏览器扩展直接复用日常浏览器里的登录状态，让 Codex、Antigravity、Claude Desktop、Cursor 等支持 MCP 的客户端帮你搜岗位、读 JD、整理候选清单，确认后再发默认招呼。

不导出 Cookie，也不启动无头浏览器。服务端与扩展只在 `127.0.0.1` 通信，需要本机密钥配对。

## 工具一览

| 工具 | 说明 |
| --- | --- |
| `get_session_status` | 检查扩展连接、登录状态与当前搜索页；首次调用弹出本机配对窗口 |
| `search_jobs` | 沿用当前页面城市和筛选条件滚动抓岗位，按 `job_id` 去重 |
| `get_job_detail` | 读取指定岗位的完整详情和沟通状态 |
| `prepare_greetings` | 基于本次会话已读详情，凑满 30 个未沟通岗位生成审核清单 |
| `send_greetings` | 按审核清单逐条点击官网「立即沟通」，发送默认招呼（默认关闭） |

## 安装

目前在 Windows（Python 3.13、Edge / Chrome）上验证。提前装好 Git 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)（`uv` 可自动安装 Python 3.13）。

### 1. 准备环境

```powershell
git clone https://github.com/ZIHAO-WAN/zhitan-mcp.git
cd zhitan-mcp
uv venv --python 3.13 .venv
uv pip install --python .venv\Scripts\python.exe --require-hashes -r requirements.lock
```

### 2. 接入 MCP 客户端

Codex 和 Antigravity 可以直接运行安装脚本，脚本会自动备份原配置，只更新 `boss_zhipin` 一项（可加 `--dry-run` 先看改动）：

```powershell
# Codex
.venv\Scripts\python.exe scripts\install_codex.py

# Antigravity
.venv\Scripts\python.exe scripts\install_antigravity.py
```

其他支持 stdio 的客户端（如 Claude Desktop、Cursor），在 MCP 配置文件里添加下面这段，把路径替换成本地仓库目录：

```json
{
  "mcpServers": {
    "boss_zhipin": {
      "command": "C:\\path\\to\\zhitan-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "boss_zp.server"],
      "env": {
        "PYTHONUTF8": "1",
        "PYTHONPATH": "C:\\path\\to\\zhitan-mcp"
      }
    }
  }
}
```

配置完成后，重启客户端或刷新 MCP 服务列表，新开会话即可使用。

### 3. 加载扩展并配对

1. 打开 `edge://extensions`（Chrome 为 `chrome://extensions`），开启「开发人员模式」，点「加载解压缩的扩展」，选择仓库里的 `browser-extension` 目录。
2. 打开 BOSS 直聘并登录，进入岗位搜索页，手动选好城市和筛选条件。
3. 在客户端里让它检查连接（比如发一句「检查 BOSS 直聘连接」）。首次调用会弹出本机配对窗口，把密钥粘贴到扩展「ZhiTan · Boss 连接器」并点击连接。密钥只填在扩展里，不要发到聊天中。

## 使用说明

配对完成后直接用自然语言提需求就行。比如你在官网选了北京，可以这样问：

> 在北京搜一下「大模型」，粗筛之后读候选岗位详情，按实际职责挑出和后训练最相关的岗位，并说明这次搜索的覆盖范围。

### 搜索范围

搜索沿用当前在官网页面选好的城市和筛选条件，打开临时标签页滚动加载，结束后自动关闭。默认最多取 60 条，单关键词上限 150 条，按 `job_id` 去重，并在结果里返回停止原因：

- `limit`：达到请求数量
- `site_end`：官网列表到底或无匹配岗位
- `no_new_ids`：滚动后 3 秒没有加载出新岗位
- `deadline`：达到 45 秒搜索上限，返回当前已取到的结果

### 审核与发送

`prepare_greetings` 只使用本次会话读过详情的岗位，自动跳过已沟通或本地已有记录的条目。凑满 30 个合格岗位后生成单次有效的审核清单，15 分钟内有效。

发送功能默认关闭。需要开启时，给安装脚本加上 `--enable-send` 重新运行（通用客户端可在 `env` 中设 `"BOSS_ZP_ENABLE_SEND": "1"`，或启动时传 `--allow-send`），重启客户端生效：

```powershell
.venv\Scripts\python.exe scripts\install_codex.py --enable-send
# 或
.venv\Scripts\python.exe scripts\install_antigravity.py --enable-send
```

开启后，每批发送前仍需你确认具体清单。只点击官网默认「立即沟通」，按北京时间每天上限 150 次。遇到验证码、断线或点击后状态不明，会立刻停批且不自动重试，本地保留记录防止重复打招呼。

配对密钥和发送记录（SQLite）保存在 `%LOCALAPPDATA%\boss-zhipin-mcp`。更新代码后，在扩展页重载连接器并新开 MCP 会话即可。

## 本地测试

需要 Node.js 运行扩展侧单测：

```powershell
uv pip install --python .venv\Scripts\python.exe -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
node tests\test_extension.js
node tests\test_extension_background.js
node tests\test_greeting_action.js
.venv\Scripts\python.exe scripts\verify_extension_stdio.py
```

如果想针对已登录的 BOSS 页面跑一次只读实测（不发招呼），先关闭占用 `boss_zhipin` 连接的其他会话，保持扩展已配对且搜索页打开：

```powershell
.venv\Scripts\python.exe scripts\verify_extension_live.py --keyword 大模型 --review-30
```

[MIT License](LICENSE)
