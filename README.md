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
- **Junk-result detection**: search engines sometimes answer a query by matching only *one* of its words and dropping the rest — e.g. `SolidWorks Wine CrossOver Linux 2026 compatibility` comes back as `solidworks.com` marketing pages, and `Hunt Showdown 1896 Easy Anti-Cheat Linux Proton support` comes back as dictionary definitions of "hunt". The server scores each result set against the query keywords (median coverage + how many query terms are missing entirely), retries Bing once, and switches to DuckDuckGo if nothing matches. If no engine produces anything relevant, the results carry a `warning` field rather than being passed off as answers
- **True zero dependencies** — including for the proxy. SOCKS5 is implemented against the stdlib socket API (~35 lines), so `use_proxy` never depends on a third-party package being installed in whichever interpreter happens to run the server
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
      "command": "C:/Users/<your-username>/AppData/Local/Programs/Python/Python313/python.exe",
      "args": ["C:/Users/<your-username>/.claude/mcp-servers/local_search_server.py"],
      "env": {}
    }
  }
}
```

> **Use an absolute path to the interpreter, not bare `"python"`.** A bare `python` is resolved through `PATH`, and on a normal Windows box that is *not* necessarily your own Python — LibreOffice, other IDEs and various tools all ship their own `python.exe` and some of them put themselves on `PATH`. Whatever interpreter wins decides how the server behaves, silently. Check with `claude mcp list` plus the process list if a search behaves oddly.
>
> This used to matter a lot more than it does now: the old code needed PySocks for `use_proxy`, and silently fell back to a direct connection without it — so a `PATH` surprise turned `use_proxy=True` into a no-op with no error. SOCKS5 is now stdlib-only, so the interpreter no longer changes whether the proxy works. Pinning the path is still worth doing so you know which Python you're on.

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

`engine` is `bing` or `duckduckgo`. A `warning` field appears on the results in two cases: every engine's results matched too few of the query's keywords (`结果与查询关键词重合度低…`), or the proxy was requested but unreachable so the results are direct-connection only (`代理不可用…`).

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

Bing is tried first (twice if the first attempt looks irrelevant). DuckDuckGo's html endpoint is reachable only through a proxy, so it needs v2rayN (or any SOCKS5 proxy) running.

> **The backup is not gated on `use_proxy`.** If Bing's results look irrelevant, DuckDuckGo is tried regardless of which route you asked for — the fallback's job is "try another engine", and letting the primary engine's route choice veto it is how you end up returning junk with no second opinion. (If the proxy is down, the attempt just fails fast.)

> **Why not Google?** The backup engine used to be Google. Modern Google no longer serves results to a plain HTTP client — it returns a JS shell page (title `Google Search`, zero result links), so the backup silently did nothing. DuckDuckGo's `html.duckduckgo.com/html/` endpoint still returns plain server-rendered results and is parseable.

> **DuckDuckGo has rate limits.** Its html endpoint starts returning an `anomaly / challenge` page if you hit it too often, after which the fallback quietly finds nothing until the limit clears. Normal interactive use stays well under it; a test loop hammering it will trip it.

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
    #    Retry once if the results don't look like they answer the query.
    for _ in range(2):
        _collect(_bing(q, num, use_proxy), use_proxy)
        if _have_usable():
            break
    # 2) DuckDuckGo html endpoint via proxy — tried whenever Bing looks wrong,
    #    regardless of use_proxy (the fallback needs the proxy itself).
    if not _have_usable():
        _collect(_ddg(q, num), True)
    # 3) Prefer a set that isn't junk; otherwise return the best-scoring one, flagged
    usable = [a for a in attempts if not _is_junk(a[1], query)]
    return _finish(max(usable or attempts, key=score)[1])
```

### Deciding whether results are junk

Two rules, OR'd, because each covers the other's blind spot:

1. **Median keyword coverage below `RELEVANCE_FLOOR` (`0.20`)** — catches "one result happens to be on-topic, the other five are filler". Scoring the *best* result instead fails here: `solidworks.com` marketing pages score `0.33` on the best-result metric, *identical* to the genuinely-correct results for `python asyncio tutorial`, so no threshold can separate them. The median gives `0.17` vs `0.33`.
2. **Most of the query's content words never appear anywhere in the result set** (query has ≥4 content words, ≥3 of them missing from every title/snippet) — catches "the engine matched one word and dropped all the qualifiers". This rule is computed over the *union* of all results, so unlike the median it doesn't change when `num_results` changes parity.

Keywords are latin words plus CJK runs, minus stopwords. **CJK bigrams are deliberately not expanded** — expansion adds fragments like `步编` (from `异步编程`) that can never match, diluting the denominator. Measured on 6 known-good and 4 known-junk queries: with bigrams the ranges overlap (good low `0.14` vs junk high `0.17`, undecidable); without, they separate (`0.25` vs `0.17`). Longer CJK runs still get a relaxed partial-match rule at *match* time, so recall isn't lost.

Measured end state: 48 decisions (7 good + 5 junk queries × `num_results` 4/5/6/8) — 0 misclassifications.

### Proxy routing

SOCKS5 is implemented directly against the stdlib socket API, so there is no PySocks dependency:

```python
def _socks5_connect(proxy_host, proxy_port, dest_host, dest_port, timeout):
    s = socket.create_connection((proxy_host, proxy_port), timeout)
    s.sendall(b"\x05\x01\x00")                 # version 5, no-auth
    if _recv_exact(s, 2) != b"\x05\x00":
        raise OSError("SOCKS5 proxy refused no-auth")
    s.sendall(b"\x05\x01\x00" + addr_bytes + struct.pack("!H", dest_port))
    head = _recv_exact(s, 4)                   # CONNECT reply
    if head[1] != 0:
        raise OSError("SOCKS5 proxy refused connection")
    return s
```

Each request builds its own opener with SOCKS-aware connection classes, so `use_proxy` is honoured per call:

```python
def _build_opener(use_proxy):
    if use_proxy:
        http_cls, https_cls = _socks_conn_classes(PROXY_HOST, PROXY_PORT)
        return urllib.request.build_opener(
            _SocksHTTPHandler(http_cls), _SocksHTTPSHandler(https_cls))
    return urllib.request.build_opener()
```

Three traps this avoids, all of which were real bugs here:

1. **No global monkey-patching.** The original code did `socket.socket = socks.socksocket`, which is global *and irreversible* — after the first `use_proxy=True` call, every later request in the process went through the proxy, including `use_proxy=False` ones. It was also a race, since content fetching is concurrent.
2. **Override socket creation, not `connect()`.** `HTTPSConnection.connect()` wraps the socket in TLS *after* connecting. Replacing `connect()` wholesale silently skips that, so requests go out as plaintext HTTP — to port 443. The handler instead swaps `self._create_connection` (which `http.client` stores as an *instance* attribute in `__init__`, so it must be replaced after `super().__init__()`).
3. **No dependency on what happens to be installed.** The earlier version called PySocks *if importable* and otherwise **silently fell back to a direct connection** — so `use_proxy=True` became a no-op with no error. That is exactly what happened on the machine this was debugged on: the MCP config used a bare `"command": "python"`, `PATH` resolved that to a LibreOffice-bundled Python with no PySocks, and every "proxied" search was quietly hitting Bing's domestic endpoint. Implementing SOCKS5 in the stdlib removes the failure mode, and a proxy that can't be reached now falls back to a direct search carrying an explicit `warning` instead of nothing at all.

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
- **`use_proxy=True` behaves exactly like `use_proxy=False`, byte for byte?** You're on v1.2.0 or earlier with no PySocks installed — the proxy was silently skipped. Fixed in v1.2.1 (stdlib SOCKS5).
- **Search fails with `surrogates not allowed`?** That was the cp936/UTF-8 stdio bug, fixed in v1.2.0. If you see it, you're running an older file.
- **Bing returns odd results for some queries?** Usually Bing, not the server — for certain queries it matches only one word and drops the rest. The server detects this, retries, and falls back to DuckDuckGo; if nothing relevant is available you'll see a `warning` field on the results.
- **The DuckDuckGo fallback stopped finding anything?** Its html endpoint rate-limits with an `anomaly / challenge` page if you query it too often. Normal use is fine; automated loops trip it.
- **Results are mojibake / full of `�`?** Also fixed in v1.2.0 — response bytes are now decoded by declared charset instead of assuming UTF-8.

## License

MIT License — free to use, modify, and share.
