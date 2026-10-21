#!/usr/bin/env python3
"""Atomically switch the four mainland-blocked FreshRSS feeds to the relay."""

from __future__ import annotations

import json
import sqlite3


DB = "/var/lib/docker/volumes/freshrss_data/_data/users/rssowner/db.sqlite"
MAPPINGS = {
    1: (
        "https://github.com/FreshRSS/FreshRSS/releases.atom",
        "https://rss.example.com/.rss-source-bridge-v1/github-freshrss.xml",
    ),
    3: (
        "https://github.com/a2aproject/A2A/releases.atom",
        "https://rss.example.com/.rss-source-bridge-v1/github-a2a.xml",
    ),
    5: (
        "https://github.com/modelcontextprotocol/modelcontextprotocol/releases.atom",
        "https://rss.example.com/.rss-source-bridge-v1/github-mcp.xml",
    ),
    10: (
        "https://github.com/windmill-labs/windmill/releases.atom",
        "https://rss.example.com/.rss-source-bridge-v1/github-windmill.xml",
    ),
    11: (
        "https://huggingface.co/blog/feed.xml",
        "https://rss.example.com/.rss-source-bridge-v1/huggingface.xml",
    ),
    12: (
        "https://github.com/openclaw/openclaw/releases.atom",
        "https://rss.example.com/.rss-source-bridge-v1/github-openclaw.xml",
    ),
    13: (
        "https://github.com/langgenius/dify/releases.atom",
        "https://rss.example.com/.rss-source-bridge-v1/github-dify.xml",
    ),
    14: (
        "https://github.com/n8n-io/n8n/releases.atom",
        "https://rss.example.com/.rss-source-bridge-v1/github-n8n.xml",
    ),
    15: (
        "https://github.com/immich-app/immich/releases.atom",
        "https://rss.example.com/.rss-source-bridge-v1/github-immich.xml",
    ),
    16: (
        "https://github.com/portainer/portainer/releases.atom",
        "https://rss.example.com/.rss-source-bridge-v1/github-portainer.xml",
    ),
    17: (
        "https://github.com/traefik/traefik/releases.atom",
        "https://rss.example.com/.rss-source-bridge-v1/github-traefik.xml",
    ),
    18: (
        "https://github.com/caddyserver/caddy/releases.atom",
        "https://rss.example.com/.rss-source-bridge-v1/github-caddy.xml",
    ),
    47: (
        "https://www.occ.gov/rss/occ_news.xml",
        "https://rss.example.com/.rss-source-bridge-v1/occ.xml",
    ),
    21: (
        "https://wp.oecd.ai/feed/",
        "https://rss.example.com/.rss-source-bridge-v1/oecd.xml",
    ),
    41: (
        "https://rbi.org.in/pressreleases_rss.xml",
        "https://rss.example.com/.rss-source-bridge-v1/rbi.xml",
    ),
    38: (
        "https://news.ycombinator.com/showrss",
        "https://rss.example.com/.rss-source-bridge-v1/hackernews-show.xml",
    ),
}


def without_simplepie_hash(raw: str | None) -> str:
    try:
        attributes = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError("invalid feed attributes JSON") from exc
    if not isinstance(attributes, dict):
        raise RuntimeError("feed attributes are not an object")
    attributes.pop("SimplePieHash", None)
    return json.dumps(attributes, ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    connection = sqlite3.connect(DB, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("BEGIN IMMEDIATE")
    try:
        changed: list[int] = []
        for feed_id, (original, relay) in MAPPINGS.items():
            row = connection.execute(
                "SELECT id, url, attributes FROM feed WHERE id = ?", (feed_id,)
            ).fetchone()
            if row is None:
                raise RuntimeError(f"feed id {feed_id} is missing")
            if row["url"] not in {original, relay}:
                raise RuntimeError(f"feed id {feed_id} has an unexpected URL")
            if row["url"] == relay:
                continue
            attributes = without_simplepie_hash(row["attributes"])
            cursor = connection.execute(
                "UPDATE feed SET url = ?, lastUpdate = 0, error = 0, attributes = ? "
                "WHERE id = ? AND url = ?",
                (relay, attributes, feed_id, original),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"feed id {feed_id} was not updated")
            changed.append(feed_id)

        for feed_id, (_original, relay) in MAPPINGS.items():
            url = connection.execute("SELECT url FROM feed WHERE id = ?", (feed_id,)).fetchone()[0]
            if url != relay:
                raise RuntimeError(f"feed id {feed_id} verification failed")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()

    print(json.dumps({"changed_feed_ids": changed, "verified": len(MAPPINGS)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
