import time
import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

MAX_WEB_URL_LENGTH = 2048
MAX_WEB_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
WEB_TIMEOUT_SECONDS = 10
ALLOWED_PORTS = {"http": 80, "https": 443}
IGNORED_TAGS = {"head", "script", "style", "noscript", "svg", "iframe", "canvas"}
BLOCK_TAGS = {"article", "br", "div", "h1", "h2", "h3", "li", "p", "section", "tr"}


class PublicWebSourceError(ValueError):
    pass


@dataclass(frozen=True)
class WebPage:
    url: str
    title: str
    text: str
    content: bytes
    content_type: str


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str):
        super().__init__(host, port, timeout=WEB_TIMEOUT_SECONDS)
        self.address = address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self.address, self.port),
            timeout=self.timeout,
            source_address=self.source_address,
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str):
        super().__init__(
            host,
            port,
            timeout=WEB_TIMEOUT_SECONDS,
            context=ssl.create_default_context(),
        )
        self.address = address

    def connect(self) -> None:
        sock = socket.create_connection(
            (self.address, self.port),
            timeout=self.timeout,
            source_address=self.source_address,
        )
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except (OSError, ssl.SSLError):
            sock.close()
            raise


class _VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._ignored_tags: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in IGNORED_TAGS:
            self._ignored_tags.append(tag)
        elif tag == "title":
            self._in_title = True
        elif not self._ignored_tags and tag in BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in self._ignored_tags:
            index = len(self._ignored_tags) - 1 - self._ignored_tags[::-1].index(tag)
            del self._ignored_tags[index:]
        elif not self._ignored_tags and tag in BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if not self._ignored_tags:
            self.text_parts.append(data)


def _validate_and_resolve(url: str) -> tuple[str, str, int, str]:
    if len(url) > MAX_WEB_URL_LENGTH or any(ord(char) < 32 for char in url):
        raise PublicWebSourceError("网页链接长度或格式无效。")
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError as exc:
        raise PublicWebSourceError("网页链接格式无效。") from exc

    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_PORTS or parsed.username is not None or parsed.password is not None:
        raise PublicWebSourceError("仅支持不含登录凭证的公开 HTTP/HTTPS 网页链接。")
    if not parsed.hostname:
        raise PublicWebSourceError("网页链接缺少有效的主机名。")
    port = port if port is not None else ALLOWED_PORTS[scheme]
    if port != ALLOWED_PORTS[scheme]:
        raise PublicWebSourceError("网页链接仅支持 HTTP 80 或 HTTPS 443 端口。")

    try:
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            resolved = socket.getaddrinfo(
                host,
                ALLOWED_PORTS[scheme],
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise PublicWebSourceError("无法解析网页主机名。") from exc
        addresses = {entry[4][0].split("%", 1)[0] for entry in resolved}
        if not addresses:
            raise PublicWebSourceError("网页主机名没有可用地址。")
        try:
            parsed_addresses = {ipaddress.ip_address(value) for value in addresses}
        except ValueError as exc:
            raise PublicWebSourceError("网页主机名解析结果无效。") from exc
        if any(not item.is_global for item in parsed_addresses):
            raise PublicWebSourceError("网页链接解析到非公开网络地址，已拒绝访问。")
        address = sorted(parsed_addresses, key=lambda item: (item.version, int(item)))[0]

    if not address.is_global:
        raise PublicWebSourceError("网页链接解析到非公开网络地址，已拒绝访问。")

    netloc_host = f"[{host}]" if ":" in host else host
    netloc = netloc_host if port == ALLOWED_PORTS[scheme] else f"{netloc_host}:{port}"
    path = parsed.path or "/"
    normalized = urlunsplit((scheme, netloc, path, parsed.query, ""))
    return normalized, host, port, str(address)


def _request(
    url: str,
) -> tuple[str, http.client.HTTPResponse, http.client.HTTPConnection]:
    normalized, host, port, address = _validate_and_resolve(url)
    parsed = urlsplit(normalized)
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parsed.query:
        path += "?" + quote(parsed.query, safe="=&?/:;+,%@!$'()*-._~")
    connection_type = _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
    connection = connection_type(host, port, address)
    try:
        connection.request(
            "GET",
            path,
            headers={
                "Accept": "text/html, application/xhtml+xml, text/plain;q=0.9",
                "Accept-Encoding": "identity",
                "Connection": "close",
                "Host": parsed.netloc,
                "User-Agent": "LearningMachineImport/1.0",
            },
        )
        response = connection.getresponse()
        return normalized, response, connection
    except (OSError, http.client.HTTPException, ValueError):
        connection.close()
        raise


def _read_bounded(
    response: http.client.HTTPResponse,
    connection: http.client.HTTPConnection,
) -> bytes:
    content_length = response.getheader("Content-Length")
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise PublicWebSourceError("网页响应长度无效。") from exc
        if declared_length > MAX_WEB_RESPONSE_BYTES:
            raise PublicWebSourceError("网页内容超过 5 MB 限制。")

    chunks: list[bytes] = []
    total = 0
    deadline = time.monotonic() + 30
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PublicWebSourceError("读取网页超过 30 秒，已停止导入。")
        if connection.sock is not None:
            connection.sock.settimeout(min(remaining, WEB_TIMEOUT_SECONDS))
        chunk = response.read(min(64 * 1024, MAX_WEB_RESPONSE_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_WEB_RESPONSE_BYTES:
            raise PublicWebSourceError("网页内容超过 5 MB 限制。")
    return b"".join(chunks)


def fetch_public_page(url: str) -> WebPage:
    current_url = url.strip()
    used_https = urlsplit(current_url).scheme.lower() == "https"
    for redirect_count in range(MAX_REDIRECTS + 1):
        try:
            normalized, response, connection = _request(current_url)
        except (OSError, http.client.HTTPException) as exc:
            raise PublicWebSourceError("读取公开网页失败，请检查链接后重试。") from exc
        try:
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location or redirect_count == MAX_REDIRECTS:
                    raise PublicWebSourceError("网页重定向次数过多或目标无效。")
                next_url = urljoin(normalized, location)
                next_scheme = urlsplit(next_url).scheme.lower()
                if used_https and next_scheme != "https":
                    raise PublicWebSourceError("网页重定向降级到不安全的 HTTP，已拒绝访问。")
                used_https = used_https or next_scheme == "https"
                current_url = next_url
                continue
            if response.status != 200:
                raise PublicWebSourceError(f"网页返回 HTTP {response.status}，无法导入。")

            content_type = response.getheader("Content-Type", "text/html").split(";", 1)[0].strip().lower()
            if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                raise PublicWebSourceError("链接内容不是可读取的 HTML 或纯文本网页。")
            content_encoding = response.getheader("Content-Encoding", "identity").lower()
            if content_encoding not in {"", "identity"}:
                raise PublicWebSourceError("网页使用了不支持的压缩编码。")
            content = _read_bounded(response, connection)
            charset = "utf-8"
            for parameter in response.getheader("Content-Type", "").split(";")[1:]:
                name, separator, value = parameter.strip().partition("=")
                if separator and name.lower() == "charset":
                    charset = value.strip(" \"'")
                    break
            try:
                decoded = content.decode(charset, errors="replace")
            except LookupError:
                decoded = content.decode("utf-8", errors="replace")

            if content_type == "text/plain":
                title = urlsplit(normalized).hostname or "网页文本"
                text = decoded.strip()
            else:
                parser = _VisibleTextParser()
                parser.feed(decoded)
                title = " ".join(" ".join(parser.title_parts).split())
                text = "\n".join(
                    line.strip()
                    for line in "".join(parser.text_parts).splitlines()
                    if line.strip()
                )
                title = title or urlsplit(normalized).hostname or "网页"
            if not text:
                raise PublicWebSourceError("网页中没有提取到可用的正文文本。")
            return WebPage(
                url=url.strip(),
                title=title[:255],
                text=text,
                content=content,
                content_type=content_type,
            )
        except (OSError, http.client.HTTPException) as exc:
            raise PublicWebSourceError("读取公开网页失败或超时，请检查链接后重试。") from exc
        finally:
            connection.close()
    raise PublicWebSourceError("网页重定向次数过多。")
