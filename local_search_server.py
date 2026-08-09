#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地 MCP 搜索服务器（轻量版）—— 给 Claude Code 用。

作用：用本机网络做真实搜索/抓取，与模型后端无关（DeepSeek 等第三方后端
不实现内置 WebSearch/WebFetch）。

网络策略：
- Bing 直连（国内可达，无需代理，兜底引擎）
- Google / DuckDuckGo 走 v2rayN SOCKS5 代理（默认 127.0.0.1:1080）

实现：纯 Python 标准库（json + urllib + threading），实现 MCP stdio 协议，
启动快（~50ms），避免重型依赖拖慢 Claude Code 会话启动。

协议：MCP over stdio，JSON-RPC 2.0。支持 initialize / tools/list / tools/call。
工具：
  web_search(query, num_results=8, use_proxy=True)
  web_fetch(url, timeout_s=20, use_proxy=True)
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser

# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------
PROXY_PORT = int(os.environ.get("LOCAL_SEARCH_SOCKS_PORT", "1080"))
PROXY_URL = f"socks5://127.0.0.1:{PROXY_PORT}"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

SERVER_NAME = "web-search"
SERVER_VERSION = "1.0.0"

TOOLS = [
    {
        "name": "web_search",
        "description": "用本机网络搜索网页。query=搜索词；num_results=返回条数(1-15)；use_proxy=True 走 v2rayN SOCKS5 代理(可访问Google)，Bing 总是尝试直连兜底。返回 [{'title','url','snippet','engine'}]。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "num_results": {"type": "integer", "default": 8},
                "use_proxy": {"type": "boolean", "default": True},
            },
            "required": ["query"],
        },
    },
    {
        "name": "web_fetch",
        "description": "抓取 URL 并返回可读文本内容(前~6000字符)。use_proxy=True 走 v2rayN SOCKS5 代理(适合境外站点)；对国内站点可设 False 直连。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "timeout_s": {"type": "number", "default": 20},
                "use_proxy": {"type": "boolean", "default": True},
            },
            "required": ["url"],
        },
    },
]


# ----------------------------------------------------------------------------
# HTML 解析（用 html.parser 标准库，不依赖 bs4）
# ----------------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip_depth = 0
        self.skip_tags = {"script", "style", "noscript", "svg", "template"}
        self.in_skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.skip_tags:
            self.in_skip += 1
        if tag in ("p", "br", "h1", "h2", "h3", "li", "div", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.skip_tags and self.in_skip:
            self.in_skip -= 1

    def handle_data(self, data):
        if not self.in_skip:
            self.parts.append(data)

    def text(self):
        raw = "".join(self.parts)
        return re.sub(r"\n{3,}", "\n\n", raw)


def html_to_text(html):
    p = _TextExtractor()
    p.feed(html)
    return p.text()


def _clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


# ----------------------------------------------------------------------------
# 网络请求
# ----------------------------------------------------------------------------
def _http_get(url, use_proxy, timeout):
    """用 urllib 发起 GET。use_proxy=True 时通过 SOCKS5 代理。"""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7"},
    )
    if use_proxy:
        # urllib 不直接支持 socks；这里用最简单可靠的方式：
        # 通过环境变量让 urllib 走 SOCKS 需要 PySocks，这里改为显式代理处理。
        # 方案：如果 PySocks 可用就用，否则退回直连。
        try:
            import socks  # PySocks
            import socket
            host, port = PROXY_URL.replace("socks5://", "").split(":")
            socks.set_default_proxy(socks.SOCKS5, host, int(port))
            socket.socket = socks.socksocket
        except ImportError:
            pass  # 无 PySocks 则直连
    opener = urllib.request.build_opener()
    return opener.open(req, timeout=timeout).read().decode("utf-8", "replace")


# ----------------------------------------------------------------------------
# 搜索引擎解析
# ----------------------------------------------------------------------------
def _parse_bing(html, num):
    results = []
    # 用正则提取 b_algo 区块
    for m in re.finditer(r'<li[^>]*class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>', html, re.S):
        if len(results) >= num:
            break
        block = m.group(1)
        am = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not am:
            continue
        url = am.group(1)
        if not url.startswith("http"):
            continue
        title = _clean(re.sub(r"<[^>]+>", "", am.group(2)))
        pm = re.search(r'class="[^"]*b_caption[^"]*"[^>]*>\s*<p[^>]*>(.*?)</p>', block, re.S)
        snippet = _clean(re.sub(r"<[^>]+>", "", pm.group(1))) if pm else ""
        results.append({"title": title, "url": url, "snippet": snippet, "engine": "bing"})
    return results


def _parse_google(html, num):
    results = []
    # Google 结果链接格式：/url?q=...
    for m in re.finditer(r'<a[^>]*href="(/url\?q=([^&"]+)[^"]*)"[^>]*>', html):
        if len(results) >= num:
            break
        url = urllib.parse.unquote(m.group(2))
        if not url.startswith("http") or "google.com" in url:
            continue
        results.append({"title": url, "url": url, "snippet": "", "engine": "google"})
    return results


def _parse_ddg(html, num):
    results = []
    for m in re.finditer(r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        if len(results) >= num:
            break
        url = m.group(1)
        if url.startswith("//duckduckgo.com/l/"):
            uddg = re.search(r"uddg=([^&]+)", url)
            url = urllib.parse.unquote(uddg.group(1)) if uddg else url
        title = _clean(re.sub(r"<[^>]+>", "", m.group(2)))
        results.append({"title": title, "url": url, "snippet": "", "engine": "duckduckgo"})
    return results


def web_search(query, num_results, use_proxy):
    num = max(1, min(int(num_results), 15))
    q = urllib.parse.quote(query)

    # 1) Bing direct（国内可达、稳定、结果质量好，主引擎）
    try:
        html = _http_get(f"https://www.bing.com/search?q={q}&count={num}&setlang=en&cc=us", False, 15)
        results = _parse_bing(html, num)
        if results:
            return results
    except Exception:
        pass
    # 2) DuckDuckGo via proxy（兜底，需要 v2rayN 运行）
    if use_proxy:
        try:
            html = _http_get(f"https://html.duckduckgo.com/html/?q={q}", True, 15)
            results = _parse_ddg(html, num)
            # 过滤掉广告链接（duckduckgo.com/y.js 是广告）
            results = [r for r in results if not r["url"].startswith("https://duckduckgo.com/")]
            if results:
                return results
        except Exception:
            pass
    return []


def web_fetch(url, timeout_s, use_proxy):
    try:
        html = _http_get(url, use_proxy, timeout_s)
        text = html_to_text(html)
        title = ""
        tm = re.search(r"<title[^>]*>(.*?)</title>", html, re.S)
        if tm:
            title = _clean(re.sub(r"<[^>]+>", "", tm.group(1)))
        return f"# {title}\n\nURL: {url}\n\n{text[:6000]}"
    except Exception as e:
        return f"[web_fetch error] {type(e).__name__}: {e}"


# ----------------------------------------------------------------------------
# MCP stdio 协议（JSON-RPC 2.0）
# ----------------------------------------------------------------------------
def _send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _call_tool(name, args):
    if name == "web_search":
        return web_search(
            args.get("query", ""),
            args.get("num_results", 8),
            args.get("use_proxy", True),
        )
    if name == "web_fetch":
        return web_fetch(
            args.get("url", ""),
            args.get("timeout_s", 20),
            args.get("use_proxy", True),
        )
    raise ValueError(f"未知工具: {name}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid = msg.get("id")
        method = msg.get("method")

        if method == "initialize":
            _send({
                "jsonrpc": "2.0", "id": mid,
                "result": {
                    "protocolVersion": msg.get("params", {}).get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            })
        elif method == "notifications/initialized":
            pass  # no reply
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = msg.get("params", {})
            name = params.get("name")
            args = params.get("arguments", {})
            try:
                result = _call_tool(name, args)
                content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2) if isinstance(result, (list, dict)) else str(result)}]
                _send({"jsonrpc": "2.0", "id": mid, "result": {"content": content, "isError": False}})
            except Exception as e:
                _send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": f"[error] {e}"}], "isError": True}})
        elif method == "ping":
            _send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method and method.startswith("notifications/"):
            pass
        elif method is None:
            pass  # response to our request, ignore


if __name__ == "__main__":
    main()
