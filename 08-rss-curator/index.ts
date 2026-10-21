import { Type } from 'typebox';
import { defineToolPlugin } from 'openclaw/plugin-sdk/tool-plugin';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { CuratorStore, freshRead } from './store.js';

const empty = Type.Object({}, { additionalProperties: false });
const store = () => new CuratorStore(process.env.RSS_INTELLIGENCE_ROOT || join(homedir(), '.openclaw', 'rss-intelligence'));
const Change = Type.Object({
  action: Type.Union([Type.Literal('add'), Type.Literal('remove'), Type.Literal('move')]),
  feedUrl: Type.Optional(Type.String()), title: Type.Optional(Type.String()),
  toCategory: Type.Optional(Type.String()), fromCategory: Type.Optional(Type.String()), reason: Type.Optional(Type.String()),
}, { additionalProperties: true });

export default defineToolPlugin({
  id: 'rss-curator', name: 'RSS Curator',
  description: 'Read FreshRSS and store local reports or pending reviews. Never changes subscriptions.',
  configSchema: empty,
  tools: tool => [
    tool({ name: 'freshrss_list_subscriptions', label: 'List subscriptions', optional: true,
      description: 'Read all subscriptions. Does not subscribe, unsubscribe or move feeds.', parameters: empty,
      execute: async () => freshRead('subscription/list') }),
    tool({ name: 'freshrss_recent_articles', label: 'Read recent articles', optional: true,
      description: 'Read at most 100 recent articles and expose continuation when present.',
      parameters: Type.Object({ limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 100 })), continuation: Type.Optional(Type.String()) }, { additionalProperties: false }),
      execute: async params => {
        const result = await freshRead('stream/contents/reading-list', { n: String(params.limit || 20), ...(params.continuation ? { c: params.continuation } : {}) });
        return { ...result, complete: !result.continuation, modifiesFreshRss: false };
      } }),
    tool({ name: 'rss_report_save', label: 'Save local report', optional: true,
      description: 'Save an immutable local report. Does not publish public content.',
      parameters: Type.Object({ reportMarkdown: Type.String({ minLength: 1 }), title: Type.Optional(Type.String()) }, { additionalProperties: true }),
      execute: async params => store().save('reports', params) }),
    tool({ name: 'rss_report_get_latest', label: 'Get latest local report', optional: true,
      description: 'Read the latest saved report.', parameters: empty,
      execute: async () => ({ report: (await store().list('reports'))[0] || null }) }),
    tool({ name: 'rss_review_save', label: 'Save pending source review', optional: true,
      description: 'Store source proposals for human review; never apply changes to FreshRSS.',
      parameters: Type.Object({ summaryMarkdown: Type.String({ minLength: 1 }), reviewDate: Type.Optional(Type.String()), changes: Type.Array(Change) }, { additionalProperties: true }),
      execute: async params => store().save('reviews', params) }),
    tool({ name: 'rss_review_get_pending', label: 'Get pending source reviews', optional: true,
      description: 'Read pending local reviews.', parameters: empty,
      execute: async () => ({ reviews: (await store().list('reviews')).filter(row => row.status === 'pending') }) }),
  ],
});
