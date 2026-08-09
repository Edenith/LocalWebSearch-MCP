# Local Web Search MCP Server ｜ 本地 Web 搜索 MCP 服务器

> 给国内 Claude Code 的零依赖本地搜索 MCP —— 一个纯 Python 标准库实现的本地 MCP 搜索服务器，让 Claude Code（或其他 MCP 客户端）拥有真实的联网搜索能力。与模型后端无关，用**本机网络**搜索网页：**Bing 直连优先，境外引擎走 SOCKS5 代理兜底**。

## 为什么要做这个

Claude Code 的**内置 WebSearch / WebFetch 工具依赖 Anthropic 官方 API**。如果你的 Claude Code 后端是第三方模型（DeepSeek、Qwen 等，经 CC Switch 等本地代理接入），内置搜索工具会失效。

这套方案用一个 **MCP over stdio** 的本地 Python 进程，把「本机真实网络」变成模型可调用的搜索工具：

```
Claude Code ──MCP stdio──▶ local_search_server.py（本机 Python 进程）
                                  │
                                  ├─ Bing 直连（国内可达，主引擎）
                                  ├─ DuckDuckGo 走 SOCKS5 代理（兜底）
                                  └─ Google 走 SOCKS5 代理（候选）
```

## 特性

- **纯 Python 标准库**（json + urllib + threading + html.parser），零依赖、启动快（~50ms）
- 完整实现 MCP over stdio 协议（JSON-RPC 2.0）：`initialize` / `tools/list` / `tools/call`
- 提供两个工具：
  - `web_search(query, num_results=8, use_proxy=True)` → 搜索网页
  - `web_fetch(url, timeout_s=20, use_proxy=True)` → 抓取网页正文
- **Bing 直连优先**（国内可达、稳定、无需代理），失败自动切 DuckDuckGo（走代理）
- 可配置 SOCKS5 代理端口（默认 `127.0.0.1:1080`，兼容 v2rayN）

## 安装

### 1. 前置要求

- Python 3.8+（标准库即可，无需 pip 安装任何包）
- （可选）v2rayN 或其他 SOCKS5 代理，端口默认 `1080` —— 用于访问 Google / DuckDuckGo

### 2. 放置文件

把 `local_search_server.py` 放到任意位置，例如：

```
C:\Users\你的用户名\.claude\mcp-servers\local_search_server.py
```

### 3. 注册到 Claude Code

编辑 `~/.claude.json`（Windows 路径 `C:\Users\你的用户名\.claude.json`），在 `mcpServers` 节点添加：

```json
{
  "mcpServers": {
    "web-search": {
      "type": "stdio",
      "command": "python",
      "args": ["C:/Users/你的用户名/.claude/mcp-servers/local_search_server.py"],
      "env": {}
    }
  }
}
```

### 4. 验证连接

```bash
claude mcp list
```

看到：

```
web-search: python .../local_search_server.py - ✔ Connected
```

即连接成功。

### 5. 在 Claude Code 中使用

按你希望的方式，让 Claude 调用这两个 MCP 工具（搜网页 / 抓网页）：

```
mcp__web-search__web_search(query="...", num_results=8, use_proxy=true)
mcp__web-search__web_fetch(url="https://...", use_proxy=true)
```

> 提示：也可以在你的全局 CLAUDE.md 里写一条规则，提醒自己「搜索用 MCP 工具、不用内置 WebSearch」，这样每次会话都自动遵守。

## 参数说明

### web_search

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `query` | string | 必填 | 搜索关键词 |
| `num_results` | int | 8 | 返回条数（1~15） |
| `use_proxy` | bool | true | 是否走 SOCKS5 代理访问 Google/DDG（Bing 总是尝试直连） |

返回：`[{title, url, snippet, engine}]`

### web_fetch

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `url` | string | 必填 | 要抓取的网址 |
| `timeout_s` | number | 20 | 超时（秒） |
| `use_proxy` | bool | true | 是否走代理（境外站点建议 true，国内站点可 false） |

返回：`# 标题` + URL + 正文前 6000 字符

## 网络策略

| 引擎 | 方式 | 场景 |
|---|---|---|
| Bing | 直连 | 国内主引擎，无需代理 |
| DuckDuckGo | SOCKS5 代理 | Bing 失败时兜底 |
| Google | SOCKS5 代理 | 候选引擎 |

代理地址默认 `socks5://127.0.0.1:1080`，可通过环境变量 `LOCAL_SEARCH_SOCKS_PORT` 修改端口。

## 配置代理端口

```bash
# Windows
set LOCAL_SEARCH_SOCKS_PORT=1080

# Linux / macOS
export LOCAL_SEARCH_SOCKS_PORT=1080
```

## 工作原理（核心代码）

### MCP stdio 协议

MCP（Model Context Protocol）客户端通过 stdin 发 JSON-RPC 2.0 消息，服务器通过 stdout 回响应，用换行分隔：

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}
{"jsonrpc":"2.0","id":1,"result":{"capabilities":{"tools":{}},"serverInfo":{"name":"web-search"}}}
```

### 搜索流程

```python
def web_search(query, num_results, use_proxy):
    # 1) Bing 直连（主引擎）
    html = _http_get(f"https://www.bing.com/search?q={q}", False, 15)
    results = _parse_bing(html, num)   # 正则提取 b_algo 结果块
    if results:
        return results
    # 2) DuckDuckGo 走代理（兜底）
    html = _http_get(f"https://html.duckduckgo.com/html/?q={q}", True, 15)
    results = _parse_ddg(html, num)
    return results
```

### 代理切换

`use_proxy=True` 时优先尝试 PySocks，没装则直连兜底：

```python
try:
    import socks
    socks.set_default_proxy(socks.SOCKS5, host, port)
    socket.socket = socks.socksocket
except ImportError:
    pass  # 无 PySocks 则直连
```

### HTML 正文提取

用标准库 `html.parser.HTMLParser` 实现轻量正文提取器，跳过 `script/style/noscript/svg`，在段落标签处加换行：

```python
class _TextExtractor(HTMLParser):
    skip_tags = {"script", "style", "noscript", "svg", "template"}
    # ... 见源码
```

## 许可

MIT License — 自由使用、修改、分享。

---

*Made with ♥ for the Claude Code community*
