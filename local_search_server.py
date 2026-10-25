#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地 MCP 搜索服务器（轻量版）—— 给 Claude Code 用。

作用：用本机网络做真实搜索/抓取，与模型后端无关（DeepSeek 等第三方后端
不实现内置 WebSearch/WebFetch）。

网络策略：
- Bing 直连（国内可达，无需代理，主引擎）
- DuckDuckGo 走 v2rayN SOCKS5 代理（默认 127.0.0.1:1080，备用引擎）

实现：纯 Python 标准库（json + urllib + threading），实现 MCP stdio 协议，
启动快（~50ms），避免重型依赖拖慢 Claude Code 会话启动。

协议：MCP over stdio，JSON-RPC 2.0。支持 initialize / tools/list / tools/call。
工具：
  web_search(query, num_results=8, use_proxy=True, fetch_content=True, fetch_top=3, content_chars=1500)
  web_fetch(url, timeout_s=20, use_proxy=True)
"""
import concurrent.futures
import gzip
import http.client
import json
import os
import re
import socket
import struct
import sys
import time
import urllib.parse
import urllib.request
import zlib
from html import unescape
from html.parser import HTMLParser

# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------
PROXY_PORT = int(os.environ.get("LOCAL_SEARCH_SOCKS_PORT", "1080"))
PROXY_HOST = "127.0.0.1"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

SERVER_NAME = "web-search"
SERVER_VERSION = "1.3.0"

# 判断一个关键词里有没有中文字（用于决定是否套用"部分命中"放宽规则）
_CJK_RE = re.compile(r"[一-鿿]")

# 判定"结果是否和查询对得上"的阈值，配合 _relevance() 的"中位数覆盖"使用。
# 0.20 是实测定的：拿 6 条正常查询和 4 条已知拿到垃圾结果的查询跑了一轮，
# 正常的最低 0.25（"Python 异步编程 asyncio 教程"——中文查询返回英文结果，
# 命中率天然低），垃圾的最高 0.17（"SolidWorks Wine CrossOver Linux 2026
# compatibility"，Bing 只认了 SolidWorks 一个词）。窗口只有 0.17~0.25，
# 所以配套做了"多次尝试取最优"，万一误判也只是多花一次请求，不会给出错结果。
RELEVANCE_FLOOR = 0.20

# 第二条判据（见 _is_junk）：查询实词够多、但其中绝大多数在所有结果里一次都没出现。
# 这条用"整批结果的并集"算，不随 num_results 变化 —— 中位数会随条数的奇偶浮动，
# 实测同一条 "SolidWorks Wine CrossOver Linux 2026 compatibility" 在 num=4/6 时
# 得 0.17（判为垃圾）、num=5 时得 0.33（判为正常），只靠中位数会漏。
MIN_TERMS_FOR_COVERAGE_RULE = 4   # 实词少于这个数就不套用本规则，避免误伤短查询
MAX_MISSED_TERMS = 3              # 未命中的实词达到这个数（且实词总数够多）就判为垃圾

# 判定不达标时，附在每条结果上的提示
WARNING_JUNK = "结果与查询关键词重合度低，可能不相关（引擎对这条查询只匹配了其中个别词，返回了填充结果）"
# 代理不可用时退回直连，必须说清楚——否则"走了代理却搜不到境外内容"很难查
WARNING_NO_PROXY = "代理不可用，本次为直连结果（境外内容可能缺失）"
WARNING_PARTIAL = "结果只命中了查询里的部分关键词，可能没有覆盖全部限定条件（例如只找到主词相关的页，没找到限定条件相关的页）"

# 结果分级。调用方据此判断这批结果能不能当证据用。
#   good    —— 关键词大部分命中，按正常结果用
#   partial —— 只命中了一部分词（通常是主词），可用，但要知道它没覆盖全部限定条件
#   junk    —— 一个关键词都没命中，是引擎塞的填充结果，绝不返回
LEVEL_GOOD = "good"
LEVEL_PARTIAL = "partial"
LEVEL_JUNK = "junk"

# --- 引擎节流 / 冷却 / 缓存 / 日志 ---
# 为什么需要：无 Key 抓取搜索引擎，短时间连续请求会被判定为自动化流量。
# 实测 Bing 被打降级后会返回"HTTP 200 + 随机内容"，搜狗打两三条就返回反爬页。
# 这些参数都可以用环境变量覆盖。
ENGINE_MIN_INTERVAL = float(os.environ.get("LOCAL_SEARCH_ENGINE_INTERVAL", "2.0"))
ENGINE_COOLDOWN = float(os.environ.get("LOCAL_SEARCH_ENGINE_COOLDOWN", "600"))   # 被拦后冷却 10 分钟
CACHE_TTL = float(os.environ.get("LOCAL_SEARCH_CACHE_TTL", "21600"))            # 正常结果缓存 6 小时
CACHE_NEG_TTL = float(os.environ.get("LOCAL_SEARCH_CACHE_NEG_TTL", "120"))      # 全垃圾的查询负缓存 2 分钟
CACHE_MAX = 256
LOG_FILE = os.environ.get(
    "LOCAL_SEARCH_LOG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "web-search.log"),
)
# 反爬/拦截特征：命中即判定该引擎被拦，进入冷却，不再解析它的页面
_BLOCK_STATUS = {202, 403, 429}
_BLOCK_MARKERS = (
    "anomaly", "captcha", "challenge", "security check",
    "安全验证", "验证码", "antispider", "too many requests", "are you a robot",
)

# 做相关性判断时忽略的虚词：英文虚词 + 中文疑问/连接词。
# 中文疑问词尤其重要——Bing 返回垃圾时经常只命中"如何""什么"这类词。
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "with",
    "is", "are", "was", "were", "be", "by", "from", "as", "it", "its", "this",
    "that", "these", "those", "how", "what", "why", "when", "where", "which",
    "can", "do", "does", "did", "not", "no", "vs", "via",
    "如何", "怎么", "怎样", "怎么样", "什么", "为何", "为什么", "哪个", "哪些",
    "哪里", "是否", "可以", "能否", "有没有", "以及", "或者", "但是", "因为",
    "所以", "如果", "就是", "这个", "那个", "这些", "那些", "一个", "我们",
    "你们", "他们", "请问", "求助",
}

TOOLS = [
    {
        "name": "web_search",
        "description": "用本机网络搜索网页。依次尝试 Bing → 搜狗 → DuckDuckGo，并按结果与查询关键词的吻合度分三级：good（可信）、partial（只命中主词、限定条件没覆盖，结果带 relevance/ warning）、junk（与查询完全无关的填充内容，一律不返回——此时工具报错而不是给垃圾）。每条结果带 relevance 字段。默认对前 fetch_top 个结果自动抓取正文片段放进 content 字段。返回 [{'title','url','snippet','content','engine','relevance'}]。内置引擎级节流、反爬冷却与 6 小时缓存；引擎被限流时请稍后重试或改用 web_fetch 直接抓已知页面。query=搜索词；num_results=返回条数(1-15)；use_proxy=True 走 v2rayN SOCKS5 代理；fetch_content=True 时自动抓正文；fetch_top=抓前几个(默认3)；content_chars=每个正文截取字符数(默认1500)。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "num_results": {"type": "integer", "default": 8},
                "use_proxy": {"type": "boolean", "default": True},
                "fetch_content": {"type": "boolean", "default": True},
                "fetch_top": {"type": "integer", "default": 3},
                "content_chars": {"type": "integer", "default": 1500},
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
def _decode_body(raw, content_type):
    """把响应字节解成文本，尽量不解错。

    中文站点仍有 GBK/GB2312 页面，硬按 utf-8 解会整页乱码。策略：
    先试 utf-8（它有强校验，非 utf-8 字节几乎不可能蒙混通过），
    不行再按 HTTP 头/HTML meta 声明的 charset，再不行 gb18030，最后兜底 replace。
    """
    charset = ""
    if content_type:
        m = re.search(r"charset=[\"']?([\w-]+)", content_type, re.I)
        if m:
            charset = m.group(1)
    if not charset:
        m = re.search(rb"charset=[\"']?([\w-]+)", raw[:4096], re.I)
        if m:
            charset = m.group(1).decode("ascii", "replace")
    for enc in ("utf-8", charset, "gb18030"):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


def _recv_exact(sock, n):
    """从 socket 精确读 n 个字节（SOCKS5 握手用）。"""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("SOCKS5 代理在握手过程中断开连接")
        buf += chunk
    return buf


def _socks5_connect(proxy_host, proxy_port, dest_host, dest_port, timeout):
    """手工完成一次 SOCKS5 握手并连到目标，返回已连接的 socket。

    为什么要自己写：PySocks 是第三方依赖，而本项目的卖点就是"纯标准库、零依赖"。
    更要命的是**缺了它原本只是静默退回直连**——如果你的 python 恰好没装 PySocks
    （比如 PATH 里排在前面的某个软件自带 python），use_proxy=True 会一声不响地变成
    直连，等到发现"怎么走了代理还是搜不到境外内容"时已经很难查了。自己实现之后
    走不走代理只取决于参数，与跑在哪个解释器上无关。

    协议（无认证）：
      C→S  05 01 00                     版本5 / 1 种认证方式 / 无认证
      S→C  05 00                        选定无认证
      C→S  05 01 00 <ATYP> <ADDR> <PORT> CONNECT 请求
      S→C  05 00 00 <ATYP> <ADDR> <PORT> 成功（第 2 字节非 0 即失败）
    """
    s = socket.create_connection((proxy_host, proxy_port), timeout)
    try:
        s.sendall(b"\x05\x01\x00")
        if _recv_exact(s, 2) != b"\x05\x00":
            raise OSError("SOCKS5 代理未接受无认证方式（要求认证或版本不符）")

        # 目标是 IP 字面量就直接塞字节，否则按域名送（让代理那边去解析，
        # 免得受本机 DNS 影响——被墙环境下本机 DNS 往往是不可靠的）
        try:
            addr = b"\x01" + socket.inet_aton(dest_host)
        except OSError:
            try:
                addr = b"\x04" + socket.inet_pton(socket.AF_INET6, dest_host)
            except OSError:
                raw = dest_host.encode("idna")
                addr = b"\x03" + bytes([len(raw)]) + raw

        s.sendall(b"\x05\x01\x00" + addr + struct.pack("!H", dest_port))
        head = _recv_exact(s, 4)
        if head[1] != 0:
            raise OSError(f"SOCKS5 代理拒绝连接（rep={head[1]}）")
        atyp = head[3]
        if atyp == 1:
            _recv_exact(s, 4)
        elif atyp == 4:
            _recv_exact(s, 16)
        elif atyp == 3:
            _recv_exact(s, _recv_exact(s, 1)[0])
        _recv_exact(s, 2)  # 绑定端口
        return s
    except Exception:
        s.close()
        raise


def _socks_conn_classes(proxy_host, proxy_port):
    """造出走 SOCKS5 的 HTTP/HTTPS 连接类。

    只替换"建连"这一步，不动 connect()——因为 HTTPSConnection.connect() 会在
    建连之后套一层 TLS。若直接重写 connect()，TLS 那步会被整个跳过，
    结果是拿明文 HTTP 去连 443 端口（服务器回 400 或直接断开）。
    """

    class _Mixin:
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # http.client 在 __init__ 里把建连函数挂成了实例属性，
            # 会盖住类属性，所以只能等 super().__init__() 跑完再换掉它。
            self._create_connection = self._socks_create_connection

        def _socks_create_connection(self, address, timeout=None, source_address=None):
            return _socks5_connect(proxy_host, proxy_port, address[0], address[1], timeout)

    class _HTTP(_Mixin, http.client.HTTPConnection):
        pass

    class _HTTPS(_Mixin, http.client.HTTPSConnection):
        pass

    return _HTTP, _HTTPS


class _SocksHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, conn_cls):
        super().__init__()
        self._conn_cls = conn_cls

    def http_open(self, req):
        return self.do_open(self._conn_cls, req)


class _SocksHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, conn_cls):
        super().__init__()  # 交给基类按当前 Python 版本建好默认 SSL context
        self._conn_cls = conn_cls

    def https_open(self, req):
        # 只传 context：3.13 的 HTTPSHandler 已经没有 _check_hostname 了，
        # 而旧版把它默认成 None 也是"不改动 context"，不传同样安全。
        return self.do_open(self._conn_cls, req, context=self._context)


def _build_opener(use_proxy):
    """按本次请求是否需要代理，构造独立的 opener。

    早先的实现是 `socket.socket = socks.socksocket`——全局且不可逆的猴子补丁：
    第一次用 use_proxy=True 之后，同进程里所有请求（含 use_proxy=False 的）都会
    被偷偷送去代理；而抓正文是并发跑的，改全局本身就是竞态。这里改成给连接类
    单独挂 SOCKS，互不干扰，也不碰全局状态。

    注意：http 和 https 必须是两个 handler。build_opener 只会跳过与传入 handler
    同类型的默认 handler，而 HTTPHandler 和 HTTPSHandler 是兄弟关系而非父子，
    只传一个的话，另一个的默认实现会排在前面把请求截走（等于没走代理）。
    """
    if use_proxy:
        http_cls, https_cls = _socks_conn_classes(PROXY_HOST, PROXY_PORT)
        return urllib.request.build_opener(
            _SocksHTTPHandler(http_cls), _SocksHTTPSHandler(https_cls))
    return urllib.request.build_opener()


def _fetch(url, use_proxy, timeout):
    """用 urllib 发起 GET，返回 (文本, HTTP 状态码)。

    状态码要单独带出来：搜索引擎被反爬拦截时不一定是 4xx——
    DuckDuckGo 是 202 + 拦截页，百度/搜狗是 200 + 验证页。
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7"},
    )
    opener = _build_opener(use_proxy)
    resp = opener.open(req, timeout=timeout)
    status = resp.status
    raw = resp.read()
    # 透明解压：个别站点会无视我们没声明 Accept-Encoding 直接压 gzip/deflate
    enc = (resp.headers.get("Content-Encoding") or "").lower()
    try:
        if "gzip" in enc:
            raw = gzip.decompress(raw)
        elif "deflate" in enc:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    except (OSError, zlib.error):
        pass
    return _decode_body(raw, resp.headers.get("Content-Type", "")), status


def _http_get(url, use_proxy, timeout):
    """只要正文的简化版，给 web_fetch / 抓正文用。"""
    return _fetch(url, use_proxy, timeout)[0]


# ----------------------------------------------------------------------------
# 引擎状态：节流 / 冷却 / 反爬检测 / 缓存 / 日志
#
# 无 Key 抓搜索引擎的软肋就在这儿：短时间连续请求会被判成自动化流量。
# 实测 Bing 被降级后不弹验证码，而是返回 HTTP 200 + 看似正常实为随机的填充结果；
# 搜狗打两三条就返回 5KB 的反爬页。所以必须自己节流、识别拦截、并让被拦的引擎
# 冷一段时间，而不是继续敲它。
# ----------------------------------------------------------------------------
class SearchUnavailable(RuntimeError):
    """所有引擎都被拦、或只肯给出填充结果时抛出。

    宁可明确报"没搜到"，也不把随机内容当搜索结果交出去 ——
    调用方不读 warning 的话，那些内容会被当成证据用。
    """


_engine_state = {}   # name -> {"last": 上次请求时间, "blocked_until": 解封时间, "reason": 被拦原因}
_search_cache = {}   # (engine, query, num) -> (过期时间, (结果, 等级))


def _log(msg):
    """记一条引擎尝试日志。写失败不影响搜索本身。"""
    if not LOG_FILE:
        return
    try:
        # 简单轮转：超过 2MB 就改名保留上一份，避免长期运行把磁盘写满
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 2 * 1024 * 1024:
            try:
                os.replace(LOG_FILE, LOG_FILE + ".1")
            except OSError:
                pass
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def _state_of(name):
    return _engine_state.setdefault(
        name, {"last": 0.0, "blocked_until": 0.0, "reason": ""}
    )


def _engine_wait(name):
    """按引擎级最小间隔让路；引擎在冷却中则返回 (False, 原因)。"""
    st = _state_of(name)
    now = time.time()
    if st["blocked_until"] > now:
        return False, f"冷却中，还需 {int(st['blocked_until'] - now)}s（{st['reason']}）"
    gap = st["last"] + ENGINE_MIN_INTERVAL - now
    if gap > 0:
        time.sleep(min(gap, ENGINE_MIN_INTERVAL))
    return True, ""


def _engine_done(name, blocked_reason=""):
    """记录一次引擎调用；被拦则让它进入冷却。"""
    st = _state_of(name)
    st["last"] = time.time()
    if blocked_reason:
        st["blocked_until"] = time.time() + ENGINE_COOLDOWN
        st["reason"] = blocked_reason


def _blocked_reason(status, text):
    """这次响应是不是反爬/拦截页？是则返回原因。"""
    if status in _BLOCK_STATUS:
        return f"HTTP {status}"
    head = text[:20000].lower()
    for marker in _BLOCK_MARKERS:
        if marker in head:
            return f"拦截页({marker})"
    return ""


def _cache_get(name, query, num):
    key = (name, query, num)
    item = _search_cache.get(key)
    if not item:
        return None
    if item[0] < time.time():
        _search_cache.pop(key, None)
        return None
    return item[1]


def _cache_put(name, query, num, value, ttl):
    if len(_search_cache) >= CACHE_MAX:
        now = time.time()
        for k in [k for k, v in _search_cache.items() if v[0] < now]:
            _search_cache.pop(k, None)
        if len(_search_cache) >= CACHE_MAX:
            for k in list(_search_cache)[: CACHE_MAX // 2]:
                _search_cache.pop(k, None)
    _search_cache[(name, query, num)] = (time.time() + ttl, value)


def _engine_status():
    now = time.time()
    parts = []
    for name, st in _engine_state.items():
        if st["blocked_until"] > now:
            parts.append(f"{name} 冷却中 {int(st['blocked_until'] - now)}s({st['reason']})")
        else:
            parts.append(f"{name} 可用")
    return "; ".join(parts) or "尚未调用任何引擎"


def _run_engine(name, query, num, fetch_fn, parse_fn):
    """跑一个引擎：缓存 → 节流 → 抓取 → 反爬检测 → 解析。

    返回解析出的结果列表；该引擎被跳过/被拦/出错时返回 None。
    结果定级与写缓存由调用方做（定级需要 query）。
    """
    cached = _cache_get(name, query, num)
    if cached is not None:
        _log(f"[{name}] 缓存命中 query={query!r} num={num} -> {len(cached[0])}条 {cached[1]}")
        return cached[0]

    ok, why = _engine_wait(name)
    if not ok:
        _log(f"[{name}] 跳过：{why}")
        return None

    try:
        text, status = fetch_fn()
    except Exception as e:
        _engine_done(name)
        _log(f"[{name}] 请求失败 {type(e).__name__}: {e}")
        return None

    blocked = _blocked_reason(status, text)
    if blocked:
        _engine_done(name, blocked)
        _log(f"[{name}] 被拦 {blocked} (HTTP {status}, {len(text)}B)")
        return None

    _engine_done(name)
    results = parse_fn(text, num)
    _log(f"[{name}] HTTP {status} {len(text)}B -> {len(results)}条 query={query!r}")
    return results


# ----------------------------------------------------------------------------
# 搜索引擎解析
# ----------------------------------------------------------------------------
def _unpack_bing_url(href):
    """解包 Bing 的 ck/a 跳转链接，提取真实 URL；非 ck 链接原样返回。"""
    if "bing.com/ck/a" not in href:
        return href
    m = re.search(r"[?&]u=([^&]+)", href)
    if not m:
        return href
    u = m.group(1)
    if u.startswith("a1"):
        u = u[2:]  # a1 是 base64url 前缀
    try:
        import base64
        pad = "=" * (-len(u) % 4)
        return base64.urlsafe_b64decode(u + pad).decode("utf-8", "replace")
    except Exception:
        return href


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
        url = unescape(am.group(1))  # 处理 &amp; 等实体
        url = _unpack_bing_url(url)      # 解包 ck/a 跳转链接
        if not url.startswith("http"):
            continue
        title = _clean(re.sub(r"<[^>]+>", "", am.group(2)))
        pm = re.search(r'class="[^"]*b_caption[^"]*"[^>]*>\s*<p[^>]*>(.*?)</p>', block, re.S)
        snippet = _clean(re.sub(r"<[^>]+>", "", pm.group(1))) if pm else ""
        results.append({"title": title, "url": url, "snippet": snippet, "engine": "bing"})
    return results


def _parse_ddg(html, num):
    """解析 DuckDuckGo 的无 JS 版端点。

    兜底引擎原本是 Google，但 Google 现在对无 JS 的抓取只返回空壳页
    （页面标题是 "Google Search"，正文里既没有结果链接也没有 <h3>），
    实测已经抓不到东西，故换成 DDG 的 html 端点。
    """
    results = []
    # 逐个 <a> 取属性再判断，不假设 class 和 href 谁先谁后（原正则假设了顺序）
    for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", html, re.S):
        if len(results) >= num:
            break
        attrs, inner = m.group(1), m.group(2)
        if "result__a" not in attrs:
            continue
        hm = re.search(r'href="([^"]+)"', attrs)
        if not hm:
            continue
        url = unescape(hm.group(1))
        # DDG 给的是 //duckduckgo.com/l/?uddg=<真实URL> 形式的跳转链接，解出来
        um = re.search(r"[?&]uddg=([^&]+)", url)
        if um:
            url = urllib.parse.unquote(um.group(1))
        if url.startswith("//"):
            url = "https:" + url
        if not url.startswith("http"):
            continue
        title = _clean(re.sub(r"<[^>]+>", "", inner))
        if not title:
            continue
        results.append({"title": title, "url": url, "snippet": "", "engine": "duckduckgo"})
    snips = [
        _clean(re.sub(r"<[^>]+>", "", m.group(2)))
        for m in re.finditer(r"<(?:a|div)\b([^>]*result__snippet[^>]*)>(.*?)</(?:a|div)>", html, re.S)
    ]
    for i, r in enumerate(results):
        if i < len(snips):
            r["snippet"] = snips[i]
    return results


def _parse_sogou(html, num):
    """解析搜狗网页搜索结果。

    搜狗是少数在国内能直连、且对技术类查询仍给出"沾边"结果的引擎
    （实测 RK3566 长尾查询能返回瑞芯微开发者社区的资料页），
    在 Bing 被降级、DDG 被硬封的时候是唯一的备用。代价是它很容易触发反爬，
    所以调用方必须配合节流与冷却。
    """
    results = []
    noise = ("相关搜索", "其他人还搜", "相关游戏", "相关软件", "反馈")
    for m in re.finditer(r"<h3[^>]*>(.*?)</h3>", html, re.S):
        if len(results) >= num:
            break
        block = m.group(1)
        am = re.search(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not am:
            continue
        title = _clean(re.sub(r"<[^>]+>", "", am.group(2)))
        if not title or any(n in title for n in noise):
            continue
        url = unescape(am.group(1))
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://www.sogou.com" + url
        if not url.startswith("http"):
            continue
        results.append({"title": title, "url": url, "snippet": "", "engine": "sogou"})
    return results


def _attach_content(results, fetch_top, content_chars, use_proxy):
    """对前 fetch_top 个结果并发抓取正文，附加 content 字段。抓取失败则 content 为空串。"""
    targets = [r for r in results[:fetch_top] if r.get("url", "").startswith("http")]

    def _fetch_one(r):
        try:
            html = _http_get(r["url"], use_proxy, 8)
            text = re.sub(r"\s+", " ", html_to_text(html)).strip()
            return text[:content_chars]
        except Exception:
            return ""

    if targets:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(targets), 3)) as ex:
            contents = list(ex.map(_fetch_one, targets))
        for r, c in zip(targets, contents):
            r["content"] = c
    for r in results:
        r.setdefault("content", "")
    return results


def _query_keywords(query):
    """抽出用于相关性判断的关键词：英文单词 + 中文词块。

    中文只取完整词块，**不展开 bigram**。bigram 会往分母里塞进"步编"这类
    永远匹配不上的碎片，把中文查询的得分稀释掉。实测（6 条正常查询 + 4 条
    已知拿到垃圾结果的查询）：带 bigram 时正常查询最低 0.14、垃圾最高 0.17，
    区间重叠，任何阈值都分不开；只取完整词块后是 0.25 vs 0.17，才分得开。
    为不损失中文子串匹配能力，匹配时对较长的中文词块放宽（见 _term_hit）。
    """
    kws = set()
    for w in re.findall(r"[A-Za-z0-9_]{2,}", query):
        w = w.lower()
        if w not in _STOPWORDS:
            kws.add(w)
    for run in re.findall(r"[一-鿿]{2,}", query):
        if run not in _STOPWORDS:
            kws.add(run)
    return kws


def _term_hit(term, hay):
    """判断单个关键词在文本里算不算命中。

    较长的中文词块允许"部分命中"：只要它的任一 bigram 出现在文本里就算 ——
    这样查询里的"异步编程"遇上只写了"异步"或"编程"的页面也能计上。
    分母仍按完整词块数算，所以不会被稀释。
    """
    if term in hay:
        return True
    if _CJK_RE.search(term) and len(term) >= 3:
        return any(term[i:i + 2] in hay for i in range(len(term) - 1))
    return False


def _relevance(results, query):
    """0.0~1.0：这批结果【中位数】命中查询关键词的比例。

    为什么取中位数而不是"最好那一条"：引擎给垃圾结果时的典型形态是
    "一条沾边 + 其余全不沾"。只看最好的那条，单独一条就能把整批垃圾掩盖过去
    —— 实测 "SolidWorks Wine CrossOver Linux 2026 compatibility" 被 Bing
    降级成官网首页 + B站教程，最好那条仍有 0.33，跟结果完全正确的
    "python asyncio tutorial" 数值一模一样，任何阈值都分不开。
    换成中位数后两者分别是 0.17 和 0.33，才分得开。
    """
    kws = _query_keywords(query)
    if not kws:
        return 1.0  # 抽不出关键词就无从判断，一律放行，避免误杀
    if not results:
        return 0.0
    per = sorted(
        (sum(1 for k in kws if _term_hit(k, f"{r.get('title', '')} {r.get('snippet', '')}".lower()))
         / len(kws)
         for r in results),
        reverse=True,
    )
    return per[len(per) // 2]


def _missed_terms(results, query):
    """整批结果里一次都没出现过的查询实词个数，以及实词总数。

    用"整批的并集"而不是"某一条"，所以结论不随 num_results 变化。
    """
    kws = _query_keywords(query)
    if not kws:
        return 0, 0
    hays = [f"{r.get('title', '')} {r.get('snippet', '')}".lower() for r in results]
    hit = sum(1 for k in kws if any(_term_hit(k, h) for h in hays))
    return len(kws) - hit, len(kws)


def _is_junk(results, query):
    """这批结果是不是"引擎没认真对待查询"的产物。

    两条判据取或，各自覆盖对方的盲区：

    1. 中位数覆盖低于阈值 —— 抓"典型结果就是垃圾"的情况（"一条沾边 + 其余全不沾"）。
       但它随 num_results 的奇偶浮动，单用会漏（见 RELEVANCE_FLOOR 处的实测）。
    2. 查询实词够多、却几乎一个都没在结果里出现过 —— 抓"引擎只认其中一个词、
       把其余限定词全丢掉"的情况。这条不受条数影响。

    实测样本（6 条正常 + 4 条已知垃圾）两条合用全部判对。
    """
    if not results:
        return True
    if _relevance(results, query) < RELEVANCE_FLOOR:
        return True
    missed, total = _missed_terms(results, query)
    return total >= MIN_TERMS_FOR_COVERAGE_RULE and missed >= MAX_MISSED_TERMS


def _level_of(results, query):
    """把一批结果分成 good / partial / junk 三级。

    junk 的判据是"整批结果里一个查询关键词都没出现"—— 这正是引擎被降级时
    塞进来的填充结果的特征（查 ED047TC2 返回 BOSS直聘、查乱码返回小游戏）。
    只要命中了主词、只是没覆盖限定条件，就算 partial：对长尾调研仍然有用，
    但不能假装它把问题答全了。
    """
    if not results:
        return LEVEL_JUNK
    kws = _query_keywords(query)
    if not kws:
        return LEVEL_GOOD
    hays = [f"{r.get('title', '')} {r.get('snippet', '')}".lower() for r in results]
    if not any(any(_term_hit(k, h) for h in hays) for k in kws):
        return LEVEL_JUNK
    return LEVEL_PARTIAL if _is_junk(results, query) else LEVEL_GOOD


def _bing_fetch(q, num, use_proxy):
    return _fetch(f"https://www.bing.com/search?q={q}&count={num}", use_proxy, 15)


def _sogou_fetch(q, num):
    return _fetch(f"https://www.sogou.com/web?query={q}", False, 15)


def _ddg_fetch(q, num):
    # 超时给得短：这是"尽力而为"的兜底，代理不通时（端口在听但出口挂了是最常见
    # 的坏法）不该让用户为了一个大概率要失败的第二引擎多等 20 秒。
    return _fetch(f"https://html.duckduckgo.com/html/?q={q}", True, 8)


def web_search(query, num_results, use_proxy, fetch_content=True, fetch_top=3, content_chars=1500):
    num = max(1, min(int(num_results), 15))
    q = urllib.parse.quote(query)
    attempts = []  # [(等级, 相关性得分, 结果, 是否走代理, 引擎名)]

    def _collect(engine, results, via_proxy):
        if not results:
            return
        level = _level_of(results, query)
        score = _relevance(results, query)
        attempts.append((level, score, results, via_proxy, engine))
        # 全垃圾的结果只做短期负缓存，避免重复查询反复去打已经降级的引擎
        ttl = CACHE_NEG_TTL if level == LEVEL_JUNK else CACHE_TTL
        _cache_put(engine, query, num, (results, level), ttl)
        _log(f"[{engine}] 定级={level} 相关性={score:.2f} {len(results)}条 query={query!r}")

    def _best(level):
        hits = [a for a in attempts if a[0] == level]
        return max(hits, key=lambda a: a[1]) if hits else None

    def _finish(choice):
        level, _, best, via_proxy, _ = choice
        best = [dict(r) for r in best]  # 别把 warning/relevance 写回缓存里那份
        for r in best:
            r["relevance"] = level
        if fetch_content:
            return _attach_content(best, fetch_top, content_chars, via_proxy)
        return best

    def _try(name, fetch_fn, parse_fn, via_proxy):
        results = _run_engine(name, query, num, fetch_fn, parse_fn)
        _collect(name, results, via_proxy)

    # 直连和代理是两条不同的网络路径，缓存/节流状态要分开记
    bing_key = "bing-proxy" if use_proxy else "bing"

    # 1) Bing —— 主引擎
    _try(bing_key, lambda: _bing_fetch(q, num, use_proxy), _parse_bing, use_proxy)
    if _best(LEVEL_GOOD):
        return _finish(_best(LEVEL_GOOD))

    # 2) 搜狗 —— 国内直连、不需要代理。Bing 被降级时它是主要的替补。
    _try("sogou", lambda: _sogou_fetch(q, num), _parse_sogou, False)
    if _best(LEVEL_GOOD):
        return _finish(_best(LEVEL_GOOD))

    # 3) DuckDuckGo（必须走代理）。兜底不受 use_proxy 限制 —— "换个引擎看看"
    #    不该被主引擎的线路选择挡住。代理不通或被拦就快速跳过。
    _try("ddg", lambda: _ddg_fetch(q, num), _parse_ddg, True)

    good = _best(LEVEL_GOOD)
    if good:
        return _finish(good)

    # 要了代理却一条都没跑通（代理没开/出口挂了）：直连 Bing 再试一次并说明
    if not attempts and use_proxy:
        _try("bing", lambda: _bing_fetch(q, num, False), _parse_bing, False)
        if attempts:
            out = _finish(attempts[0])
            for r in out:
                r.setdefault("warning", WARNING_NO_PROXY)
            return out

    partial = _best(LEVEL_PARTIAL)
    if partial:
        # 只命中了主词、没覆盖限定条件：可以给，但必须标明
        out = _finish(partial)
        for r in out:
            r["warning"] = WARNING_PARTIAL
        return out

    # 全是 junk（或一个引擎都没跑通）：明确失败。
    # 绝不把"看似正常、实际随机"的填充内容当搜索结果交出去 ——
    # 调用方只要不读 warning，就会拿它当证据。
    raise SearchUnavailable(
        f"没有搜索引擎给出与 {query!r} 相关的结果，已放弃返回填充内容。"
        f"引擎状态：{_engine_status()}。"
        "可以稍后再试、简化查询，或者直接用 web_fetch 抓已知页面。"
    )


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
    """把 JSON-RPC 响应以 UTF-8 字节直接写进 stdout。

    MCP stdio 协议规定 UTF-8。中文 Windows 上 sys.stdout 默认是 cp936，
    直接走文本层会把中文按 GBK 发出去，客户端按 UTF-8 读就是乱码，
    故这里绕过文本层，往底层二进制缓冲写。
    """
    obj = _strip_surrogates(obj)  # 协议层输出同样清理，防止毒 OOM 整个服务器
    try:
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
    except (UnicodeEncodeError, ValueError):
        data = (json.dumps(obj, ensure_ascii=True) + "\n").encode("ascii")  # 兜底
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _strip_surrogates(o):
    """递归清洗：丢弃 UTF-16 孤立代理字符（U+D800–U+DFFF），防止 json.dumps 编码报错。

    搜到的网页内容偶发含损坏的半个 emoji/代理片段，直接序列化会抛
    'utf-8' codec can't encode ... surrogates not allowed，故在输出前统一清理。
    """
    def _clean_str(s):
        try:
            # 先按 ascii 编一次能发现孤立代理？不可靠；直接逐字符过滤最稳
            return "".join(c for c in s if not 0xD800 <= ord(c) <= 0xDFFF)
        except Exception:
            return s
    if isinstance(o, str):
        return _clean_str(o)
    if isinstance(o, list):
        return [_strip_surrogates(x) for x in o]
    if isinstance(o, dict):
        return {k: _strip_surrogates(v) for k, v in o.items()}
    return o


def _call_tool(name, args):
    if name == "web_search":
        return web_search(
            args.get("query", ""),
            args.get("num_results", 8),
            args.get("use_proxy", True),
            args.get("fetch_content", True),
            args.get("fetch_top", 3),
            args.get("content_chars", 1500),
        )
    if name == "web_fetch":
        return web_fetch(
            args.get("url", ""),
            args.get("timeout_s", 20),
            args.get("use_proxy", True),
        )
    raise ValueError(f"未知工具: {name}")


def main():
    # 直接读二进制行并按 UTF-8 解码：中文 Windows 上 sys.stdin 默认是 cp936，
    # 会把客户端发来的 UTF-8 中文查询解成乱码，查询里的中文随之报废。
    for raw_line in sys.stdin.buffer:
        line = raw_line.decode("utf-8", "replace").strip()
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
                result = _strip_surrogates(result)
                try:
                    text = json.dumps(result, ensure_ascii=False, indent=2) if isinstance(result, (list, dict)) else str(result)
                except (UnicodeEncodeError, ValueError):
                    text = json.dumps(result, ensure_ascii=True, indent=2)  # 终极兜底：全转 \uXXXX
                content = [{"type": "text", "text": text}]
                _send({"jsonrpc": "2.0", "id": mid, "result": {"content": content, "isError": False}})
            except Exception as e:
                _send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": f"[error] {type(e).__name__}: {e}"}],
                    "isError": True}})
        elif method == "ping":
            _send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method and method.startswith("notifications/"):
            pass
        elif method is None:
            pass  # response to our request, ignore


if __name__ == "__main__":
    main()
