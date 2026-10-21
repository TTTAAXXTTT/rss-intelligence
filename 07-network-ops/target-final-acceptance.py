#!/usr/bin/env python3
from __future__ import annotations

import base64
import concurrent.futures
from datetime import datetime, timezone
import hashlib
import html
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo


CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
TRANSLATION_RE = re.compile(r'href="(https://[^"\s]+/translations/[a-f0-9]{64}\.html)"')
SAFE_PAGE = re.compile(r"[a-f0-9]{64}\.html")
AI_REPORT_RE = re.compile(r"AI\s*(?:报告|日报|内容)", re.I)
NOW = datetime.now(timezone.utc)
PACKAGE_ROOT = Path("/home/ubuntu/rss-intelligence-v5.2")
EXPECTED_PACKAGE_FILES = sum(1 for line in (PACKAGE_ROOT / 'checksums.sha256').read_text(encoding='utf-8').splitlines() if line.strip())
FEED_MINIMUMS = {
    "all.xml": 16,
    "priority.xml": 1,
    "reports.xml": 1,
    "brief.xml": 1,
}
RSS_TIMERS = {
    "rss-v52-freshrss-fetch.timer",
    "rss-v52-morning.timer",
    "rss-v52-afternoon.timer",
    "rss-v52-review.timer",
    "rss-v53-source-health.timer",
    "rss-v53-translation-cleanup.timer",
    "rss-v53-daily-catchup-morning.timer",
    "rss-v53-daily-catchup-afternoon.timer",
}
SYSTEM_SERVICES = {
    "apache2.service",
    "docker.service",
    "rss-dnsproxy.service",
}
RELAY_URLS = {
    "https://rss.example.com/.rss-source-bridge-v1/github-freshrss.xml",
    "https://rss.example.com/.rss-source-bridge-v1/github-a2a.xml",
    "https://rss.example.com/.rss-source-bridge-v1/github-mcp.xml",
    "https://rss.example.com/.rss-source-bridge-v1/github-windmill.xml",
    "https://rss.example.com/.rss-source-bridge-v1/huggingface.xml",
    "https://rss.example.com/.rss-source-bridge-v1/github-openclaw.xml",
    "https://rss.example.com/.rss-source-bridge-v1/github-dify.xml",
    "https://rss.example.com/.rss-source-bridge-v1/github-n8n.xml",
    "https://rss.example.com/.rss-source-bridge-v1/github-immich.xml",
    "https://rss.example.com/.rss-source-bridge-v1/github-portainer.xml",
    "https://rss.example.com/.rss-source-bridge-v1/github-traefik.xml",
    "https://rss.example.com/.rss-source-bridge-v1/github-caddy.xml",
    "https://rss.example.com/.rss-source-bridge-v1/oecd.xml",
    "https://rss.example.com/.rss-source-bridge-v1/hackernews-show.xml",
    "https://rss.example.com/.rss-source-bridge-v1/rbi.xml",
    "https://rss.example.com/.rss-source-bridge-v1/occ.xml",
}
RELAY_PREFIX = "https://rss.example.com/.rss-source-bridge-v1/"
DNS_OVERRIDE_HOSTS = {
    "news.ycombinator.com",
    "huggingface.co",
    "www.occ.gov",
    "wp.oecd.ai",
    "rbi.org.in",
}
DNS_OVERRIDE_FILE = Path("/var/lib/rss-dns-overrides/hosts")
# The configured refresh windows are 06:35 and 12:35 Asia/Shanghai; the
# overnight interval is 18 hours, so retain a strict two-hour execution margin.
DNS_OVERRIDE_MAX_AGE_SECONDS = 20 * 60 * 60
HF_PAGE_URL = "https://huggingface.co/blog/asr-benchmark-optimization"
HF_PAGE_RELAY_BASE = "https://rss.example.com/.rss-source-bridge-v1/page"


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in Path("/home/ubuntu/.openclaw/.env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("naive timestamp")
    return parsed.astimezone(timezone.utc)


def text_from_html(fragment: str, *, remove_links: bool = False) -> str:
    value = fragment
    if remove_links:
        value = re.sub(r"<a\b[^>]*>[\s\S]*?</a>", " ", value, flags=re.I)
    value = re.sub(r"<(?:script|style)\b[^>]*>[\s\S]*?</(?:script|style)>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def assert_one_sentence_summary(value: str) -> None:
    summary = re.sub(r"\s+", " ", value).strip()
    assert summary
    assert len(re.findall(r"[。！？]", summary)) <= 1


def assert_report_name_clean(document: str) -> None:
    title = re.search(r"<title\b[^>]*>([\s\S]*?)</title>", document, re.I)
    cover = re.search(
        r'<header\b[^>]*class="[^"]*\bcover\b[^"]*"[^>]*>[\s\S]*?<h1\b[^>]*>([\s\S]*?)</h1>',
        document,
        re.I,
    )
    assert title is not None and cover is not None
    assert not AI_REPORT_RE.search(text_from_html(title.group(1)))
    assert not AI_REPORT_RE.search(text_from_html(cover.group(1)))


def parse_dns_overrides() -> tuple[dict[str, set[str]], int]:
    info = DNS_OVERRIDE_FILE.lstat()
    assert stat.S_ISREG(info.st_mode) and not DNS_OVERRIDE_FILE.is_symlink()
    assert stat.S_IMODE(info.st_mode) == 0o644
    addresses: dict[str, set[str]] = {}
    refreshed_at: datetime | None = None
    for raw in DNS_OVERRIDE_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            match = re.fullmatch(r"# Refreshed UTC: (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", line)
            if match:
                assert refreshed_at is None
                refreshed_at = parse_time(match.group(1))
            continue
        parts = line.split()
        assert len(parts) == 2
        address = ipaddress.ip_address(parts[0])
        host = parts[1].lower().rstrip(".")
        assert isinstance(address, ipaddress.IPv4Address) and address.is_global
        assert host in DNS_OVERRIDE_HOSTS
        addresses.setdefault(host, set()).add(str(address))
    assert set(addresses) == DNS_OVERRIDE_HOSTS
    assert all(values for values in addresses.values())
    assert refreshed_at is not None

    checked_at = datetime.now(timezone.utc)
    file_time = datetime.fromtimestamp(info.st_mtime, timezone.utc)
    file_age = (checked_at - file_time).total_seconds()
    rendered_age = (checked_at - refreshed_at).total_seconds()
    assert 0 <= file_age < DNS_OVERRIDE_MAX_AGE_SECONDS
    assert 0 <= rendered_age < DNS_OVERRIDE_MAX_AGE_SECONDS
    assert abs((file_time - refreshed_at).total_seconds()) < 60
    return addresses, int(file_age)


def fetch(url: str) -> tuple[int, bytes, dict[str, str]]:
    request = urllib.request.Request(url, headers={"User-Agent": "rss-tencent-final-acceptance/1.0"})
    with urllib.request.urlopen(request, timeout=40) as response:
        return response.status, response.read(), {key.lower(): value for key, value in response.headers.items()}


def run_checked(arguments: list[str], *, environment: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=45,
        check=False,
        env=environment,
    )
    assert completed.returncode == 0
    return completed.stdout.strip()


def run_as_ubuntu(arguments: list[str]) -> str:
    environment = dict(os.environ)
    environment.update({
        "HOME": "/home/ubuntu",
        "USER": "ubuntu",
        "LOGNAME": "ubuntu",
        "XDG_RUNTIME_DIR": "/run/user/1000",
    })
    if os.geteuid() == 0:
        return run_checked([
            "runuser", "--user", "ubuntu", "--", "env",
            "HOME=/home/ubuntu",
            "USER=ubuntu",
            "LOGNAME=ubuntu",
            "XDG_RUNTIME_DIR=/run/user/1000",
            *arguments,
        ])
    assert os.geteuid() == 1000
    return run_checked(arguments, environment=environment)


def verify_package() -> int:
    assert PACKAGE_ROOT.is_dir() and not PACKAGE_ROOT.is_symlink()
    build = json.loads((PACKAGE_ROOT / "BUILD_INFO_V54.json").read_text(encoding="utf-8"))
    assert build.get("package") == "rss-intelligence-v5.4-final"
    assert build.get("version") == "5.4.0"
    assert build.get("productionDirectory") == str(PACKAGE_ROOT)

    package_resolved = PACKAGE_ROOT.resolve(strict=True)
    verified = 0
    seen: set[str] = set()
    for raw in (PACKAGE_ROOT / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        expected, relative = raw.split(maxsplit=1)
        relative = relative.strip().lstrip("*")
        assert re.fullmatch(r"[a-f0-9]{64}", expected)
        assert relative not in seen
        seen.add(relative)
        candidate = PACKAGE_ROOT / relative
        assert candidate.is_file() and not candidate.is_symlink()
        assert candidate.resolve(strict=True).is_relative_to(package_resolved)
        assert hashlib.sha256(candidate.read_bytes()).hexdigest() == expected
        verified += 1
    assert verified == EXPECTED_PACKAGE_FILES
    return verified


def verify_services() -> tuple[str, int]:
    for service in sorted(SYSTEM_SERVICES):
        assert run_checked(["systemctl", "is-active", service]) == "active"
        assert run_checked(["systemctl", "is-enabled", service]) in {"enabled", "static"}
    run_checked(["apache2ctl", "configtest"])

    docker_state = json.loads(run_checked([
        "docker", "inspect", "freshrss", "--format", "{{json .State}}",
    ]))
    assert docker_state.get("Running") is True
    assert docker_state.get("Status") == "running"
    assert (docker_state.get("Health") or {}).get("Status") == "healthy"

    assert run_as_ubuntu(["systemctl", "--user", "is-active", "openclaw-gateway.service"]) == "active"
    assert run_as_ubuntu(["systemctl", "--user", "is-enabled", "openclaw-gateway.service"]) == "enabled"
    openclaw_config = json.loads(run_as_ubuntu([
        "/bin/bash", "-lc", "openclaw config validate --json",
    ]))
    assert isinstance(openclaw_config, dict)
    openclaw_health = json.loads(run_as_ubuntu([
        "/bin/bash", "-lc", "openclaw health --json",
    ]))
    assert isinstance(openclaw_health, dict) and openclaw_health

    for timer in sorted(RSS_TIMERS):
        assert run_as_ubuntu(["systemctl", "--user", "is-active", timer]) == "active"
        assert run_as_ubuntu(["systemctl", "--user", "is-enabled", timer]) == "enabled"
    unit_rows = run_as_ubuntu([
        "systemctl", "--user", "list-units", "--type=timer", "--state=active",
        "--plain", "--no-legend", "--no-pager",
    ])
    active_rss_timers = {
        line.split()[0]
        for line in unit_rows.splitlines()
        if line.split() and line.split()[0].startswith("rss-")
    }
    assert active_rss_timers == RSS_TIMERS
    return str((docker_state.get("Health") or {}).get("Status")), len(active_rss_timers)


def verify_dns_automation() -> tuple[int, int]:
    timer = "rss-dns-override-update.timer"
    service = "rss-dns-override-update.service"
    assert run_checked(["systemctl", "is-enabled", timer]) == "enabled"
    assert run_checked(["systemctl", "is-active", timer]) == "active"
    assert run_checked(["systemctl", "show", service, "--property=Result", "--value"]) == "success"
    assert run_checked(["systemctl", "show", service, "--property=ExecMainStatus", "--value"]) == "0"
    updater = Path("/usr/local/sbin/rss-dns-override-update")
    assert updater.is_file() and not updater.is_symlink()
    assert "https://rss.example.com/.rss-source-bridge-v1/dns-overrides.json" in updater.read_text(encoding="utf-8")

    expected, file_age = parse_dns_overrides()
    for host in sorted(DNS_OVERRIDE_HOSTS):
        host_addresses = {
            row[4][0]
            for row in socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
        }
        assert host_addresses == expected[host]
        container_rows = run_checked([
            "docker", "exec", "freshrss", "getent", "ahostsv4", host,
        ])
        container_addresses: set[str] = set()
        for raw in container_rows.splitlines():
            fields = raw.split()
            if not fields:
                continue
            address = ipaddress.ip_address(fields[0])
            assert isinstance(address, ipaddress.IPv4Address)
            container_addresses.add(str(address))
        assert container_addresses == expected[host]
    return len(expected), file_age


def assert_relay_not_stale(headers: dict[str, str]) -> None:
    warning = headers.get("warning", "")
    assert not re.search(r"(?:^|\D)110(?:\D|$)|stale", warning, re.I)


def verify_huggingface_page_relay() -> int:
    unique_query = hashlib.sha256(os.urandom(32)).hexdigest()[:24]
    source_url = f"{HF_PAGE_URL}?rss_acceptance={unique_query}"
    token = base64.urlsafe_b64encode(source_url.encode("utf-8")).decode("ascii").rstrip("=")
    url = f"{HF_PAGE_RELAY_BASE}/huggingface.co/{token}"
    status_code, body, headers = fetch(url)
    assert status_code == 200 and len(body) >= 10000
    assert_relay_not_stale(headers)
    assert "no-store" in headers.get("cache-control", "").lower()
    assert "html" in headers.get("content-type", "").lower()
    decoded = body.decode("utf-8", "replace")
    assert "<html" in decoded[:10000].lower()
    page_text = text_from_html(decoded)
    assert len(page_text) >= 300
    assert "benchmark" in page_text.lower()
    assert "asr" in page_text.lower() or "speech" in page_text.lower()
    return len(page_text)


def main() -> None:
    package_file_count = verify_package()
    docker_health, active_timer_count = verify_services()
    dns_override_host_count, dns_override_age_seconds = verify_dns_automation()
    huggingface_page_chars = verify_huggingface_page_relay()
    env = read_env()
    token = env.get("RSS_PUBLISH_TOKEN", "")
    base = env.get("RSS_PUBLISH_BASE_URL", "").rstrip("/")
    assert re.fullmatch(r"[A-Fa-f0-9]{64}", token)
    assert base == "https://rss.example.org/intelligence"
    feed_base = f"{base}/{token}"
    public_root = Path(env.get("RSS_PUBLISH_PUBLIC_ROOT", "/var/www/rss-intelligence"))
    public = public_root / token
    assert public.is_dir() and not public.is_symlink()
    assert stat.S_IMODE(public.stat().st_mode) == 0o711

    translation_root = Path(env.get(
        "RSS_TRANSLATION_ROOT",
        "/home/ubuntu/.openclaw/rss-intelligence/translation-v53",
    ))
    active_pages: dict[str, dict] = {}
    active_manifest_urls: dict[tuple[str, str, str], set[str]] = {}
    active_manifest_events: dict[tuple[str, str, str], set[str]] = {}
    active_manifest_expiries: dict[tuple[str, str, str], datetime] = {}
    active_manifest_generated: dict[tuple[str, str, str], datetime] = {}
    methods: dict[str, int] = {}
    expiries: list[datetime] = []
    for manifest_path in sorted((translation_root / "manifests").glob("*/*.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expiry = parse_time(manifest["expiresAt"])
        saved = parse_time(manifest.get("firstSavedAt") or manifest["savedAt"])
        assert int((expiry - saved).total_seconds()) == 172800
        if expiry <= NOW:
            continue
        edition = str(manifest.get("edition") or "")
        candidate_run_id = str(manifest.get("candidateRunId") or "")
        stage_id = str(manifest.get("stageId") or "")
        assert edition in {"morning", "afternoon"}
        assert manifest_path.parent.name == edition
        assert candidate_run_id and stage_id
        manifest_key = (candidate_run_id, stage_id, edition)
        assert manifest_key not in active_manifest_urls
        active_manifest_urls[manifest_key] = set()
        active_manifest_events[manifest_key] = set()
        active_manifest_expiries[manifest_key] = expiry
        active_manifest_generated[manifest_key] = parse_time(str(manifest.get("generatedAt") or manifest["savedAt"]))
        expiries.append(expiry)
        rows = manifest.get("items") or []
        assert manifest.get("status") == "complete"
        assert manifest.get("stageItemCount") == manifest.get("itemCount") == len(rows)
        assert rows
        for row in rows:
            assert row.get("status") == "complete"
            assert row.get("sourceCompleteness") == "full"
            assert row.get("truncated") is False
            event_key = str(row.get("eventKey") or "")
            assert event_key and event_key not in active_manifest_events[manifest_key]
            active_manifest_events[manifest_key].add(event_key)
            public_file = str(row.get("publicFile") or "")
            name = Path(public_file).name
            assert public_file == f"translations/{name}" and SAFE_PAGE.fullmatch(name)
            expected_url = f"{feed_base}/{public_file}"
            assert row.get("translatedUrl") == expected_url
            assert expected_url not in active_manifest_urls[manifest_key]
            active_manifest_urls[manifest_key].add(expected_url)
            page = public / public_file
            payload = page.read_bytes()
            assert page.is_file() and not page.is_symlink() and stat.S_IMODE(page.stat().st_mode) == 0o644
            assert hashlib.sha256(payload).hexdigest() == row.get("outputHash")
            text = payload.decode("utf-8")
            assert len(re.findall(r"[\u3400-\u9fff]", text)) >= 20
            if name in active_pages:
                assert active_pages[name].get("outputHash") == row.get("outputHash")
                assert active_pages[name].get("translatedUrl") == row.get("translatedUrl")
            else:
                active_pages[name] = row
            method = str(row.get("sourceMethod") or "missing")
            methods[method] = methods.get(method, 0) + 1
        assert len(active_manifest_urls[manifest_key]) == len(rows)
        assert len(active_manifest_events[manifest_key]) == len(rows)

    active_manifests = len(active_manifest_urls)
    assert active_manifests > 0 and active_pages and expiries

    disk_pages = {
        path.name for path in (public / "translations").glob("*.html")
        if path.is_file() and not path.is_symlink()
    }
    assert disk_pages == set(active_pages)

    data_root = Path(env.get(
        "RSS_PUBLISH_DATA_ROOT",
        "/home/ubuntu/.openclaw/rss-intelligence/publisher-data-v52",
    ))
    stored_reports: dict[tuple[str, str], dict] = {}
    for path in (data_root / "reports").glob("*.json"):
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("schemaVersion") == 1:
            report_id = (str(report.get("dateKey")), str(report.get("reportType")))
            assert report_id not in stored_reports
            stored_reports[report_id] = report

    manifest_report_ids: dict[tuple[str, str, str], tuple[str, str]] = {}
    active_report_ids: dict[tuple[str, str], tuple[str, str, str]] = {}
    for report_id, report in stored_reports.items():
        report_manifest_key = (
            str(report.get("candidateRunId") or ""),
            str(report.get("stageId") or ""),
            str(report.get("reportType") or ""),
        )
        if report_manifest_key not in active_manifest_urls:
            continue
        assert report_manifest_key not in manifest_report_ids
        report_title = str(report.get("title") or "").strip()
        assert report_title and not AI_REPORT_RE.search(report_title)
        rows = report.get("translationItems") or []
        assert int(report.get("itemCount") or 0) == len(rows) > 0
        report_urls = {str(row.get("translatedUrl") or "") for row in rows}
        report_events = {str(row.get("eventKey") or "") for row in rows}
        assert "" not in report_urls and "" not in report_events
        assert len(report_urls) == len(rows) and len(report_events) == len(rows)
        assert report_urls == active_manifest_urls[report_manifest_key]
        assert report_events == active_manifest_events[report_manifest_key]
        assert parse_time(str(report.get("translationExpiresAt"))) == active_manifest_expiries[report_manifest_key]
        manifest_report_ids[report_manifest_key] = report_id
        active_report_ids[report_id] = report_manifest_key
    assert manifest_report_ids and set(manifest_report_ids).issubset(active_manifest_urls)
    superseded_manifests = set(active_manifest_urls) - set(manifest_report_ids)
    shanghai = ZoneInfo("Asia/Shanghai")
    for manifest_key in superseded_manifests:
        edition = manifest_key[2]
        local_date = active_manifest_generated[manifest_key].astimezone(shanghai).date().isoformat()
        current_key = active_report_ids[(local_date, edition)]
        assert active_manifest_generated[current_key] > active_manifest_generated[manifest_key]

    feed_counts: dict[str, int] = {}
    feed_hashes: dict[str, str] = {}
    feed_translation_links: dict[str, set[str]] = {}
    report_ids_by_feed: dict[str, set[tuple[str, str]]] = {}
    report_pages: list[str] = []
    for name in ("all.xml", "priority.xml", "reports.xml", "brief.xml"):
        path = public / name
        raw = path.read_bytes()
        root = ET.fromstring(raw)
        items = root.findall("./channel/item")
        feed_counts[name] = len(items)
        assert len(items) >= FEED_MINIMUMS[name]
        feed_hashes[name] = hashlib.sha256(raw).hexdigest()
        channel_title = root.findtext("./channel/title") or ""
        assert channel_title.strip() and not AI_REPORT_RE.search(channel_title)
        status_code, public_raw, headers = fetch(f"{feed_base}/{name}")
        assert status_code == 200 and public_raw == raw
        assert "application/rss+xml" in headers.get("content-type", "") or "xml" in headers.get("content-type", "")
        links: set[str] = set()
        feed_report_ids: set[tuple[str, str]] = set()
        for item in items:
            body_node = item.find(CONTENT)
            body = body_node.text if body_node is not None and body_node.text else ""
            found = TRANSLATION_RE.findall(body)
            assert len(found) == len(set(found))
            if name in {"all.xml", "priority.xml"}:
                assert len(found) == 1
                description = (item.findtext("description") or "").strip()
                assert description and "\n" not in description
                assert_one_sentence_summary(description)
                assert "/translations/" not in (item.findtext("link") or "")
            else:
                parts = (item.findtext("guid") or "").split(":")
                assert len(parts) == 4 and parts[0] == "rss-report"
                expected_kind = "full" if name == "reports.xml" else "brief"
                assert parts[1] == expected_kind
                item_title = (item.findtext("title") or "").strip()
                assert item_title and not AI_REPORT_RE.search(item_title)
                report_id = (parts[2], parts[3])
                assert report_id in active_report_ids and report_id not in feed_report_ids
                feed_report_ids.add(report_id)
                report = stored_reports[report_id]
                assert len(found) == int(report.get("itemCount") or 0) > 0
                report_urls = {
                    str(row.get("translatedUrl") or "")
                    for row in (report.get("translationItems") or [])
                }
                assert set(found) == report_urls
                if name == "brief.xml":
                    brief_items = re.findall(r"<li\b[^>]*>([\s\S]*?)</li>", body, re.I)
                    assert len(brief_items) == int(report.get("itemCount") or 0)
                    for fragment in brief_items:
                        without_translation = re.sub(
                            r'<a\b[^>]*href="https://[^"\s]+/translations/[a-f0-9]{64}\.html"[^>]*>[\s\S]*?</a>',
                            " ",
                            fragment,
                            flags=re.I,
                        )
                        summary_text = text_from_html(without_translation)
                        summary_text = re.sub(r"^\[[SABC]\]\s*", "", summary_text)
                        assert_one_sentence_summary(summary_text)
                report_pages.append(item.findtext("link") or "")
            assert links.isdisjoint(found)
            links.update(found)
        feed_translation_links[name] = links
        report_ids_by_feed[name] = feed_report_ids

    active_urls = {f"{feed_base}/translations/{name}" for name in active_pages}
    manifest_urls = set().union(*active_manifest_urls.values())
    assert manifest_urls == active_urls
    assert feed_translation_links["all.xml"].issubset(active_urls)
    current_report_urls = set().union(
        *(active_manifest_urls[key] for key in manifest_report_ids)
    )
    assert feed_translation_links["reports.xml"] == current_report_urls
    assert feed_translation_links["brief.xml"] == feed_translation_links["reports.xml"]
    assert feed_translation_links["priority.xml"].issubset(feed_translation_links["all.xml"])
    assert len(feed_translation_links["all.xml"]) == feed_counts["all.xml"]
    assert len(feed_translation_links["priority.xml"]) == feed_counts["priority.xml"]
    assert report_ids_by_feed["all.xml"] == report_ids_by_feed["priority.xml"] == set()
    assert report_ids_by_feed["reports.xml"] == set(active_report_ids)
    assert report_ids_by_feed["brief.xml"] == set(active_report_ids)
    assert feed_counts["reports.xml"] == feed_counts["brief.xml"] == len(active_report_ids)

    def verify_translation(url: str) -> int:
        status_code, body, headers = fetch(url)
        text = body.decode("utf-8")
        name = Path(urllib.parse.urlsplit(url).path).name
        assert status_code == 200
        assert hashlib.sha256(body).hexdigest() == active_pages[name]["outputHash"]
        assert "no-store" in headers.get("cache-control", "").lower()
        assert "text/html" in headers.get("content-type", "").lower()
        assert "noindex" in text.lower()
        assert len(re.findall(r"[\u3400-\u9fff]", text)) >= 20
        return len(body)

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        translation_sizes = list(pool.map(verify_translation, sorted(active_urls)))

    unique_report_pages = sorted({url for url in report_pages if url})
    assert len(unique_report_pages) == len(active_report_ids) * 2
    for url in unique_report_pages:
        parsed = urllib.parse.urlsplit(url)
        assert parsed.scheme == "https" and parsed.hostname == "rss.example.org"
        status_code, body, headers = fetch(url)
        text = body.decode("utf-8")
        assert status_code == 200 and text.count('class="cover"') == 1
        assert "noindex" in text.lower() and "no-store" in headers.get("cache-control", "").lower()
        assert_report_name_clean(text)

    health = json.loads(Path(
        "/home/ubuntu/.openclaw/rss-intelligence/source-health-v53/latest.json"
    ).read_text(encoding="utf-8"))
    health_generated_at = parse_time(str(health.get("generatedAt") or ""))
    health_age_seconds = (datetime.now(timezone.utc) - health_generated_at).total_seconds()
    assert 0 <= health_age_seconds < 7200
    summary = health.get("summary") or {}
    counts = summary.get("statusCounts") or {}
    assert health.get("expectedSourceCount") == 49
    assert health.get("sourceCount") == 49
    assert health.get("databaseFeedCount") == 49
    assert sum(int(counts.get(key, 0)) for key in ("ok", "watch", "error")) == 49
    assert counts.get("error", 0) == 0
    assert summary.get("databaseMissingSources", 0) == 0
    assert summary.get("freshRssErrorSources", 0) == 0
    assert summary.get("emptySources", 0) == 0
    assert not (health.get("infrastructureErrors") or [])

    database = sqlite3.connect(
        "file:/var/lib/docker/volumes/freshrss_data/_data/users/rssowner/db.sqlite?mode=ro",
        uri=True,
    )
    database.execute("PRAGMA query_only=ON")
    assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    database_rows = database.execute(
        "SELECT id, url, COALESCE(error, 0) FROM feed ORDER BY id"
    ).fetchall()
    database.close()
    database_feed_count = len(database_rows)
    database_error_count = sum(int(row[2]) != 0 for row in database_rows)
    assert database_feed_count == 49 and database_error_count == 0
    database_relay_urls = {
        str(row[1]) for row in database_rows if str(row[1]).startswith(RELAY_PREFIX)
    }
    assert len(database_relay_urls) == 16
    assert database_relay_urls == RELAY_URLS

    def verify_relay(url: str) -> tuple[bool, int]:
        try:
            status_code, body, headers = fetch(url)
            if status_code != 200 or not body:
                return False, 0
            assert_relay_not_stale(headers)
            root = ET.fromstring(body)
            return bool(root.tag), len(body)
        except Exception:
            return False, 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        relay_results = list(pool.map(verify_relay, sorted(RELAY_URLS)))
    assert len(relay_results) == 16 and all(result[0] for result in relay_results)
    relay_bytes = sum(result[1] for result in relay_results)

    print(json.dumps({
        "status": "TENCENT_FINAL_ACCEPTANCE_OK",
        "packageVersion": "5.4.0",
        "packageFilesVerified": package_file_count,
        "systemServicesActive": len(SYSTEM_SERVICES),
        "dockerHealth": docker_health,
        "openClawHealth": "ok",
        "rssUserTimersActive": active_timer_count,
        "dnsOverrideTimer": "active",
        "dnsOverrideHosts": dns_override_host_count,
        "dnsOverrideAgeSeconds": dns_override_age_seconds,
        "huggingFacePageRelayChars": huggingface_page_chars,
        "feedCounts": feed_counts,
        "feedHashes": feed_hashes,
        "activeManifests": active_manifests,
        "supersededRetainedManifests": len(superseded_manifests),
        "translationPages": len(active_pages),
        "translationBytes": sum(translation_sizes),
        "translationMethods": dict(sorted(methods.items())),
        "reportPages": len(unique_report_pages),
        "earliestExpiryUtc": min(expiries).isoformat().replace("+00:00", "Z"),
        "latestExpiryUtc": max(expiries).isoformat().replace("+00:00", "Z"),
        "sourceHealth": {
            "ok": counts.get("ok", 0),
            "watch": counts.get("watch", 0),
            "error": counts.get("error", 0),
            "ageSeconds": int(health_age_seconds),
        },
        "databaseFeedCount": database_feed_count,
        "databaseErrorCount": database_error_count,
        "databaseRelayCount": len(database_relay_urls),
        "relayHttpXmlOk": len(relay_results),
        "relayFreshResponses": len(relay_results),
        "relayBytes": relay_bytes,
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"TENCENT_FINAL_ACCEPTANCE_FAILED errorClass={type(exc).__name__}")
        raise SystemExit(1) from None
