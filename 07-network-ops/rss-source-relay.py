#!/usr/bin/env python3
"""Small, allow-listed RSS egress relay for sources blocked from mainland China."""

from __future__ import annotations

import base64
import hashlib
import http.client
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import tempfile
import threading
import time
import urllib.request
from urllib.parse import urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET


LISTEN = ("127.0.0.1", 8091)
CACHE_DIR = Path("/var/lib/rss-source-relay")
FRESH_SECONDS = 300
STALE_SECONDS = 172800
MAX_BYTES = 5 * 1024 * 1024
MAX_PAGE_BYTES = 5 * 1024 * 1024
PAGE_FRESH_SECONDS = 3600
PAGE_PREFIX = "/page/"
PAGE_HOSTS = {"huggingface.co", "www.huggingface.co"}
PAGE_TOKEN = re.compile(r"[A-Za-z0-9_-]{8,5600}")
DNS_PATH = "/dns-overrides.json"
SOURCES = {
    "/huggingface.xml": "https://huggingface.co/blog/feed.xml",
    "/occ.xml": "https://www.occ.gov/rss/occ_news.xml",
    "/oecd.xml": "https://wp.oecd.ai/feed/",
    "/rbi.xml": "https://rbi.org.in/pressreleases_rss.xml",
    "/hackernews-show.xml": "https://news.ycombinator.com/showrss",
    "/github-freshrss.xml": "https://github.com/FreshRSS/FreshRSS/releases.atom",
    "/github-a2a.xml": "https://github.com/a2aproject/A2A/releases.atom",
    "/github-mcp.xml": "https://github.com/modelcontextprotocol/modelcontextprotocol/releases.atom",
    "/github-windmill.xml": "https://github.com/windmill-labs/windmill/releases.atom",
    "/github-openclaw.xml": "https://github.com/openclaw/openclaw/releases.atom",
    "/github-dify.xml": "https://github.com/langgenius/dify/releases.atom",
    "/github-n8n.xml": "https://github.com/n8n-io/n8n/releases.atom",
    "/github-immich.xml": "https://github.com/immich-app/immich/releases.atom",
    "/github-portainer.xml": "https://github.com/portainer/portainer/releases.atom",
    "/github-traefik.xml": "https://github.com/traefik/traefik/releases.atom",
    "/github-caddy.xml": "https://github.com/caddyserver/caddy/releases.atom",
}
LOCKS = {path: threading.Lock() for path in SOURCES}
PAGE_LOCKS: dict[str, threading.Lock] = {}
PAGE_LOCKS_GUARD = threading.Lock()
DNS_LOCK = threading.Lock()
TLS = ssl.create_default_context()
DNS_NETWORKS = {
    "news.ycombinator.com": (ipaddress.ip_network("209.216.230.0/24"),),
    "huggingface.co": (
        ipaddress.ip_network("13.32.0.0/15"),
        ipaddress.ip_network("13.35.0.0/16"),
        ipaddress.ip_network("13.224.0.0/14"),
        ipaddress.ip_network("18.64.0.0/14"),
        ipaddress.ip_network("52.84.0.0/15"),
        ipaddress.ip_network("54.182.0.0/16"),
        ipaddress.ip_network("54.192.0.0/16"),
        ipaddress.ip_network("99.84.0.0/16"),
        ipaddress.ip_network("130.176.0.0/16"),
        ipaddress.ip_network("143.204.0.0/16"),
        ipaddress.ip_network("204.246.164.0/22"),
    ),
    "www.occ.gov": (ipaddress.ip_network("199.83.40.0/24"),),
    "wp.oecd.ai": (ipaddress.ip_network("82.196.10.0/24"),),
    "rbi.org.in": (
        ipaddress.ip_network("13.107.226.0/24"),
        ipaddress.ip_network("13.107.253.0/24"),
        ipaddress.ip_network("150.171.110.0/24"),
    ),
}


def resolve_page_url(value: str) -> tuple[str, str, tuple[str, ...]]:
    if not isinstance(value, str) or len(value) > 4096:
        raise RuntimeError("page URL invalid")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("page URL port invalid") from exc
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or hostname not in PAGE_HOSTS
        or parsed.username
        or parsed.password
        or port not in (None, 443)
    ):
        raise RuntimeError("page URL is outside the allow-list")
    addresses: list[str] = []
    for info in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not address.is_global:
            raise RuntimeError("page URL resolved to a non-public address")
        if str(address) not in addresses:
            addresses.append(str(address))
    if not addresses:
        raise RuntimeError("page URL has no address")
    netloc = hostname if port is None else f"{hostname}:{port}"
    canonical = urlunsplit(("https", netloc, parsed.path or "/", parsed.query, ""))
    return canonical, hostname, tuple(addresses)


def validate_page_url(value: str) -> str:
    return resolve_page_url(value)[0]


def cache_paths(path: str) -> tuple[Path, Path]:
    name = path.removeprefix("/").removesuffix(".xml")
    return CACHE_DIR / f"{name}.xml", CACHE_DIR / f"{name}.json"


def page_url_from_token(token: str) -> str:
    if not PAGE_TOKEN.fullmatch(token):
        raise RuntimeError("page token invalid")
    padding = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode((token + padding).encode("ascii"))
        value = raw.decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError("page token decode failed") from exc
    canonical = validate_page_url(value)
    encoded = base64.urlsafe_b64encode(canonical.encode("utf-8")).decode("ascii").rstrip("=")
    if encoded != token:
        raise RuntimeError("page token is not canonical")
    return canonical


def page_cache_paths(url: str) -> tuple[Path, Path]:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    page_dir = CACHE_DIR / "pages"
    page_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
    return page_dir / f"{digest}.html", page_dir / f"{digest}.json"


def page_lock(url: str) -> threading.Lock:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    with PAGE_LOCKS_GUARD:
        return PAGE_LOCKS.setdefault(key, threading.Lock())


def pinned_page_get(url: str) -> tuple[bytes, str, str]:
    current = url
    for _ in range(6):
        canonical, hostname, addresses = resolve_page_url(current)
        parsed = urlsplit(canonical)
        request_path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        redirected: str | None = None
        last_error: Exception | None = None
        for address in addresses:
            raw_socket = None
            tls_socket = None
            connection = None
            try:
                raw_socket = socket.create_connection((address, 443), timeout=10)
                raw_socket.settimeout(35)
                tls_socket = TLS.wrap_socket(raw_socket, server_hostname=hostname)
                raw_socket = None
                connection = http.client.HTTPConnection(hostname, 443, timeout=35)
                connection.sock = tls_socket
                tls_socket = None
                connection.request(
                    "GET",
                    request_path,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
                        "Accept-Encoding": "identity",
                        "User-Agent": "Mozilla/5.0 (compatible; PrivateTranslationReader/1.0)",
                    },
                )
                response = connection.getresponse()
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader("Location")
                    if not location:
                        raise RuntimeError("upstream redirect has no location")
                    redirected = validate_page_url(urljoin(canonical, location))
                    break
                if response.status != 200:
                    raise RuntimeError(f"upstream status {response.status}")
                content_type = str(response.getheader("Content-Type") or "").split(";", 1)[0].lower()
                body = response.read(MAX_PAGE_BYTES + 1)
                return body, canonical, content_type
            except Exception as exc:
                last_error = exc
            finally:
                if connection is not None:
                    connection.close()
                elif tls_socket is not None:
                    tls_socket.close()
                elif raw_socket is not None:
                    raw_socket.close()
        if redirected is not None:
            current = redirected
            continue
        raise RuntimeError(f"upstream page connection failed: {last_error}")
    raise RuntimeError("upstream page redirect limit reached")


def atomic_write(path: Path, data: bytes, mode: int = 0o640) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def fetch(path: str) -> tuple[bytes, str, bool]:
    body_path, meta_path = cache_paths(path)
    now = time.time()
    if body_path.exists() and now - body_path.stat().st_mtime <= FRESH_SECONDS:
        return body_path.read_bytes(), SOURCES[path], False

    with LOCKS[path]:
        if body_path.exists() and now - body_path.stat().st_mtime <= FRESH_SECONDS:
            return body_path.read_bytes(), SOURCES[path], False
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                request = urllib.request.Request(
                    SOURCES[path],
                    headers={
                        "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.1",
                        "Accept-Encoding": "identity",
                        "User-Agent": "FreshRSS/1.29.1 (+https://freshrss.org)",
                    },
                )
                with urllib.request.urlopen(request, timeout=35, context=TLS) as response:
                    body = response.read(MAX_BYTES + 1)
                    final_url = response.geturl()
                    status = response.status
                if status != 200:
                    raise RuntimeError(f"upstream status {status}")
                if not body or len(body) > MAX_BYTES:
                    raise RuntimeError("upstream body is empty or too large")
                root = ET.fromstring(body)
                root_name = root.tag.rsplit("}", 1)[-1].lower()
                if root_name not in {"rss", "rdf", "feed"}:
                    raise RuntimeError(f"unexpected XML root {root_name}")
                atomic_write(body_path, body)
                metadata = json.dumps(
                    {
                        "fetched_at": int(time.time()),
                        "source": SOURCES[path],
                        "final_url": final_url,
                        "bytes": len(body),
                    },
                    sort_keys=True,
                ).encode("utf-8") + b"\n"
                atomic_write(meta_path, metadata)
                return body, final_url, False
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1 + attempt)

        if body_path.exists() and now - body_path.stat().st_mtime <= STALE_SECONDS:
            return body_path.read_bytes(), SOURCES[path], True
        raise RuntimeError(f"upstream unavailable: {last_error}")


def fetch_page(route_host: str, token: str) -> tuple[bytes, str, bool]:
    url = page_url_from_token(token)
    if route_host.lower().rstrip(".") != (urlsplit(url).hostname or "").lower().rstrip("."):
        raise RuntimeError("page route host mismatch")
    body_path, meta_path = page_cache_paths(url)
    now = time.time()
    if body_path.exists() and now - body_path.stat().st_mtime <= PAGE_FRESH_SECONDS:
        return body_path.read_bytes(), "https://huggingface.co/", False

    with page_lock(url):
        now = time.time()
        if body_path.exists() and now - body_path.stat().st_mtime <= PAGE_FRESH_SECONDS:
            return body_path.read_bytes(), "https://huggingface.co/", False
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                body, final_url, content_type = pinned_page_get(url)
                if not body or len(body) > MAX_PAGE_BYTES:
                    raise RuntimeError("upstream page is empty or too large")
                if content_type and not any(value in content_type for value in ("html", "xhtml", "text/plain")):
                    raise RuntimeError("upstream page content type refused")
                atomic_write(body_path, body)
                metadata = json.dumps(
                    {
                        "fetched_at": int(time.time()),
                        "source_hash": hashlib.sha256(url.encode("utf-8")).hexdigest(),
                        "final_host": urlsplit(final_url).hostname,
                        "bytes": len(body),
                    },
                    sort_keys=True,
                ).encode("utf-8") + b"\n"
                atomic_write(meta_path, metadata)
                return body, "https://huggingface.co/", False
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1 + attempt)

        if body_path.exists() and now - body_path.stat().st_mtime <= STALE_SECONDS:
            return body_path.read_bytes(), "https://huggingface.co/", True
        raise RuntimeError(f"upstream page unavailable: {last_error}")


def validate_dns_payload(body: bytes) -> dict[str, list[str]]:
    payload = json.loads(body)
    if payload.get("schemaVersion") != 1 or payload.get("status") != "ok":
        raise RuntimeError("DNS payload identity invalid")
    raw = payload.get("addresses")
    if not isinstance(raw, dict) or set(raw) != set(DNS_NETWORKS):
        raise RuntimeError("DNS payload host set invalid")
    validated: dict[str, list[str]] = {}
    for host, networks in DNS_NETWORKS.items():
        values = raw.get(host)
        if not isinstance(values, list) or not 1 <= len(values) <= 16:
            raise RuntimeError("DNS payload answer count invalid")
        addresses: list[str] = []
        for value in values:
            address = ipaddress.ip_address(str(value))
            if (
                not isinstance(address, ipaddress.IPv4Address)
                or not address.is_global
                or not any(address in network for network in networks)
            ):
                raise RuntimeError("DNS payload address outside trusted networks")
            if str(address) not in addresses:
                addresses.append(str(address))
        if len(addresses) != len(values):
            raise RuntimeError("DNS payload contains duplicate addresses")
        validated[host] = sorted(addresses, key=lambda value: int(ipaddress.ip_address(value)))
    return validated


def resolve_dns_payload() -> bytes:
    addresses: dict[str, list[str]] = {}
    for host, networks in DNS_NETWORKS.items():
        values = sorted(
            {
                str(ipaddress.ip_address(info[4][0].split("%", 1)[0]))
                for info in socket.getaddrinfo(host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)
            },
            key=lambda value: int(ipaddress.ip_address(value)),
        )
        if not 1 <= len(values) <= 16:
            raise RuntimeError("DNS resolver answer count invalid")
        for value in values:
            address = ipaddress.ip_address(value)
            if not address.is_global or not any(address in network for network in networks):
                raise RuntimeError("DNS resolver address outside trusted networks")
        addresses[host] = values
    body = json.dumps(
        {
            "schemaVersion": 1,
            "status": "ok",
            "generatedAt": int(time.time()),
            "addresses": addresses,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    validate_dns_payload(body)
    return body


def fetch_dns_overrides() -> tuple[bytes, str, bool]:
    body_path = CACHE_DIR / "dns-overrides.json"
    now = time.time()
    if body_path.exists() and now - body_path.stat().st_mtime <= PAGE_FRESH_SECONDS:
        body = body_path.read_bytes()
        validate_dns_payload(body)
        return body, "validated-aws-system-dns", False
    with DNS_LOCK:
        now = time.time()
        if body_path.exists() and now - body_path.stat().st_mtime <= PAGE_FRESH_SECONDS:
            body = body_path.read_bytes()
            validate_dns_payload(body)
            return body, "validated-aws-system-dns", False
        try:
            body = resolve_dns_payload()
            atomic_write(body_path, body)
            return body, "validated-aws-system-dns", False
        except Exception:
            if body_path.exists() and now - body_path.stat().st_mtime <= STALE_SECONDS:
                body = body_path.read_bytes()
                validate_dns_payload(body)
                return body, "validated-aws-system-dns", True
            raise


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "RSSSourceRelay/1.0"

    def do_HEAD(self) -> None:  # noqa: N802
        self._serve(send_body=False)

    def do_GET(self) -> None:  # noqa: N802
        self._serve(send_body=True)

    def _serve(self, *, send_body: bool) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            body = b"ok\n"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if send_body:
                self.wfile.write(body)
            return
        is_page = path.startswith(PAGE_PREFIX) and path.count("/") == 3
        is_dns = path == DNS_PATH
        if path not in SOURCES and not is_page and not is_dns:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        started = time.monotonic()
        try:
            if is_dns:
                body, source, stale = fetch_dns_overrides()
                content_type = "application/json; charset=utf-8"
            elif is_page:
                route_host, token = path.removeprefix(PAGE_PREFIX).split("/", 1)
                body, source, stale = fetch_page(route_host, token)
                content_type = "text/html; charset=utf-8"
            else:
                body, source, stale = fetch(path)
                content_type = "application/xml; charset=utf-8"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, no-store, max-age=0")
            self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")
            self.send_header("X-Content-Source", source)
            if stale:
                self.send_header("Warning", '110 - "stale upstream response"')
            self.end_headers()
            if send_body:
                self.wfile.write(body)
            self._log("ok", path, len(body), stale, started)
        except Exception as exc:
            self.send_error(HTTPStatus.BAD_GATEWAY, "upstream unavailable")
            self._log("error", path, 0, False, started, error=type(exc).__name__)

    def _log(
        self,
        status: str,
        path: str,
        size: int,
        stale: bool,
        started: float,
        *,
        error: str | None = None,
    ) -> None:
        row = {
            "status": status,
            "path": path,
            "bytes": size,
            "stale": stale,
            "milliseconds": round((time.monotonic() - started) * 1000),
            "client": self.client_address[0],
        }
        if error:
            row["error"] = error
        print(json.dumps(row, sort_keys=True), flush=True)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(LISTEN, Handler)
    server.daemon_threads = True
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
