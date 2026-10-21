# RSS Initialization Validation Rules V5.2

对一个 Micro Batch 的最多 2 个候选做严格验证。

硬规则：
1. 必须读取 `/agent/RSS_POLICY.md`，并以其来源评分、安全、PR/Sponsored、SEO/AEO规则为准。
2. 最多 1 次 web_search + 2 次 web_fetch；优先直接 fetch 候选网站与 RSS/Atom Feed。一次 Feed 返回的最近条目用于抽样，不逐篇抓 10~20 个页面。
3. 必须确认 Feed URL 为真实、纯原始 HTTP(S) URL；Markdown URL、描述文本、javascript/data/file URL 禁止进入结构化 changes。为兼容当前生产 `rss_review_save` 的结构化 URL 校验，`feedUrl` 不得含空白、`[`、`]`、`(`、`)`、`<`、`>`；若源只提供含字面圆括号的 Feed，只有在已验证等价 percent-encoded URL 可正常访问时才用编码后的 URL，否则最高 WATCH，不得因保存失败反复重试。
4. 尽可能依据 Feed 最近 10~20 条条目判断持续更新、PR/Sponsored/营销占比、原创性、信噪比。
5. 主题相关度不能替代一手性/权威性。商业/PR占比高时严格应用 RSS_POLICY 封顶规则。
6. 82~100 才能 ADD；70~81 WATCH；<=69 不建议。无法验证真实 Feed 不得 ADD。
7. 最终只调用一次 `rss_review_save` 保存这个批次 Pending Review；绝不修改 FreshRSS。
8. summaryMarkdown 必须包含调用方给出的 `INIT_V52_BATCH_ID=<id>` 与 `INIT_V52_CATEGORY=<category>`。
9. changes 仅包含评分 >=82 的 ADD；KEEP/WATCH/拒绝不进入 changes；每个 ADD 的 `toCategory` 必须与调用方目标分类完全一致，不得改成其他分类或创造新分类。
10. reason 应简明包含来源类型、主要证据、语言/地区（可确认时）和关键风险。
