# RSS Intelligence V5.2

这是针对真实 V5.1 Page100 / >4000 raw 事故重构后的升级包。它不是从零部署包，也不是继续给 V5.1 加 timeout/MAX_PAGES 补丁。

## 为什么升级
V5.1 的完整性边界有效，但真实 E2E 暴露了执行顺序问题：4000 raw 中 3623 条被 Host 标记为历史 backfill；Page100 仍有 continuation。旧流程在判断历史库存之前已经进行了约 200 个常规模型 chunk，最终因 100 页安全上限失败。

V5.2 把顺序改为：

`FreshRSS Fetch → Host Immutable Snapshot → Capacity/ID Audit → Bootstrap Quarantine → Isolated AI Batches → Exact Raw Accounting → Deterministic Host Finalizer → Host Publish → Bootstrap Commit → Watermark`

## 真实事故 replay
包内 `incident-replay-test.py` 已对 V5.1 冻结的 4000 篇真实样本回放：
- raw: 4000
- bootstrap quarantine: 3623
- normal model input: 377
- dynamic batches: 20
- Page70–100 archive tail: 1240/1240 quarantine
- Morning quarantine state模拟提交后，同一 4000 条作为 Afternoon 输入：3623 persistent quarantine，仍仅 377 model input

即常规分类调用从约 200+ 个 chunk 降至 20 个 batch 量级，同时保持 4000/4000 raw accounting。

## 核心变化
1. **Immutable snapshot**：分类期间不再依赖变化中的 FreshRSS continuation。
2. **Pre-model capacity guard**：最多 500 snapshot pages；超限在模型调用前失败。
3. **Bootstrap quarantine**：首次导入历史库存 Host 隔离、可审计、不删 Source。
4. **Persistent article quarantine**：同一历史 ID + 相同内容 fingerprint 在 Afternoon 不会重新进入模型；同 ID 内容变化或新的 late-arrival ID 会重新进入模型。
5. **Stable synthetic IDs**：缺少 FreshRSS 原生 ID 时使用与 continuation 页码无关的稳定内容键。
6. **Dynamic batches**：<=20 articles 且 <=28,000 input chars。
7. **Exact raw accounting**：Host quarantine + model 必须覆盖 snapshot 全部 ID。
8. **YIELD ≠ FAILURE**：时间片让出不消耗 3 次真失败预算。
9. **Lock 不再 false success**：冲突等待/失败，不再 `exit 0` 冒充成功。
10. **Source dataset lock**：V5.2 actualize 与 snapshot 互斥；snapshot 完成后后续 fetch 不影响当前 run。
11. **Persistent=false timers**：Cutover 当天不会突然补跑已错过的 07/08/13/14。
12. **Deterministic Host Finalizer**：Host 重验 immutable provenance、派生完整 accounting、强制 Morning/Afternoon 24/16 条上限；不再依赖模型最后一次大参数 tool call。
13. **Read-only Host Review**：定期审核只读取 FreshRSS 订阅库存并保存零变更 Pending Review；不直接增删移动来源。
14. **Private V5.2 Stage compatibility**：Stage plugin 与模板任务保留作兼容/取证，但生产 runner 不调用，且任务始终 disabled。
15. **Non-destructive migration**：只迁移可复用的 publisher/daily history；不迁移 bootstrap 状态。

## 当前环境的正确升级路径
不要重新做 33 Micro Batch 来源初始化。当前 48 个来源继续使用。

按照 `INSTALL_V52.md`：
1. 校验包 + Preflight；
2. 迁移历史 runtime；
3. 安装独立 V5.2 Stage compatibility plugin；
4. 安装 V5.2 workspace/prompt/template jobs（模板任务保持 disabled）；
5. 安装但不启用 V5.2 systemd timers；
6. 跑必要 E2E；
7. E2E Ready 后事务式 Cutover。

## 文件结构
- `00-preflight/`：整包回归、snapshot/daily fixture、真实事故 replay（当提供事故目录时）。
- `01-init-kit/`：保留来源初始化工具，仅供未来显式需要；本次升级不要重跑。
- `02-rss-intelligence-stage/`：独立私有 V5.2 Stage compatibility plugin（生产 runner 不调用）。
- `03-workspace-files/`：V5.2 Publish Policy / Morning / Afternoon / Review Prompt。
- `04-daily-pipeline/`：snapshot、prepare、deterministic Host Finalizer、read-only Host Review、publisher、runtime migration。
- `04-retry-scheduler/`：V5.2 Fetch/Daily/Review runner + systemd units。
- `05-apache/`：幂等 Apache patch；现网已存在时只 check，不重复修改。
- `06-cutover/`：template jobs、E2E Ready、事务式 Cutover/Rollback。
- `HISTORY_V51_TO_V52.md`：从 V5.1 故障到 V5.2 的问题与优化记录。
