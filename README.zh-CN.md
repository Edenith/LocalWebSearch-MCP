# Local Web Search MCP Server ｜ 本地 Web 搜索 MCP 服务器

> 给国内 Claude Code 的零依赖本地搜索 MCP —— 一个纯 Python 标准库实现的本地 MCP 搜索服务器，让 Claude Code（或其他 MCP 客户端）拥有真实的联网搜索能力。与模型后端无关，用**本机网络**搜索网页：**Bing 直连**（国内）或走 **SOCKS5 代理**（外网），并自动抓取正文片段供 AI 先粗略浏览。

🌐 **语言：** [English](README.md) | [简体中文](README.zh-CN.md)

## 为什么要做这个

Claude Code 的**内置 WebSearch / WebFetch 工具依赖 Anthropic 官方 API**。如果你的 Claude Code 后端是第三方模型（DeepSeek、Qwen 等，经 CC Switch 等本地代理接入），内置搜索工具会失效。

这套方案用一个 **MCP over stdio** 的本地 Python 进程，把「本机真实网络」变成模型可调用的搜索工具：

```
Claude Code ──MCP stdio──▶ local_search_server.py（本机 Python 进程）
                                  │
                                  ├─ Bing 直连或走 SOCKS5 代理（主引擎）
                                  └─ DuckDuckGo html 端点走 SOCKS5 代理（备用）
```

## 特性

- **纯 Python 标准库**（json + urllib + threading + html.parser），零依赖、启动快（~50ms）
- 完整实现 MCP over stdio 协议（JSON-RPC 2.0）：`initialize` / `tools/list` / `tools/call`
- 提供两个工具：
  - `web_search(query, num_results=8, use_proxy=True, fetch_content=True, fetch_top=3, content_chars=1500)` → 搜索网页，并自动抓取前几条结果的正文
  - `web_fetch(url, timeout_s=20, use_proxy=True)` → 抓取网页正文
- **自动抓正文**：搜索后并发抓取前 N 个结果页面，把正文片段附加到每条结果的 `content` 字段 —— AI 先粗略看内容，再决定抓哪个详情页
- **双搜索模式**：`use_proxy=False` → Bing 直连（国内视角）；`use_proxy=True` → Bing 走 SOCKS5 代理（外网视角，附 DuckDuckGo 备用）—— 用于搜索境外内容、访问国内被墙的站点
- **垃圾结果识别**：部分查询（尤其中文）会让 Bing 返回与查询毫不相干的结果，而且每次请求给的还不一样。当结果与查询关键词几乎不重合时，服务器会重试一次 Bing，再不行就换 DuckDuckGo；如果始终没有相关的，返回的结果会带上 `warning` 字段，而不是把垃圾当答案蒙混过去
- **Windows 下 UTF-8 正确**：MCP stdio 规定 UTF-8，但中文 Windows 上 Python 的 stdio 默认是 cp936/GBK —— 会让每个中文查询报废、每个中文结果变乱码。服务器直接读写 `sys.stdin.buffer` / `sys.stdout.buffer`，完全绕开 locale 编码
- **按声明识别网页编码**：响应字节按 UTF-8 → HTTP/meta 声明的 `charset` → gb18030 依次尝试解码，GBK/GB2312 中文页不会再满屏 `�`；`gzip`/`deflate` 响应自动解压
- 可配置 SOCKS5 代理端口（默认 `127.0.0.1:1080`，兼容 v2rayN）
- **代理不污染全局**：每次请求各自构造 opener，不改 `socket` 模块，并发抓正文也不会互相串

## 安装

### 1. 前置要求

- Python 3.8+（标准库即可，无需 pip 安装任何包）
- （可选）v2rayN 或其他 SOCKS5 代理，端口默认 `1080` —— 仅在**外网搜索**（`use_proxy=True`）或抓取国内被墙站点时需要。不装也能用国内 Bing 直连搜索

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

让 Claude 调用这两个 MCP 工具（搜网页 / 抓网页）：

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
| `use_proxy` | bool | true | `false`=Bing 直连（国内）；`true`=Bing 走代理（外网）+ DuckDuckGo 备用 |
| `fetch_content` | bool | true | 是否自动抓取前几条结果的正文到 `content` 字段 |
| `fetch_top` | int | 3 | 抓取前几个结果的正文 |
| `content_chars` | int | 1500 | 每条结果保留的正文最大字符数 |

返回：`[{title, url, snippet, content, engine}]`

`engine` 为 `bing` 或 `duckduckgo`。若所有引擎的结果与查询关键词的重合度都过低，每条结果会额外带上 `warning` 字段，提示内容可能不相关。

### web_fetch

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `url` | string | 必填 | 要抓取的网址 |
| `timeout_s` | number | 20 | 超时（秒） |
| `use_proxy` | bool | true | 是否走代理（境外站点建议 true，国内站点可 false） |

返回：`# 标题` + URL + 正文前 6000 字符

## 网络策略

| 模式 | 引擎 | 方式 | 场景 |
|---|---|---|---|
| `use_proxy=False` | Bing | 直连 | 国内搜索，无需代理 |
| `use_proxy=True` | Bing | SOCKS5 代理 | 外网搜索 —— 境外内容、国内被墙的站点 |
| `use_proxy=True` | DuckDuckGo（html 端点） | SOCKS5 代理 | 备用：Bing 结果与查询对不上时启用 |

先试 Bing（若第一次结果不相关会再重试一次）。DuckDuckGo 只在 `use_proxy=True` 下启用，因为 `html.duckduckgo.com` 国内直连不通。

> **为什么不用 Google 了？** 备用引擎原本是 Google。但现在的 Google 不再给无 JS 的 HTTP 客户端返回结果，只回一个空壳页（页面标题是 `Google Search`，正文里 0 个结果链接），兜底等于没做。DuckDuckGo 的 `html.duckduckgo.com/html/` 端点仍然返回服务端渲染好的纯 HTML 结果，可以正则直接解析。

代理地址默认 `socks5://127.0.0.1:1080`，可通过环境变量 `LOCAL_SEARCH_SOCKS_PORT` 修改端口：

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
def web_search(query, num_results, use_proxy, fetch_content=True, fetch_top=3, content_chars=1500):
    # 1) Bing —— use_proxy=True 走代理（外网视角），False 直连（国内视角）。
    #    Bing 有时会返回与查询无关的结果（而且每次请求给的还不一样），
    #    所以结果对不上就重试一次。
    for _ in range(2):
        results = _bing(q, num, use_proxy)
        if _relevance(results, query) >= RELEVANCE_FLOOR:
            return _finish(results)
    # 2) DuckDuckGo 的 html 端点走代理（备用，仅外网模式启用）
    if use_proxy:
        results = _ddg(q, num)
        if _relevance(results, query) >= RELEVANCE_FLOOR:
            return _finish(results)
    # 3) 都不相关：把手上最好的一份返回，并打上 warning，而不是假装它是答案
    return _finish(fallback_with_warning)
```

`_relevance()` 从查询里抽关键词（英文单词 + 中文词块与 bigram，去掉停用词），看最匹配的那条结果在标题/摘要里命中了多大比例。`RELEVANCE_FLOOR` 特意取很低（`0.15`）——中文查询被英文页面回答时命中率天然就低，这个指标只为拦住"一个关键词都没命中"的纯垃圾。

### 代理路由

每次请求各自构造 opener，并挂上走 SOCKS 的连接类，`use_proxy` 因此是逐次生效的：

```python
class _SocksHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(self._conn_cls, req)

def _build_opener(use_proxy):
    if use_proxy and socks is not None:
        http_cls, https_cls = _socks_conn_classes(PROXY_HOST, PROXY_PORT)
        return urllib.request.build_opener(
            _SocksHTTPHandler(http_cls), _SocksHTTPSHandler(https_cls))
    return urllib.request.build_opener()
```

这里避开了两个坑，都是本项目真踩过的：

1. **不改全局。** 原实现是 `socket.socket = socks.socksocket` —— 全局且不可逆：第一次 `use_proxy=True` 之后，同进程里所有请求（包括 `use_proxy=False` 的）都会走代理；而抓正文是并发跑的，改全局本身就是竞态。
2. **换"建连"，不换 `connect()`。** `HTTPSConnection.connect()` 是在建连之后才套 TLS 的，直接重写 `connect()` 会把 TLS 那步整个跳过 —— 结果是拿明文 HTTP 去连 443 端口。正确做法是替换 `self._create_connection`（`http.client` 把它存成**实例属性**，所以必须在 `super().__init__()` 之后再换）。

### UTF-8 stdio

MCP stdio 规定走 UTF-8，但中文 Windows 上 Python 默认把 `sys.stdin`/`sys.stdout` 设成 cp936 + `surrogateescape`。这个 bug 的表现很迷惑 —— **纯英文查询完全正常**，所以看起来像是偶发：

```
>>> sys.stdin.encoding, sys.stdin.errors
('gbk', 'surrogateescape')
>>> 'Python 异步编程 asyncio 教程'.encode('utf-8').decode('gbk', 'surrogateescape')
'Python 寮傛\udcadョ紪绋\udc8b asyncio ...'
>>> urllib.parse.quote(上面那个字符串)
UnicodeEncodeError: 'utf-8' codec can't encode character '\udcad' in position 9: surrogates not allowed
```

所以修法是彻底不碰文本层：

```python
for raw_line in sys.stdin.buffer:                       # 读
    line = raw_line.decode("utf-8", "replace").strip()

sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))   # 写
sys.stdout.buffer.flush()
```

上一版针对这个 bug 只是清理了**输出**里的孤立代理字符，但异常是在**输入**路径上抛的（那时还没有任何结果），所以完全没治到。

### HTML 正文提取

用标准库 `html.parser.HTMLParser` 实现轻量正文提取器，跳过 `script/style/noscript/svg`，在段落标签处加换行：

```python
class _TextExtractor(HTMLParser):
    skip_tags = {"script", "style", "noscript", "svg", "template"}
    # ... 见源码
```

## 常见问题

- **搜索没结果？** 先确认网络：国内搜索需能连 Bing；外网搜索（`use_proxy=True`）需开 v2rayN
- **报 PySocks 缺失？** 无妨，Bing 直连仍可用，外网模式退回直连
- **报 `surrogates not allowed`？** 那是 cp936/UTF-8 stdio 的 bug，v1.2.0 已修。还会看到它就说明你在用旧文件
- **某些查询结果怪异？** 那是 Bing 自己的问题 —— 对部分查询（尤其中文）它会返回毫不相干的填充结果，而且每次请求都不一样。服务器现在会识别、重试并切换到 DuckDuckGo；实在没有相关的，结果里会带 `warning` 字段
- **结果全是乱码 / `�`？** 同样是 v1.2.0 修掉的 —— 响应字节现在按声明的 charset 解码，不再一律当 UTF-8

## 许可

MIT License — 自由使用、修改、分享。
