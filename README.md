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
- **Three-level result grading, and it fails closed**: search engines that suspect automation don't always show a captcha — they answer `HTTP 200` with a normal-looking page full of *unrelated* results. Measured on this setup: `"ED047TC2"` came back as BOSS直聘 job listings, `asdfqwer1234zzz` as Poki browser games, and a long-tail `RK3566 EBC_SDDO0 GPIO4 pin mux datasheet` matched only the first word. Every result set is graded **good** (most keywords matched), **partial** (the subject matched but qualifiers didn't — returned, but flagged) or **junk** (nothing in the query appears in any result). Junk is **never returned**: the tool raises an error instead, because callers that don't read `warning` fields will happily treat filler as evidence
- **Three engines, throttled and cooled down**: Bing → Sogou → DuckDuckGo. An engine that answers with an anti-bot page (`202`/`403`/`429`, `anomaly`, `captcha`, `安全验证`, `antispider`, …) is put into a 10-minute cooldown instead of being hammered. Requests per engine are spaced out, and results are cached for 6 hours
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

Returns: `[{title, url, snippet, content, engine, relevance}]`

`engine` is `bing`, `sogou` or `duckduckgo`. `relevance` is `good` or `partial`; `partial` results also carry a `warning` saying the query's qualifiers weren't covered. When every engine returns junk, the call **fails with an error** (message includes per-engine status) rather than returning filler — fall back to `web_fetch` on a known page, or retry later. A `warning` also appears when the proxy was requested but unreachable and the results are direct-only.

### web_fetch

| Param | Type | Default | Description |
|---|---|---|---|
| `url` | string | required | URL to fetch |
| `timeout_s` | number | 20 | Timeout (seconds) |
| `use_proxy` | bool | true | Use proxy (true for overseas sites, false for CN sites) |

Returns: `# Title` + URL + first 6000 chars of page text

## Network strategy

| Order | Engine | Method | Scenario |
|---|---|---|---|
| 1 | Bing | direct (`use_proxy=False`) or SOCKS5 (`use_proxy=True`) | Primary engine |
| 2 | Sogou | direct | CN-reachable backup; no proxy needed. Strong on Chinese results, weak on English |
| 3 | DuckDuckGo (html endpoint) | SOCKS5 proxy | Backup, needs a proxy |

The next engine is only tried when the previous one produced no `good` result. DuckDuckGo is not gated on `use_proxy` — the fallback's job is "try another engine", and letting the primary engine's route choice veto it is how you end up with no second opinion at all.

> **Why not Google?** The backup engine used to be Google. Modern Google no longer serves results to a plain HTTP client — it returns a JS shell page (title `Google Search`, zero result links), so the backup silently did nothing. DuckDuckGo's `html.duckduckgo.com/html/` endpoint still returns plain server-rendered results and is parseable.

> **Bing's RSS endpoint is not a workaround.** `&format=rss` returns the same ranking — measured: `"ED047TC2"` came back as 6G telecom news. The problem is the ranking, not the markup.

### Rate limiting, cooldown, cache

Plain HTTP scraping of search engines is fighting against anti-bot heuristics, so the server paces itself:

| Setting | Default | Env var | Meaning |
|---|---|---|---|
| Min interval per engine | 2 s | `LOCAL_SEARCH_ENGINE_INTERVAL` | Spacing between requests to the same engine |
| Cooldown after a block | 600 s | `LOCAL_SEARCH_ENGINE_COOLDOWN` | How long a blocked engine is benched |
| Result cache TTL | 6 h | `LOCAL_SEARCH_CACHE_TTL` | Repeat queries come from memory |
| Junk cache TTL | 120 s | `LOCAL_SEARCH_CACHE_NEG_TTL` | Short negative cache so junk queries don't re-hit engines |
| Log file | `web-search.log` next to the script | `LOCAL_SEARCH_LOG` | One line per engine attempt; rotates at 2 MB. Set to empty to disable |

A response is treated as a block if the status is `202`/`403`/`429`, or the body contains `anomaly`, `captcha`, `challenge`, `安全验证`, `验证码`, `antispider`, `too many requests` or `are you a robot`. Blocked engines are benched for the cooldown period and skipped instantly until then.

Measured on this setup: DuckDuckGo `html` → `202` + anomaly; Baidu → `200` + `百度安全验证`; Mojeek → `403`; Yandex → captcha; Brave → `429`; Sogou → works, but blocks after ~2–3 rapid queries. That's why the pacing exists.

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
def web_search(query, num_results, use_proxy, ...):
    # Engines are tried in order; stop as soon as one gives a `good` result.
    _try(bing_key, lambda: _bing_fetch(q, num, use_proxy), _parse_bing, use_proxy)
    if _best(LEVEL_GOOD): return _finish(_best(LEVEL_GOOD))

    _try("sogou", lambda: _sogou_fetch(q, num), _parse_sogou, False)
    if _best(LEVEL_GOOD): return _finish(_best(LEVEL_GOOD))

    _try("ddg", lambda: _ddg_fetch(q, num), _parse_ddg, True)

    if _best(LEVEL_GOOD): return _finish(_best(LEVEL_GOOD))
    if _best(LEVEL_PARTIAL):                       # subject matched, qualifiers didn't
        out = _finish(_best(LEVEL_PARTIAL))
        for r in out: r["warning"] = WARNING_PARTIAL
        return out
    raise SearchUnavailable(engine_status)         # never return junk
```

### Grading results: good / partial / junk

Three levels, because a binary split can't be both safe and useful:

| Level | Rule | What happens |
|---|---|---|
| `good` | Passes both junk rules below | Returned normally |
| `partial` | Some query keyword appears somewhere in the set, but the set still fails a junk rule | Returned with `relevance: "partial"` and a `warning` |
| `junk` | **No** query keyword appears anywhere in the set | Never returned — the call raises |

`junk` is what soft-blocked engines actually serve: `"ED047TC2"` → BOSS直聘 job listings, `asdfqwer1234zzz` → Poki games. `partial` is what you get when only the subject matched: `RK3566 EBC_SDDO0 GPIO4 pin mux datasheet` → RK3566 datasheet pages. That's genuinely useful for long-tail research, but it doesn't answer the pin-mux question, so it's labelled rather than silently simplified into a different query.

The two junk rules are OR'd, because each covers the other's blind spot:

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
- **`web_search` returns an error instead of results?** That's intentional (v1.3.0+): every engine returned filler that had nothing to do with the query, and returning filler is worse than failing. The message lists each engine's state. Retry later, simplify the query, or `web_fetch` a known page directly. `web_fetch` is unaffected.
- **All engines say "cooldown"?** They were detected as anti-bot pages (`202`/`403`/`429`, anomaly/captcha/安全验证 pages). Cooldown defaults to 10 minutes; tune with `LOCAL_SEARCH_ENGINE_COOLDOWN`. Lower `LOCAL_SEARCH_ENGINE_INTERVAL` to search faster at the risk of getting blocked sooner.
- **`use_proxy=True` behaves exactly like `use_proxy=False`, byte for byte?** You're on v1.2.0 or earlier with no PySocks installed — the proxy was silently skipped. Fixed in v1.2.1 (stdlib SOCKS5).
- **Search fails with `surrogates not allowed`?** That was the cp936/UTF-8 stdio bug, fixed in v1.2.0. If you see it, you're running an older file.
- **Results are mojibake / full of `�`?** Also fixed in v1.2.0 — response bytes are now decoded by declared charset instead of assuming UTF-8.
- **I need to debug what the engines did.** Check `web-search.log` next to the script (one line per attempt: engine, HTTP status, body size, parsed count, grade, relevance). Set `LOCAL_SEARCH_LOG` to relocate or empty it to disable.

## License

MIT License — free to use, modify, and share.
