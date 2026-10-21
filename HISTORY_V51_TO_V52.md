> 历史技术参考，已将部署域名、任务标识和个人环境替换为示例；历史验收记录不代表当前开源副本已完成在线部署。首次部署以 README 和 DEPLOYMENT.md 为准。

# RSS Intelligence V5.1 完整复盘与优化台账

更新时间：2026-08-11
当前阶段：Stage 7 Morning V5.1 E2E 运行中；截至本次复盘前已确认从历史 checkpoint Page 46 恢复，后续推进到 Page 70+。尚未完成 Morning Finalizer / 三个 AI RSS 验证 / Afternoon E2E / Review E2E / E2E Ready / Cutover。

---

## 0. 项目目标与固定约束

目标是在现有 AWS Lightsail + FreshRSS + OpenClaw 环境上构建个人情报系统：

- FreshRSS：原始 RSS 仓库；
- OpenClaw + OpenCode Go：筛选、验证、聚类、分析；
- 输出：`all.xml`、`priority.xml`、`reports.xml`；
- 07:00 / 13:00 FreshRSS 抓取；
- 08:00 Morning；
- 14:00 Afternoon；
- 每月 15 / 28 日 03:00 Source Review；
- 时区：Asia/Shanghai；
- Review 只生成 Pending Review，不自动改订阅；
- 所有生产任务失败固定等待 180 秒，总尝试 3 次；
- 不允许静默漏文；
- 不允许模型直接公网发布；
- 正式切换前旧系统保留，V5.1 E2E 全通过后才事务式 Cutover；
- 避免重复执行已验证组件和无意义的错误测试。

---

# 1. 从旧方案到 V5 / V5.1 的设计演进

## P01：旧方案存在 75/50 条硬截断
状态：已解决

旧方案存在单次只处理固定数量文章的风险，无法证明完整窗口内所有文章都被读取。

修复：
- Host Python 直接使用 FreshRSS Google Reader API；
- 每页 40 条；
- 使用 `continuation + cutoff`；
- 一直读到 `hasMore=false`；
- 100 页仍未结束时明确失败，绝不发布半成品。

保留原因：
完整性优先于“看起来成功”。

后续注意：
100 页只是安全阈值，不代表 100 页以内的工作量一定合理。此次大规模 backfill 已证明“完整分页”和“运行效率”必须分开设计。

---

## P02：FreshRSS 07:00 / 13:00 原料抓取没有统一重试
状态：已解决

修复：
- 新增 FreshRSS retry runner；
- 固定 180 秒；
- 总计 3 次尝试；
- 使用 last-run / last-success 双状态；
- 最新一次 Fetch 失败时，不能用更早成功状态冒充最新原料。

---

## P03：日报正文可能被截断
状态：已解决

修复：
- `reports.xml` 保留完整正文；
- `<description>` / `<content:encoded>` 输出完整 HTML；
- 公网输出仍由 Host Publisher 完成。

---

## P04：初始化来源覆盖不足
状态：已解决

修复：
- 33 个基础 Micro Batch；
- S/A/B/C 分类覆盖；
- Coverage Gap Pass；
- 不为了凑数量降低 82 分来源质量门槛；
- 全球多语言作为硬规则。

---

## P05：初始化一次性全跑造成长上下文、额度与定位风险
状态：已解决（V5.1）

V5.1 删除生产使用的 `run-init all`，改为：
- `one S_CHAIN_01` Smoke；
- `next 5`；
- `gaps-next 5`。

目的：
- 限制长任务；
- 避免网络抖动导致整批失败；
- 减少上下文积累；
- 更容易定位失败 Batch；
- 已成功 Batch 自动跳过。

---

## P06：模型可能静默漏掉文章
状态：已解决

修复：
- Host 保存每页真实 article.id；
- 模型输出 accounting；
- accounting ID 集合必须与输入 ID 集合完全相等；
- 每个 ID 恰好出现一次；
- disposition 必须属于 backfill / filtered / merged / candidate；
- rawCount、分类计数、candidate 主文章 ID 必须一致。

这是整个方案最重要的正确性边界之一，后续任何性能优化都不能破坏。

---

## P07：失败重试会重复大量已经完成的工作
状态：已解决

修复：
- 每页 checkpoint；
- Page N 完成后立即原子写 progress；
- 下一次 attempt 从 N+1 继续；
- Finalizer Stage 已生成时可以复用；
- 完整完成后才删除 progress。

---

## P08：Coverage Gap 中文分类 ID 可能碰撞
状态：已解决

修复：
- Gap ID 加分类 SHA1 短哈希；
- 避免多个中文类别退化成相同 ID。

---

## P09：模型直接持有公网发布能力
状态：已解决（V5.1 安全重构）

修复：
- Finalizer 只有 `rss_daily_finalize_save`；
- 只能保存私有 Stage；
- 模型无 `rss_intelligence_publish` / `rss_report_publish`；
- Host `host-publish.py` 二次 schema/accounting 校验后才写公开 RSS；
- authoritative watermark 最后提交。

---

## P10：Afternoon retry 时 Morning 基线可能漂移
状态：已解决（V5.1）

修复：
- Afternoon 每个新 run 固化一次 Morning baseline；
- retry 始终复用同一个 baseline ID；
- 中途状态不能污染下一次 attempt。

---

## P11：上一轮失败会形成信息窗口洞
状态：已解决（V5.1）

修复：
- Intelligence success watermark；
- 正常 Morning 18h / Afternoon 6h；
- 上一轮失败时向前扩展到最后成功的 `sourceFetchFinishedAt`；
- 保留 120 秒重叠；
- 自动恢复最大 72h；
- 超过 72h 明确失败，要求人工 catch-up。

---

## P12：正式切换破坏旧系统、难回滚
状态：设计已解决，尚未实际 Cutover

修复：
- E2E Ready Gate；
- Cutover 前快照；
- disable 旧 timer/job，但不删除；
- enable V5.1 timers；
- 验证失败自动 rollback。

尚未执行：
当前还在 Stage 7，不能认为 Cutover 已完成。

---

# 2. 实际部署过程问题复盘

## P13：Preflight 缺失 `@types/node`
状态：已解决

实际服务器 Preflight 曾出现：
`FAIL dependency missing ... node_modules/@types/node`

模型路由本身同时验证正常：
- Flash PASS；
- Pro PASS。

后续处理：
- 使用独立的 V5.1 build deps；
- 关键 Node 依赖使用真实目录；
- `@types/node`、`undici-types` 等依赖确认存在；
- 插件 Build / Test / Validate 后再安装。

优化记录：
以后不能仅因为 `node_modules` 目录存在就认为依赖完整，Preflight 要验证实际 build 依赖。

---

## P14：rss-intelligence-stage 单元测试 fixture ID 长度问题
状态：已解决

`npm test` 初始 fixture 使用 `"e1"`，与实际 schema 最小长度要求冲突。

修复：
- 仅修改测试 fixture 为 `"evt1"`；
- 不降低生产 schema；
- 3/3 测试通过。

优化原则：
测试数据应适配生产约束，不能为了让测试通过而放宽生产校验。

---

## P15：插件依赖复用方式潜在不稳定
状态：已解决

最终确认：
- Stage plugin 顶层 node_modules 是真实目录；
- `@types/node` 为真实依赖；
- `undici-types` 正常；
- `.bin/vitest` 指向正确入口；
- Build/Test/Validate 全通过。

---

## P16：Runtime 权限边界
状态：已验证通过

结果：
- `rss-intelligence-stage` loaded；
- 只暴露 `rss_daily_finalize_save`；
- `HOST_ONLY_PUBLISH_BOUNDARY_OK`；
- `/agent` 只读；
- 公网发布工具没有暴露给 Finalizer。

---

# 3. Stage 4～6 安装复盘

## Stage 4：Prompt + disabled Template Jobs
状态：完成

已安装：
- `RSS_PUBLISH_POLICY_V51.md`
- `PROMPT_MORNING_V51.md`
- `PROMPT_AFTERNOON_V51.md`
- `PROMPT_REVIEW_V51.md`

Template Jobs：
- Morning
- Afternoon
- Review

全部保持 `disabled=true`，由 systemd 使用 `cron run` force-run。

---

## Stage 5：Apache `/intelligence/`
状态：完成

原则：
- 静态 `/intelligence/` 路由在 catch-all proxy 前；
- Apache configtest 必须 `Syntax OK` 才 reload；
- 私有 token 不打印、不要求用户粘贴。

---

## Stage 6：systemd runner / timer
状态：完成安装，尚未正式 Cutover

已验证：
- Linger=yes；
- runner 安装；
- OpenClaw runtime 固定；
- service/timer systemd verify；
- Asia/Shanghai calendar parse；
- V5.1 Timer 当前不应正式 enable；
- 旧系统在 Cutover 前继续保留。

---

# 4. Stage 7 E2E 实际问题详细时间线

## P17：旧 FreshRSS actualize 冲突风险
状态：已有防护，但组合风险仍需优化

E2E 开始前确认旧 `freshrss-actualize.service` 不在 active 状态。

原设计只避免“开始 E2E 时正在撞车”，但没有充分覆盖 Morning 运行数小时后跨过下一次 13:00 actualize 的情况。

该问题在后文列为高优先级潜在风险。

---

## P18：FreshRSS Retry E2E
状态：通过

新的 FreshRSS Retry Service 已验证：
- attempt；
- Fetch 成功；
- last-run status=ok；
- last-run / last-success runId 相同。

---

## P19：Checkpoint reuse 被 cutoff 时间变化破坏
状态：已解决

最初 retry 会因为重新计算 effectiveCutoff / 当前时间边界而认为旧 checkpoint 不兼容，从而创建新 run，造成已经完成页面被浪费。

修复：
- 已完成 result reuse 不再要求“当前重新计算的 cutoff 与旧 cutoff 相等”；
- partial progress 只要求同一个 `sourceFetchRunId`、同 edition hours、未完成；
- resume 时恢复 checkpoint 内原始 cutoff；
- 保证同一轮分页窗口稳定。

原则：
checkpoint 必须绑定“原始 source snapshot/run + 原 cutoff”，而不是绑定“重试时的当前时间”。

---

## P20：模型 JSON 截断/格式错误
状态：已缓解

保持严格 JSON：
- 不自动猜测修复截断 JSON；
- JSONDecodeError 视为失败。

增加：
- Flash 失败后 Pro fallback；
- timeout / JSONDecodeError / RuntimeError 均可触发 fallback。

原因：
自动补 JSON 可能破坏 article.id accounting，完整性优先。

---

## P21：40 篇一次交给模型过重
状态：已解决当前结构，但仍有进一步优化空间

现象：
40 篇单次分类在真实文章长度下容易出现输出截断或失败。

修复：
- FreshRSS source pagination 仍是 40/page；
- 模型分类拆为 20 + 20；
- 两个 chunk 分别严格 accounting；
- merge 后必须与原 40 ID 集合完全一致；
- 不递归拆成 10+10。

后续潜在问题：
“固定 20 篇”仍然只按文章数，不按字符/token 体积；20 篇超长文章仍可能出问题。

---

## P22：模型改写 source URL / event URL
状态：已解决当前路径

实际出现：
`CANDIDATE_SOURCE_URL_NOT_IN_RAW_PAGE`

原因：
模型把 URL canonicalize / 改参数 / 换官网，违反严格来源事实边界。

修复：
- Prompt 明确 `candidate.sources[].url` 必须逐字符复制当前 raw article.url；
- URL 不得规范化；
- eventKey 如果使用 URL 也必须逐字符复制；
- semantic validation 失败时仅对该 chunk Pro fallback；
- strict validator 不放宽。

---

## P23：模型字段缺失、candidate count 等语义错误
状态：已缓解

真实运行中出现过：
- 缺字段；
- candidate count 不一致；
- URL 不合法；
- Flash JSONDecodeError。

修复：
- `call_classifier_validated`；
- Flash 结构正确但语义 validator 失败时 Pro retry；
- `PAGE_VALIDATION_FALLBACK` / `_OK` 日志；
- Host strict validation 保持不变。

---

## P24：FreshRSS source state 超过 2 小时导致 Morning 无法启动
状态：新 run 行为正确；resume 行为曾错误，现已修复

第一次遇到 stale：
`SOURCE_FETCH_STALE ageSeconds=49424`

当时做法：
重新跑 FreshRSS Fetch，生成新 source run。

后来发现更深层问题：
`main()` 原先先执行 `fresh_state()`，之后才读取 checkpoint。
因此长任务一旦 source age > 2h，即使存在同一个 source run 的合法 checkpoint，也会在读取 checkpoint 前被 stale 拦住。

---

## P25：FreshRSS stale 与 checkpoint resume 的结构冲突
状态：已修复并离线验证

新规则：
- 新任务仍要求 source ≤ 2h；
- checkpoint resume 可以使用超过 2h 的 source；
- 但必须与 checkpoint 的 `sourceFetchRunId` 完全相同；
- 当前设置 resume 最大 6h；
- 不允许跨新的 FreshRSS run 复用旧 checkpoint。

离线测试已验证：
- 3h stale 新 run：拒绝；
- 3h stale + 同 source run resume：接受；
- 3h stale + 不同 source run：拒绝；
- 7h stale + 同 run：拒绝。

---

## P26：45 分钟 Prepare deadline 不适合真实高容量
状态：已临时修复，后续还需架构优化

原值：
`PREPARE_DEADLINE_SECONDS=2700`

实际：
- Attempt 在 Page 13 后触发 deadline；
- checkpoint 保存；
- 后续 attempt 继续；
- 再到 Page 45 又触发 deadline；
- 三次 attempt 最终耗尽，Morning service failed。

修复：
- Prepare 单次 deadline 调整为 3h；
- Morning/Afternoon systemd `TimeoutStartSec` 调整为 330min；
- checkpoint 保留。

注意：
这是“让当前 E2E 可以完成”的修复，不是最终理想架构。
3h 单 attempt 与“总共 3 attempt”及 5.5h outer timeout 之间仍存在语义不一致，后文列为待优化。

---

## P27：第一次综合审计写错 progress ROOT
状态：已识别，流程问题

审计脚本曾把 progress 路径手写为：
`/home/ubuntu/.openclaw/rss-intelligence/daily-state-v51`

而生产实际 `ROOT` 是：
`/home/ubuntu/.openclaw/workspace/rss-daily-stage-v51`

因此出现：
`STOP_NO_MORNING_PROGRESS`

这不是 checkpoint 丢失，而是审计路径错误。

后续规则：
- 不再手写推测路径；
- 先读取生产脚本常量；
- 审计工具应从 production constant / env 推导路径；
- 不能根据一个错误探针得出产品状态结论。

---

## P28：Page 45 checkpoint 最终核验
状态：通过

真实 checkpoint：
- runId=`morning-20260811T010342Z-091390e4`
- sourceFetchRunId=`fetch-20260811T005809Z-6419`
- pageCount=45
- allPagesComplete=false
- nextCursor 存在
- raw=1800
- backfill=1382
- low=215
- merged=8
- candidate_articles=195
- cross_dup=0
- source_before_cutoff=0
- source_duplicates=0

Page 45：
- fetchedCount=40
- rawCount=40
- hasMore=true
- nextCursor 存在

证明 checkpoint 完整，可以从 Page 46 恢复。

---

## P29：测试命令漏传 fixture 参数
状态：已解决，属于操作方案错误

错误调用：
`daily-fixture-test.py` 没传 package path。

结果：
`IndexError: sys.argv[1]`

同时 shell 使用 `set -euo pipefail`，导致 SSH shell 随失败退出。

优化记录：
- 给用户执行的测试命令必须先确认 CLI 参数；
- 临时诊断不应在登录 shell 顶层使用 `set -e`；
- 更安全做法：使用 subshell，或显式检查 RC；
- 单项测试失败不应导致 SSH 会话一起退出。

---

## P30：修改生产函数签名后 fixture mock 未同步
状态：已解决

生产：
`fresh_state(resume_source_run_id=None)`

旧 fixture：
`m.fresh_state = lambda: ...`

导致：
`TypeError: <lambda>() takes 0 positional arguments but 1 was given`

修复：
fixture mock 改为：
`lambda *_args, **_kwargs: ...`

最终：
- DAILY_V51_FIXTURE_OK
- PACKAGE_SELFTEST_OK
- SYSTEMD_VERIFY_OK
- PRODUCTION_CONFIG_ASSERT_OK
- CHECKPOINT_FINAL_ASSERT_OK

优化记录：
以后任何函数接口变化必须同时搜索并修改：
- production call sites；
- fixture mocks；
- unit tests；
- package selftest；
- docs。

---

## P31：真实 Resume 验证
状态：通过

真实 Morning 恢复日志确认：
- `SOURCE_FETCH_STALE_ALLOWED_FOR_RESUME`
- source runId 保持 `fetch-20260811T005809Z-6419`
- Morning runId 保持 `morning-20260811T010342Z-091390e4`
- `PREPARE_RESUME ... nextPage=46`
- Page 46 开始继续处理

说明 Page 1～45 没有重跑。

---

## P32：SSH tail 过程中远端断线
状态：非业务故障

SSH 断开时：
- user systemd service 仍有 MainPID；
- `ActiveState=activating`；
- 重新连接后任务继续；
- 后续页面继续推进。

优化：
监控连接与业务进程解耦；不能因为 `tail -f` 断开就 restart service。

---

# 5. 当前新暴露的核心问题：Backfill 放大

## P33：初始化/首次抓取后大量历史文章被 FreshRSS 当成“最近收到”
状态：已发生，尚未最终优化
优先级：CRITICAL / 必须在正式 Cutover 前处理

证据：
Page 45 checkpoint 时：
- raw=1800
- backfill=1382
- backfill 比例约 76.8%
- candidate_articles=195

当前运行继续到 Page 70+：
- 已处理至少 2800 raw；
- 大量后续页面 candidate=0；
- 仍必须执行两个 20 篇 model chunk。

根因：
系统时间窗口以 FreshRSS `receivedTimestamp` 为准，这是为了不因为 publisher date 不可靠而漏文。
但首次导入来源后，大量历史 item 在同一时间被 FreshRSS 首次接收，因此都落入 18h receive-time window。

当前设计的问题：
`isLikelyBackfill` 虽然可以被识别，但识别/分类仍发生在模型路径中，因此历史垃圾仍消耗：
- API 调用；
- 模型 token；
- 时间；
- fallback；
- checkpoint；
- 100-page 容量。

必须保留的正确性：
不能简单改成只按 publisher date 截断，否则可能静默漏掉真实延迟到达的更新。

建议最终设计：
增加“Bootstrap / Warm-up Baseline”阶段：
1. FreshRSS 导入后先 Host-only 枚举；
2. 建立 initial article-id baseline；
3. 明确区分“导入时已经存在的历史库存”和“系统上线后的新接收 article”；
4. 对 bootstrap 库存只保留近期 publisher items 进入 AI，老库存留在 FreshRSS 原仓但不逐篇跑模型；
5. 后续正常运行依然使用 receivedTimestamp + watermark，保证不漏真正新到达的信息。

这是本轮最重要的后续架构优化。

---

# 6. 同类风险扫描：尚未发生或未完全验证的问题

## R01：100 页 / 4000 raw 上限可能被 backfill 撞满
状态：高风险，当前正在接近验证
优先级：CRITICAL

当前上限：
- 40 raw/page；
- MAX_PAGES=100；
- 4000 raw。

如果 Page 100 仍有 continuation：
- 明确 `MAX_PAGES_EXCEEDED=100`；
- 整轮不发布。

这是正确的安全失败，但如果原因只是 bootstrap backfill，意味着可能已经花大量 token 后才发现无法完成。

优化：
采用 Two-phase ingestion：
Phase A：Host-only 快速分页，把完整窗口 raw spool 到磁盘，不调用模型；
Phase B：确认：
- 总页数；
- raw 总量；
- backfill 数；
- duplicate 数；
- safety limits；
之后才开始模型分类。

这样若 >100 页，可以在几秒/几分钟级发现，而不是模型跑数小时后失败。

---

## R02：当前分页不是“真正不可变的原料快照”
状态：高风险，待验证/优化
优先级：CRITICAL

当前 checkpoint 保存：
- sourceFetchRunId；
- cutoff；
- nextCursor。

但分类过程中仍按 cursor 再从 FreshRSS API 读后续页。
如果任务持续跨过下一次 13:00 actualize，FreshRSS 数据库可能新增文章。

风险：
- continuation 在数据库发生插入后是否仍保持完全稳定，需要明确保证；
- 如果不稳定，可能导致跨页重复/位移；
- sourceFetchRunId 只是 fetch state 标识，并没有冻结数据库内容。

最终优化：
最佳做法不是依赖长期 continuation：
- FreshRSS Fetch 成功后立即建立 raw snapshot/spool；
- Morning/Afternoon 只读取该 snapshot；
- 后续 actualize 不影响正在执行的 intelligence run。

---

## R03：13:00 Fetch 与长 Morning 重叠
状态：高风险
优先级：CRITICAL

设计时间：
- 08:00 Morning
- 13:00 Fetch

正常假设 Morning 在 5h 内结束。
本轮已证明极端数据量可以逼近/超过这个假设。

当前 E2E 又仍保留旧 13:00 actualize。

必须检查：
- Fetch runner 是否与 Daily pipeline 使用同一全局锁；
- 旧 system-level actualize 肯定不受 V5.1 user-level daily lock 自动约束。

正式版本要求：
- 任何会修改 FreshRSS source dataset 的 actualize 与 active Morning/Afternoon snapshot ingestion 必须有明确互斥/快照隔离。

---

## R04：14:00 Afternoon 与未结束 Morning 冲突
状态：高风险
优先级：CRITICAL

Morning/Afternoon 当前确实共用：
`v51-daily-global.lock`

但是 lock 失败路径是：
`V51_DAILY_PIPELINE_ALREADY_RUNNING`
然后 `exit 0`。

问题：
systemd 会把 exit 0 当作成功，但业务任务其实没有执行。

这属于“技术状态成功、业务状态失败”。

必须修改：
- lock conflict 不能用普通成功退出；
- 可返回专用非 0 / temporary unavailable；
- 或 queue/defer；
- 或 systemd dependency；
- 最终 success 必须要求实际产生对应 run result，而不是仅进程 exit=0。

---

## R05：3 小时 Prepare deadline vs 总计 3 attempts vs 5.5h systemd
状态：设计语义不一致
优先级：HIGH

当前临时参数：
- 单次 prepare：3h；
- runner：3 attempts；
- retry wait：180s；
- systemd outer：5.5h。

理论上 3 次完整 3h attempt 需要 >9h，但 outer 只有 5.5h。

因此“总计 3 attempts”的业务语义在极端情况下无法真正成立。

建议重构：
把“checkpoint 时间片耗尽”与“失败 attempt”区分：
- deadline 到达但 checkpoint 正常保存：返回 `YIELD/CONTINUE`，不消耗 failure retry count；
- 网络/API/model 真失败：才计入 3 attempts；
- outer service 使用明确总 wall-clock budget；
- 到总预算才取消本轮。

---

## R06：6h resume 上限仍与超长任务耦合
状态：MEDIUM-HIGH

现在 6h 解决了当前 2h stale 冲突。
但如果：
- run 接近 5.5h被 systemd 终止；
- 人工恢复稍晚；
就可能超过 6h，合法 checkpoint 再次无法 resume。

长期方案：
如果使用完整 raw snapshot：
- resume 不再依赖 FreshRSS 当前 freshness；
- 只依赖 snapshot hash / sourceFetchRunId / cutoff；
- snapshot 未损坏即可恢复。

这样可以彻底删除“source age 与 checkpoint 生命周期耦合”。

---

## R07：模型 chunk 固定 20 篇，不按实际字符/token
状态：HIGH

已经证明 40 篇太大。
20 篇目前表现更稳定，但文章长度差异很大。

风险：
20 篇长文章仍可能：
- 输入过大；
- JSON 输出截断；
- Flash 超时；
- Pro fallback；
- 再次拉长运行。

优化：
使用双阈值 chunk：
- maxArticles；
- maxInputChars / estimated tokens。

例如先保证 article count <=20，同时限制 payload 总字符。
具体阈值必须基于当前模型实际 context/output 限制和日志采样后确定，不应拍脑袋。

---

## R08：Finalizer 大候选集可能再次形成单次大请求
状态：HIGH，尚未实测到 Finalizer

现有安全阈值：
- candidates <=500；
- combined JSON <=750,000 chars；
- Finalizer wait 30m。

750k chars 虽低于原设计 context 安全线，但真实 Finalizer 还需要：
- system prompt；
- policy；
- input；
- tool call；
- output；
- 完整报告正文。

因此“文件低于 750k”不等于“Finalizer 一定稳定”。

优化：
候选量大时使用分层 Finalizer：
1. category/chunk summary；
2. deterministic merge；
3. 最终报告生成；
同时保持最终 item accounting。

---

## R09：Finalizer 30m timeout 尚未真实高负载验证
状态：MEDIUM-HIGH

当前 `WAITM=30m; TIMEOUT=1900000` 只在 prepare 完成后使用。
此前失败都发生在 Prepare，因此没有证据需要现在改。

但本轮 candidate 可能较大，必须把 Finalizer 的：
- 实际耗时；
- 是否 timeout；
- Stage 是否成功；
记录进本次 E2E 统计。

不能现在预防性乱改，但必须作为 Gate 观察。

---

## R10：缺少“运行前容量预估”
状态：HIGH

当前只有边跑模型边知道 pageCount。

应新增 Host-only preflight：
- total raw pages；
- total raw；
- estimated backfill；
- source distribution；
- rough candidate-capacity risk；
- estimated model chunks。

若超阈值：
- 在模型调用前 safe-fail；
- 或走 bootstrap mode。

---

## R11：缺少每个 RSS Source 的健康度指标
状态：HIGH

大量 backfill 可能由少数 feed 一次吐出大量历史文章导致。

建议维护 source health：
- 每次 received count；
- backfill ratio；
- duplicate ratio；
- candidate yield；
- fetch failure；
- 长期低信噪比；
- 异常 burst。

15/28 Review 应使用这些真实运行指标辅助：
ADD / WATCH / REMOVE / MOVE。

这样可以从来源层减少长期噪音，而不是只优化模型。

---

## R12：跨页 raw article.id 全局重复需要单独审计
状态：MEDIUM-HIGH

当前已确认：
- 每页内 source duplicate 有检查；
- candidate eventKey 有跨页 merge。

仍建议明确增加：
- run 级 `seen_raw_article_ids`；
- 同一 article.id 若在不同页重复出现，要么明确 fail source pagination anomaly，要么 deterministic dedup 并记录。

这对防止 continuation 在数据库变化时发生位移尤其重要。

---

## R13：正式成功与“锁冲突/跳过”语义需要加强
状态：CRITICAL

成功定义文档要求：
1. source fetch 成功；
2. 完整分页；
3. accounting；
4. Stage；
5. publish；
6. 三个 RSS 刷新；
7. watermark。

因此任何“因为锁冲突没有执行但 exit 0”的路径都与成功定义冲突。

后续需要统一：
- Process exit success
- Business run success
两个概念不能混用。

---

## R14：Package checksum 已因现场 hotfix 失效
状态：HIGH，待收尾

最初 ZIP 的 `checksums.sha256` 已验证全部 OK。

之后现场修改了至少：
- `prepare-daily.py`
- morning.service
- afternoon.service
- `daily-fixture-test.py`

因此原 checksums manifest 已不再代表当前运行版本。

必须在所有 E2E 和最终优化结束后：
1. 固化新版本号（建议 V5.1.1 或 V5.2，不继续叫原“final”）；
2. 更新 README / INSTALL / AUDIT；
3. 重新生成 checksums；
4. package-selftest；
5. `sha256sum -c`；
6. 保存最终变更日志。

否则以后会出现“服务器运行的是修复版，但 ZIP / 文档 / checksum 还是旧版”的配置漂移。

---

## R15：文档已经发生版本漂移
状态：HIGH

现有文档仍主要描述原始 V5.1：
- 40/page；
- 固定重试；
- 原 timeout 设计；
- 没有完整记录 20+20 split；
- 没有 stale resume 2h/6h 新策略；
- 没有 3h/330min hotfix；
- 没有 semantic validation fallback；
- 没有 bootstrap/backfill 问题。

必须更新：
- REQUIREMENTS
- README
- INSTALL
- AUDIT
- runtime changelog

不能只让代码变化、文档继续叫 Final。

---

# 7. 测试体系为什么没提前发现这些问题

原 package selftest 已覆盖：
- Bash/Python/TS syntax；
- canonical 分类；
- Stage accounting；
- Host Publisher；
- Watermark；
- 2-page checkpoint fixture；
- Init Merge；
- Apache；
- systemd verify；
- calendar。

这些测试对“代码正确性”很好，但对“真实大规模运行行为”覆盖不足。

缺失的场景：
1. 50～100 页长分页；
2. 高 backfill 比例；
3. source stale + checkpoint resume；
4. FreshRSS run 在分页期间变化；
5. 20 篇高字符 payload；
6. semantic-invalid model output；
7. lock conflict；
8. Morning 跨 13:00；
9. Morning 跨 14:00；
10. systemd total budget 与 retry budget；
11. 大 candidate Finalizer；
12. checksums / docs 在 hotfix 后的一致性。

因此后续测试要增加“负载/状态机/调度交叉”fixture，而不是只增加更多语法检查。

---

# 8. 后续新的测试矩阵

正式 Cutover 前建议新增以下离线/模拟测试：

### T01 Bootstrap Backfill
模拟 4000 条首次收到、其中 80% publisher 很旧。
验证老库存不会全部进入模型，但近期文章不漏。

### T02 100-page Preflight
Host-only 模拟 101 页。
必须在调用模型前发现容量超限。

### T03 Snapshot Immutability
分页过程中模拟 FreshRSS 新增 item。
snapshot 后的 run 结果不能漂移。

### T04 Resume State
覆盖：
- fresh new run；
- stale same source resume；
- stale different source；
- snapshot resume。

### T05 Dynamic Chunk
同样 20 篇但不同字符量。
必须按 payload budget 自动分块。

### T06 Model Failure Matrix
- timeout
- invalid JSON
- missing field
- wrong URL
- wrong count
- duplicate accounting
- wrong eventKey URL

### T07 Scheduler Collision
Morning active 时：
- 13:00 Fetch
- 14:00 Afternoon
不能出现“业务没跑但 exit 0 成功”。

### T08 Retry Budget
验证：
- YIELD 不消耗 failure attempts；
- 真失败才计数；
- outer wall-clock 到期时明确 CANCEL。

### T09 Finalizer Large Payload
测试接近 candidate / chars 上限时：
- 不超 context；
- 不超 timeout；
- accounting 完整。

### T10 Artifact Integrity
每次现场 hotfix 后：
- production source
- fixture
- installed unit
- package unit
- docs
- checksums
保持一致。

---

# 9. 后续实施优先级

## P0：当前 Morning 完成前不要改
当前 run 已从 Page 46 正确恢复。
在它完成/明确失败前，不再修改运行代码。

## P1：Morning 完成后、Afternoon E2E 前
必须先做一次“运行统计审计”：
- 最终 page count
- raw
- backfill
- low
- candidates
- Flash calls
- Pro fallback count
- Prepare duration
- Finalizer duration
- 最终 candidate JSON size
- 是否接近 100 pages / 500 candidates / 750k chars

然后决定是否先优化再跑 Afternoon。

## P2：正式 Cutover 前必须解决
1. bootstrap/backfill；
2. two-phase raw snapshot；
3. 13/14 overlap；
4. lock conflict exit=0；
5. retry/deadline semantics；
6. dynamic model chunk；
7. package/docs/checksum versioning。

## P3：可在 Cutover 后迭代
1. source health scoring；
2. 更精细的 cost/time metrics；
3. source anomaly quarantine；
4. 更好的 Finalizer 分层。

---

# 10. 后续操作纪律（避免重复错误测试）

从现在开始执行以下规则：

1. **先读状态，再改代码。**
2. **一次只解决一个已证实 root cause；同类相关修改可以合并。**
3. **任何修改前备份。**
4. **生产函数签名变化必须同时搜索 fixture/mock/call sites。**
5. **所有路径从生产常量/env 推导，不凭记忆手写。**
6. **临时诊断命令不在 SSH 登录 shell 顶层使用 `set -e`。**
7. **不因 tail/SSH 断开而重启 service。**
8. **不因为单个测试探针错误就判定生产状态失败。**
9. **修改后先 compile → targeted fixture → package selftest → static/systemd verify → 最后一次真实 E2E。**
10. **E2E 正在运行时不热改生产代码。**
11. **不重新 Fetch，除非确认不会使合法 checkpoint 的 sourceFetchRunId 失效。**
12. **每次真实 E2E 后记录容量、耗时、fallback、失败类型。**
13. **任何 safe limit 被触发时先分析原因，不直接粗暴抬高阈值。**
14. **所有 hotfix 最终必须回写 package/docs/checksum，不能只留在服务器现场。**
15. **Cutover 只在 Morning + 3 RSS + Afternoon + Review E2E + E2E Ready 全通过后执行。**

---

# 11. 当前状态快照

已完成：
- 旧环境复用；
- V5/V5.1 方案设计；
- 初始化来源体系；
- Stage Plugin；
- Host-only publish boundary；
- Prompt + disabled jobs；
- Apache；
- systemd 安装/verify；
- FreshRSS Retry E2E；
- Morning 多轮 bug 修复；
- stale checkpoint resume；
- deadline hotfix；
- 离线完整回归；
- Page 45 checkpoint 恢复；
- 真实 Morning 从 Page 46 继续。

当前运行：
- Morning runId：`morning-20260811T010342Z-091390e4`
- sourceFetchRunId：`fetch-20260811T005809Z-6419`
- 已确认 Page 70+；
- 当前未确认 `hasMore=false`；
- 未进入/未确认 Finalizer 完成。

尚未完成：
- Morning `PREPARE_COMPLETE`
- Morning Finalizer
- Morning Host Publish
- Morning SUCCESS
- 三个 AI RSS 验证
- Afternoon E2E
- Review E2E
- `verify-v51-e2e.py`
- E2E Ready
- transactional Cutover
- 正式 timers
- 最终文档/版本/checksum 固化

---

# 12. 总结

V5.1 的安全性、完整性和可恢复性基础设计是正确方向：
- Host deterministic pagination；
- exact article accounting；
- checkpoint；
- watermark；
- private Stage；
- Host-only publish；
- transactional cutover。

这次真实 E2E 暴露的核心不是这些安全边界错误，而是“**真实数据规模和调度状态组合没有在离线测试里充分模拟**”。

最主要的新结论：

> 下一版不能再采用“边从 FreshRSS continuation 拉、边逐页调用模型直到结束”的单阶段流水线。

应升级为：

FreshRSS Fetch
→ Host-only immutable raw snapshot / capacity audit
→ Bootstrap/backfill deterministic handling
→ Dynamic-budget model classification
→ Candidate capacity check
→ Hierarchical Finalizer
→ Host Publish
→ Watermark commit

并且 Fetch / Morning / Afternoon 调度要显式处理跨时段运行和锁冲突，不能依赖“通常会在一小时内跑完”的隐含假设。

本文件作为后续问题与优化台账基线；新增问题继续按 Pxx / Rxx / Txx 编号追加。


---

# 13. 新增运行事件：Page 100 硬上限实际触发

## P34：Morning 实际超过 100 页 / 4000 raw
状态：已发生，当前 E2E 必须停止继续重试
时间：2026-08-11 12:49 +08
优先级：CRITICAL

真实日志：
- Page 70～100 连续完成；
- Page 100：`raw=40 candidates=0 hasMore=True`；
- 随后 `[morning] PREPARE_FAIL rc=1`。

由当前生产代码可确定：
- `LIMIT=40`
- `MAX_PAGES=100`
- Page 100 仍有 continuation 时，不允许部分发布；
- 因此本轮至少涉及超过 4000 个 raw items。

结论：
此前 R01“100 页安全阈值可能在大量 bootstrap/backfill 下被实际撞满”已经从潜在风险升级为真实故障 P34。

重要现象：
- Page 70～100 共 31 页连续 `candidates=0`；
- 这些 1240 篇 raw 仍然执行了模型分类；
- 这进一步证明当前 bootstrap/backfill 处理顺序存在严重效率问题；
- 不能简单把 `MAX_PAGES` 从 100 提高到 150/200，因为那只会扩大 token/时间浪费，并掩盖数据摄入结构问题。

当前正确处理：
1. 停止本轮 Morning retry，不再继续 Attempt 2/3；
2. 保留 Page100 checkpoint / raw-page 数据用于审计；
3. 不执行 Finalizer、不发布半成品；
4. 不手工提高 MAX_PAGES；
5. 13:00 前停止继续读 FreshRSS，避免下一次 actualize 与当前长 run 数据集交叉；
6. 以本轮真实数据设计 V5.2 raw snapshot + bootstrap/backfill host filtering；
7. 完成离线大规模 fixture 后，再重新做 Morning E2E。

本事件同时提升以下风险优先级：
- R01 100-page capacity：已发生 → P34；
- R02 非 immutable raw snapshot：CRITICAL；
- R03 13:00 Fetch 与 Morning 重叠：CRITICAL；
- R07 固定 20 篇模型 chunk：HIGH；
- R10 缺少运行前容量预估：CRITICAL。



---

# 14. P34 最终现场数据补充（2026-08-11 12:53 +08）

主动停止 Morning 后，最终 checkpoint 确认为：

- runId：`morning-20260811T010342Z-091390e4`
- sourceFetchRunId：`fetch-20260811T005809Z-6419`
- pageCount：100
- allPagesComplete：false
- nextCursor：仍存在
- raw：4000
- backfill disposition：3545（88.625%）
- filtered low-value：241（6.025%）
- merged：10（0.25%）
- candidate articles：204（5.1%）
- cross-page candidate duplicate：0
- source_before_cutoff：0
- source_duplicates：0

Page 100：
`raw=40 candidates=0 hasMore=True`

随后：
`RuntimeError: MAX_PAGES_EXCEEDED=100; refusing partial publish`

外层 runner 在 180 秒后启动 Attempt 2：
- 正确读取同一个 sourceFetchRunId；
- 正确 `PREPARE_RESUME ... nextPage=101`；
- 因 MAX_PAGES=100，立即再次 `MAX_PAGES_EXCEEDED`；
- 随后进入第二次 180 秒等待。

在第二次等待期间人工执行：
`systemctl --user stop rss-v51-morning.service`

结果：
- `MainPID=0`
- `ActiveState=failed`
- `Result=signal`

此处 `failed/signal` 是人工 stop oneshot service 时终止正在 sleep/retry 的进程造成，不是新增业务故障。当前 Morning 已停止，checkpoint 保留。

新的定量结论：
- 当前窗口至少 >4000 raw；
- 已处理部分中 88.625% 最终进入 backfill；
- 真正 candidate 仅 5.1%；
- 因此“先让模型逐篇识别 backfill”的当前顺序具有约一个数量级的无效工作放大；
- V5.2 必须把容量审计、bootstrap/backfill 确定性处理放到模型之前。



---

## P35：SSH 断开后 Bash 命令误在 Windows PowerShell 执行
状态：已识别，无服务器副作用
时间：2026-08-11 12:56 +08
类型：操作流程 / Shell 上下文错误

现象：
- SSH 已先出现目标 Lightsail 连接关闭；
- 随后 `PID=$(systemctl ...)`、`if [ ... ]; then` 等 Bash 语法在 `PS <LOCAL_PROJECT_PATH>>` 下执行；
- PowerShell 报 CommandNotFound / ParserError；
- `echo "morningMainPID=$PID"` 输出了 `3008`。

原因：
- SSH 已断开，当前 shell 已经回到 Windows PowerShell；
- PowerShell 内置自动变量 `$PID` 表示当前 PowerShell 进程 ID，因此 `3008` 与服务器 Morning service 无关。

影响：
- 这些失败命令没有在服务器执行；
- `echo STOP_MORNING_STILL_RUNNING` 只是本机输出；
- `exit 1` 只退出当前 PowerShell 进程/终端；
- 不会修改 Morning checkpoint、FreshRSS、OpenClaw 或服务器文件。

后续操作规范：
1. 任何 Linux/Bash 命令执行前必须确认提示符为 `ubuntu@...:~$`；
2. 若看到 `PS C:\...>`，先重新 SSH，不得继续粘贴 Bash；
3. Linux 脚本变量避免使用通用名 `PID`，改用 `MORNING_PID` / `SERVICE_PID`，减少跨 Shell 混淆；
4. 长任务监控 SSH 断开后，默认认为只是会话中断，先重新连接并读状态，不自动 restart；
5. 用户可见操作块开头增加远端身份 guard（`whoami` / `hostname`）。



---

# 15. P36：4000 篇 Host-only 审计结论（2026-08-11 12:59 +08）

状态：已完成第一轮离线数据审计
类型：容量 / Bootstrap / Source Burst / Backfill
优先级：CRITICAL

## 15.1 全局统计

- rawTotal：4000
- uniqueArticleIds：4000
- crossPageDuplicateIdCount：0
- missingAccountingCount：0
- extraAccountingCount：0
- feedCount：48
- rawLikelyBackfillFlag：3623（90.575%）
- 模型 disposition:
  - backfill：3545（88.625%）
  - candidate：204（5.1%）
  - filtered：241（6.025%）
  - merged：10（0.25%）
- summaryTruncated：418（10.45%）
- receiveLag median：122.35 天
- receiveLag max：1048.04 天
- publisher year:
  - 2026：2837
  - 2025：1140
  - 2024：19
  - 2023：4

receiveLag bucket：
- <=1d：141
- 1-7d：236
- 7-30d：453
- 30-90d：839
- 90-365d：2161
- >365d：170

重要数学一致性：
`453 + 839 + 2161 + 170 = 3623`

这与 `rawLikelyBackfillFlag=3623` 完全一致，证明当前 Host 的 `isLikelyBackfill` 本质上就是稳定执行“receive lag > 7 days”规则，数据本身没有统计漂移。

但：
- Host backfill flag：3623
- 模型最终 backfill disposition：3545
- 差额：78

因此不能直接把所有 `isLikelyBackfill=true` 在 Host 层无条件丢弃；需要进一步做 flag × disposition 交叉表，确认这 78 篇分别是 candidate / filtered / merged 的多少，以及是否集中在特定来源。

## 15.2 洪水高度集中在少数 Archive/Changelog Feed

Top 5 raw：
1. Cloudflare Developer Platform Changelog：847
2. Vercel Changelog：825
3. HKMA Press Releases：675
4. OpenAI Newsroom：546
5. Hugging Face Blog：215

Top 5 合计：
`847+825+675+546+215 = 3108`

占 4000：
`77.7%`

Top 4 合计：
2893，占 72.325%。

这说明 >4000 的异常并不是 48 个来源平均产生，而是少数“大历史库型 feed”首次导入时一次性吐出大量库存。

Page 70-100 共 1240 raw。
上述 Top 5 在 tail70to100 中：
- Cloudflare：308
- Vercel：310
- HKMA：280
- OpenAI：229
- Hugging Face：98

合计 1225；再加 Product Hunt 1 条为 1226。
即 Page70-100 的 1240 条中约 98.8% 由 Top 5 大历史库 Feed 构成。

因此 Page70-100 连续 candidate=0 的现象，本质上是少数 archive feed 的历史尾部，而不是整个订阅库都低质量。

## 15.3 高流量不等于应该删除来源

高 backfill Feed 中仍有 candidate：
- Vercel：31
- OpenAI：23
- Cloudflare：17
- Hugging Face：7
- LangChain：12

所以不能因为首次导入 raw 很大就 REMOVE 这些来源。
正确动作是：
- 保留 Source；
- 对首次 bootstrap 历史库存做特殊处理；
- 对 source burst 做限流/归档识别；
- 正常增量运行时继续保留其近期内容。

## 15.4 高产出 Feed

候选率很高：
- GitHub Changelog：69.23%
- Hacker News Show HN：55.74%
- Product Hunt：51.79%
- ESMA：50%
- Agência Sebrae：35.71%
- CFTC：27.27%
- Anthropic Claude Blog：25%
- OpenClaw Releases：25%

这些来源不应被 bootstrap 优化误伤。

## 15.5 第一轮审计后的设计结论

V5.2 不应采用“删除大 Feed”作为主要解决方案。

正确结构应是：

1. FreshRSS Fetch
2. Host-only immutable snapshot
3. Source/burst/capacity audit
4. Bootstrap inventory recognition
5. `lag<=7d` 正常进入模型候选池
6. `lag>7d` 进入 historical/backfill rescue path，而不是逐篇正常分类
7. dynamic payload-budget chunk
8. candidate accounting
9. Finalizer
10. Host publish
11. watermark commit

下一步仍需要一个纯 Host 的二次审计：
- `isLikelyBackfill × disposition` 交叉表；
- backfill-flagged candidate 来源；
- receivedAt 最早/最晚范围；
- Page70-100 精确 source concentration；
- summaryTruncated 与 candidate/backfill 的交叉情况。

在得到该交叉表前，不对 `lag>7d` 做无条件 Host drop。



---

# 16. P37：Backfill × Disposition 交叉审计（2026-08-11 13:01 +08）

状态：已完成
类型：Bootstrap / Backfill rescue / 模型行为
优先级：CRITICAL

## 16.1 交叉表

Host `isLikelyBackfill=false`：
- candidate：172
- filtered：199
- merged：6
- backfill：0
- 合计：377

Host `isLikelyBackfill=true`：
- candidate：32
- filtered：42
- merged：4
- backfill：3545
- 合计：3623

一致性：
377 + 3623 = 4000

重要比率：
- Host flagged 中模型最终 backfill：3545 / 3623 = 97.85%
- Host flagged 中模型 candidate：32 / 3623 = 0.88%
- Host flagged 中模型 filtered：42 / 3623 = 1.16%
- Host flagged 中模型 merged：4 / 3623 = 0.11%
- 全部 candidate 中 flagged candidate：32 / 204 = 15.69%

结论：
1. 对所有 `lag>7d` 无条件 Host drop 不安全，会按当前模型输出损失 15.69% Candidate。
2. 但让全部 3623 篇走正常模型分类又极低效，因为 97.85% 最终仍被归为 backfill。
3. 必须增加 Bootstrap-aware rescue lane，而不是简单 keep/drop 二元规则。

## 16.2 Candidate Lag 分布

204 个 Candidate：
- <=1d：64
- 1-7d：108
- 7-30d：9
- 30-90d：3
- 90-365d：20
- >365d：0

即：
- lag<=7d candidate：172（84.31%）
- lag>7d candidate：32（15.69%）

值得注意：
lag>90d 仍被模型选为 candidate 的有 20 篇。
这进一步说明当前模型在 bootstrap 场景下把“主题相关/重要”与“当前新进展”混在了一起。

## 16.3 Flagged Candidate 来源

主要集中：
- Vercel Changelog：11
- OpenAI Newsroom：9
- Cloudflare Developer Platform Changelog：4
- Happy Bootstrapping：2
- CFTC：1
- BIS：1
- Product Hunt：1
- Portainer：1
- Hugging Face：1
- HKMA：1

Top 3（Vercel/OpenAI/Cloudflare）合计 24/32 = 75%。

很多示例具有明显历史属性，例如 lag 90～164 天的：
- Running Codex safely at OpenAI
- Next.js May 2026 security release
- Testing ads in ChatGPT
- Gamma builds design-first agents with Vercel
等。

这说明当前 classifier 的 Prompt 即使要求：
`backfill=isLikelyBackfill=true 且没有本页可证明的当前新进展`
模型仍可能因为文章“重要/相关”而保留为 candidate。

因此下一版不应把“是否当前新闻”的判断完全交给模型。

## 16.4 Received 时间范围

4000 篇 raw 的 receivedAt：
- earliest：2026-08-10 08:17:14.958 UTC = 2026-08-10 16:17:14 +08
- latest：2026-08-11 00:58:34.631 UTC = 2026-08-11 08:58:34 +08
- span：60079 秒 ≈ 16h 41m 19s

该范围与 Morning 18h receive-time window 基本一致，证明：
- 系统确实是在按 FreshRSS receive time 正确取窗口；
- >4000 条不是 pagination cutoff 漂移导致，而是该 18h 窗口真实包含大量首次导入历史库存。

## 16.5 Page70-100 Source Concentration

Page70-100：1240 raw。

其中：
- Vercel：310（25.00%）
- Cloudflare：308（24.84%）
- HKMA：280（22.58%）
- OpenAI：229（18.47%）
- Hugging Face：98（7.90%）

Top 5 合计 1225 / 1240 = 98.79%。

且这 1225 条全部 `isLikelyBackfill=true`。

因此 Page70-100 几乎完全是少数大 archive feed 的历史库存尾部。

## 16.6 Summary truncation 交叉

- flag=false candidate：172，其中 truncated=26
- flag=false filtered：199，其中 truncated=20
- flag=false merged：6，其中 truncated=1
- flag=true candidate：32，其中 truncated=16
- flag=true filtered：42，其中 truncated=3
- flag=true merged：4，其中 truncated=0
- flag=true backfill：3545，其中 truncated=352

重要点：
flagged candidate 的 50%（16/32）摘要被截断。
说明对 rescue lane 不能仅依赖当前截断 summary 做轻量关键词判断，否则可能误判。

## 16.7 V5.2 设计原则据此收敛

### 正常运行（steady-state）
继续保留 receive-time window + watermark，确保真正延迟到达信息不漏。

### Bootstrap/首次导入
单独进入 bootstrap mode：
1. Host-only snapshot；
2. 识别首次导入库存；
3. lag<=7d 进入正常分类；
4. lag>7d 不进入正常逐篇分类，而进入历史 rescue lane；
5. rescue lane 应按 source + time bucket + title/metadata 聚合，而不是逐篇正常模型调用；
6. 只有 rescue 选中的极少数文章再进入主 Candidate 分类；
7. 不能把“大 Feed”直接删除。

### Rescue Lane 最低安全要求
- 不仅看 summary；
- 保留 title、source、publishedAt、receivedAt、URL、summaryTruncated；
- 对 truncated item 必须保守处理；
- source-level batching；
- 对明显 bootstrap archive 的大批旧条目优先 deterministic suppress；
- 任何 rescue 规则都要用当前 4000 篇作为回放 fixture，测量 Candidate recall。

### 新的回放验收指标
在当前 4000 篇真实样本上：
- raw 4000 全量 accounting 保留；
- 正常 AI 路径目标减少至少 80%；
- 对当前 204 candidate 的 recall 目标 >= 98%，若不能达到则进入人工/二级 rescue；
- 不能出现 silent drop；
- page70-100 这类 archive tail 不应再执行 62 次常规模型 chunk。



---

# 17. P38：V5.1 事故现场快照冻结完成（2026-08-11 13:04 +08）

状态：完成
类型：版本冻结 / 事故复现 / V5.2 输入基线

快照目录：
`/home/ubuntu/rss-v51-incident-20260811-130419`

压缩包：
`/home/ubuntu/rss-v51-incident-20260811-130419.tar.gz`

目录大小：
- 原目录：13M
- tar.gz：2.8M

SHA256：
`1fedee84bc9f7c90d251f70deb7a24f993e6b5a987b391f344fde711325bf475`

快照包含：
- 当前 V5.1 production package；
- Morning Page1-100 失败 run；
- morning-progress.json；
- Host-only audit；
- v51-morning.log；
- installed systemd units；
- MANIFEST.txt；
- 当前关键 production source hash。

结论：
从此刻开始停止对 V5.1 进行现场补丁式修改。
V5.2 应以该事故快照为唯一回放基线之一，通过离线修改、fixture/selftest/replay 后再重新部署。


---

# V5.2 构建阶段追加记录

## P39：Morning→Afternoon bootstrap 重复灌入风险
在 V5.2 第一版离线设计复核中发现：如果只提交“Feed 已 bootstrap”，Morning 隔离的历史 article 在同日 Afternoon 6h receive window 中仍可能重新进入模型。修复为同时持久提交 `quarantinedArticleIds`；相同 article ID 后续继续 Host quarantine，而一个新的 late-arrival article ID 仍进入模型。

真实 4000 篇 replay：模拟 Morning commit 后再以同一 4000 条作为 Afternoon 输入，`persistentBootstrapQuarantineHits=3623`、model input 仍为 377，未重新膨胀为 4000。

## P40：Cutover timer catch-up 风险
V5.1/V5.2 早期 timer 使用 `Persistent=true`。如果 E2E 完成后在白天执行 `enable --now`，存在将当天已错过的 07/08/13/14 schedule 立即 catch-up 的组合风险。V5.2 全部生产 timer 改为 `Persistent=false`；漏窗口由 intelligence watermark 在下一正常 schedule 恢复，不在 Cutover 时并发补跑。

## P41：V5.2 E2E/Cutover 路径复核
修复 `verify-v52-e2e.py` 使用 `fetch-state-v52`，并增加 snapshot hash、raw exact accounting、bootstrap state、schemaVersion=2 Gate。Cutover 同时确保旧 system Fetch、旧3 OpenClaw jobs 和任何 V5.1 timer 均不与 V5.2 双调度；失败自动恢复切换前状态。
\n\n## P42：Persistent quarantine 不能只按 article ID 永久屏蔽\nV5.2 构建后期复核发现：如果 `quarantinedArticleIds` 只保存 article ID，那么某些 Feed 在同一个 ID 下真正更新正文/标题时，会因为“这个 ID 以前属于 bootstrap 历史库存”而永久被 Host 隔离，形成长期漏报风险。\n\n修复：\n- persistent quarantine 状态改为保存 `article ID + contentFingerprint`；\n- 同一 ID 且 fingerprint 未变：继续确定性 quarantine；\n- 同一 ID 但 fingerprint 已变：重新进入模型路径；\n- 该 changed-content item 最终成功发布后，从 persistent quarantine 中释放旧记录。\n\nFixture 已覆盖 `fingerprintMismatch=1` 场景，验证内容变化后的旧 ID 不会被永久屏蔽。\n\n## P43：Synthetic article ID 不能依赖分页位置\nV5.2 构建后期复核发现：FreshRSS 极少数条目如果缺失原始 article ID，而 synthetic ID 使用 `page/ordinal` 等位置数据，那么 continuation 分页位置变化时，同一文章会获得不同 ID，破坏：\n- snapshot dedup；\n- exact accounting；\n- persistent quarantine；\n- retry/resume 一致性。\n\n修复：\n- 缺失原始 ID 时，synthetic ID 使用稳定内容 seed；\n- seed 基于 Feed/URL/title/published/author 等稳定字段；\n- 不使用 page、ordinal 或 receive-position；\n- snapshot fixture 已模拟同一文章从 page1/ordinal1 移动到 page99/ordinal40，synthetic ID 保持一致。\n\n## P44：交付物必须按“用户实际拿到的 ZIP”重新验证\n过去多次现场修复证明，“工作目录测试通过”不等于“最终打包文件一定正确”。V5.2 增加最终发布 Gate：\n\n1. clean source tree `sha256sum -c checksums.sha256`；\n2. 生成 ZIP；\n3. 将 ZIP 解压到全新目录；\n4. 对解压后的文件再次 `sha256sum -c`；\n5. 从解压目录运行完整 `package-selftest.sh`；\n6. 真实 4000 篇事故快照 replay 必须再次通过；\n7. 检查 ZIP 不包含事故 raw 数据；\n8. 检查 ZIP 不包含真实 `RSS_PUBLISH_TOKEN`；\n9. 只有以上全部通过后，ZIP 才可交付。\n

---

## P45：V5.2 Stage manifest 缺少 OpenClaw 必需的 configSchema
状态：R2 已修复
时间：2026-08-11
优先级：CRITICAL / 发布 Gate 漏检

真实服务器在阶段 2 `npm run plugin:validate` 返回：
`plugin manifest requires configSchema`。

根因：
- V5.2 `openclaw.plugin.json` 声明了 id/name/tools，但没有 `configSchema`；
- OpenClaw 原生插件要求 manifest 始终包含 inline JSON Schema，即使插件无配置；
- 原 package selftest 只检查插件 id/tool 隔离，没有校验 manifest schema；
- Stage 0 preflight 只确认 CLI 支持 `plugins validate`，没有执行真实插件 validate。

修复：
- manifest 增加空严格 schema：`type=object, additionalProperties=false, properties={}`；
- package selftest 增加 configSchema 结构断言；
- 发布包升级为 R2，并重新生成 checksum、重新做 4000 篇 replay。

部署纪律：
真实 `npm run plugin:validate` 仍作为 Stage 2 强制 Gate；任何失败不得继续 link install。

---

## P46：defineToolPlugin generated metadata 未纳入真实构建链（R2 live validate 发现）
状态：R3 修复
时间：2026-08-11 15:10 +08
优先级：CRITICAL / 发布流程

R2 在真实 OpenClaw 2026.7.1-2 上：
- `tsc` PASS；
- vitest 2/2 PASS；
- `openclaw plugins validate --entry ./dist/index.js` FAIL；
- 报错：`openclaw.plugin.json generated metadata is stale`；
- 报错：`openclaw.plugin.json contracts.tools is missing: rss_daily_finalize_save_v52`。

根因：
V5.2 Stage 使用 `defineToolPlugin`。该 API 的 manifest 是 OpenClaw generator 从 runtime entry 静态 metadata 生成的契约，不应把 `openclaw.plugin.json` 当普通手写清单维护。R1/R2 都只做了 TypeScript build + 静态 manifest 修补，没有在 Stage 2 正确执行 `openclaw plugins build`。

R3 修复：
1. manifest 结构改为 generator 契约：`configSchema`、`activation.onStartup`、`contracts.tools`、`toolMetadata.<tool>.optional`；
2. 删除 legacy top-level `tools`；
3. `src/index.ts` 显式声明 strict empty `configSchema`；
4. Stage 2 必须先 `npm run plugin:build`；
5. `plugin:validate` 增加 `plugins build --check`，再 `plugins validate`；
6. package-selftest 新增 generated metadata 静态一致性 Gate；
7. 以后 live OpenClaw generator/validate 是插件发布的强制 Gate，不能只做本地 TypeScript/fixture。
