# Local Web Search MCP Server

> A zero-dependency, local MCP search server for Claude Code — implemented in pure Python standard library. Gives Claude Code (or any MCP client) real web-search capability using your **local network**, independent of your model backend. **Bing direct-first** (domestic) or via **SOCKS5 proxy** (overseas), with automatic page-content fetching so the AI can skim before deciding what to read.

🌐 **Languages:** [English](README.md) | [简体中文](README.zh-CN.md)

## Why this exists

Claude Code's **built-in WebSearch / WebFetch tools rely on the Anthropic official API**. If your Claude Code backend is a third-party model (DeepSeek, Qwen, etc., connected via a local proxy like CC Switch), the built-in search tools stop working.

This project turns your **local, real network** into a model-callable search tool via a **MCP-over-stdio** Python process:

```
Claude Code ──MCP stdio──▶ local_search_server.py (local Python process)
                                  │
                                  ├─ Bing direct or via SOCKS5 proxy (primary)
                                  └─ DuckDuckGo HTML endpoint via SOCKS5 proxy (backup)
```

## Features

- **Pure Python standard library** (json + urllib + threading + html.parser) — zero dependencies, fast startup (~50ms)
- Full **MCP over stdio** protocol (JSON-RPC 2.0): `initialize` / `tools/list` / `tools/call`
- Two tools:
  - `web_search(query, num_results=8, use_proxy=True, fetch_content=True, fetch_top=3, content_chars=1500)` → search the web, auto-fetch snippets of the top results
  - `web_fetch(url, timeout_s=20, use_proxy=True)` → fetch readable page text
- **Auto content fetch**: after a search, the top N result pages are fetched in parallel and their text is attached to each result as `content` — so the AI can skim real content before deciding which page to read in full
- **Dual search modes**: `use_proxy=False` → Bing direct (China-reachable, domestic view); `use_proxy=True` → Bing via SOCKS5 proxy (overseas view) with DuckDuckGo backup — for searching foreign content / accessing sites blocked in CN
- **Junk-result detection**: some queries (especially Chinese ones) make Bing return results that have nothing to do with the query, and a *different* random set on every request. When the top results share almost no keywords with the query, the server retries Bing once and then switches to DuckDuckGo. If nothing relevant is found, the returned results carry a `warning` field instead of silently passing off junk as answers
- **UTF-8 correct on Windows**: MCP stdio is UTF-8, but on a Chinese Windows Python's stdio defaults to cp936/GBK — which garbles every Chinese query and turns every Chinese result into mojibake. The server talks to `sys.stdin.buffer` / `sys.stdout.buffer` directly, bypassing the locale encoding entirely
- **Charset-aware decoding**: response bytes are decoded by sniffing UTF-8 → HTTP/meta `charset` → gb18030, so GBK/GB2312 Chinese pages don't come back as `�`. `gzip`/`deflate` responses are decompressed transparently
- Configurable SOCKS5 proxy port (default `127.0.0.1:1080`, v2rayN-compatible)
- **Race-free proxying**: each request builds its own opener; the proxy is never applied by mutating the global `socket` module

## Install

### 1. Requirements

- Python 3.8+ (standard library only, no pip packages needed)
- *(Optional)* v2rayN or any SOCKS5 proxy, port `1080` — needed only for overseas search (`use_proxy=True`) or fetching CN-blocked sites. Works without it for domestic Bing search.

### 2. Place the file

Put `local_search_server.py` anywhere, e.g.:

```
C:\Users\<your-username>\.claude\mcp-servers\local_search_server.py
```

### 3. Register in Claude Code

Edit `~/.claude.json` (Windows: `C:\Users\<your-username>\.claude.json`), add under `mcpServers`:

```json
{
  "mcpServers": {
    "web-search": {
      "type": "stdio",
      "command": "python",
      "args": ["C:/Users/<your-username>/.claude/mcp-servers/local_search_server.py"],
      "env": {}
    }
  }
}
```

### 4. Verify connection

```bash
claude mcp list
```

You should see:

```
web-search: python .../local_search_server.py - ✔ Connected
```

### 5. Use it in Claude Code

Ask Claude to call these two tools:

```
mcp__web-search__web_search(query="...", num_results=8, use_proxy=true)
mcp__web-search__web_fetch(url="https://...", use_proxy=true)
```

> Tip: add a rule to your global `CLAUDE.md` reminding yourself to use these MCP tools instead of the built-in WebSearch.

## Parameters

### web_search

| Param | Type | Default | Description |
|---|---|---|---|
| `query` | string | required | Search keywords |
| `num_results` | int | 8 | Results to return (1–15) |
| `use_proxy` | bool | true | `false`=Bing direct (domestic); `true`=Bing via SOCKS5 (overseas) + DuckDuckGo backup |
| `fetch_content` | bool | true | Auto-fetch text of the top results into a `content` field |
| `fetch_top` | int | 3 | How many of the top results to fetch content for |
| `content_chars` | int | 1500 | Max chars of page text to keep per result |

Returns: `[{title, url, snippet, content, engine}]`

`engine` is `bing` or `duckduckgo`. If every engine's results scored too low against the query keywords, each result also gets a `warning` field saying they may be irrelevant fillers.

### web_fetch

| Param | Type | Default | Description |
|---|---|---|---|
| `url` | string | required | URL to fetch |
| `timeout_s` | number | 20 | Timeout (seconds) |
| `use_proxy` | bool | true | Use proxy (true for overseas sites, false for CN sites) |

Returns: `# Title` + URL + first 6000 chars of page text

## Network strategy

| Mode | Engine | Method | Scenario |
|---|---|---|---|
| `use_proxy=False` | Bing | direct | Domestic CN search, no proxy needed |
| `use_proxy=True` | Bing | SOCKS5 proxy | Overseas search — foreign content, sites blocked in CN |
| `use_proxy=True` | DuckDuckGo (html endpoint) | SOCKS5 proxy | Backup, used when Bing's results don't match the query |

Bing is tried first (twice if the first attempt looks irrelevant). DuckDuckGo only runs in `use_proxy=True` mode, because `html.duckduckgo.com` is not reachable from CN directly.

> **Why not Google?** The backup engine used to be Google. Modern Google no longer serves results to a plain HTTP client — it returns a JS shell page (title `Google Search`, zero result links), so the backup silently did nothing. DuckDuckGo's `html.duckduckgo.com/html/` endpoint still returns plain server-rendered results and is parseable.

Default proxy `socks5://127.0.0.1:1080`. Change the port via env var `LOCAL_SEARCH_SOCKS_PORT`:

```bash
# Windows
set LOCAL_SEARCH_SOCKS_PORT=1080

# Linux / macOS
export LOCAL_SEARCH_SOCKS_PORT=1080
```

## How it works

### MCP stdio protocol

The MCP client sends JSON-RPC 2.0 messages on stdin; the server replies on stdout, newline-delimited:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}
{"jsonrpc":"2.0","id":1,"result":{"capabilities":{"tools":{}},"serverInfo":{"name":"web-search"}}}
```

### Search flow

```python
def web_search(query, num_results, use_proxy, fetch_content=True, fetch_top=3, content_chars=1500):
    # 1) Bing — via proxy if use_proxy=True (overseas view), else direct (domestic).
    #    Bing sometimes answers with results unrelated to the query (a fresh random
    #    set each time), so retry once if the results don't match the query.
    for _ in range(2):
        results = _bing(q, num, use_proxy)
        if _relevance(results, query) >= RELEVANCE_FLOOR:
            return _finish(results)
    # 2) DuckDuckGo html endpoint via proxy (backup, overseas mode only)
    if use_proxy:
        results = _ddg(q, num)
        if _relevance(results, query) >= RELEVANCE_FLOOR:
            return _finish(results)
    # 3) Nothing matched — return the best we saw, flagged, rather than pretend
    return _finish(fallback_with_warning)
```

`_relevance()` extracts keywords from the query (latin words + CJK runs and bigrams, minus stopwords) and scores the best-matching result by how many of them appear in its title/snippet. `RELEVANCE_FLOOR` is deliberately low (`0.15`) — a CN query answered by EN pages legitimately scores badly, and the goal is only to catch results that match *nothing*.

### Proxy routing

Each request builds its own opener with SOCKS-aware connection classes, so `use_proxy` is honoured per call:

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

Two traps this avoids, both of which were real bugs here:

1. **No global monkey-patching.** The original code did `socket.socket = socks.socksocket`, which is global *and irreversible* — after the first `use_proxy=True` call, every later request in the process went through the proxy, including `use_proxy=False` ones. It was also a race, since content fetching is concurrent.
2. **Override socket creation, not `connect()`.** `HTTPSConnection.connect()` wraps the socket in TLS *after* connecting. Replacing `connect()` wholesale silently skips that, so requests go out as plaintext HTTP — to port 443. The handler instead swaps `self._create_connection` (which `http.client` stores as an *instance* attribute in `__init__`, so it must be replaced after `super().__init__()`).

### UTF-8 stdio

MCP stdio is defined as UTF-8, but Python on a Chinese Windows machine defaults `sys.stdin`/`sys.stdout` to cp936 with `surrogateescape`. The failure mode is nasty and query-dependent — pure-ASCII queries work fine, so it looks intermittent:

```
>>> sys.stdin.encoding, sys.stdin.errors
('gbk', 'surrogateescape')
>>> 'Python 异步编程 asyncio 教程'.encode('utf-8').decode('gbk', 'surrogateescape')
'Python 寮傛\udcadョ紪绋\udc8b asyncio ...'
>>> urllib.parse.quote(that)
UnicodeEncodeError: 'utf-8' codec can't encode character '\udcad' in position 9: surrogates not allowed
```

So the fix is to never touch the text layer:

```python
for raw_line in sys.stdin.buffer:                       # read
    line = raw_line.decode("utf-8", "replace").strip()

sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))   # write
sys.stdout.buffer.flush()
```

The previous attempt at this bug cleaned up stray surrogates in the *output* — but the exception was thrown on the *input* path, before any result existed, so it never helped.

### HTML text extraction

A lightweight extractor built on stdlib `html.parser.HTMLParser`, skipping `script/style/noscript/svg` and adding newlines at block tags:

```python
class _TextExtractor(HTMLParser):
    skip_tags = {"script", "style", "noscript", "svg", "template"}
    # ... see source
```

## FAQ

- **No search results?** Check your network. Domestic search needs Bing reachable; overseas search (`use_proxy=True`) needs v2rayN running.
- **"PySocks missing"?** No problem — Bing direct still works; `use_proxy=True` just falls back to a direct connection.
- **Search fails with `surrogates not allowed`?** That was the cp936/UTF-8 stdio bug, fixed in v1.2.0. If you see it, you're running an older file.
- **Bing returns odd results for some queries?** It's Bing, not the server — for certain queries (especially Chinese) it serves unrelated filler that changes on every request. The server now detects this, retries, and falls back to DuckDuckGo; if nothing relevant is available you'll see a `warning` field on the results.
- **Results are mojibake / full of `�`?** Also fixed in v1.2.0 — response bytes are now decoded by declared charset instead of assuming UTF-8.

## License

MIT License — free to use, modify, and share.
