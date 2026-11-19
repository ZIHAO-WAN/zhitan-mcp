# 职探 MCP

让 Codex 在你已登录的 BOSS 直聘浏览器中搜索岗位、阅读详情，并根据实际职责整理候选清单。职探是本机运行的个人求职工具，通过浏览器扩展复用登录状态。

## 快速开始

已验证环境为 Windows、Python 3.13、Microsoft Edge 和 Codex。先安装 Git、[uv](https://docs.astral.sh/uv/getting-started/installation/)、Edge 和 Codex。uv 可为这个项目安装 Python 3.13。

**1. 在 PowerShell 安装 MCP**

```powershell
git clone https://github.com/ZIHAO-WAN/zhitan-mcp.git
cd zhitan-mcp
uv venv --python 3.13 .venv
uv pip install --python .venv\Scripts\python.exe --require-hashes -r requirements.lock
.venv\Scripts\python.exe scripts\install_codex.py
```

安装脚本会备份既有 Codex 配置，只更新 `boss_zhipin` 项。安装后开启新的 Codex 聊天以加载工具。

**2. 在 Edge 加载扩展**

打开 `edge://extensions`，启用开发人员模式，点「加载解压缩的扩展」，选择仓库中的 `browser-extension` 文件夹。打开 BOSS 直聘，登录并进入岗位搜索页，手动设置城市和筛选条件。

**3. 配对并发起首次搜索**

在 Codex 中说「用职探检查浏览器连接」。首次调用会显示本地配对窗口，将密钥粘贴到 Edge 的「职探 · Boss 连接器」并点击连接。密钥只填在扩展里。连接后，假设你在官网选了北京，可以这样使用：

> 在北京搜索「大模型」，读取初筛候选的详情，按实际职责找出与大模型后训练最相关的岗位，并说明搜索覆盖范围。

## 搜索和审核

搜索沿用官网筛选，在同一标签页滚动加载，默认读取最多 60 个岗位，单关键词上限 150。结果按 `job_id` 去重。达到请求量时返回 `limit`，官网明确结束时返回 `site_end`，滚动后 3 秒没有新 ID 时返回 `no_new_ids`，到 45 秒搜索上限时返回 `deadline`。后两种情形返回当时已取得的岗位。

审核清单复用本次会话已读详情，满 30 个合格岗位才生成，有效期 15 分钟。发送默认关闭。需要发送时，用 `.venv\Scripts\python.exe scripts\install_codex.py --enable-send` 更新配置并重新开启聊天，每批仍须明确批准具体清单。职探只发送官网默认招呼，按北京时间每日最多 150 次。结果未知时停批，保留该岗位记录并阻止重复发送。

配对和发送记录均保存在 `%LOCALAPPDATA%\boss-zhipin-mcp`。更新代码后，在 Edge 扩展页重载连接器并开启新 Codex 聊天。

## 验证

安装 Node.js 后可以运行本地检查：

```powershell
uv pip install --python .venv\Scripts\python.exe -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
node tests\test_extension.js
node tests\test_extension_background.js
node tests\test_greeting_action.js
.venv\Scripts\python.exe scripts\verify_extension_stdio.py
```

跑官网只读验收前，先关闭占用职探连接的其他 MCP 会话，保持 Edge 已配对且岗位搜索页已打开。以下命令沿用官网所选城市和筛选，验证滚动、详情和 30 岗清单准备：

```powershell
.venv\Scripts\python.exe scripts\verify_extension_live.py --keyword 大模型 --review-30
```

连接器 1.3.1 的官网验收中，北京「大模型」搜索返回 60 个不同岗位，读取 30 份详情并生成 30 岗审核清单。发送、额度和防重通过模拟测试。该次官网验收发送 0 次。

[MIT License](LICENSE)
