from io import BytesIO

import pytest

from app import web_source
from app.web_source import PublicWebSourceError


class FakeResponse:
    def __init__(self, status, body=b"", headers=None):
        self.status = status
        self.body = BytesIO(body)
        self.headers = headers or {}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, size=-1):
        return self.body.read(size)


class FakeConnection:
    sock = None

    def close(self):
        pass


def test_private_web_destinations_are_rejected_before_request(monkeypatch):
    def unexpected_connection(*_args, **_kwargs):
        raise AssertionError("private destination must be rejected before connecting")

    monkeypatch.setattr(web_source.socket, "create_connection", unexpected_connection)
    for url in [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "ftp://public.example/file",
        "http://user:password@public.example/",
        "http://public.example:8080/",
    ]:
        with pytest.raises(PublicWebSourceError):
            web_source._request(url)


def test_mixed_public_and_private_dns_answers_are_rejected(monkeypatch):
    monkeypatch.setattr(
        web_source.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 80)),
            (2, 1, 6, "", ("10.0.0.2", 80)),
        ],
    )
    with pytest.raises(PublicWebSourceError, match="非公开网络地址"):
        web_source._validate_and_resolve("http://public.example/")


def test_public_ipv6_resolution_preserves_hostname_and_rejects_port_zero(monkeypatch):
    monkeypatch.setattr(
        web_source.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (10, 1, 6, "", ("2001:4860:4860::8888", 443, 0, 0)),
        ],
    )
    normalized, host, port, address = web_source._validate_and_resolve(
        "https://public.example/page"
    )
    assert normalized == "https://public.example/page"
    assert host == "public.example"
    assert port == 443
    assert address == "2001:4860:4860::8888"
    with pytest.raises(PublicWebSourceError):
        web_source._validate_and_resolve("http://public.example:0/")


def test_redirect_target_is_validated_again_before_connection(monkeypatch):
    original_request = web_source._request

    def redirect_response(url):
        if url == "https://public.example/start":
            return (
                url,
                FakeResponse(302, headers={"Location": "http://127.0.0.1/admin"}),
                FakeConnection(),
            )
        return original_request(url)

    def unexpected_connection(*_args, **_kwargs):
        raise AssertionError("redirect to a private destination must not connect")

    monkeypatch.setattr(web_source, "_request", redirect_response)
    monkeypatch.setattr(web_source.socket, "create_connection", unexpected_connection)
    with pytest.raises(PublicWebSourceError):
        web_source.fetch_public_page("https://public.example/start")

    redirects = [
        ("https://public.example/start", "https://public.example/secure"),
        ("https://public.example/secure", "http://public.example/plain"),
    ]

    def downgrade_response(_url):
        source_url, target_url = redirects.pop(0)
        return (
            source_url,
            FakeResponse(302, headers={"Location": target_url}),
            FakeConnection(),
        )

    monkeypatch.setattr(web_source, "_request", downgrade_response)
    with pytest.raises(PublicWebSourceError):
        web_source.fetch_public_page("https://public.example/start")
    assert len(redirects) == 0


def test_fetch_public_page_extracts_visible_html_and_bounds_response(monkeypatch):
    response = FakeResponse(
        200,
        b"<html><head><title>History page</title><script>secret()</script></head>"
        b"<body><h1>Learning</h1><p>Useful content.</p></body></html>",
        {"Content-Type": "text/html; charset=utf-8", "Content-Length": "142"},
    )
    monkeypatch.setattr(
        web_source,
        "_request",
        lambda _url: ("https://public.example/page", response, FakeConnection()),
    )

    page = web_source.fetch_public_page("https://public.example/page")

    assert page.title == "History page"
    assert "Learning" in page.text and "Useful content." in page.text
    assert "secret" not in page.text
    assert page.url == "https://public.example/page"

    oversized = FakeResponse(
        200,
        b"",
        {
            "Content-Type": "text/html",
            "Content-Length": str(web_source.MAX_WEB_RESPONSE_BYTES + 1),
        },
    )
    monkeypatch.setattr(
        web_source,
        "_request",
        lambda _url: ("https://public.example/page", oversized, FakeConnection()),
    )
    with pytest.raises(PublicWebSourceError):
        web_source.fetch_public_page("https://public.example/page")
