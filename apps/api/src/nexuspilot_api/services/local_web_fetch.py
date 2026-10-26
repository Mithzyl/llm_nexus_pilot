"""Bounded anonymous public HTTP reads with checked-IP connections and basic HTML text extraction.

No browser JavaScript, cookies, credentials, full-page persistence or content hashes are used.
Only the calling web tool decides whether an ordinary failure may fall back to Tavily.
"""

import asyncio
import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from nexuspilot_api.services.agent_file_tool_service import ToolOperationError

IGNORED_TAGS = {"head", "script", "style", "nav", "footer", "aside", "form", "template", "svg"}
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "main",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "pre",
    "tr",
    "ul",
    "ol",
    "blockquote",
}


class PageTextParser(HTMLParser):
    """Extract title and readable text, preferring main/article while excluding non-content tags."""

    def __init__(self):
        """Keep bounded response-sized buffers and parsing state; no resources are fetched."""
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.title_open = False
        self.ignored = []
        self.main_depth = 0
        self.body_parts = []
        self.main_parts = []

    def append(self, value):
        """Retain layout separators without flattening code indentation into a single line."""
        self.body_parts.append(value)
        if self.main_depth:
            self.main_parts.append(value)

    def handle_starttag(self, tag, attrs):
        """Track hidden/non-content subtrees and add simple paragraph/list/table separators."""
        if tag == "title":
            self.title_open = True
        hidden = any(k == "hidden" or (k == "aria-hidden" and v == "true") for k, v in attrs)
        if self.ignored or tag in IGNORED_TAGS or hidden:
            if tag not in VOID_TAGS:
                self.ignored.append(tag)
            return
        if tag in {"main", "article"}:
            self.main_depth += 1
        if tag in BLOCK_TAGS or tag in {"br", "hr"}:
            self.append("\n")
        elif tag == "li":
            self.append("\n- ")
        elif tag in {"td", "th"}:
            self.append("\t")

    def handle_endtag(self, tag):
        """Close parser state without accepting malformed hidden tags as visible content."""
        if tag == "title":
            self.title_open = False
        if self.ignored:
            if tag in self.ignored:
                position = len(self.ignored) - 1 - self.ignored[::-1].index(tag)
                del self.ignored[position:]
            return
        if tag in BLOCK_TAGS or tag == "li":
            self.append("\n")
        if tag in {"main", "article"}:
            self.main_depth = max(0, self.main_depth - 1)

    def handle_data(self, data):
        """Keep title separately; ignored script/style and navigation text never enters output."""
        if self.title_open:
            self.title_parts.append(data)
        elif not self.ignored:
            self.append(data)

    def result(self):
        """Return basic extracted text, without claiming dynamic or complete page coverage."""
        content = "".join(self.main_parts).strip() or "".join(self.body_parts).strip()
        content = "\n".join(line.rstrip() for line in content.splitlines() if line.strip())
        return {"title": " ".join("".join(self.title_parts).split())[:4096], "content": content}


async def resolve_addresses(host, port):
    """Resolve once per hop; the caller validates every answer and connects to an exact IP."""
    addresses = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(item[4][0] for item in addresses))


class LocalWebAdapter:
    """Fetch anonymous public pages on the Nexus host without a second DNS lookup at connect."""

    def __init__(self, *, resolver=resolve_addresses, transport=None):
        """Allow test-only injection; production uses the checked IP and verified TLS."""
        self.resolver = resolver
        self.transport = transport

    async def fetch(self, arguments):
        """Follow at most three checked redirects within 30 seconds and return extracted text."""
        from nexuspilot_api.services.web_tools import MAX_BODY_BYTES, public_url

        current = arguments.url
        try:
            async with asyncio.timeout(30):
                async with httpx.AsyncClient(
                    transport=self.transport,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=30,
                    limits=httpx.Limits(max_keepalive_connections=0),
                ) as client:
                    for hop in range(4):
                        try:
                            public_url(current)
                            url = httpx.URL(current)
                        except (ValueError, httpx.InvalidURL) as exc:
                            raise ToolOperationError(
                                "web_private_target", status="permission_denied"
                            ) from exc
                        addresses = await self.resolver(
                            url.host, url.port or (443 if url.scheme == "https" else 80)
                        )
                        if not addresses or any(
                            not ipaddress.ip_address(ip).is_global for ip in addresses
                        ):
                            raise ToolOperationError(
                                "web_private_target", status="permission_denied"
                            )
                        # Keep the public host for TLS verification and HTTP virtual hosting, but
                        # connect to the validated IP. No proxy or later DNS rebinding is involved.
                        pinned = url.copy_with(host=addresses[0])
                        client.cookies.clear()
                        async with client.stream(
                            "GET",
                            pinned,
                            headers={
                                "Host": url.netloc.decode("ascii"),
                                "Connection": "close",
                                "User-Agent": "NexusPilot/0.8 public-page-reader",
                                "Accept": "text/html,application/xhtml+xml,"
                                "text/plain,text/markdown",
                            },
                            extensions={"sni_hostname": url.host},
                        ) as response:
                            if response.status_code in {301, 302, 303, 307, 308}:
                                if hop == 3:
                                    raise ToolOperationError("local_redirect_limit")
                                location = response.headers.get("location")
                                if not location:
                                    raise ToolOperationError("local_invalid_redirect")
                                current = urljoin(current, location)
                                continue
                            if response.status_code != 200:
                                raise ToolOperationError(f"local_http_{response.status_code}")
                            content_type = (
                                response.headers.get("content-type", "").split(";", 1)[0].lower()
                            )
                            if content_type not in {
                                "text/html",
                                "application/xhtml+xml",
                                "text/plain",
                                "text/markdown",
                            }:
                                raise ToolOperationError("local_unsupported_content")
                            body = bytearray()
                            async for chunk in response.aiter_bytes():
                                body.extend(chunk)
                                if len(body) > MAX_BODY_BYTES:
                                    raise ToolOperationError("local_response_too_large")
                            text = body.decode(response.encoding or "utf-8", errors="replace")
                            if content_type in {"text/html", "application/xhtml+xml"}:
                                parser = PageTextParser()
                                parser.feed(text)
                                parser.close()
                                result = parser.result()
                            else:
                                result = {"title": "", "content": text.strip()}
                            if not result["content"]:
                                raise ToolOperationError("local_empty_content")
                            return {
                                **result,
                                "final_url": current,
                                "usage": None,
                                "extraction_method": "local_static_text",
                                "javascript_executed": False,
                            }
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ToolOperationError("local_timeout", status="timed_out") from exc
        except (httpx.RequestError, OSError) as exc:
            raise ToolOperationError("local_connection_failed") from exc
        except (ValueError, LookupError) as exc:
            raise ToolOperationError("local_invalid_content") from exc
