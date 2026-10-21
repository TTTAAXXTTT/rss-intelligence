import { describe, expect, it } from 'vitest';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { CuratorStore, freshRead } from './store.js';

describe('curator compatibility boundary', () => {
  it('persists immutable pending proposals without applying them', async () => {
    const root = await mkdtemp(join(tmpdir(), 'rss-curator-'));
    try {
      const store = new CuratorStore(root);
      const result = await store.save('reviews', { summaryMarkdown: 'Synthetic source review', changes: [{ action: 'add', feedUrl: 'https://example.test/feed.xml' }] });
      expect(result.modifiesFreshRss).toBe(false);
      expect((await store.list('reviews'))[0].status).toBe('pending');
      await expect(store.save('reviews', { summaryMarkdown: 'bad', changes: [{ action: 'add', feedUrl: 'file:///etc/passwd' }] })).rejects.toThrow('REVIEW_URL_INVALID');
      const second = await store.save('reports', { reportMarkdown: 'Synthetic report' });
      expect(second.id).not.toBe(result.id);
    } finally { await rm(root, { recursive: true, force: true }); }
  });
  it('rejects arbitrary API endpoints before credentials or network are accessed', async () => {
    let called = false;
    const transport = (async () => { called = true; throw new Error('network forbidden'); }) as typeof fetch;
    await expect(freshRead('subscription/edit', {}, transport)).rejects.toThrow('READ_ENDPOINT_NOT_ALLOWED');
    expect(called).toBe(false);
  });
});
