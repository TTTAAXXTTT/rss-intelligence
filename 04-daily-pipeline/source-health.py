#!/usr/bin/env python3
"""Read-only FreshRSS source health monitor.

The monitor performs one direct HTTP/XML probe for every configured FreshRSS
subscription, then correlates that observation with FreshRSS's own SQLite
state.  It never disables, deletes, re-adds, or otherwise mutates a
subscription.  Full feed URLs and authentication material are deliberately
kept out of both outputs and stdout.

Production outputs:
  * internal JSON (0600):
      RSS_SOURCE_HEALTH_ROOT/latest.json
  * private HTML (0644 under the unguessable publication-token directory):
      RSS_PUBLISH_PUBLIC_ROOT/RSS_PUBLISH_TOKEN/source-health.html

FreshRSS database access is query-only.  The host runs PHP in the existing
FreshRSS container over stdin, opens the user's db.sqlite, executes
``PRAGMA query_only=ON``, and reads only the ``feed`` and ``entry`` tables.
Use ``--explain-db`` for the exact safe command shape and field list.
"""

from __future__ import annotations

import argparse
import concurrent.futures
from contextlib import contextmanager
import gzip
import hashlib
import html
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fixture only
    fcntl = None


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import daily_common  # noqa: E402


SCHEMA_VERSION = 1
PRODUCER = "source-health-v1"
REASON_LABELS = {
    "freshrss_database_row_missing": "FreshRSS 数据库缺少该来源",
    "freshrss_error_observed": "FreshRSS 本轮刷新出现上游错误，正在连续观察",
    "freshrss_error_persistent": "FreshRSS 已连续多轮刷新失败",
    "freshrss_last_update_missing": "FreshRSS 缺少刷新时间",
    "freshrss_last_update_stale": "FreshRSS 已超过允许的刷新间隔",
    "freshrss_last_update_in_future": "FreshRSS 刷新时间异常",
    "freshrss_cache_empty": "FreshRSS 数据库没有缓存任何内容",
    "direct_http_403_freshrss_ok": "服务器直连被拒绝，但 FreshRSS 缓存正常",
    "direct_empty_freshrss_cache_ok": "直连内容为空，但 FreshRSS 缓存仍可用",
    "direct_http_freshrss_ok": "服务器直连返回 HTTP 错误，但 FreshRSS 缓存仍可用",
    "direct_timeout_freshrss_ok": "服务器直连超时，但 FreshRSS 缓存仍可用",
    "direct_parse_freshrss_ok": "直连格式暂时无法解析，但 FreshRSS 缓存仍可用",
    "direct_dns_freshrss_ok": "服务器直连 DNS 失败，但 FreshRSS 缓存仍可用",
    "direct_tls_freshrss_ok": "服务器直连 TLS 失败，但 FreshRSS 缓存仍可用",
    "content_stale": "来源较长时间没有发布新内容",
    "publication_time_unknown": "无法从直连内容判断发布时间",
    "publication_time_in_future": "来源发布时间异常",
    "content_empty": "直连与 FreshRSS 均无可用内容",
}
SHANGHAI = timezone(timedelta(hours=8))
URL_TEXT = re.compile(r"https?://[^\s<>]+", re.I)
SAFE_HTML_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}\.html", re.I)
SAFE_CONTAINER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
SAFE_USERNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


class HealthError(RuntimeError):
    """An intentionally credential-safe operational failure."""


class ProbeError(RuntimeError):
    """A credential-safe direct-probe classification."""


def safe_text(value, maximum=180):
    text = str(value or "")
    text = URL_TEXT.sub("<URL>", text)
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:maximum]


def env_int(env, key, default, minimum, maximum):
    raw = str(env.get(key, default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise HealthError(f"{key}_INVALID") from exc
    if not minimum <= value <= maximum:
        raise HealthError(f"{key}_OUT_OF_RANGE")
    return value


def env_float(env, key, default, minimum, maximum):
    raw = str(env.get(key, default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise HealthError(f"{key}_INVALID") from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise HealthError(f"{key}_OUT_OF_RANGE")
    return value


def require_absolute(path, code):
    path = Path(path)
    if not path.is_absolute():
        raise HealthError(code)
    return path


@dataclass(frozen=True)
class Config:
    expected_sources: int
    workers: int
    http_timeout_seconds: int
    db_timeout_seconds: int
    max_body_bytes: int
    stale_days: float
    freshrss_stale_hours: float
    freshrss_error_confirmations: int
    state_root: Path
    json_path: Path
    public_root: Path
    publish_token: str
    html_name: str
    docker_bin: str
    docker_container: str
    docker_user: str
    db_path: str
    user_agent: str

    @classmethod
    def from_env(cls, env):
        state_root = require_absolute(
            env.get(
                "RSS_SOURCE_HEALTH_ROOT",
                "/home/ubuntu/.openclaw/rss-intelligence/source-health-v53",
            ),
            "RSS_SOURCE_HEALTH_ROOT_NOT_ABSOLUTE",
        )
        json_path = require_absolute(
            env.get("RSS_SOURCE_HEALTH_JSON_PATH", str(state_root / "latest.json")),
            "RSS_SOURCE_HEALTH_JSON_PATH_NOT_ABSOLUTE",
        )
        if json_path.parent != state_root or json_path.suffix.lower() != ".json":
            raise HealthError("RSS_SOURCE_HEALTH_JSON_PATH_INVALID")

        public_root = require_absolute(
            env.get("RSS_PUBLISH_PUBLIC_ROOT", "/var/www/rss-intelligence"),
            "RSS_PUBLISH_PUBLIC_ROOT_NOT_ABSOLUTE",
        )
        token = str(env.get("RSS_PUBLISH_TOKEN", "")).strip()
        if not re.fullmatch(r"[A-Fa-f0-9]{64}", token):
            raise HealthError("RSS_PUBLISH_TOKEN_INVALID")
        html_name = str(env.get("RSS_SOURCE_HEALTH_HTML_NAME", "source-health.html")).strip()
        if not SAFE_HTML_NAME.fullmatch(html_name):
            raise HealthError("RSS_SOURCE_HEALTH_HTML_NAME_INVALID")

        container = str(env.get("FRESHRSS_DOCKER_CONTAINER", "freshrss")).strip()
        if not SAFE_CONTAINER.fullmatch(container):
            raise HealthError("FRESHRSS_DOCKER_CONTAINER_INVALID")
        docker_user = str(env.get("FRESHRSS_DOCKER_USER", "www-data")).strip()
        if not SAFE_USERNAME.fullmatch(docker_user):
            raise HealthError("FRESHRSS_DOCKER_USER_INVALID")
        db_path = str(env.get("FRESHRSS_DB_PATH", "")).strip()
        if db_path:
            if (
                not db_path.startswith("/var/www/FreshRSS/data/users/")
                or not db_path.endswith("/db.sqlite")
                or ".." in Path(db_path).parts
                or "\x00" in db_path
                or "\n" in db_path
            ):
                raise HealthError("FRESHRSS_DB_PATH_INVALID")

        return cls(
            expected_sources=env_int(env, "RSS_SOURCE_HEALTH_EXPECTED_COUNT", 49, 1, 5000),
            workers=env_int(env, "RSS_SOURCE_HEALTH_WORKERS", 8, 1, 32),
            http_timeout_seconds=env_int(env, "RSS_SOURCE_HEALTH_HTTP_TIMEOUT", 18, 2, 120),
            db_timeout_seconds=env_int(env, "RSS_SOURCE_HEALTH_DB_TIMEOUT", 30, 2, 300),
            max_body_bytes=env_int(
                env, "RSS_SOURCE_HEALTH_MAX_BODY_BYTES", 16 * 1024 * 1024, 64 * 1024, 64 * 1024 * 1024
            ),
            stale_days=env_float(env, "RSS_SOURCE_HEALTH_STALE_DAYS", 14, 1, 3650),
            freshrss_stale_hours=env_float(
                env, "RSS_SOURCE_HEALTH_FRESHRSS_STALE_HOURS", 36, 2, 24 * 30
            ),
            freshrss_error_confirmations=env_int(
                env, "RSS_SOURCE_HEALTH_ERROR_CONFIRMATIONS", 3, 1, 12
            ),
            state_root=state_root,
            json_path=json_path,
            public_root=public_root,
            publish_token=token,
            html_name=html_name,
            docker_bin=str(env.get("FRESHRSS_DOCKER_BIN", "/usr/bin/docker")).strip(),
            docker_container=container,
            docker_user=docker_user,
            db_path=db_path,
            user_agent=safe_text(
                env.get("RSS_SOURCE_HEALTH_USER_AGENT", "FreshRSS-Source-Health/1.0 (+read-only)"),
                160,
            ),
        )


def now_iso(value=None):
    value = value or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def scheduled_observation_id(reference):
    """Return one stable id for each 07:03/13:03 timer window.

    Re-running the service manually inside the same window reuses the id and
    therefore cannot inflate the consecutive-error counter.
    """
    local = reference.astimezone(SHANGHAI)
    minute = local.hour * 60 + local.minute
    for hour, start, end in ((7, 6 * 60 + 58, 8 * 60 + 30), (13, 12 * 60 + 58, 14 * 60 + 30)):
        if start <= minute < end:
            return f"{local.date().isoformat()}T{hour:02d}:03"
    return None


def observation_index(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T(?:07|13):03", value):
        return None
    try:
        day = datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return day.toordinal() * 2 + (1 if value[11:13] == "13" else 0)


def advance_error_counter(previous, observation_id, active, limit):
    previous = previous if isinstance(previous, dict) else {}
    prior_count = previous.get("consecutiveErrorSlots", 0)
    prior_id = previous.get("lastErrorObservationId")
    if isinstance(prior_count, bool) or not isinstance(prior_count, int) or not 0 <= prior_count <= limit:
        raise HealthError("OBSERVATION_COUNTER_INVALID")
    if prior_id is not None and observation_index(prior_id) is None:
        raise HealthError("OBSERVATION_ID_INVALID")
    if observation_id is None:
        # Manual checks report current health but do not mutate scheduled streaks.
        return (max(1, prior_count) if active else 0), {
            "consecutiveErrorSlots": prior_count,
            "lastErrorObservationId": prior_id,
        }
    if observation_index(observation_id) is None:
        raise HealthError("CURRENT_OBSERVATION_ID_INVALID")
    if not active:
        return 0, {"consecutiveErrorSlots": 0, "lastErrorObservationId": None}
    if prior_id == observation_id:
        count = max(1, prior_count)
    elif prior_id is not None and observation_index(prior_id) + 1 == observation_index(observation_id):
        count = min(limit, prior_count + 1)
    else:
        count = 1
    return count, {"consecutiveErrorSlots": count, "lastErrorObservationId": observation_id}


def epoch_datetime(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    if number > 10_000_000_000:
        number /= 1000
    try:
        return datetime.fromtimestamp(number, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def iso_datetime(value):
    parsed = epoch_datetime(value)
    return now_iso(parsed) if parsed is not None else None


def age_hours(value, reference):
    parsed = epoch_datetime(value)
    if parsed is None:
        return None
    return round((reference - parsed).total_seconds() / 3600, 2)


def parse_date(value):
    text = safe_text(value, 180)
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def feed_digest(url):
    return hashlib.sha256(url.encode("utf-8", "surrogatepass")).hexdigest()


def host_only(url):
    try:
        host = (urlsplit(url).hostname or "").encode("idna").decode("ascii").lower()
    except Exception:
        return "<invalid-host>"
    return safe_text(host, 180) or "<missing-host>"


def public_destination(url):
    try:
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except (TypeError, ValueError) as exc:
        raise ProbeError("unsupported_url") from exc
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise ProbeError("unsupported_url")
    if parsed.username is not None or parsed.password is not None:
        raise ProbeError("url_credentials_forbidden")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ProbeError("dns_error") from exc
    if not addresses:
        raise ProbeError("dns_error")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address[4][0].split("%", 1)[0])
        except ValueError as exc:
            raise ProbeError("dns_address_invalid") from exc
        if not ip.is_global:
            raise ProbeError("non_public_destination")


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_destination(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_response_body(response, limit):
    encoding = str(response.headers.get("Content-Encoding") or "").strip().lower()
    if encoding in ("", "identity"):
        body = response.read(limit + 1)
    elif encoding == "gzip":
        with gzip.GzipFile(fileobj=response) as stream:
            body = stream.read(limit + 1)
    else:
        raise ProbeError("unsupported_content_encoding")
    if len(body) > limit:
        raise ProbeError("body_too_large")
    return body


def parse_feed_xml(body):
    prefix = body[:4096].upper()
    if b"<!DOCTYPE" in prefix or b"<!ENTITY" in prefix:
        raise ProbeError("unsafe_xml_declaration")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ProbeError("parse_error") from exc
    root_name = root.tag.rsplit("}", 1)[-1].lower()
    if root_name == "rss":
        items = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() == "item"]
        feed_format = "rss"
    elif root_name == "feed":
        items = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() == "entry"]
        feed_format = "atom"
    elif root_name in ("rdf", "rdf:rdf"):
        items = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() == "item"]
        feed_format = "rdf"
    else:
        raise ProbeError("xml_not_feed")

    newest = None
    for item in items:
        for node in item.iter():
            name = node.tag.rsplit("}", 1)[-1].lower()
            if name not in ("pubdate", "published", "updated", "date", "created"):
                continue
            parsed = parse_date(node.text)
            if parsed is not None and (newest is None or parsed > newest):
                newest = parsed
    return feed_format, len(items), newest


def classify_url_error(reason):
    if isinstance(reason, ssl.SSLError):
        return "tls_error"
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(reason, socket.gaierror):
        return "dns_error"
    text = str(reason).lower()
    if any(marker in text for marker in ("name or service not known", "temporary failure in name", "nodename nor servname")):
        return "dns_error"
    if any(marker in text for marker in ("certificate", "ssl", "tls")):
        return "tls_error"
    if "timed out" in text:
        return "timeout"
    return "transport_error"


def direct_probe(subscription, config, reference):
    url = subscription["_url"]
    started = time.monotonic()
    result = {
        "http": None,
        "contentType": None,
        "bytes": 0,
        "latencySec": None,
        "parse": "not_attempted",
        "format": None,
        "items": None,
        "newestItemAt": None,
        "ageDays": None,
        "errorClass": None,
    }
    try:
        public_destination(url)
        opener = urllib.request.build_opener(
            SafeRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )
        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "User-Agent": config.user_agent,
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.2",
                "Accept-Encoding": "identity",
                "Cache-Control": "no-cache",
            },
        )
        with opener.open(request, timeout=config.http_timeout_seconds) as response:
            result["http"] = int(getattr(response, "status", response.getcode()))
            result["contentType"] = safe_text(response.headers.get_content_type(), 100)
            body = read_response_body(response, config.max_body_bytes)
            result["bytes"] = len(body)
        feed_format, item_count, newest = parse_feed_xml(body)
        result["parse"] = "ok"
        result["format"] = feed_format
        result["items"] = item_count
        if item_count == 0:
            result["errorClass"] = "empty_feed"
        if newest is not None:
            result["newestItemAt"] = now_iso(newest)
            result["ageDays"] = round((reference - newest).total_seconds() / 86400, 2)
    except urllib.error.HTTPError as exc:
        result["http"] = int(exc.code)
        result["errorClass"] = "http_error"
    except urllib.error.URLError as exc:
        result["errorClass"] = classify_url_error(exc.reason)
    except (socket.timeout, TimeoutError):
        result["errorClass"] = "timeout"
    except ssl.SSLError:
        result["errorClass"] = "tls_error"
    except ProbeError as exc:
        code = str(exc)
        result["errorClass"] = code if re.fullmatch(r"[a-z0-9_]+", code) else "probe_error"
        if code in ("parse_error", "xml_not_feed", "unsafe_xml_declaration"):
            result["parse"] = code
    except Exception:
        result["errorClass"] = "unexpected_error"
    result["latencySec"] = round(time.monotonic() - started, 3)
    return result


def subscriptions_from_api(api, auth):
    payload = daily_common.get_json(api + "/reader/api/0/subscription/list?output=json", auth)
    rows = payload.get("subscriptions") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise HealthError("FRESHRSS_SUBSCRIPTION_LIST_SCHEMA_INVALID")
    result = []
    seen_urls = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        identity = str(raw.get("id") or "").strip()
        url = str(raw.get("url") or "").strip()
        if not url and identity.startswith("feed/http"):
            url = identity[5:]
        if not url:
            continue
        digest = feed_digest(url)
        if digest in seen_urls:
            raise HealthError("FRESHRSS_DUPLICATE_SUBSCRIPTION_URL")
        seen_urls.add(digest)
        categories = []
        for category in raw.get("categories") or []:
            label = category.get("label") if isinstance(category, dict) else category
            label = safe_text(label, 100)
            if label:
                categories.append(label)
        result.append(
            {
                "_url": url,
                "_digest": digest,
                "sourceId": digest[:12],
                "title": safe_text(raw.get("title") or "(untitled)"),
                "host": host_only(url),
                "categories": categories,
            }
        )
    return result


def php_single_quote(value):
    return value.replace("\\", "\\\\").replace("'", "\\'")


def db_php(db_path):
    quoted_path = php_single_quote(db_path)
    return f"""<?php
declare(strict_types=1);
$dbPath = '{quoted_path}';
$output = ['records' => []];
$pdo = new PDO('sqlite:' . $dbPath, null, null, [
    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_TIMEOUT => 5,
]);
$pdo->exec('PRAGMA query_only = ON');
$sql = 'SELECT f."id" AS id, f."url" AS url, f."name" AS name, f."lastUpdate" AS lastUpdate, '
    . 'f."error" AS error, f."ttl" AS ttl, f."attributes" AS attributes, '
    . 'f."cache_nbEntries" AS cacheEntries, f."cache_nbUnreads" AS cacheUnreads, '
    . 'COUNT(e."id") AS entryCount, MAX(e."date") AS newestEntry, '
    . 'MAX(e."lastSeen") AS lastSeen '
    . 'FROM "feed" f LEFT JOIN "entry" e ON e."id_feed" = f."id" '
    . 'GROUP BY f."id", f."url", f."name", f."lastUpdate", f."error", f."ttl", '
    . 'f."attributes", f."cache_nbEntries", f."cache_nbUnreads" ORDER BY f."id"';
foreach ($pdo->query($sql, PDO::FETCH_ASSOC) as $row) {{
    $problemKeys = [];
    $attributes = json_decode((string)($row['attributes'] ?? ''), true);
    if (is_array($attributes)) {{
        foreach ($attributes as $key => $value) {{
            if (!preg_match('/error|fail/i', (string)$key)) {{
                continue;
            }}
            $normalized = strtolower(trim(is_scalar($value) ? (string)$value : json_encode($value)));
            if (!in_array($normalized, ['', '0', 'false', 'none', 'null', '[]', '{{}}'], true)) {{
                $problemKeys[] = substr((string)$key, 0, 100);
            }}
        }}
    }}
    $output['records'][] = [
        'id' => (string)$row['id'],
        'digest' => hash('sha256', (string)($row['url'] ?? '')),
        'name' => (string)($row['name'] ?? ''),
        'lastUpdate' => $row['lastUpdate'] ?? null,
        'error' => $row['error'] ?? null,
        'ttl' => $row['ttl'] ?? null,
        'cacheEntries' => $row['cacheEntries'] ?? null,
        'cacheUnreads' => $row['cacheUnreads'] ?? null,
        'entryCount' => $row['entryCount'] ?? null,
        'newestEntry' => $row['newestEntry'] ?? null,
        'lastSeen' => $row['lastSeen'] ?? null,
        'attributeErrorKeys' => $problemKeys,
    ];
}}
echo json_encode($output, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
"""


def query_freshrss_database(config, freshrss_user):
    if config.db_path:
        db_path = config.db_path
    else:
        if not SAFE_USERNAME.fullmatch(freshrss_user):
            raise HealthError("FRESHRSS_API_USER_UNSAFE_FOR_DB_PATH")
        db_path = f"/var/www/FreshRSS/data/users/{freshrss_user}/db.sqlite"
    command = [
        config.docker_bin,
        "exec",
        "--user",
        config.docker_user,
        "-i",
        config.docker_container,
        "php",
    ]
    try:
        completed = subprocess.run(
            command,
            input=db_php(db_path),
            capture_output=True,
            text=True,
            timeout=config.db_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HealthError("FRESHRSS_DB_QUERY_TIMEOUT") from exc
    except OSError as exc:
        raise HealthError("FRESHRSS_DB_QUERY_COMMAND_FAILED") from exc
    if completed.returncode != 0:
        raise HealthError("FRESHRSS_DB_QUERY_FAILED")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise HealthError("FRESHRSS_DB_QUERY_BAD_JSON") from exc
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise HealthError("FRESHRSS_DB_QUERY_SCHEMA_INVALID")
    return records


def number_or_none(value, integer=False):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if integer else number


def error_is_active(value):
    if isinstance(value, bool):
        return value
    number = number_or_none(value)
    if number is not None:
        return number != 0
    return str(value or "").strip().lower() not in ("", "0", "false", "none", "null")


def safe_error_value(value):
    """Expose error state without copying an upstream URL-bearing message."""
    number = number_or_none(value)
    if number is not None:
        return int(number) if number.is_integer() else number
    return "nonzero_text" if error_is_active(value) else "none"


def normalize_db_record(raw, reference):
    last_update = raw.get("lastUpdate")
    newest_entry = raw.get("newestEntry")
    last_seen = raw.get("lastSeen")
    keys = raw.get("attributeErrorKeys") if isinstance(raw.get("attributeErrorKeys"), list) else []
    return {
        "present": True,
        "lastUpdate": iso_datetime(last_update),
        "lastUpdateAgeHours": age_hours(last_update, reference),
        "error": safe_error_value(raw.get("error")),
        "errorActive": error_is_active(raw.get("error")),
        "ttl": number_or_none(raw.get("ttl"), integer=True),
        "cacheEntries": number_or_none(raw.get("cacheEntries"), integer=True),
        "cacheUnreads": number_or_none(raw.get("cacheUnreads"), integer=True),
        "entryCount": number_or_none(raw.get("entryCount"), integer=True),
        "newestEntryAt": iso_datetime(newest_entry),
        "newestEntryAgeDays": (
            round(age_hours(newest_entry, reference) / 24, 2)
            if age_hours(newest_entry, reference) is not None
            else None
        ),
        "lastSeenAt": iso_datetime(last_seen),
        "attributeErrorKeys": [safe_text(key, 100) for key in keys[:20]],
    }


def direct_class(result):
    error = result.get("errorClass")
    if error is None and result.get("parse") == "ok" and (result.get("items") or 0) > 0:
        return "ok"
    if error == "http_error":
        return "http"
    if error == "dns_error":
        return "dns"
    if error == "tls_error":
        return "tls"
    if error == "timeout":
        return "timeout"
    if error == "empty_feed":
        return "empty"
    if error in (
        "parse_error",
        "xml_not_feed",
        "unsafe_xml_declaration",
        "body_too_large",
        "unsupported_content_encoding",
    ):
        return "parse"
    if error in ("non_public_destination", "url_credentials_forbidden", "unsupported_url", "dns_address_invalid"):
        return "blocked"
    return "transport"


def evaluate(subscription, direct, db_record, config, reference, counter_state=None, observation_id=None):
    if db_record is None:
        fresh = {
            "present": False,
            "lastUpdate": None,
            "lastUpdateAgeHours": None,
            "error": None,
            "errorActive": None,
            "ttl": None,
            "cacheEntries": None,
            "cacheUnreads": None,
            "entryCount": None,
            "newestEntryAt": None,
            "newestEntryAgeDays": None,
            "lastSeenAt": None,
            "attributeErrorKeys": [],
        }
    else:
        fresh = normalize_db_record(db_record, reference)

    current_error_active = bool(fresh.get("errorActive") or fresh.get("attributeErrorKeys"))
    consecutive_errors, next_counter = advance_error_counter(
        counter_state,
        observation_id,
        current_error_active,
        config.freshrss_error_confirmations,
    )
    fresh["consecutiveErrorObservations"] = consecutive_errors
    fresh["activeErrorObserved"] = current_error_active

    reasons = []
    state = "ok"
    if not fresh["present"]:
        reasons.append("freshrss_database_row_missing")
        state = "error"
    else:
        if fresh["lastUpdateAgeHours"] is None:
            reasons.append("freshrss_last_update_missing")
            state = "error"
        elif fresh["lastUpdateAgeHours"] > config.freshrss_stale_hours:
            reasons.append("freshrss_last_update_stale")
            state = "error"
        elif fresh["lastUpdateAgeHours"] < -1:
            reasons.append("freshrss_last_update_in_future")
            state = "watch"
        if (fresh.get("entryCount") or 0) <= 0 and (fresh.get("cacheEntries") or 0) <= 0:
            reasons.append("freshrss_cache_empty")
            state = "error"
    if current_error_active:
        reasons.append("freshrss_error_observed")
        if consecutive_errors >= config.freshrss_error_confirmations:
            reasons.append("freshrss_error_persistent")
            state = "error"
        elif state == "ok":
            state = "watch"

    fresh_normal = state != "error"
    dclass = direct_class(direct)
    if dclass != "ok":
        if fresh_normal:
            if dclass == "http" and direct.get("http") == 403:
                reasons.append("direct_http_403_freshrss_ok")
            elif dclass == "empty" and (fresh.get("entryCount") or 0) > 0:
                reasons.append("direct_empty_freshrss_cache_ok")
            else:
                reasons.append(f"direct_{dclass}_freshrss_ok")
            if state == "ok":
                state = "watch"
        else:
            reasons.append(f"direct_{dclass}_and_freshrss_unhealthy")
            state = "error"

    direct_age = direct.get("ageDays")
    db_age = fresh.get("newestEntryAgeDays")
    if direct_age is not None:
        newest_age = direct_age
        newest_at = direct.get("newestItemAt")
        newest_basis = "direct_feed"
    elif db_age is not None:
        newest_age = db_age
        newest_at = fresh.get("newestEntryAt")
        newest_basis = "freshrss_cache"
    else:
        newest_age = None
        newest_at = None
        newest_basis = "unknown"

    if newest_age is None:
        reasons.append("publication_time_unknown")
        if state == "ok":
            state = "watch"
    elif newest_age > config.stale_days:
        reasons.append("content_stale")
        if state == "ok":
            state = "watch"
    elif newest_age < -1:
        reasons.append("publication_time_in_future")
        if state == "ok":
            state = "watch"

    cache_entries = fresh.get("entryCount")
    direct_items = direct.get("items")
    if direct_items == 0 and (cache_entries is None or cache_entries == 0):
        if "content_empty" not in reasons:
            reasons.append("content_empty")
        state = "error"

    primary = reasons[0] if reasons else "ok"
    return {
        "sourceId": subscription["sourceId"],
        "title": subscription["title"],
        "host": subscription["host"],
        "categories": subscription["categories"],
        "status": state,
        "classification": primary,
        "reasons": reasons,
        "freshRss": fresh,
        "direct": {**direct, "class": dclass},
        "content": {
            "newestAt": newest_at,
            "ageDays": newest_age,
            "basis": newest_basis,
            "stale": newest_age is not None and newest_age > config.stale_days,
        },
        "_counterState": next_counter,
    }


def build_report(subscriptions, direct_results, db_records, config, reference, infrastructure_errors, observation_state=None, observation_id=None):
    db_index = {}
    for row in db_records:
        identity = str(row.get("digest") or "").lower()
        if re.fullmatch(r"[a-f0-9]{64}", identity):
            db_index[identity] = row

    observation_sources = (observation_state or {}).get("sources") or {}
    next_observation_sources = {}
    sources = []
    for subscription in subscriptions:
        row = evaluate(
            subscription,
            direct_results[subscription["_digest"]],
            db_index.get(subscription["_digest"]),
            config,
            reference,
            observation_sources.get(subscription["sourceId"]),
            observation_id,
        )
        next_observation_sources[subscription["sourceId"]] = row.pop("_counterState")
        sources.append(row)
    sources.sort(key=lambda row: (row["status"] != "error", row["status"] != "watch", row["title"].casefold()))

    states = Counter(row["status"] for row in sources)
    direct_classes = Counter(row["direct"]["class"] for row in sources)
    summary = {
        "statusCounts": {key: states.get(key, 0) for key in ("ok", "watch", "error")},
        "directClasses": dict(sorted(direct_classes.items())),
        "http2xx": sum(1 for row in sources if isinstance(row["direct"].get("http"), int) and 200 <= row["direct"]["http"] < 300),
        "directHttp403FreshRssOk": sum("direct_http_403_freshrss_ok" in row["reasons"] for row in sources),
        "freshRssErrorSources": sum(row["freshRss"].get("activeErrorObserved") is True for row in sources),
        "freshRssPersistentErrorSources": sum("freshrss_error_persistent" in row["reasons"] for row in sources),
        "freshRssStaleSources": sum("freshrss_last_update_stale" in row["reasons"] for row in sources),
        "databaseMissingSources": sum(not row["freshRss"]["present"] for row in sources),
        "contentStaleSources": sum(row["content"]["stale"] for row in sources),
        "emptySources": sum("content_empty" in row["reasons"] for row in sources),
    }

    errors = list(infrastructure_errors)
    if len(subscriptions) != config.expected_sources:
        errors.append("subscription_count_mismatch")
    if len(db_records) != config.expected_sources:
        errors.append("database_feed_count_mismatch")
    if summary["databaseMissingSources"]:
        errors.append("database_subscription_identity_mismatch")
    errors = list(dict.fromkeys(errors))

    if errors or states.get("error", 0):
        overall = "critical"
    elif states.get("watch", 0):
        overall = "watch"
    else:
        overall = "ok"

    return {
        "schemaVersion": SCHEMA_VERSION,
        "producer": PRODUCER,
        "generatedAt": now_iso(reference),
        "status": overall,
        "expectedSourceCount": config.expected_sources,
        "sourceCount": len(subscriptions),
        "databaseFeedCount": len(db_records),
        "policy": {
            "singleDirectRequestPerSource": True,
            "subscriptionMutation": False,
            "contentStaleDays": config.stale_days,
            "freshRssLastUpdateStaleHours": config.freshrss_stale_hours,
            "freshRssErrorConfirmations": config.freshrss_error_confirmations,
            "scheduledObservationId": observation_id,
            "directFailureWithFreshRssSuccess": "watch",
        },
        "database": {
            "backend": "pdo_sqlite_via_docker_stdin",
            "queryOnly": True,
            "tables": ["feed", "entry"],
            "feedFields": [
                "id",
                "name",
                "lastUpdate",
                "error",
                "ttl",
                "attributes",
                "cache_nbEntries",
                "cache_nbUnreads",
            ],
            "entryFields": ["id", "id_feed", "date", "lastSeen"],
        },
        "infrastructureErrors": errors,
        "summary": summary,
        "sources": sources,
        "_nextObservationSources": next_observation_sources,
    }


def ensure_directory(path, mode):
    path = Path(path)
    if path.exists():
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise HealthError("OUTPUT_DIRECTORY_INVALID")
    else:
        path.mkdir(parents=True, exist_ok=False)
    if os.name == "posix":
        os.chmod(path, mode)


def atomic_text(path, text, mode):
    path = Path(path)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise HealthError("OUTPUT_FILE_INVALID")
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    os.close(fd)
    try:
        Path(temporary).write_text(text, encoding="utf-8")
        if os.name == "posix":
            os.chmod(temporary, mode)
        os.replace(temporary, path)
        if os.name == "posix":
            os.chmod(path, mode)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@contextmanager
def observation_lock(state_root):
    ensure_directory(state_root, 0o700)
    info = state_root.lstat()
    if state_root.is_symlink() or not stat.S_ISDIR(info.st_mode):
        raise HealthError("OBSERVATION_STATE_ROOT_INVALID")
    if state_root.resolve(strict=True) != Path(os.path.abspath(state_root)):
        raise HealthError("OBSERVATION_STATE_ROOT_RESOLVE_INVALID")
    if os.name == "posix" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700):
        raise HealthError("OBSERVATION_STATE_ROOT_PERMISSION_INVALID")
    lock_path = state_root / ".health.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (os.name == "posix" and info.st_uid != os.geteuid()):
            raise HealthError("OBSERVATION_LOCK_INVALID")
        if os.name == "posix":
            os.chmod(lock_path, 0o600)
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(descriptor)


def source_set_hash(source_ids):
    return hashlib.sha256("\n".join(sorted(source_ids)).encode()).hexdigest()


def load_observation_state(path, source_ids, reference, limit):
    empty = {
        "schemaVersion": 1,
        "producer": "source-health-observation-v1",
        "sourceSetHash": source_set_hash(source_ids),
        "generatedAt": now_iso(reference),
        "sources": {},
    }
    try:
        info = path.lstat()
    except FileNotFoundError:
        return empty
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= 1024 * 1024:
        raise HealthError("OBSERVATION_STATE_FILE_INVALID")
    if os.name == "posix" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
        raise HealthError("OBSERVATION_STATE_FILE_PERMISSION_INVALID")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HealthError("OBSERVATION_STATE_JSON_INVALID") from exc
    if (
        not isinstance(data, dict)
        or data.get("schemaVersion") != 1
        or data.get("producer") != "source-health-observation-v1"
        or not isinstance(data.get("sources"), dict)
    ):
        raise HealthError("OBSERVATION_STATE_IDENTITY_INVALID")
    try:
        generated = datetime.fromisoformat(str(data["generatedAt"]).replace("Z", "+00:00"))
        if generated.tzinfo is None or generated.astimezone(timezone.utc) > reference + timedelta(minutes=5):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise HealthError("OBSERVATION_STATE_TIME_INVALID") from exc
    if data.get("sourceSetHash") != source_set_hash(source_ids):
        return empty
    if set(data["sources"]) - set(source_ids):
        raise HealthError("OBSERVATION_STATE_SOURCE_SET_INVALID")
    for source_id, row in data["sources"].items():
        if not isinstance(source_id, str) or not isinstance(row, dict):
            raise HealthError("OBSERVATION_STATE_ROW_INVALID")
        count = row.get("consecutiveErrorSlots")
        last_id = row.get("lastErrorObservationId")
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= limit:
            raise HealthError("OBSERVATION_STATE_COUNTER_INVALID")
        if (last_id is not None and observation_index(last_id) is None) or (count == 0 and last_id is not None):
            raise HealthError("OBSERVATION_STATE_OBSERVATION_INVALID")
    return data


def save_observation_state(path, source_ids, sources, reference):
    if set(sources) != set(source_ids):
        raise HealthError("OBSERVATION_STATE_OUTPUT_SET_INVALID")
    data = {
        "schemaVersion": 1,
        "producer": "source-health-observation-v1",
        "sourceSetHash": source_set_hash(source_ids),
        "generatedAt": now_iso(reference),
        "sources": sources,
    }
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", 0o600)


def html_escape(value):
    return html.escape(str(value if value is not None else "—"), quote=True)


def local_time(value):
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return "—"


def format_age(value, unit):
    if value is None:
        return "未知"
    return f"{value:.1f}{unit}"


def render_html(report):
    summary = report["summary"]
    counts = summary["statusCounts"]
    cards = [
        ("总来源", report["sourceCount"], "neutral"),
        ("正常", counts["ok"], "ok"),
        ("观察", counts["watch"], "watch"),
        ("故障", counts["error"], "error"),
        ("直连 2xx", summary["http2xx"], "neutral"),
        ("FreshRSS 正常但直连 403", summary["directHttp403FreshRssOk"], "watch"),
        ("内容陈旧", summary["contentStaleSources"], "watch"),
        ("空内容", summary["emptySources"], "error" if summary["emptySources"] else "neutral"),
    ]
    cards_html = "".join(
        f'<section class="card {kind}"><span>{html_escape(label)}</span><strong>{html_escape(value)}</strong></section>'
        for label, value, kind in cards
    )

    rows = []
    for source in report["sources"]:
        direct = source["direct"]
        fresh = source["freshRss"]
        if direct.get("http") is not None:
            direct_label = f'HTTP {direct["http"]}'
        else:
            direct_label = direct.get("class") or "未知"
        if direct.get("parse") == "ok":
            direct_label += f' · {direct.get("format") or "feed"} · {direct.get("items") or 0} 条'
        direct_label += f' · {direct.get("latencySec") or 0:.2f}s'
        reasons = "；".join(REASON_LABELS.get(reason, reason) for reason in source["reasons"]) if source["reasons"] else "正常"
        observation_count = int(fresh.get("consecutiveErrorObservations") or 0)
        observation_text = (
            f' · 错误观察 {observation_count}/{int(report["policy"].get("freshRssErrorConfirmations", 3))}'
            if fresh.get("errorActive") else " · 无活动错误"
        )
        rows.append(
            "<tr>"
            f'<td><strong>{html_escape(source["title"])}</strong><small>{html_escape(source["host"])} · {html_escape(source["sourceId"])}</small></td>'
            f'<td><span class="pill {html_escape(source["status"])}">{html_escape(source["status"])}</span></td>'
            f'<td>{html_escape(direct_label)}</td>'
            f'<td>{html_escape(local_time(fresh.get("lastUpdate")))}<small>距今 {html_escape(format_age(fresh.get("lastUpdateAgeHours"), "h"))}{html_escape(observation_text)}</small></td>'
            f'<td>{html_escape(local_time(source["content"].get("newestAt")))}<small>{html_escape(format_age(source["content"].get("ageDays"), "d"))} · {html_escape(source["content"].get("basis"))}</small></td>'
            f'<td>{html_escape(reasons)}</td>'
            "</tr>"
        )
    error_html = ""
    if report["infrastructureErrors"]:
        error_html = '<aside class="infra"><strong>基础设施错误：</strong>' + html_escape(
            "、".join(report["infrastructureErrors"])
        ) + "</aside>"
    generated = local_time(report["generatedAt"])
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow,noarchive"><title>FreshRSS 来源健康</title>
<style>
:root{{--bg:#f5f7fb;--card:#fff;--text:#172033;--muted:#667085;--line:#e5e9f2;--ok:#067647;--watch:#b54708;--error:#b42318}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1500px;margin:0 auto;padding:28px}}h1{{margin:0 0 4px;font-size:28px}}.sub{{color:var(--muted);margin-bottom:20px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px;margin:18px 0}}.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}}
.card span,.card small,td small{{display:block;color:var(--muted)}}.card strong{{font-size:24px}}.card.ok strong{{color:var(--ok)}}.card.watch strong{{color:var(--watch)}}.card.error strong{{color:var(--error)}}
.infra{{background:#fff1f0;border:1px solid #fecdca;border-radius:10px;padding:12px;margin:14px 0;color:var(--error)}}
.table{{overflow:auto;background:var(--card);border:1px solid var(--line);border-radius:12px}}table{{border-collapse:collapse;width:100%;min-width:1180px}}th,td{{padding:11px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:#f8fafc;color:#475467;font-size:12px;text-transform:uppercase}}tr:last-child td{{border-bottom:0}}
.pill{{display:inline-block;border-radius:999px;padding:2px 9px;font-weight:700}}.pill.ok{{background:#ecfdf3;color:var(--ok)}}.pill.watch{{background:#fffaeb;color:var(--watch)}}.pill.error{{background:#fef3f2;color:var(--error)}}
.note{{margin-top:14px;color:var(--muted)}}code{{background:#eef2f7;border-radius:5px;padding:2px 5px}}@media(max-width:720px){{main{{padding:16px}}h1{{font-size:23px}}}}
</style></head><body><main>
<h1>FreshRSS 49 源健康</h1><div class="sub">生成时间：{html_escape(generated)}（Asia/Shanghai） · 总状态：<strong>{html_escape(report["status"])}</strong></div>
{error_html}<div class="cards">{cards_html}</div>
<div class="table"><table><thead><tr><th>来源</th><th>状态</th><th>单次直连</th><th>FreshRSS</th><th>最新内容</th><th>判定</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<p class="note">判定原则：单次上游错误先观察，连续 {int(report["policy"].get("freshRssErrorConfirmations", 3))} 轮才升级为故障；直连失败但 FreshRSS 缓存正常时不自动停用；内容陈旧与抓取故障分开统计。本程序不会修改订阅。</p>
</main></body></html>"""


def output_paths(config):
    ensure_directory(config.state_root, 0o700)
    public_root = config.public_root
    if public_root.exists():
        if public_root.is_symlink() or not public_root.is_dir():
            raise HealthError("RSS_PUBLISH_PUBLIC_ROOT_INVALID")
    else:
        public_root.mkdir(parents=True, exist_ok=False)
        if os.name == "posix":
            os.chmod(public_root, 0o2750)
    public_real = public_root.resolve(strict=True)
    private_dir = public_root / config.publish_token
    if private_dir.exists():
        if private_dir.is_symlink() or not private_dir.is_dir():
            raise HealthError("RSS_PRIVATE_DIRECTORY_INVALID")
    else:
        private_dir.mkdir(mode=0o711)
    if private_dir.resolve(strict=True).parent != public_real:
        raise HealthError("RSS_PRIVATE_DIRECTORY_OUTSIDE_ROOT")
    if os.name == "posix":
        os.chmod(private_dir, 0o711)
    html_path = private_dir / config.html_name
    if html_path.parent.resolve(strict=True) != private_dir.resolve(strict=True):
        raise HealthError("RSS_SOURCE_HEALTH_HTML_PATH_INVALID")
    return config.json_path, html_path


def explain_db():
    print("Read-only backend command shape:")
    print("  docker exec --user www-data -i <FreshRSS-container> php")
    print("  PHP source is supplied on stdin; it executes PRAGMA query_only=ON.")
    print("Tables: feed LEFT JOIN entry ON entry.id_feed = feed.id")
    print("feed fields: id, url (hashed in-container), name, lastUpdate, error, ttl, attributes, cache_nbEntries, cache_nbUnreads")
    print("entry aggregates: COUNT(id), MAX(date), MAX(lastSeen)")
    print("No INSERT, UPDATE, DELETE, subscription API mutation, or automatic disable operation is present.")


def run():
    env = daily_common.read_env()
    config = Config.from_env(env)
    reference = datetime.now(timezone.utc)
    with observation_lock(config.state_root):
        return run_locked(env, config, reference)


def run_locked(env, config, reference):
    infrastructure_errors = []

    try:
        api, freshrss_user, freshrss_password = daily_common.fresh_config()
        auth = daily_common.login_freshrss(api, freshrss_user, freshrss_password)
        subscriptions = subscriptions_from_api(api, auth)
    except Exception as exc:
        raise HealthError("FRESHRSS_API_INVENTORY_FAILED") from exc

    source_ids = [row["sourceId"] for row in subscriptions]
    if len(source_ids) != len(set(source_ids)):
        raise HealthError("SUBSCRIPTION_SOURCE_ID_DUPLICATE")
    observation_id = scheduled_observation_id(reference)
    observation_path = config.state_root / "observation-state.json"
    observation_state = load_observation_state(
        observation_path,
        source_ids,
        reference,
        config.freshrss_error_confirmations,
    )

    try:
        db_records = query_freshrss_database(config, freshrss_user)
    except HealthError as exc:
        db_records = []
        infrastructure_errors.append(str(exc))

    direct_results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=config.workers) as executor:
        future_map = {
            executor.submit(direct_probe, subscription, config, reference): subscription
            for subscription in subscriptions
        }
        for future in concurrent.futures.as_completed(future_map):
            subscription = future_map[future]
            try:
                result = future.result()
            except Exception:
                result = {
                    "http": None,
                    "contentType": None,
                    "bytes": 0,
                    "latencySec": None,
                    "parse": "not_attempted",
                    "format": None,
                    "items": None,
                    "newestItemAt": None,
                    "ageDays": None,
                    "errorClass": "worker_error",
                }
            direct_results[subscription["_digest"]] = result

    report = build_report(
        subscriptions,
        direct_results,
        db_records,
        config,
        reference,
        infrastructure_errors,
        observation_state,
        observation_id,
    )
    next_observation_sources = report.pop("_nextObservationSources")
    save_observation_state(observation_path, source_ids, next_observation_sources, reference)
    json_path, html_path = output_paths(config)
    atomic_text(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n", 0o600)
    atomic_text(html_path, render_html(report), 0o644)
    print(
        json.dumps(
            {
                "status": report["status"],
                "sourceCount": report["sourceCount"],
                "databaseFeedCount": report["databaseFeedCount"],
                "jsonPath": str(json_path),
                "privateHtmlFile": config.html_name,
                "subscriptionMutation": False,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 1 if report["status"] == "critical" else 0


def main():
    parser = argparse.ArgumentParser(description="Read-only FreshRSS source health monitor")
    parser.add_argument("--explain-db", action="store_true", help="describe the read-only database query")
    args = parser.parse_args()
    if args.explain_db:
        explain_db()
        return 0
    try:
        return run()
    except HealthError as exc:
        code = str(exc) if re.fullmatch(r"[A-Z0-9_]+", str(exc)) else type(exc).__name__
        print(f"SOURCE_HEALTH_FAILED code={code}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"SOURCE_HEALTH_FAILED errorClass={type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
