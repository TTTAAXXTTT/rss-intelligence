import { mkdir, open, readdir, readFile, lstat } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';

/** Compatibility implementation for the original separately deployed curator.
 * FreshRSS operations are read-only. Review changes are proposals, never applied.
 */
export class CuratorStore {
  constructor(readonly root: string) {}
  private async directory(kind: 'reports' | 'reviews') {
    const base = resolve(this.root);
    await mkdir(base, { recursive: true, mode: 0o700 });
    if ((await lstat(base)).isSymbolicLink()) throw new Error('STORE_SYMLINK_REJECTED');
    const directory = join(base, kind);
    await mkdir(directory, { recursive: true, mode: 0o700 });
    if ((await lstat(directory)).isSymbolicLink()) throw new Error('STORE_SYMLINK_REJECTED');
    return directory;
  }
  async save(kind: 'reports' | 'reviews', input: Record<string, unknown>) {
    if (typeof input.summaryMarkdown !== 'string' && typeof input.reportMarkdown !== 'string') {
      throw new Error('MARKDOWN_REQUIRED');
    }
    if (kind === 'reviews') {
      if (!Array.isArray(input.changes)) throw new Error('REVIEW_CHANGES_REQUIRED');
      for (const change of input.changes as Record<string, unknown>[]) {
        if (!change || !['add', 'remove', 'move'].includes(String(change.action))) throw new Error('REVIEW_ACTION_INVALID');
        if (change.feedUrl !== undefined) {
          const value = String(change.feedUrl);
          const u = new URL(value);
          if (!['http:', 'https:'].includes(u.protocol) || !u.hostname || u.username || u.password || /[\s\[\]()<>]/.test(value)) {
            throw new Error('REVIEW_URL_INVALID');
          }
        }
      }
    }
    const id = randomUUID();
    const record = { ...input, id, status: kind === 'reviews' ? 'pending' : 'saved', savedAt: new Date().toISOString() };
    const file = join(await this.directory(kind), id + '.json');
    const handle = await open(file, 'wx', 0o600);
    try { await handle.writeFile(JSON.stringify(record, null, 2) + '\n', 'utf8'); await handle.sync(); }
    finally { await handle.close(); }
    return { id, status: record.status, modifiesFreshRss: false };
  }
  async list(kind: 'reports' | 'reviews') {
    const dir = await this.directory(kind);
    const result: Record<string, unknown>[] = [];
    for (const name of await readdir(dir)) {
      if (!/^[a-f0-9-]{36}\.json$/.test(name)) continue;
      const file = join(dir, name);
      if (!(await lstat(file)).isFile() || (await lstat(file)).isSymbolicLink()) throw new Error('STORE_FILE_INVALID');
      result.push(JSON.parse(await readFile(file, 'utf8')));
    }
    return result.sort((a, b) => String(b.savedAt).localeCompare(String(a.savedAt)));
  }
}

export async function freshRead(endpoint: string, query: Record<string, string> = {}, transport: typeof fetch = fetch) {
  if (!['subscription/list', 'stream/contents/reading-list'].includes(endpoint)) throw new Error('READ_ENDPOINT_NOT_ALLOWED');
  const base = process.env.FRESHRSS_API_URL;
  const user = process.env.FRESHRSS_API_USER;
  const password = process.env.FRESHRSS_API_PASSWORD;
  if (!base || !user || !password) throw new Error('FRESHRSS_CONFIG_REQUIRED');
  const parsed = new URL(base);
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.search || parsed.hash) throw new Error('FRESHRSS_HTTPS_REQUIRED');
  const prefix = base.replace(/\/$/, '');
  const login = await transport(prefix + '/accounts/ClientLogin', {
    method: 'POST', body: new URLSearchParams({ Email: user, Passwd: password }), redirect: 'error', signal: AbortSignal.timeout(30000),
  });
  if (!login.ok) throw new Error('FRESHRSS_AUTH_FAILED');
  const auth = (await login.text()).match(/^Auth=(.+)$/m)?.[1]?.trim();
  if (!auth) throw new Error('FRESHRSS_AUTH_INVALID');
  const url = new URL(prefix + '/reader/api/0/' + endpoint);
  url.search = new URLSearchParams({ ...query, output: 'json' }).toString();
  const response = await transport(url, { headers: { Authorization: 'GoogleLogin auth=' + auth }, redirect: 'error', signal: AbortSignal.timeout(30000) });
  if (!response.ok) throw new Error('FRESHRSS_READ_FAILED');
  return await response.json() as Record<string, unknown>;
}
