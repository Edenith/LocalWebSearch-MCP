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
- **垃圾结果识别**：搜索引擎有时只拿查询里的**一个词**去做匹配，把其余限定词全丢掉 —— 比如 `SolidWorks Wine CrossOver Linux 2026 compatibility` 会返回 solidworks.com 官网营销页，`Hunt Showdown 1896 Easy Anti-Cheat Linux Proton support` 会返回"hunt"的词典释义。服务器会把结果集和查询关键词比对（中位数覆盖 + 有多少实词一个都没出现），不合格就重试一次 Bing，再不行换 DuckDuckGo；如果没有任何引擎给出相关结果，返回的结果会带上 `warning` 字段，而不是把垃圾当答案蒙混过去
- **真正的零依赖** —— 连代理也是。SOCKS5 是直接用标准库 socket 实现的（约 35 行），所以 `use_proxy` 不再取决于"恰好跑它的那个解释器里有没有装第三方包"
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
      "command": "C:/Users/你的用户名/AppData/Local/Programs/Python/Python313/python.exe",
      "args": ["C:/Users/你的用户名/.claude/mcp-servers/local_search_server.py"],
      "env": {}
    }
  }
}
```

> **`command` 请写解释器的绝对路径，不要写裸的 `"python"`。** 裸命令靠 `PATH` 解析，而 Windows 上 `PATH` 里的 `python` 未必是你自己装的那个 —— LibreOffice、各种 IDE、其它工具都可能自带 `python.exe` 并把自己加进 `PATH`。**最终哪个解释器胜出，决定了服务器的实际行为，而且是悄无声息的。** 搜索行为反常时，用 `claude mcp list` 配合进程列表查一下。
>
> 这个问题以前比现在严重得多：旧代码的 `use_proxy` 依赖 PySocks，没装就**静默退回直连** —— 于是 `PATH` 的一点意外就能让 `use_proxy=True` 变成空操作，还一声不吭。现在 SOCKS5 已改为纯标准库实现，解释器是谁不再影响代理是否生效；但把路径写死仍然值得做，至少你知道自己跑在哪个 Python 上。

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

`engine` 为 `bing` 或 `duckduckgo`。结果上出现 `warning` 字段有两种情况：所有引擎的结果与查询关键词的重合度都过低（`结果与查询关键词重合度低…`），或者要求走代理但代理不可用、只拿到了直连结果（`代理不可用…`）。

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

先试 Bing（若第一次结果不相关会再重试一次）。DuckDuckGo 的 html 端点只能走代理，需要 v2rayN（或任意 SOCKS5 代理）在跑。

> **兜底不受 `use_proxy` 限制。** 只要 Bing 的结果看起来不相关，就会去试 DuckDuckGo，不管你这次要求走哪条线路 —— 兜底的本职是"换个引擎看看"，让主引擎的线路选择去否决它，结果就是"没有第二意见、直接把垃圾返回"。代理不通时它只是快速失败而已。

> **为什么不用 Google 了？** 备用引擎原本是 Google。但现在的 Google 不再给无 JS 的 HTTP 客户端返回结果，只回一个空壳页（页面标题是 `Google Search`，正文里 0 个结果链接），兜底等于没做。DuckDuckGo 的 `html.duckduckgo.com/html/` 端点仍然返回服务端渲染好的纯 HTML 结果，可以正则直接解析。

> **DuckDuckGo 有限流。** 请求太频繁时它的 html 端点会开始返回 `anomaly / challenge` 拦截页，之后兜底就会一直什么都找不到，直到限流解除。正常交互式使用远达不到这个量；但拿测试脚本反复打会被拦。

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
    #    结果看起来不像在回答这条查询，就重试一次。
    for _ in range(2):
        _collect(_bing(q, num, use_proxy), use_proxy)
        if _have_usable():
            break
    # 2) DuckDuckGo 的 html 端点走代理 —— 只要 Bing 看着不对就试，
    #    不论 use_proxy 传的是什么（兜底自己需要代理）。
    if not _have_usable():
        _collect(_ddg(q, num), True)
    # 3) 优先取"不像垃圾"的那一批；全是垃圾就返回得分最高的，并打上警告
    usable = [a for a in attempts if not _is_junk(a[1], query)]
    return _finish(max(usable or attempts, key=score)[1])
```

### 怎么判断结果是不是垃圾

两条判据取或，因为各自都有对方覆盖不到的盲区：

1. **中位数关键词覆盖低于 `RELEVANCE_FLOOR`（`0.20`）** —— 抓"一条碰巧沾边、其余五条全是填充"的情况。这里如果按"最好那一条"打分就会失效：solidworks.com 的营销页在"最好一条"口径下得 `0.33`，和结果完全正确的 `python asyncio tutorial` **数值一模一样**，任何阈值都分不开；换成中位数后两者是 `0.17` 和 `0.33`。
2. **查询的实词绝大多数在整批结果里一次都没出现**（实词 ≥4 个，其中 ≥3 个任何标题/摘要里都没有）—— 抓"引擎只认了一个词、把其余限定词全丢掉"的情况。这条按**整批结果的并集**算，所以不像中位数那样会随 `num_results` 的奇偶变化。

关键词 = 英文单词 + 中文词块，去掉停用词。**中文刻意不展开 bigram** —— 展开会塞进 `步编`（来自"异步编程"）这种永远匹配不上的碎片，把分母稀释掉。实测（6 条正常 + 4 条已知垃圾查询）：带 bigram 时区间重叠（正常最低 `0.14` 对垃圾最高 `0.17`，无法判定）；去掉后分得开（`0.25` 对 `0.17`）。较长的中文词块在**匹配**时仍有"部分命中"的放宽规则，所以召回不受影响。

实测终态：48 次判定（7 条正常 + 5 条垃圾 × `num_results` 取 4/5/6/8），0 次误判。

### 代理路由

SOCKS5 直接基于标准库 socket 实现，不再依赖 PySocks：

```python
def _socks5_connect(proxy_host, proxy_port, dest_host, dest_port, timeout):
    s = socket.create_connection((proxy_host, proxy_port), timeout)
    s.sendall(b"\x05\x01\x00")                 # 版本5，无认证
    if _recv_exact(s, 2) != b"\x05\x00":
        raise OSError("SOCKS5 代理未接受无认证方式")
    s.sendall(b"\x05\x01\x00" + addr_bytes + struct.pack("!H", dest_port))
    head = _recv_exact(s, 4)                   # CONNECT 应答
    if head[1] != 0:
        raise OSError("SOCKS5 代理拒绝连接")
    return s
```

每次请求各自构造 opener，并挂上走 SOCKS 的连接类，`use_proxy` 因此是逐次生效的：

```python
def _build_opener(use_proxy):
    if use_proxy:
        http_cls, https_cls = _socks_conn_classes(PROXY_HOST, PROXY_PORT)
        return urllib.request.build_opener(
            _SocksHTTPHandler(http_cls), _SocksHTTPSHandler(https_cls))
    return urllib.request.build_opener()
```

这里避开了三个坑，都是本项目真踩过的：

1. **不改全局。** 原实现是 `socket.socket = socks.socksocket` —— 全局且不可逆：第一次 `use_proxy=True` 之后，同进程里所有请求（包括 `use_proxy=False` 的）都会走代理；而抓正文是并发跑的，改全局本身就是竞态。
2. **换"建连"，不换 `connect()`。** `HTTPSConnection.connect()` 是在建连之后才套 TLS 的，直接重写 `connect()` 会把 TLS 那步整个跳过 —— 结果是拿明文 HTTP 去连 443 端口。正确做法是替换 `self._create_connection`（`http.client` 把它存成**实例属性**，所以必须在 `super().__init__()` 之后再换）。
3. **不依赖"恰好装了什么包"。** 早先的版本是"能 import 到 PySocks 就用，否则**静默退回直连**" —— 于是 `use_proxy=True` 会一声不响地变成空操作。在排查这个问题的那台机器上就是这么中的招：MCP 配置里写的是裸 `"command": "python"`，`PATH` 把它解析成了 LibreOffice 自带的 Python，那个解释器没装 PySocks，于是每一次"走代理"的搜索其实都在打 Bing 国内版。改成标准库实现 SOCKS5 之后这个失效模式就不存在了；另外，代理连不上时会退回直连并在结果上带明确 `warning`，而不是干脆返回空。

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
- **`use_proxy=True` 和 `use_proxy=False` 的结果一模一样、字节级相同？** 那是 v1.2.0 及更早版本 + 没装 PySocks 的表现 —— 代理被静默跳过了。v1.2.1 已改为纯标准库 SOCKS5
- **报 `surrogates not allowed`？** 那是 cp936/UTF-8 stdio 的 bug，v1.2.0 已修。还会看到它就说明你在用旧文件
- **某些查询结果怪异？** 通常是 Bing 自己的问题 —— 它只匹配了查询里的一个词，把其余限定词丢了。服务器现在会识别、重试并切换到 DuckDuckGo；实在没有相关的，结果里会带 `warning` 字段
- **DuckDuckGo 兜底突然什么都找不到了？** 它的 html 端点有限流，请求太频繁会返回 `anomaly / challenge` 拦截页。正常使用没问题，脚本反复打会被拦
- **结果全是乱码 / `�`？** 同样是 v1.2.0 修掉的 —— 响应字节现在按声明的 charset 解码，不再一律当 UTF-8

## 许可

MIT License — 自由使用、修改、分享。
