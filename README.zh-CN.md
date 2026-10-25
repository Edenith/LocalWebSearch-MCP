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
- **三级结果分级，并且失败关闭**：怀疑被自动化访问的搜索引擎不一定会弹验证码 —— 它们会返回 `HTTP 200` + 一个看起来正常、内容却**毫不相干**的页面。本机实测：`"ED047TC2"` 返回 BOSS直聘招聘页，`asdfqwer1234zzz` 返回 Poki 小游戏，长尾查询 `RK3566 EBC_SDDO0 GPIO4 pin mux datasheet` 只匹配了第一个词。每批结果会被分成 **good**（关键词大部分命中）、**partial**（命中主词但限定词没覆盖，返回但明确标注）、**junk**（查询里任何一个词都没出现在任何结果中）。**junk 一律不返回** —— 直接报错，因为不读 `warning` 的调用方会把填充内容当成证据
- **三个引擎，带节流与冷却**：Bing → 搜狗 → DuckDuckGo。返回反爬页（`202`/`403`/`429`、`anomaly`、`captcha`、`安全验证`、`antispider` …）的引擎会被冷却 10 分钟，而不是继续敲；同引擎请求之间有最小间隔，结果缓存 6 小时
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

返回：`[{title, url, snippet, content, engine, relevance}]`

`engine` 为 `bing` / `sogou` / `duckduckgo`。`relevance` 为 `good` 或 `partial`；`partial` 的结果会额外带上 `warning`，说明查询的限定条件没被覆盖。当所有引擎返回的都是 junk 时，调用会**直接报错**（错误信息里带各引擎状态），而不是返回填充内容 —— 此时请稍后重试，或改用 `web_fetch` 直接抓已知页面。另外，要求走代理但代理不可用、只拿到直连结果时，也会带 `warning`。

`engine` 为 `bing` 或 `duckduckgo`。结果上出现 `warning` 字段有两种情况：所有引擎的结果与查询关键词的重合度都过低（`结果与查询关键词重合度低…`），或者要求走代理但代理不可用、只拿到了直连结果（`代理不可用…`）。

### web_fetch

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `url` | string | 必填 | 要抓取的网址 |
| `timeout_s` | number | 20 | 超时（秒） |
| `use_proxy` | bool | true | 是否走代理（境外站点建议 true，国内站点可 false） |

返回：`# 标题` + URL + 正文前 6000 字符

## 网络策略

| 顺序 | 引擎 | 方式 | 场景 |
|---|---|---|---|
| 1 | Bing | 直连（`use_proxy=False`）或 SOCKS5（`use_proxy=True`） | 主引擎 |
| 2 | 搜狗 | 直连 | 国内可达的备用，不需要代理。中文强、英文弱 |
| 3 | DuckDuckGo（html 端点） | SOCKS5 代理 | 备用，需要代理 |

只有前一个引擎没给出 `good` 结果时，才会试下一个。DuckDuckGo 不受 `use_proxy` 限制 —— 兜底的本职是"换个引擎看看"，让主引擎的线路选择去否决它，结果就是"压根没有第二意见"。

> **为什么不用 Google 了？** 备用引擎原本是 Google。但现在的 Google 不再给无 JS 的 HTTP 客户端返回结果，只回一个空壳页（页面标题是 `Google Search`，正文里 0 个结果链接），兜底等于没做。DuckDuckGo 的 `html.duckduckgo.com/html/` 端点仍然返回服务端渲染好的纯 HTML 结果，可以正则直接解析。

> **Bing 的 RSS 端点不是解药。** `&format=rss` 拿到的是同一套排序 —— 实测 `"ED047TC2"` 在 RSS 里返回的是 6G 通信新闻。问题出在排序，不在 HTML 结构。

### 限流、冷却与缓存

无 Key 抓搜索引擎，本质上是在跟反爬启发式对抗，所以服务器会自己控速：

| 参数 | 默认值 | 环境变量 | 含义 |
|---|---|---|---|
| 同引擎最小间隔 | 2 秒 | `LOCAL_SEARCH_ENGINE_INTERVAL` | 两次请求同一引擎之间的间隔 |
| 被拦后冷却 | 600 秒 | `LOCAL_SEARCH_ENGINE_COOLDOWN` | 被判定反爬后停用多久 |
| 结果缓存 | 6 小时 | `LOCAL_SEARCH_CACHE_TTL` | 重复查询直接走内存 |
| 垃圾结果负缓存 | 120 秒 | `LOCAL_SEARCH_CACHE_NEG_TTL` | 短时缓存，避免反复去打已降级的引擎 |
| 日志文件 | 脚本同目录的 `web-search.log` | `LOCAL_SEARCH_LOG` | 每次引擎尝试一行；超过 2MB 轮转。设为空串可关闭 |

判定为反爬的条件：状态码 `202`/`403`/`429`，或正文里出现 `anomaly`、`captcha`、`challenge`、`安全验证`、`验证码`、`antispider`、`too many requests`、`are you a robot`。被拦的引擎会进入冷却，期间直接跳过。

本机实测：DuckDuckGo `html` → `202` + anomaly；百度 → `200` + `百度安全验证`；Mojeek → `403`；Yandex → 验证码；Brave → `429`；搜狗 → 可用，但连续两三条就触发反爬。节流的存在就是为了这个。

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
def web_search(query, num_results, use_proxy, ...):
    # 按顺序试引擎；一旦某个给出 good 结果就停止
    _try(bing_key, lambda: _bing_fetch(q, num, use_proxy), _parse_bing, use_proxy)
    if _best(LEVEL_GOOD): return _finish(_best(LEVEL_GOOD))

    _try("sogou", lambda: _sogou_fetch(q, num), _parse_sogou, False)
    if _best(LEVEL_GOOD): return _finish(_best(LEVEL_GOOD))

    _try("ddg", lambda: _ddg_fetch(q, num), _parse_ddg, True)

    if _best(LEVEL_GOOD): return _finish(_best(LEVEL_GOOD))
    if _best(LEVEL_PARTIAL):                       # 命中主词、限定词没覆盖
        out = _finish(_best(LEVEL_PARTIAL))
        for r in out: r["warning"] = WARNING_PARTIAL
        return out
    raise SearchUnavailable(engine_status)         # 绝不返回垃圾
```

### 结果分级：good / partial / junk

分三级，因为"非黑即白"没法同时做到安全和有用：

| 等级 | 判据 | 处理 |
|---|---|---|
| `good` | 通过下面两条垃圾判据 | 正常返回 |
| `partial` | 查询里有关键词出现在某条结果里，但仍未通过垃圾判据 | 返回，带 `relevance: "partial"` 和 `warning` |
| `junk` | **查询里任何一个关键词都没出现在任何结果中** | 绝不返回 —— 直接报错 |

`junk` 正是被软封的引擎喂给我们的东西：`"ED047TC2"` → BOSS直聘招聘页，`asdfqwer1234zzz` → Poki 小游戏。`partial` 则是"只命中了主词"：`RK3566 EBC_SDDO0 GPIO4 pin mux datasheet` → 一批 RK3566 数据手册页。这对长尾调研确实有用，但它没有回答 pin mux 那个问题，**所以明确标注，而不是偷偷把查询简化成另一个问题**。

两条垃圾判据取或，因为各自都有对方覆盖不到的盲区：

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
- **`web_search` 报错而不是返回结果？** 这是 v1.3.0+ 的有意行为：所有引擎返回的都是与查询无关的填充内容，**返回填充内容比失败更糟**。错误信息里列出了每个引擎的状态。稍后重试、简化查询，或用 `web_fetch` 直接抓已知页面 —— `web_fetch` 不受影响
- **引擎全都显示"冷却中"？** 它们被识别为反爬页（`202`/`403`/`429`，或 anomaly/captcha/安全验证 等页面）。冷却默认 10 分钟，可用 `LOCAL_SEARCH_ENGINE_COOLDOWN` 调；想搜得更快可以调小 `LOCAL_SEARCH_ENGINE_INTERVAL`，代价是更容易被拦
- **`use_proxy=True` 和 `use_proxy=False` 的结果一模一样、字节级相同？** 那是 v1.2.0 及更早版本 + 没装 PySocks 的表现 —— 代理被静默跳过了。v1.2.1 已改为纯标准库 SOCKS5
- **报 `surrogates not allowed`？** 那是 cp936/UTF-8 stdio 的 bug，v1.2.0 已修。还会看到它就说明你在用旧文件
- **结果全是乱码 / `�`？** 同样是 v1.2.0 修掉的 —— 响应字节现在按声明的 charset 解码，不再一律当 UTF-8
- **想查引擎到底做了什么？** 看脚本同目录的 `web-search.log`（每次尝试一行：引擎、HTTP 状态、正文大小、解析条数、等级、相关性）。用 `LOCAL_SEARCH_LOG` 改位置，设为空串可关闭

## 许可

MIT License — 自由使用、修改、分享。
