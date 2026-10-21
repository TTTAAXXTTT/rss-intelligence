# RSS Intelligence Stage V5.2

OpenClaw private staging plugin for V5.2. It exposes only `rss_daily_finalize_save_v52`.

The tool validates exact accounting for the prepared candidate set and writes schemaVersion 2 private Stage JSON under `finalizer-stage-v52`. It never publishes public RSS and never mutates FreshRSS subscriptions.

## Generated metadata workflow (R3)

This package uses `defineToolPlugin`; therefore `openclaw.plugin.json` is generated metadata, not an independently maintained tool list.

On the target OpenClaw host:

```bash
npm run plugin:build
npm run plugin:validate
npm test
```

`plugin:build` runs TypeScript build and `openclaw plugins build`. `plugin:validate` runs a fresh TypeScript build, `plugins build --check`, then `plugins validate`. Do not replace this with `npm run build && plugins validate`.
