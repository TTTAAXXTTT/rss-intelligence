# RSS Intelligence V5.2 — 当前服务器升级教程

> 适用环境：已经完成 V5.1 安装但未正式 Cutover；V5.1 Morning E2E 因 Page100 / >4000 raw 失败，事故快照已冻结。本文不是从零安装教程。
>
> 原则：旧稳定系统在 V5.2 E2E Ready 前继续运行；V5.1 不再修补；来源初始化不重跑；任何 `STOP` / `FAIL` / `FAILED` 立即停止。

> P50 生产说明：AI 只用于隔离的动态分类批次；Morning/Afternoon 由 `host-finalize.py` 确定性终稿，Review/E2E 由 `host-review.py` 只读生成。V5.2 OpenClaw Finalizer/Review jobs 仅为兼容模板，必须始终 disabled，生产 runners 不再执行 `cron run`。

## 固定当前环境
- Server: `ubuntu@<Lightsail-public-IP>`
- SSH key: `<path-to-Lightsail-PEM>`
- Domain: `rss.example.com`
- FreshRSS: container `freshrss`
- OpenClaw: current stable runtime
- Workspace: `/home/ubuntu/.openclaw/workspace` → Agent `/agent` ro
- V5.1 package: `/home/ubuntu/rss-intelligence-v5.1`
- V5.1 incident: `/home/ubuntu/rss-v51-incident-20260811-130419`
- V5.1 plugin: `/home/ubuntu/openclaw-plugins/rss-intelligence-stage`
- Old stable Fetch timer: `freshrss-actualize.timer`
- Old Morning/Afternoon/Review Job IDs:
  - `5b758d06-5857-5070-abd6-79ffb622e472`
  - `8dfc2dea-0e2c-5e1e-a012-c1d0c1a4d634`
  - `f57c34f5-4333-5156-9efc-d60ec0547010`

---

# 阶段 0：上传与只读 Gate

## 0.1 Windows PowerShell：校验交付 ZIP
```powershell
Get-FileHash "D:\rss-intelligence-v5.2-final.zip" -Algorithm SHA256
```
必须与交付消息中的 SHA256 完全一致。

## 0.2 上传
```powershell
scp -i "<path-to-Lightsail-PEM>" `
  "D:\rss-intelligence-v5.2-final.zip" `
  ubuntu@<Lightsail-public-IP>:/home/ubuntu/rss-intelligence-v5.2-final.zip
```

## 0.3 SSH
```powershell
ssh -i "<path-to-Lightsail-PEM>" ubuntu@<Lightsail-public-IP>
```
必须看到：
```text
ubuntu@example-server:~$
```
后续 Bash 命令只能在该提示符执行，不要在 `PS C:\...>` 下执行。

## 0.4 Server SHA
```bash
sha256sum /home/ubuntu/rss-intelligence-v5.2-final.zip
```
必须与交付 SHA 相同。

## 0.5 防覆盖
```bash
test ! -e /home/ubuntu/rss-intelligence-v5.2 && echo READY || echo STOP_V52_ALREADY_EXISTS
```
必须 `READY`。若已存在，不要 `rm -rf`，先停止。

## 0.6 解压
```bash
cd /home/ubuntu
unzip rss-intelligence-v5.2-final.zip
```

```bash
ls /home/ubuntu/rss-intelligence-v5.2
```
至少应有：
```text
00-preflight
01-init-kit
02-rss-intelligence-stage
03-workspace-files
04-daily-pipeline
04-retry-scheduler
05-apache
06-cutover
REQUIREMENTS_V52.md
README_V52.md
AUDIT_V52.md
INSTALL_V52.md
HISTORY_V51_TO_V52.md
checksums.sha256
```

## 0.7 包内 checksum
```bash
cd /home/ubuntu/rss-intelligence-v5.2
sha256sum -c checksums.sha256
```
所有条目必须 `OK`。

## 0.8 整包 selftest + 真实4000篇事故 replay
```bash
RSS_V52_INCIDENT_ROOT=/home/ubuntu/rss-v51-incident-20260811-130419 \
  bash /home/ubuntu/rss-intelligence-v5.2/00-preflight/package-selftest.sh \
  /home/ubuntu/rss-intelligence-v5.2
```
最终必须同时看到：
```text
INCIDENT_RAW=4000
INCIDENT_BOOTSTRAP_QUARANTINE=3623
INCIDENT_MODEL_INPUT=377
INCIDENT_DYNAMIC_BATCHES=20
INCIDENT_TAIL70_100_QUARANTINED=1240
INCIDENT_AFTERNOON_PERSISTENT_QUARANTINE=3623
INCIDENT_REPLAY_V52_OK
PACKAGE_SELFTEST_OK
```
该步骤不调用模型、不访问 FreshRSS live API、不修改服务器运行状态。

## 0.9 当前环境 Preflight
```bash
bash /home/ubuntu/rss-intelligence-v5.2/00-preflight/preflight.sh
```
最终必须：
```text
PREFLIGHT_OK
```
重点 Gate：
- old stable Fetch timer 仍 enabled；
- old 3 Jobs 仍 enabled；
- V5.1 timers 不得 enabled；
- V5.1 service MainPID 均为 0；
- FreshRSS/Gateway/Apache 正常；
- rss-curator 6 tools 正常；
- Flash/Pro catalog 正常。

## 0.10 模型最小 live test
```bash
bash /home/ubuntu/rss-intelligence-v5.2/00-preflight/model-preflight.sh
```
必须：
```text
PASS FLASH model=opencode-go/deepseek-v4-flash
PASS PRO model=opencode-go/deepseek-v4-pro
MODEL_PREFLIGHT_OK
```
只有这里各调用一次最小模型。

**阶段0 Gate：** checksum + `PACKAGE_SELFTEST_OK` + `PREFLIGHT_OK` + `MODEL_PREFLIGHT_OK` 全部成立才继续。

---

# 阶段 1：迁移可复用历史状态，不迁移 bootstrap

本次**不重新跑来源初始化**。现有 48 个来源继续使用。

## 1.1 Check-only
```bash
python3 /home/ubuntu/rss-intelligence-v5.2/04-daily-pipeline/migrate-v51-runtime.py
```
应看到：
```text
V52_RUNTIME_MIGRATION_PLAN
...
CHECK_ONLY=true
```

## 1.2 Apply
```bash
python3 /home/ubuntu/rss-intelligence-v5.2/04-daily-pipeline/migrate-v51-runtime.py --apply
```
最终必须：
```text
V52_RUNTIME_MIGRATION_OK
```
说明：
- `publisher-data-v51` 若存在则复制为 `publisher-data-v52`；
- `daily-state-v51` 若存在则复制为 `daily-state-v52`；
- target 已存在时不覆盖；
- **绝不从 V5.1 复制 bootstrap state**。

## 1.3 切换 Host Publisher data root 到 V5.2（复用原 token）
V5.1 `.env` 仍可能指向 `publisher-data-v51`，V5.2 E2E 前必须改为 v52。脚本会复用已有 `RSS_PUBLISH_TOKEN`，不会打印 token，并自动备份 `.env`：
```bash
python3 /home/ubuntu/rss-intelligence-v5.2/00-preflight/setup-publish-env.py
```
必须出现：
```text
RSS_PUBLISH_ENV_OK token=reused backup=...
```
若此前从未有 token，也允许 `token=generated`。

只验证非秘密配置：
```bash
grep -E '^(RSS_PUBLISH_DATA_ROOT|RSS_PUBLISH_PUBLIC_ROOT|RSS_PUBLISH_BASE_URL)=' /home/ubuntu/.openclaw/.env
```
其中 data root 必须：
```text
RSS_PUBLISH_DATA_ROOT=/home/ubuntu/.openclaw/rss-intelligence/publisher-data-v52
```
不要执行 `cat /home/ubuntu/.openclaw/.env`，不要把 token 粘贴到聊天。

---

# 阶段 2：安装独立 V5.2 private Stage Plugin

V5.2 不覆盖 V5.1 plugin。

## 2.1 路径 Gate
```bash
test ! -e /home/ubuntu/openclaw-plugins/rss-intelligence-stage-v52 \
  && echo READY \
  || echo STOP_V52_STAGE_DIR_EXISTS
```
必须 `READY`。

同时确认 V5.1 已验证依赖存在：
```bash
test -d /home/ubuntu/openclaw-plugins/rss-intelligence-stage/node_modules \
  && echo V51_BUILD_DEPS_READY \
  || echo STOP_V51_BUILD_DEPS_MISSING
```

## 2.2 安装源码与独立依赖副本
```bash
cp -a \
  /home/ubuntu/rss-intelligence-v5.2/02-rss-intelligence-stage \
  /home/ubuntu/openclaw-plugins/rss-intelligence-stage-v52

cp -a \
  /home/ubuntu/openclaw-plugins/rss-intelligence-stage/node_modules \
  /home/ubuntu/openclaw-plugins/rss-intelligence-stage-v52/node_modules
```

## 2.3 Generated Metadata Build / Check / Validate / Test
`defineToolPlugin` 的 `openclaw.plugin.json` 是 generated metadata，不能只运行 `tsc` 后直接 validate。先由当前服务器的 OpenClaw generator 生成，再用 `--check` 和 validate 做一致性验证：
```bash
cd /home/ubuntu/openclaw-plugins/rss-intelligence-stage-v52
npm run plugin:build
npm run plugin:validate
npm test
```
其中 `plugin:build` = `tsc + openclaw plugins build`；`plugin:validate` 会重新 `tsc`、执行 `plugins build --check`，然后执行 `plugins validate`。必须看到 `Plugin rss-intelligence-stage-v52 is valid.`，且 test 全部 PASS。

额外只读确认 generated manifest：
```bash
python3 - <<'PY'
import json
p='/home/ubuntu/openclaw-plugins/rss-intelligence-stage-v52/openclaw.plugin.json'
d=json.load(open(p,encoding='utf-8'))
assert d.get('contracts',{}).get('tools') == ['rss_daily_finalize_save_v52']
assert d.get('toolMetadata',{}).get('rss_daily_finalize_save_v52',{}).get('optional') is True
assert d.get('activation') == {'onStartup': True}
print('V52_GENERATED_MANIFEST_OK')
PY
```

## 2.4 Link install
```bash
openclaw plugins install --link /home/ubuntu/openclaw-plugins/rss-intelligence-stage-v52
openclaw plugins enable rss-intelligence-stage-v52
```

## 2.5 加入 sandbox plugin allowlist
```bash
ALLOW=$(openclaw config get tools.sandbox.tools.alsoAllow --json 2>/dev/null || printf '[]')
MERGED=$(python3 -c 'import json,sys; a=json.loads(sys.argv[1]); assert isinstance(a,list); print(json.dumps(list(dict.fromkeys(a+["rss-intelligence-stage-v52"]))))' "$ALLOW")
openclaw config set tools.sandbox.tools.alsoAllow "$MERGED" --strict-json
```

## 2.6 Restart + sandbox recreate
```bash
systemctl --user restart openclaw-gateway.service
openclaw sandbox recreate --agent main --force
```

## 2.7 Runtime boundary
```bash
openclaw plugins inspect rss-intelligence-stage-v52 --runtime --json > /tmp/rss-v52-stage-runtime.json
python3 - <<'PY'
import json
p='/tmp/rss-v52-stage-runtime.json'
d=json.load(open(p,encoding='utf-8'))
s=json.dumps(d,ensure_ascii=False)
assert 'loaded' in s
assert 'rss_daily_finalize_save_v52' in s
assert 'rss_intelligence_publish' not in s
assert 'rss_report_publish' not in s
print('V52_PRIVATE_STAGE_RUNTIME_OK')
PY
rm -f /tmp/rss-v52-stage-runtime.json
```
必须：
```text
V52_PRIVATE_STAGE_RUNTIME_OK
```

---

# 阶段 3：安装 Workspace V5.2 Policy/Prompt

```bash
cp -a /home/ubuntu/rss-intelligence-v5.2/03-workspace-files/RSS_PUBLISH_POLICY_V52.md /home/ubuntu/.openclaw/workspace/
cp -a /home/ubuntu/rss-intelligence-v5.2/03-workspace-files/PROMPT_MORNING_V52.md /home/ubuntu/.openclaw/workspace/
cp -a /home/ubuntu/rss-intelligence-v5.2/03-workspace-files/PROMPT_AFTERNOON_V52.md /home/ubuntu/.openclaw/workspace/
cp -a /home/ubuntu/rss-intelligence-v5.2/03-workspace-files/PROMPT_REVIEW_V52.md /home/ubuntu/.openclaw/workspace/
chmod 644 /home/ubuntu/.openclaw/workspace/*_V52.md
```

不要改旧 `RSS_POLICY.md`。

---

# 阶段 4：创建 V5.2 disabled Template Jobs

```bash
bash /home/ubuntu/rss-intelligence-v5.2/06-cutover/create-v52-cron-templates.sh
```
最终必须：
```text
V52_JOB_TEMPLATES_OK file=/home/ubuntu/.openclaw/rss-intelligence/v52-finalizer-jobs.env
```
并看到三个 `VERIFIED_V52_JOB ... disabled=true`。

关键：V5.2 Template Jobs **始终 disabled**，只作兼容/取证。生产 Daily runner 直接调用 `host-finalize.py`，Review runner 直接调用 `host-review.py`；两者都不执行 OpenClaw `cron run`。

---

# 阶段 5：安装 V5.2 runners/systemd，但不启用 timers

## 5.1 Linger
```bash
loginctl show-user ubuntu -p Linger
```
必须：
```text
Linger=yes
```
否则：
```bash
sudo loginctl enable-linger ubuntu
```
再验证一次。

## 5.2 安装 runners
```bash
mkdir -p /home/ubuntu/.local/bin /home/ubuntu/bin /home/ubuntu/.config/systemd/user

cp /home/ubuntu/rss-intelligence-v5.2/04-retry-scheduler/rss-v52-freshrss-fetch-retry.sh /home/ubuntu/.local/bin/
cp /home/ubuntu/rss-intelligence-v5.2/04-retry-scheduler/rss-v52-daily-retry-runner.sh /home/ubuntu/.local/bin/
cp /home/ubuntu/rss-intelligence-v5.2/04-retry-scheduler/rss-v52-review-retry-runner.sh /home/ubuntu/.local/bin/
chmod 700 /home/ubuntu/.local/bin/rss-v52-*.sh
ln -sf "$(command -v openclaw)" /home/ubuntu/bin/openclaw-runtime
```

## 5.3 安装 units
```bash
cp /home/ubuntu/rss-intelligence-v5.2/04-retry-scheduler/*.service /home/ubuntu/.config/systemd/user/
cp /home/ubuntu/rss-intelligence-v5.2/04-retry-scheduler/*.timer /home/ubuntu/.config/systemd/user/
systemctl --user daemon-reload
```

**现在不要 `enable --now`。**

## 5.4 Unit verify
```bash
systemd-analyze --user verify \
  /home/ubuntu/.config/systemd/user/rss-v52-freshrss-fetch.service \
  /home/ubuntu/.config/systemd/user/rss-v52-freshrss-fetch.timer \
  /home/ubuntu/.config/systemd/user/rss-v52-morning.service \
  /home/ubuntu/.config/systemd/user/rss-v52-morning.timer \
  /home/ubuntu/.config/systemd/user/rss-v52-afternoon.service \
  /home/ubuntu/.config/systemd/user/rss-v52-afternoon.timer \
  /home/ubuntu/.config/systemd/user/rss-v52-review.service \
  /home/ubuntu/.config/systemd/user/rss-v52-review.timer
```
无 error 才通过。

## 5.5 Timer 防 catch-up 验证
```bash
grep -H '^Persistent=' /home/ubuntu/.config/systemd/user/rss-v52-*.timer
```
四个必须全部：
```text
Persistent=false
```

## 5.6 Calendar
```bash
systemd-analyze calendar '*-*-* 07:00:00 Asia/Shanghai'
systemd-analyze calendar '*-*-* 08:00:00 Asia/Shanghai'
systemd-analyze calendar '*-*-* 13:00:00 Asia/Shanghai'
systemd-analyze calendar '*-*-* 14:00:00 Asia/Shanghai'
systemd-analyze calendar '*-*-15 03:00:00 Asia/Shanghai'
systemd-analyze calendar '*-*-28 03:00:00 Asia/Shanghai'
```

## 5.7 Apache 只读确认
V5.1 已经安装 `/intelligence/`，所以本次不重新 patch：
```bash
sudo python3 /home/ubuntu/rss-intelligence-v5.2/05-apache/patch_apache_ai_rss.py --check
```
必须显示 target 正确且 `ALREADY_PATCHED=true`。若不是，STOP，不要直接 apply。

---

# 阶段 6：V5.2 E2E（正式 Cutover 前唯一真实链路测试）

不要在旧 07:00 / 08:00 / 13:00 / 14:00 任务正在执行时启动 snapshot。

## 6.0 确认旧 actualize 当前没有正在运行
```bash
systemctl is-active freshrss-actualize.service || true
```
必须是 `inactive` / `failed` 才继续；若为 `active`，此刻不要启动 V5.2 E2E。

## 6.1 V5.2 FreshRSS Fetch E2E
```bash
systemctl --user start --no-block rss-v52-freshrss-fetch.service
```

监控：
```bash
tail -f /home/ubuntu/.openclaw/rss-intelligence/retry-logs/v52-freshrss-fetch.log
```
成功必须出现：
```text
FETCH SUCCESS attempt=...
```
`Ctrl+C` 只退出 tail，不会停止 service。

确认：
```bash
systemctl --user is-active rss-v52-freshrss-fetch.service
```
完成后应 `inactive`。

状态 Gate：
```bash
python3 - <<'PY'
import json
base='/home/ubuntu/.openclaw/rss-intelligence/fetch-state-v52'
r=json.load(open(base+'/last-run.json'))
s=json.load(open(base+'/last-success.json'))
print('lastRunStatus=',r.get('status'))
print('sameRunId=',r.get('runId')==s.get('runId'))
print('schema2=',r.get('schemaVersion')==2 and s.get('schemaVersion')==2)
PY
```
必须：status ok / sameRunId True / schema2 True。

## 6.2 Morning V5.2 E2E
```bash
systemctl --user reset-failed rss-v52-morning.service 2>/dev/null || true
systemctl --user start --no-block rss-v52-morning.service
```

监控只看关键事件：
```bash
tail -f /home/ubuntu/.openclaw/rss-intelligence/retry-logs/v52-morning.log \
| grep --line-buffered -E \
'SNAPSHOT_PAGE_OK|SNAPSHOT_COMPLETE|PREPARE_PLAN_READY|BATCH_OK|PREPARE_YIELD|PREPARE_COMPLETE|HOST_FINALIZER_START|HOST_FINAL_STAGE_READY|SUCCESS|FAILURE|CANCEL'
```

V5.2 与 V5.1 最大区别：必须先看到完整 Host snapshot，再看到一次 `PREPARE_PLAN_READY`，之后才开始 `BATCH_*`。

第一轮已有 48 个 Feed 尚未建立 V5.2 bootstrap，所以 `PREPARE_PLAN_READY` 应显示大量 `hostQuarantine`，而 `modelInput` 显著小于 `raw`。实际数字会随最新 FreshRSS 数据变化，不要求等于事故 replay 的 3623/377/20。

最终必须：
```text
[morning] SUCCESS ...
```

### Morning 结果统计
成功后：
```bash
python3 - <<'PY'
import json
p='/home/ubuntu/.openclaw/workspace/rss-daily-stage-v52/current/morning-candidates.json'
d=json.load(open(p,encoding='utf-8'))
for k in ['runId','pageCount','totalRawArticles','accountedRawArticles','hostBootstrapBackfillArticles','persistentBootstrapQuarantineHits','quarantineFingerprintMismatchArticles','newBootstrapQuarantineArticles','modelInputArticles','classifierBatchCount','candidateArticleCount']:
    print(k,'=',d.get(k))
print('exactAccounting=',d.get('totalRawArticles')==d.get('accountedRawArticles'))
PY
```
必须 `exactAccounting=True`。

### Bootstrap state Gate
```bash
python3 - <<'PY'
import json
p='/home/ubuntu/.openclaw/rss-intelligence/bootstrap-state-v52.json'
d=json.load(open(p,encoding='utf-8'))
print('schemaVersion=',d.get('schemaVersion'))
print('bootstrappedFeeds=',len(d.get('feeds',{})))
print('quarantinedArticleIds=',len(d.get('quarantinedArticleIds',{})))
PY
```
必须 schemaVersion=2；首次 Morning 后 bootstrappedFeeds 应 >0。

## 6.3 三个 AI RSS — 不打印 token
```bash
python3 - <<'PY'
import os,re,xml.etree.ElementTree as ET
from pathlib import Path
env={}
for raw in Path('/home/ubuntu/.openclaw/.env').read_text().splitlines():
    line=raw.strip()
    if line and not line.startswith('#') and '=' in line:
        k,v=line.split('=',1); env[k.strip()]=v.strip().strip('"\'')
token=env.get('RSS_PUBLISH_TOKEN','')
assert re.fullmatch(r'[A-Fa-f0-9]{64}',token)
root=Path(env.get('RSS_PUBLISH_PUBLIC_ROOT','/var/www/rss-intelligence'))/token
for n in ('all.xml','priority.xml','reports.xml'):
    ET.parse(root/n); print(n,'XML_OK')
PY
```

再验证 HTTPS，但不显示 URL/token：
```bash
TOKEN=$(sed -n 's/^RSS_PUBLISH_TOKEN=//p' /home/ubuntu/.openclaw/.env | tail -n1)
for f in all.xml priority.xml reports.xml; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' "https://rss.example.com/intelligence/$TOKEN/$f")
  echo "$f HTTP=$code"
done
unset TOKEN
```
三项应 HTTP=200。

## 6.4 Afternoon 前重新跑一次 V5.2 Fetch
先再次确认旧 actualize 非 active：
```bash
systemctl is-active freshrss-actualize.service || true
```
然后：
```bash
systemctl --user start --no-block rss-v52-freshrss-fetch.service
```
等日志出现新的 `FETCH SUCCESS`，再继续。

## 6.5 Afternoon V5.2 E2E
```bash
systemctl --user start --no-block rss-v52-afternoon.service
```

```bash
tail -f /home/ubuntu/.openclaw/rss-intelligence/retry-logs/v52-afternoon.log \
| grep --line-buffered -E \
'SNAPSHOT_COMPLETE|PREPARE_PLAN_READY|BATCH_OK|PREPARE_COMPLETE|HOST_FINALIZER_START|HOST_FINAL_STAGE_READY|SUCCESS|FAILURE|CANCEL'
```
最终必须：
```text
[afternoon] SUCCESS ...
```

重点检查 persistent quarantine 正常工作：
```bash
python3 - <<'PY'
import json
p='/home/ubuntu/.openclaw/workspace/rss-daily-stage-v52/current/afternoon-candidates.json'
d=json.load(open(p,encoding='utf-8'))
for k in ['totalRawArticles','hostBootstrapBackfillArticles','persistentBootstrapQuarantineHits','quarantineFingerprintMismatchArticles','newBootstrapQuarantineArticles','modelInputArticles','classifierBatchCount','candidateArticleCount','morningBaselineFound']:
    print(k,'=',d.get(k))
print('exactAccounting=',d.get('totalRawArticles')==d.get('accountedRawArticles'))
PY
```
必须 `exactAccounting=True`，且 `morningBaselineFound=True`。

## 6.6 Review E2E（只保存测试 Pending）
```bash
RSS_REVIEW_KIND=e2e /home/ubuntu/.local/bin/rss-v52-review-retry-runner.sh
```
最终必须：
```text
[review] HOST_REVIEW_SUCCESS attempt=...
```
E2E 模式不会做真实 web source search，也不会修改订阅。

## 6.7 E2E Ready Gate
```bash
python3 /home/ubuntu/rss-intelligence-v5.2/06-cutover/verify-v52-e2e.py
```
必须：
```text
V52_E2E_READY file=/home/ubuntu/.openclaw/rss-intelligence/v52-e2e-ready.json
```

**没有这行，不进入 Cutover。**

---

# 阶段 7：事务式正式 Cutover

## 7.1 只读调度审计
```bash
bash /home/ubuntu/rss-intelligence-v5.2/06-cutover/audit-old-fetch-schedulers.sh
```
确认没有未知额外 actualize/RSS scheduler。

## 7.2 执行 Cutover
```bash
bash /home/ubuntu/rss-intelligence-v5.2/06-cutover/cutover-v52.sh
```
成功必须：
```text
V52_CUTOVER_OK snapshot=...
```

Cutover 会事务式：
1. 快照旧 timer/3 jobs/V5.1 timer 状态；
2. disable 旧 `freshrss-actualize.timer`；
3. disable 旧3 OpenClaw Jobs；
4. 确保 V5.1 timers disabled；
5. enable V5.2 四个 timers；
6. 验证；
7. 任一步失败自动 rollback。

若出现：
```text
CUTOVER_FAILED_ROLLBACK_START
CUTOVER_ROLLED_BACK snapshot=...
```
不要手工继续切换。

## 7.3 最终 timer 状态
```bash
systemctl --user list-timers --all | grep -E 'rss-v52-|rss-v51-' || true
```
V5.2 四个 timer 可见；V5.1 不应 enabled。

```bash
systemctl is-enabled freshrss-actualize.timer || true
```
必须 `disabled`。

旧3 jobs：
```bash
openclaw cron list --all --json > /tmp/rss-v52-after-cutover.json
python3 - <<'PY'
import json
ids={'5b758d06-5857-5070-abd6-79ffb622e472','8dfc2dea-0e2c-5e1e-a012-c1d0c1a4d634','f57c34f5-4333-5156-9efc-d60ec0547010'}
d=json.load(open('/tmp/rss-v52-after-cutover.json'))
for j in d.get('jobs',[]):
    if j.get('id') in ids: print(j['id'],'enabled=',j.get('enabled'))
PY
rm -f /tmp/rss-v52-after-cutover.json
```
三行必须 `enabled=False`。

> 因为 V5.2 timers 是 `Persistent=false`，如果 Cutover 时当天 07/08/13/14 已经过了，不会立刻补跑；从下一个正常 schedule 开始。

---

# 阶段 8：回滚

如果 Cutover 后需要恢复旧稳定系统：
```bash
bash /home/ubuntu/rss-intelligence-v5.2/06-cutover/rollback-v52.sh
```
必须：
```text
V52_MANUAL_ROLLBACK_OK
```
它会 disable V5.2/V5.1 timers，重新 enable 旧 system Fetch 与旧3 Jobs。

---

# 阶段 9：日常运行应看到什么

Morning/Afternoon 正常日志阶段：
```text
SNAPSHOT_START
SNAPSHOT_PAGE_OK ...
SNAPSHOT_COMPLETE ...
PREPARE_PLAN_READY raw=... hostQuarantine=... persistentHits=... newQuarantine=... modelInput=... batches=...
BATCH_START ...
BATCH_OK ...
PREPARE_COMPLETE ...
HOST_FINALIZER_START ...
HOST_FINAL_STAGE_READY ...
SUCCESS ...
```

关键异常语义：
- `SNAPSHOT_FATAL_CAPACITY`：Host-only 容量超限；未开始模型，不要粗暴加阈值。
- `PREPARE_YIELD_CONTINUE`：正常 continuation，不是失败。
- `FAILURE 1/3`：一次真实失败；180s 后重试。
- `DAILY_LOCK_TIMEOUT_NOT_SUCCESS` / `FETCH_ALREADY_RUNNING_NOT_SUCCESS`：任务没有执行成功，不会伪装成 SUCCESS。
- `CANCEL ... after 3 true failures`：只取消当前轮，下个 schedule 保留。

---

# 阶段 10：来源 Review 的使用

查看最新 Pending Review 时，可让 OpenClaw main 只读：
```bash
openclaw agent --agent main \
  --session-key "rss-review-read-$(date +%s)" \
  --model "opencode-go/deepseek-v4-flash" \
  --timeout 120 \
  --message '必须调用 rss_review_get_pending，读取最新 Pending Review。完整使用简体中文展示 ADD、WATCH、REMOVE、MOVE、每项评分与理由、reviewId。只读，不得修改 FreshRSS、OpenClaw 或服务器。'
```

任何来源变更仍需用户明确确认；V5.2 自动任务本身没有 mutation 权限。
