"""Work out where a request came from, using request headers only.

Order matters: browser signals are the hardest to fake, so they win; then
official SDK headers; then known tools; then generic HTTP libraries.
Every label is a hint, never proof: any client can set any User-Agent.
"""
import re

_SDK = [
    (r"^OpenAI/Python", "OpenAI SDK (Python)"),
    (r"^OpenAI/JS", "OpenAI SDK (Node)"),
    (r"^Groq/Python", "Groq SDK (Python)"),
    (r"^Groq/JS", "Groq SDK (Node)"),
    (r"^Anthropic/Python", "Anthropic SDK (Python)"),
    (r"^Anthropic/JS", "Anthropic SDK (Node)"),
    (r"ai-sdk/", "Vercel AI SDK"),
    (r"langchain", "LangChain"),
    (r"litellm", "LiteLLM"),
]
_CLI = [
    (r"^curl/", "curl"),
    (r"^HTTPie/", "HTTPie"),
    (r"^Wget/", "wget"),
    (r"^PostmanRuntime/", "Postman"),
    (r"^insomnia/", "Insomnia"),
]
_LIB = [
    (r"^python-requests/", "python requests"),
    (r"^python-httpx/", "python httpx"),
    (r"aiohttp/", "python aiohttp"),
    (r"^axios/", "axios"),
    (r"^node-fetch|^undici|^node$", "Node fetch"),
    (r"^Go-http-client/", "Go net/http"),
    (r"^okhttp/", "OkHttp"),
]


def _match(table, ua):
    for pattern, label in table:
        if re.search(pattern, ua, re.I):
            return label
    return None


def classify(headers: dict) -> tuple[str, str]:
    """Return (source_class, detail). Classes: browser, sdk, cli, script, ide, unknown."""
    h = {k.lower(): v for k, v in headers.items()}
    ua = h.get("user-agent", "")
    runtime = h.get("x-stainless-runtime", "")
    lang = h.get("x-stainless-lang", "")
    os_name = h.get("x-stainless-os", "")

    # Node's built-in fetch (undici) also sends sec-fetch-mode, so require a browser UA too.
    if runtime.startswith("browser") or (
        ua.startswith("Mozilla/") and (h.get("sec-fetch-mode") or h.get("origin"))
    ):
        browser = _browser_name(h.get("sec-ch-ua", ""), ua)
        where = _site(h.get("origin") or h.get("referer")) or "unknown origin"
        via = " via SDK" if lang else ""
        return "browser", f"{browser}{via} · {where}"

    sdk = _match(_SDK, ua)
    if sdk or lang:
        label = sdk or f"Stainless SDK ({lang})"
        extra = " · ".join(x for x in (runtime and f"{runtime} {h.get('x-stainless-runtime-version', '')}".strip(), os_name) if x)
        return "sdk", f"{label}{' · ' + extra if extra else ''}"

    if h.get("x-title") or h.get("http-referer"):
        return "ide", h.get("x-title") or h.get("http-referer")

    cli = _match(_CLI, ua)
    if cli:
        return "cli", cli

    lib = _match(_LIB, ua)
    if lib:
        return "script", lib

    return "unknown", ua[:60] or "no user-agent"


def _site(url: str | None) -> str | None:
    """Scheme and host only, so no page paths or query strings are stored."""
    if not url:
        return None
    m = re.match(r"^([a-z][a-z0-9+.-]*://[^/?#]+)", url.strip(), re.I)
    return m.group(1)[:120] if m else url[:60]


def _browser_name(sec_ch_ua: str, ua: str) -> str:
    for name in ("Edg", "Brave", "Chrome", "Firefox", "Safari"):
        if name.lower() in sec_ch_ua.lower() or name in ua:
            return {"Edg": "Edge"}.get(name, name)
    return "Browser"
