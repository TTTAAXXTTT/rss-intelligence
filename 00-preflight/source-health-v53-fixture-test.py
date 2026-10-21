#!/usr/bin/env python3
"""Offline policy fixture for the read-only source health monitor."""

import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


HERE = Path(__file__).resolve().parent
SOURCE = HERE / "source-health.py"
if not SOURCE.is_file():
    SOURCE = HERE.parent / "04-daily-pipeline" / "source-health.py"
spec = importlib.util.spec_from_file_location("source_health_v53", SOURCE)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

now = datetime(2026, 8, 15, 5, 0, tzinfo=timezone.utc)
config = SimpleNamespace(freshrss_stale_hours=36.0, stale_days=14.0, freshrss_error_confirmations=3)
subscription = {
    "sourceId": "fixture-source",
    "title": "Fixture Source",
    "host": "example.com",
    "categories": ["fixture"],
}
direct_ok = {
    "http": 200,
    "parse": "ok",
    "items": 2,
    "newestItemAt": now.isoformat(),
    "ageDays": 0.0,
    "errorClass": None,
}


def db(error, updated_hours=1):
    timestamp = int((now - timedelta(minutes=1)).timestamp()) if error else 0
    return {
        "lastUpdate": int((now - timedelta(hours=updated_hours)).timestamp()),
        "newestEntry": int((now - timedelta(hours=2)).timestamp()),
        "lastSeen": int((now - timedelta(hours=1)).timestamp()),
        "error": timestamp,
        "ttl": 0,
        "cacheEntries": 2,
        "cacheUnreads": 1,
        "entryCount": 2,
        "attributeErrorKeys": [],
    }


first = module.evaluate(subscription, direct_ok, db(True), config, now, None, "2026-08-15T07:03")
assert first["status"] == "watch"
assert first["freshRss"]["consecutiveErrorObservations"] == 1
assert "freshrss_error_persistent" not in first["reasons"]

# Re-running the same scheduled slot or running manually must not inflate it.
same = module.evaluate(
    subscription, direct_ok, db(True), config, now, first["_counterState"], "2026-08-15T07:03"
)
assert same["status"] == "watch"
assert same["freshRss"]["consecutiveErrorObservations"] == 1
manual = module.evaluate(subscription, direct_ok, db(True), config, now, same["_counterState"], None)
assert manual["status"] == "watch"
assert manual["_counterState"] == same["_counterState"]

# Adjacent scheduled slots, not mutable error text/timestamps, advance the streak.
second_db = db(True)
second = module.evaluate(
    subscription, direct_ok, second_db, config, now, first["_counterState"], "2026-08-15T13:03"
)
assert second["status"] == "watch"
assert second["freshRss"]["consecutiveErrorObservations"] == 2
third_db = db(True)
third = module.evaluate(
    subscription, direct_ok, third_db, config, now, second["_counterState"], "2026-08-16T07:03"
)
assert third["status"] == "error"
assert third["freshRss"]["consecutiveErrorObservations"] == 3
assert "freshrss_error_persistent" in third["reasons"]

# Attribute-only observations use the same slot counter and cannot be inflated manually.
attribute_db = db(False)
attribute_db["attributeErrorKeys"] = ["http_error"]
attribute_first = module.evaluate(
    subscription, direct_ok, attribute_db, config, now, None, "2026-08-15T07:03"
)
attribute_manual = module.evaluate(
    subscription, direct_ok, attribute_db, config, now, attribute_first["_counterState"], None
)
assert attribute_manual["status"] == "watch"
assert attribute_manual["_counterState"]["consecutiveErrorSlots"] == 1

# Recovery resets on a scheduled slot; stale/missing/empty DB state is immediate error.
recovered = module.evaluate(
    subscription, direct_ok, db(False), config, now, third["_counterState"], "2026-08-16T13:03"
)
assert recovered["status"] == "ok"
assert recovered["freshRss"]["consecutiveErrorObservations"] == 0
stale_db = db(True, updated_hours=48)
stale = module.evaluate(subscription, direct_ok, stale_db, config, now, None, "2026-08-15T07:03")
assert stale["status"] == "error" and "freshrss_last_update_stale" in stale["reasons"]
missing = module.evaluate(subscription, direct_ok, None, config, now)
assert missing["status"] == "error" and "freshrss_database_row_missing" in missing["reasons"]
empty_db = db(False)
empty_db["entryCount"] = 0
empty_db["cacheEntries"] = 0
empty = module.evaluate(subscription, direct_ok, empty_db, config, now)
assert empty["status"] == "error" and "freshrss_cache_empty" in empty["reasons"]

# Gaps reset the streak instead of carrying stale historical counts forever.
gap = module.evaluate(
    subscription, direct_ok, db(True), config, now, second["_counterState"], "2026-08-17T13:03"
)
assert gap["freshRss"]["consecutiveErrorObservations"] == 1

with tempfile.TemporaryDirectory(prefix="source-health-state-") as temporary:
    root = Path(temporary)
    path = root / "observation-state.json"
    ids = ["fixture-source"]
    state_rows = {"fixture-source": second["_counterState"]}
    module.save_observation_state(path, ids, state_rows, now)
    if os.name == "posix":
        os.chmod(path, 0o600)
    loaded = module.load_observation_state(path, ids, now, 3)
    assert loaded["sources"] == state_rows
    forged = json.loads(path.read_text(encoding="utf-8"))
    forged["sources"]["fixture-source"]["consecutiveErrorSlots"] = 999
    path.write_text(json.dumps(forged), encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)
    try:
        module.load_observation_state(path, ids, now, 3)
        raise AssertionError("forged observation counter was accepted")
    except module.HealthError as exc:
        assert str(exc) == "OBSERVATION_STATE_COUNTER_INVALID"

print("SOURCE_HEALTH_V53_FIXTURES_OK")
