#!/usr/bin/env python3
"""Refresh trusted IPv4 overrides through the restricted AWS egress relay."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.request


ENDPOINT = "https://rss.example.com/.rss-source-bridge-v1/dns-overrides.json"
HOSTS = (
    "news.ycombinator.com",
    "huggingface.co",
    "www.occ.gov",
    "wp.oecd.ai",
    "rbi.org.in",
)

# Every returned address must remain inside a reviewed provider prefix.  The
# prefixes are deliberately wider than one observed address so normal load
# balancing does not freeze the service onto a single IP, while still failing
# closed if the DNS response is redirected to an unrelated network.
TRUSTED_NETWORKS = {
    "news.ycombinator.com": (
        ipaddress.ip_network("209.216.230.0/24"),
    ),
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
    "www.occ.gov": (
        ipaddress.ip_network("199.83.40.0/24"),
    ),
    "wp.oecd.ai": (
        ipaddress.ip_network("82.196.10.0/24"),
    ),
    "rbi.org.in": (
        ipaddress.ip_network("13.107.226.0/24"),
        ipaddress.ip_network("13.107.253.0/24"),
        ipaddress.ip_network("150.171.110.0/24"),
    ),
}

# Addresses repeatedly returned for unrelated services by the affected route.
BLOCKED = tuple(
    ipaddress.ip_network(value)
    for value in (
        "31.13.64.0/18",
        "69.63.176.0/20",
        "74.86.0.0/16",
        "104.244.40.0/21",
        "108.160.160.0/20",
        "157.240.0.0/16",
        "162.125.0.0/16",
        "185.45.6.0/23",
        "202.160.128.0/22",
        "209.95.56.0/24",
    )
)


def query_all() -> dict[str, list[ipaddress.IPv4Address]]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(
                ENDPOINT,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "rss-source-resolver/1.0",
                },
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read())
            if payload.get("schemaVersion") != 1 or payload.get("status") != "ok":
                raise RuntimeError("relay DNS payload identity invalid")
            raw = payload.get("addresses")
            if not isinstance(raw, dict) or set(raw) != set(HOSTS):
                raise RuntimeError("relay DNS host set invalid")
            results: dict[str, list[ipaddress.IPv4Address]] = {}
            for host in HOSTS:
                values = raw.get(host)
                if not isinstance(values, list):
                    raise RuntimeError("relay DNS answer invalid")
                results[host] = sorted(
                    {ipaddress.ip_address(str(value)) for value in values},
                    key=int,
                )
            validate(results)
            return results
        except Exception as exc:  # retry transient network and parse failures
            last_error = exc
            if attempt < 2:
                time.sleep(1.0 + attempt)
    raise RuntimeError(f"relay DNS lookup failed: {last_error}")


def validate(results: dict[str, list[ipaddress.IPv4Address]]) -> None:
    expected_hosts = set(HOSTS)
    if set(results) != expected_hosts or set(TRUSTED_NETWORKS) != expected_hosts:
        raise RuntimeError("DNS result or trust-policy host set mismatch")
    for host in HOSTS:
        addresses = results[host]
        if not addresses or len(addresses) > 16:
            raise RuntimeError(f"{host}: invalid IPv4 answer count")
        networks = TRUSTED_NETWORKS[host]
        for address in addresses:
            if not any(address in network for network in networks):
                raise RuntimeError(f"{host}: address outside trusted provider prefixes: {address}")


def render(results: dict[str, list[ipaddress.IPv4Address]]) -> str:
    lines = [
        "# Managed by rss-dns-override-update; do not edit manually.",
        f"# Refreshed UTC: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
    ]
    for host in HOSTS:
        lines.extend(f"{address} {host}" for address in results[host])
    return "\n".join(lines) + "\n"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/var/lib/rss-dns-overrides/hosts"),
    )
    args = parser.parse_args()

    results = query_all()
    atomic_write(args.output, render(results))
    summary = {host: [str(value) for value in results[host]] for host in HOSTS}
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
