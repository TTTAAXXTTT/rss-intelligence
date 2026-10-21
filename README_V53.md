# Content Subscription System v5.3

This release extends the compatible v5.2 production tree with:

- one-sentence `brief.xml` reports;
- complete item-by-item reports and simple HTML covers;
- best-effort Chinese reading pages with a fixed 48-hour first-generation lifetime;
- a read-only 49-source health monitor;
- smaller validated classifier batches and per-batch structural retries;
- guarded 08:30 / 14:30 catch-up checks;
- strict XML, translation-manifest, path, ownership and timestamp validation.

The production path remains `/home/ubuntu/rss-intelligence-v5.2` so existing units and state paths do not change.

## Runtime

- Ubuntu system packages: `python3`, `python3-venv`, `apache2`, `libapache2-mod-proxy-html`, `jq`, `curl`, `docker.io`.
- Python environment: `/home/ubuntu/.local/share/rss-intelligence-v53-venv` from `04-daily-pipeline/requirements-v53.txt`.
- Dedicated translation agent: `rss-v53-translator`, Flash primary model, agent-scoped sandbox, no workspace access, only `session_status` allowed.
- Existing FreshRSS, OpenClaw and private publisher environment configuration are reused; no credentials are shipped.

## Schedule (Asia/Shanghai)

- FreshRSS: 07:00 / 13:00
- Source health: 07:03 / 13:03
- Morning preparation: 07:10; guarded catch-up: 08:30
- Afternoon preparation: 13:10; guarded catch-up: 14:30
- Translation expiry cleanup: every minute

## Verification

```bash
/home/ubuntu/rss-intelligence-v5.2/00-preflight/package-selftest.sh \
  /home/ubuntu/rss-intelligence-v5.2

cd /home/ubuntu/rss-intelligence-v5.2
sha256sum -c checksums.sha256

systemctl --user list-timers --all --no-pager
systemctl --user status rss-v53-source-health.timer --no-pager
systemctl --user status rss-v53-translation-cleanup.timer --no-pager
```

See the separately delivered Chinese project manual and production acceptance report for user-facing operation and the live acceptance record.

