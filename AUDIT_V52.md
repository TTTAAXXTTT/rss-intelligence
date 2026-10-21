> 历史技术参考，已将部署域名、任务标识和个人环境替换为示例；历史验收记录不代表当前开源副本已完成在线部署。首次部署以 README 和 DEPLOYMENT.md 为准。

# RSS Intelligence V5.2 — 最终工程复核

## 1. 审计结论
V5.2 不再通过提高 V5.1 `MAX_PAGES` 或 timeout 解决事故，而是重构 Daily ingestion 前半段。V5.1 已证明的安全边界继续保留：Host 确定性读取、exact article accounting、私有 Final Stage、Host-only publish、watermark、人工审批 Review、E2E Gate 和 transactional cutover。

真正改变的是“模型何时介入”：V5.2 必须先冻结完整 Host snapshot，再做 bootstrap/capacity/accounting，然后只把必要文章送给模型。

## 2. 真实事故证据
V5.1 Morning `morning-20260811T010342Z-091390e4`：
- Page100 仍 `hasMore=true`；
- raw=4000；
- Host likely-backfill flag=3623；
- V5.1 model disposition backfill=3545；
- candidates=204；
- unique article IDs=4000；
- cross-page duplicate IDs=0；
- accounting missing/extra=0。

Page70–100 共1240 raw，Top5 archive/changelog source 占1225（98.79%）。根因是首次导入历史库存，而不是整个来源库正常日更量。

## 3. V5.2 架构
### 3.1 Host Immutable Snapshot
- 40 raw/page；固定 cutoff + continuation；
- 在任何 model call 前读完整窗口；
- run 级 article ID 去重；
- sourceFetchRunId 在 snapshot 前后必须不变；
- snapshot hash 绑定 source run/cutoff/有序 IDs；
- Prepare 只读 snapshot，不可访问 FreshRSS；
- Host-only max 500 pages / 20,000 raw，超限 pre-model fatal。

### 3.2 Bootstrap quarantine
- 新 Feed 首次窗口中的 `receiveLagDays>7` 历史库存进入 Host quarantine；
- 不删除 Source，不静默删除 article 证据；
- successful publish 后才提交 Feed bootstrap + quarantine article IDs；
- 后续相同 ID 且 content fingerprint 未变化时持续 deterministic quarantine；
- 同 ID 内容改变时解除旧 quarantine 并重新进入模型；
- 新 ID 的 delayed article 不因 publisher date 旧而自动 drop。

### 3.3 Stable fallback IDs
FreshRSS item 缺少原生 ID 时，synthetic ID 只基于 feed/URL/title/published/author，不依赖 continuation page、ordinal 或 receive crawl 位置；分页重排不会改变 ID。

### 3.4 Dynamic model batches
- default <=20 articles；
- default <=28,000 serialized input chars；
- Flash → Pro fallback；
- exact batch ID accounting；
- strict raw source URL copy；
- completed batches checkpoint/reuse。

### 3.5 Raw accounting
最终 `raw-accounting.json` 必须对 snapshot 的每个 ID 恰好一行：Host quarantine 或 model。不存在 silent drop。

### 3.6 Failure state machine
- true failure：最多3次，间隔180s；
- `PREPARE_YIELD`：正常 continuation，不消耗 failure budget；
- snapshot capacity：fatal pre-model；
- daily/source/review lock conflict 不再 `exit 0` false success。

### 3.7 Scheduler isolation
- V5.2 actualize 与 snapshot 共用 source dataset lock；
- Morning/Afternoon 共用 daily global lock；
- timers `Persistent=false`，Cutover 后不补跑白天已经错过的 schedule；
- Cutover disable 旧 system fetch、旧3 jobs 与 V5.1 timers，再 enable V5.2 timers。

## 4. 真实4000篇 replay
`incident-replay-test.py` 对冻结事故数据得到：
- raw=4000；
- bootstrap quarantine=3623；
- normal model input=377；
- dynamic batches=20；
- V5.1 fresh candidates routed to model=172/172；
- historical V5.1 candidates quarantined=32；
- Page70–100 archive tail quarantined=1240/1240。

加入 cross-schedule fixture 后：模拟 Morning bootstrap commit，再让 Afternoon 看到同一4000条：
- persistent quarantine=3623；
- normal model input仍=377；
- 没有重新变成4000模型输入。

注意：V5.1 的204 candidates不是绝对 ground truth；其中32篇 `lag>7d` 的历史“candidate”包含明显数周至数月前内容。V5.2 的目标不是机械复刻旧 classifier，而是防止历史重要文章被包装成当天新进展。

## 5. Offline/fixture Gate
最终包必须通过：
- all Bash `bash -n`；
- all Python compile；
- TypeScript syntax transpile；
- canonical categories；
- no stale Volcengine model refs；
- V5.2 path/tool isolation；
- snapshot no model transport；
- Prepare no FreshRSS transport；
- pre-model capacity guard；
- dynamic count+char limits；
- YIELD != FAILURE；
- lock conflict != success；
- shared source dataset lock；
- persistent bootstrap quarantine；
- Init fixture；
- immutable snapshot/reuse/capacity fixture；
- daily bootstrap/dynamic/yield/accounting/cross-schedule fixture；
- Apache idempotent fixture；
- systemd static verify + calendar parse；
- private Stage schema2 smoke；
- Host Publisher full report/XML/bootstrap/watermark smoke；
- non-destructive runtime migration fixture；
- optional real 4000-article incident replay。

## 6. 仍必须在真实服务器验证的 Gate
离线测试不能替代：
1. current OpenClaw CLI/runtime；
2. Flash/Pro live minimal call；
3. V5.2 plugin load/tool boundary；
4. FreshRSS actualize + V5.2 fetch state；
5. real Morning snapshot/classification/finalizer/publish；
6. real Afternoon baseline + persistent quarantine；
7. Review E2E pending-only；
8. `verify-v52-e2e.py`；
9. transactional Cutover。

任何一项失败都不进入正式 Cutover，旧系统继续保留。

## 7. 版本完整性
V5.1 现场 hotfix 后原 checksum 已漂移。V5.2 最终交付重新生成 `checksums.sha256`，并在最终 archive 生成前后分别执行 package selftest 与 checksum verification。不得把现场代码继续称为原 V5.1 final。
