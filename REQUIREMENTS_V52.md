# RSS Intelligence V5.2 — 需求与工程基线

## 1. 目标
在现有 AWS / FreshRSS / OpenClaw 环境上继续运行个人情报系统：FreshRSS 保存原始 RSS；Host 确定性冻结和审计原料；OpenCode Go 对需要 AI 的部分做分类与最终分析；最终输出 `all.xml`、`priority.xml`、`reports.xml` 供多设备 RSS 阅读。

V5.2 是对已经部署但尚未 Cutover 的 V5.1 的升级，不重装 Lightsail、域名、HTTPS、FreshRSS、Apache、OpenClaw，也不重新做已经完成的 48 个来源初始化。

## 2. 固定环境
- Ubuntu / AWS，使用部署时配置的 Lightsail 公网地址。
- `rss.example.com`，Apache + HTTPS 已工作。
- FreshRSS container `freshrss`，本机 `127.0.0.1:8080`。
- OpenClaw Gateway 本机 `127.0.0.1:18789`。
- Workspace `/home/ubuntu/.openclaw/workspace`，Agent 内 `/agent` 只读。
- `rss-curator` 保留只读 FreshRSS、Report、Pending Review 能力。
- 旧稳定 FreshRSS `freshrss-actualize.timer` 与旧 Morning/Afternoon/Review Job 在 V5.2 E2E Ready 前继续保留。
- V5.1 timers 必须保持未启用，V5.1 service 不得仍在运行。

## 3. 模型
- 分类：`opencode-go/deepseek-v4-flash`，严格失败后可用 `opencode-go/deepseek-v4-pro` fallback。
- Morning/Afternoon Finalizer：Pro 主模型，Flash fallback。
- Review：Pro 主模型，Flash fallback。
- 不修改 OpenClaw 全局默认模型。

## 4. 时间表
全部 `Asia/Shanghai`：
- 07:00 FreshRSS Fetch
- 08:00 Morning Intelligence
- 13:00 FreshRSS Fetch
- 14:00 Afternoon Intelligence
- 15/28 03:00 Source Review

V5.2 timers 必须 `Persistent=false`，Cutover 后不得因为当天已错过 07/08/13/14 而立刻补跑多个任务。漏掉的情报窗口由下一正常运行的 watermark recovery 处理。

## 5. 优先级与主题
S：`S-链上金融`、`S-跨境金融`、`S-商业机会`。

A：`A-AI-Agent`、`A-AI工具自动化`、`A-微信企微私域`、`A-AIxCryptoPayment`。AI 默认 A，不因 AI 标签自动升 S。

B：`B-海外互联网`、`B-开源自托管`、`B-政策监管`。

C：`C-宏观流动性`、`C-消费科技`，只保留真正影响核心主线的内容。

D：娱乐、社会热点、普通行情/币价、泛资讯噪音，不进入核心 AI RSS。

四条主线：
1. 全球资金与金融基础设施变化；
2. 新商业模式与小众可执行机会；
3. Crypto 真实产业进展；
4. 海外正在增长、国内尚未普及的新产品/服务/模式。

## 6. Immutable Snapshot — V5.2 的首要硬约束
1. FreshRSS Fetch 成功后，Morning/Afternoon 的第一阶段必须由 Host 完整冻结有效 receive-time 窗口。
2. Snapshot 每页 40 raw，使用 continuation + 固定 cutoff，一直读取到 `hasMore=false`。
3. Snapshot 完成前不得调用模型。
4. Snapshot 内 raw article ID 必须全局唯一；跨页重复 ID 明确失败。
5. Snapshot hash 绑定 sourceFetchRunId、cutoff 和有序 article IDs；Prepare 只能消费该 snapshot，不能重新访问 FreshRSS。
6. Snapshot 与 V5.2 actualize 共用 `v52-source-dataset.lock`；不能在 live dataset 读写过程中并发。
7. Host-only snapshot 安全上限默认 500 页（20,000 raw）。仍有 continuation 时在任何模型调用前 `SNAPSHOT_CAPACITY_EXCEEDED`。

## 7. Bootstrap / 历史库存
真实 V5.1 事故证明首次导入可在一个 18h receive window 内出现 >4000 条历史库存，其中 4000 样本的 3623 条 `receiveLagDays>7`。

V5.2 规则：
1. 未成功 bootstrap 的 Feed，其 `isLikelyBackfill=true` 条目不进入正常逐篇模型分类，而进入 Host `bootstrap-quarantine.json`。
2. Quarantine 不是静默丢弃：保留 ID、Feed、title、URL、publishedAt、receivedAt、lag、summaryTruncated 和原因。
3. Feed bootstrap 状态与 quarantine article IDs 只在 Host Publish 成功后提交到 `bootstrap-state-v52.json`。
4. 同一历史 article ID 在 Afternoon/后续窗口再次出现且内容 fingerprint 未变化时继续确定性 quarantine，避免重复喂模型。
5. 如果同一已 quarantine ID 的 title/URL/published/summary fingerprint 发生变化，则释放旧 quarantine 并重新进入模型；成功后从 persistent quarantine state 移除。
6. 已 bootstrap Feed 后续出现一个新的、此前从未 quarantine 的旧发布时间 article ID，仍进入正常模型路径；不能用 `lag>7d` 永久一刀切，保护真正 late arrival。
7. 不能因为历史库存量大而自动 REMOVE Cloudflare/Vercel/OpenAI/HKMA 等高价值来源。

## 8. 动态分类与 exact accounting
- 模型 Batch 同时限制 article count 与输入体积：默认 <=20 篇且 <=28,000 JSON chars。
- 每个 Batch 的 article.id 必须被 accounting 恰好一次。
- source URL 必须逐字符来自当前 Batch raw article，不得自行 canonicalize。
- Flash transport/JSON/strict semantic validation 失败可对该 Batch 用 Pro fallback；验证规则本身不放宽。
- 最终 raw accounting 必须覆盖 snapshot 的每一个 raw ID：Host quarantine 或 model，两者不能遗漏/重复。
- Candidate >500 或 Candidate JSON >750,000 chars：明确失败，不静默裁剪。

## 9. Yield、失败和重试
- `PREPARE_YIELD`：正常时间片让出，已完成 Batch checkpoint 保留；不算失败、不消耗 3 次失败预算、不等待 180 秒。
- 真失败：固定等待 180 秒；全链路共享总计 3 次 true failure budget。
- 第 3 次真失败：取消当前轮，下一正常 schedule 继续。
- Snapshot capacity overflow：fatal safe-stop；不进入模型、不做无意义 retry。
- Lock conflict 不允许 `exit 0` 冒充业务成功。

## 10. Watermark 与 Afternoon baseline
- 正常 Morning 18h、Afternoon 6h。
- 上一 AI Intelligence 失败时，下一成功轮 effective cutoff 可向前扩展至最后成功 `sourceFetchFinishedAt`，120 秒重叠。
- 自动恢复最多 72h，超过后明确要求人工 catch-up。
- Afternoon 每轮固化同日 Morning baseline；同一轮 retry 不漂移。

## 11. Finalizer / Publisher 安全边界
- Finalizer 只有 `rss_daily_finalize_save_v52` 私有 Stage 工具。
- Finalizer 无公网 RSS publish 工具、无 FreshRSS subscription mutation 工具。
- Host Publisher 二次验证 schema/candidate accounting/baseline/category/eventKey/source URL 后才能生成公网 RSS/HTML。
- Bootstrap state 在 publish 后提交，authoritative intelligence watermark 最后提交。
- Secrets/tokenized RSS URL 不出现在模型报告和公开日志中。

## 12. Review
15/28 Review 只读当前订阅、搜索/验证/评分并保存 Pending Review；绝不自动 ADD/REMOVE/MOVE。用户不主动查看并明确确认，FreshRSS 不发生来源变更。

## 13. Cutover / Rollback
- V5.2 Template Jobs 始终 disabled，由 systemd `cron run` force-run。
- 正式切换前必须完成 V5.2 Fetch → Morning → Public RSS → Afternoon → Review E2E → `V52_E2E_READY`。
- Cutover 必须事务式：快照旧调度 → disable 旧 system timer/旧3 jobs/V5.1 timers → enable V5.2 timers → 验证；任一步失败自动 rollback。
- 原系统与 V5.1 事故快照保留，不删除。
