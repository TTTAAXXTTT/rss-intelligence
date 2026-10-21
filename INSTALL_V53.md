# v5.3 Upgrade / Rebuild Notes

These notes assume the v5.2 package layout and existing private FreshRSS/OpenClaw publisher configuration. They never print or copy secrets into the package.

## 1. Python runtime

```bash
sudo apt-get update
sudo apt-get install -y python3-venv
python3 -m venv /home/ubuntu/.local/share/rss-intelligence-v53-venv
/home/ubuntu/.local/share/rss-intelligence-v53-venv/bin/pip install \
  --requirement /home/ubuntu/rss-intelligence-v5.2/04-daily-pipeline/requirements-v53.txt
```

## 2. Dedicated translator

```bash
OPENCLAW_BIN=/home/ubuntu/bin/openclaw-runtime \
  /home/ubuntu/rss-intelligence-v5.2/00-preflight/configure-v53-translator.sh
```

Restart the OpenClaw gateway through its existing production unit, then validate the configuration with `openclaw config validate`.

## 3. User units

Copy the files from `04-retry-scheduler/` to `/home/ubuntu/.config/systemd/user/` and the executable runner scripts to `/home/ubuntu/.local/bin/`, preserving 0644 unit and 0755 script modes.

```bash
systemctl --user daemon-reload
systemctl --user enable --now \
  rss-v52-freshrss-fetch.timer \
  rss-v52-morning.timer rss-v52-afternoon.timer \
  rss-v53-source-health.timer \
  rss-v53-translation-cleanup.timer \
  rss-v53-daily-catchup-morning.timer \
  rss-v53-daily-catchup-afternoon.timer
```

## 4. Apache privacy headers

```bash
sudo install -m 0644 \
  /home/ubuntu/rss-intelligence-v5.2/05-apache/rss-v53-private-content-headers.conf \
  /etc/apache2/conf-available/rss-v53-private-content-headers.conf
sudo a2enconf rss-v53-private-content-headers
sudo apache2ctl configtest
sudo systemctl reload apache2
```

## 5. Acceptance gates

Run the package self-test and checksum verification from `README_V53.md`, then run one isolated `e2e` review and wait for a real morning or afternoon timer cycle before declaring the deployment complete.

Do not replay an old production stage merely to rebuild presentation files: an old stage can regress processing state. Presentation history is rebuilt by the publisher during the next valid current run.

