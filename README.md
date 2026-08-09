# Local Web Search MCP Server

> A zero-dependency, local MCP search server for Claude Code — implemented in pure Python standard library. Gives Claude Code (or any MCP client) real web-search capability using your **local network**, independent of your model backend. **Bing直连-first**, with SOCKS5 proxy fallback for overseas engines.

🌐 **Languages:** [English](README.md) | [简体中文](README.zh-CN.md)

## Why this exists

Claude Code's **built-in WebSearch / WebFetch tools rely on the Anthropic official API**. If your Claude Code backend is a third-party model (DeepSeek, Qwen, etc., connected via a local proxy like CC Switch), the built-in search tools stop working.

This project turns your **local, real network** into a model-callable search tool via a **MCP-over-stdio** Python process:

```
Claude Code ──MCP stdio──▶ local_search_server.py (local Python process)
                                  │
                                  ├─ Bing direct (China-reachable, primary)
                                  ├─ DuckDuckGo via SOCKS5 proxy (fallback)
                                  └─ Google via SOCKS5 proxy (candidate)
```

## Features

- **Pure Python standard library** (json + urllib + threading + html.parser) — zero dependencies, fast startup (~50ms)
- Full **MCP over stdio** protocol (JSON-RPC 2.0): `initialize` / `tools/list` / `tools/call`
- Two tools:
  - `web_search(query, num_results=8, use_proxy=True)` → search the web
  - `web_fetch(url, timeout_s=20, use_proxy=True)` → fetch readable page text
- **Bing direct-first** (stable, no proxy needed in CN), auto-falls back to DuckDuckGo (via proxy)
- Configurable SOCKS5 proxy port (default `127.0.0.1:1080`, v2rayN-compatible)

## Install

### 1. Requirements

- Python 3.8+ (standard library only, no pip packages needed)
- *(Optional)* v2rayN or any SOCKS5 proxy, port `1080` — only for Google / DuckDuckGo. Works without it too (Bing only).

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
| `use_proxy` | bool | true | Use SOCKS5 proxy for Google/DDG (Bing is always direct) |

Returns: `[{title, url, snippet, engine}]`

### web_fetch

| Param | Type | Default | Description |
|---|---|---|---|
| `url` | string | required | URL to fetch |
| `timeout_s` | number | 20 | Timeout (seconds) |
| `use_proxy` | bool | true | Use proxy (true for overseas sites, false for CN sites) |

Returns: `# Title` + URL + first 6000 chars of page text

## Network strategy

| Engine | Method | Scenario |
|---|---|---|
| Bing | direct | Primary engine in CN, no proxy needed |
| DuckDuckGo | SOCKS5 proxy | Fallback if Bing fails |
| Google | SOCKS5 proxy | Candidate engine |

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
def web_search(query, num_results, use_proxy):
    # 1) Bing direct (primary engine)
    html = _http_get(f"https://www.bing.com/search?q={q}", False, 15)
    results = _parse_bing(html, num)   # regex-extract b_algo blocks
    if results:
        return results
    # 2) DuckDuckGo via proxy (fallback)
    html = _http_get(f"https://html.duckduckgo.com/html/?q={q}", True, 15)
    results = _parse_ddg(html, num)
    return results
```

### Proxy switching

On `use_proxy=True`, tries PySocks first, falls back to direct:

```python
try:
    import socks
    socks.set_default_proxy(socks.SOCKS5, host, port)
    socket.socket = socks.socksocket
except ImportError:
    pass  # no PySocks → direct connection
```

### HTML text extraction

A lightweight extractor built on stdlib `html.parser.HTMLParser`, skipping `script/style/noscript/svg` and adding newlines at block tags:

```python
class _TextExtractor(HTMLParser):
    skip_tags = {"script", "style", "noscript", "svg", "template"}
    # ... see source
```

## FAQ

- **No search results?** Check your network / Bing reachability. For overseas engines, start v2rayN.
- **"PySocks missing"?** No problem — Bing direct still works, only overseas engines degrade.
- **File `USAGE.md` removed?** English instructions live in this README; Chinese in `README.zh-CN.md`.

## License

MIT License — free to use, modify, and share.
