import { randomUUID } from "node:crypto";
import { chmod, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { join } from "node:path";

export type Edition = "morning" | "afternoon";
export type Priority = "S" | "A" | "B" | "C";
export type AccountingDisposition = "retained" | "merged" | "filtered";
export type Source = { name: string; url: string };
export type IntelligenceItem = {
  eventKey: string;
  title: string;
  priority: Priority;
  category: string;
  publishedAt?: string;
  eventAt?: string;
  summary: string;
  whyImportant: string;
  impact: string;
  opportunity?: string;
  isHighValueOpportunity: boolean;
  risk?: string;
  originalTitle?: string;
  sources: Source[];
};
export type AccountingRow = {
  primaryArticleId: string;
  disposition: AccountingDisposition;
  finalEventKey?: string;
  reason: string;
};
export type FinalizeInput = {
  edition: Edition;
  candidateRunId: string;
  morningBaselineId?: string;
  generatedAt?: string;
  title: string;
  reportMarkdown: string;
  accounting: AccountingRow[];
  items: IntelligenceItem[];
};

type CandidateFile = {
  schemaVersion: 2;
  edition: Edition;
  runId: string;
  allPagesComplete: boolean;
  morningBaselineId?: string;
  candidates: Array<{ primaryArticleId: string }>;
};

const DEFAULT_CANDIDATE_ROOT = "/home/ubuntu/.openclaw/workspace/rss-daily-stage-v52/current";
const DEFAULT_STAGE_ROOT = "/home/ubuntu/.openclaw/rss-intelligence/finalizer-stage-v52";
const RAW_URL = /^https?:\/\/[^\s\[\]<>]+$/i;
const ALLOWED_CATEGORIES = new Set(["S-链上金融","S-跨境金融","S-商业机会","A-AI-Agent","A-AI工具自动化","A-微信企微私域","A-AIxCryptoPayment","B-海外互联网","B-开源自托管","B-政策监管","C-宏观流动性","C-消费科技"]);

function bounded(value: unknown, name: string, max: number, allowEmpty = false): string {
  if (typeof value !== "string") throw new Error(`${name} must be a string`);
  const v = value.trim();
  if (!allowEmpty && !v) throw new Error(`${name} must not be empty`);
  if (v.length > max) throw new Error(`${name} exceeds ${max} characters`);
  return v;
}
function rawUrl(value: unknown, name: string): string {
  const v = bounded(value, name, 4096);
  if (!RAW_URL.test(v)) throw new Error(`${name} must be a raw HTTP(S) URL`);
  const u = new URL(v);
  if ((u.protocol !== "http:" && u.protocol !== "https:") || !u.hostname || u.hostname === "...") {
    throw new Error(`${name} is not a usable HTTP(S) URL`);
  }
  return v;
}
function normalizeIso(value: string | undefined): string {
  if (value === undefined || !value.trim()) return new Date().toISOString();
  if (value.length > 64) throw new Error("generatedAt is too long");
  const d = new Date(value);
  if (!Number.isFinite(d.getTime())) throw new Error("generatedAt must be a valid date-time");
  return d.toISOString();
}
function safeRunId(value: string): string {
  const v = bounded(value, "candidateRunId", 160);
  if (!/^[A-Za-z0-9._:-]+$/.test(v)) throw new Error("candidateRunId contains unsafe characters");
  return v;
}
function validateItem(item: IntelligenceItem): IntelligenceItem {
  const eventKey = bounded(item.eventKey, "eventKey", 500);
  if (eventKey.length < 3) throw new Error("eventKey must be at least 3 characters");
  const title = bounded(item.title, "title", 300);
  if (!["S", "A", "B", "C"].includes(item.priority)) throw new Error("invalid priority");
  const category = bounded(item.category, "category", 160);
  if (!ALLOWED_CATEGORIES.has(category)) throw new Error("invalid category");
  const summary = bounded(item.summary, "summary", 3000);
  const whyImportant = bounded(item.whyImportant, "whyImportant", 3000);
  const impact = bounded(item.impact, "impact", 3000);
  if (!Array.isArray(item.sources) || item.sources.length < 1 || item.sources.length > 5) {
    throw new Error("sources must contain 1..5 entries");
  }
  const sources = item.sources.map((s, i) => ({
    name: bounded(s.name || s.url, `sources[${i}].name`, 200),
    url: rawUrl(s.url, `sources[${i}].url`),
  }));
  const checkDate = (v: string | undefined, n: string) => {
    if (v === undefined || !v.trim()) return undefined;
    const d = new Date(v);
    if (!Number.isFinite(d.getTime())) throw new Error(`${n} invalid`);
    return d.toISOString();
  };
  return {
    eventKey,
    title,
    priority: item.priority,
    category,
    publishedAt: checkDate(item.publishedAt, "publishedAt"),
    eventAt: checkDate(item.eventAt, "eventAt"),
    summary,
    whyImportant,
    impact,
    opportunity: item.opportunity ? bounded(item.opportunity, "opportunity", 3000) : undefined,
    isHighValueOpportunity: item.isHighValueOpportunity === true,
    risk: item.risk ? bounded(item.risk, "risk", 3000) : undefined,
    originalTitle: item.originalTitle ? bounded(item.originalTitle, "originalTitle", 1000) : undefined,
    sources,
  };
}
async function atomicJson(path: string, value: unknown): Promise<void> {
  const tmp = `${path}.${process.pid}.${randomUUID()}.tmp`;
  await writeFile(tmp, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf8", mode: 0o600, flag: "wx" });
  await chmod(tmp, 0o600);
  await rename(tmp, path);
  await chmod(path, 0o600);
}

export async function saveFinalStage(input: FinalizeInput, candidateRoot = DEFAULT_CANDIDATE_ROOT, stageRoot = DEFAULT_STAGE_ROOT) {
  if (input.edition !== "morning" && input.edition !== "afternoon") throw new Error("invalid edition");
  const candidateRunId = safeRunId(input.candidateRunId);
  const candidatePath = join(candidateRoot, `${input.edition}-candidates.json`);
  const candidate = JSON.parse(await readFile(candidatePath, "utf8")) as CandidateFile;
  if (candidate.schemaVersion !== 2 || candidate.edition !== input.edition || candidate.allPagesComplete !== true) {
    throw new Error("candidate file is not complete/compatible");
  }
  if (candidate.runId !== candidateRunId) throw new Error("candidateRunId does not match current candidate file");
  if (!Array.isArray(candidate.candidates)) throw new Error("candidate list missing");
  if (input.edition === "afternoon") {
    const expected = String(candidate.morningBaselineId || "");
    const got = String(input.morningBaselineId || "");
    if (expected !== got) throw new Error("morningBaselineId mismatch");
  }
  if (!Array.isArray(input.accounting)) throw new Error("accounting must be an array");
  if (!Array.isArray(input.items) || input.items.length > 500) throw new Error("items must contain <=500 entries");
  const expectedIds = new Set(candidate.candidates.map((c) => bounded(c.primaryArticleId, "candidate.primaryArticleId", 512)));
  if (expectedIds.size !== candidate.candidates.length) throw new Error("candidate primaryArticleId values are not unique");
  if (input.accounting.length !== expectedIds.size) throw new Error("final accounting length mismatch");
  const items = input.items.map(validateItem);
  const itemKeys = new Set<string>();
  for (const i of items) {
    if (itemKeys.has(i.eventKey)) throw new Error("duplicate final eventKey");
    itemKeys.add(i.eventKey);
  }
  const seenIds = new Set<string>();
  const retainedKeys = new Set<string>();
  const normalizedAccounting = input.accounting.map((row, idx) => {
    const id = bounded(row.primaryArticleId, `accounting[${idx}].primaryArticleId`, 512);
    if (!expectedIds.has(id)) throw new Error(`accounting contains unknown candidate id: ${id}`);
    if (seenIds.has(id)) throw new Error(`candidate accounted more than once: ${id}`);
    seenIds.add(id);
    if (!["retained", "merged", "filtered"].includes(row.disposition)) throw new Error("invalid accounting disposition");
    const reason = bounded(row.reason, `accounting[${idx}].reason`, 600);
    let finalEventKey: string | undefined;
    if (row.disposition === "retained" || row.disposition === "merged") {
      finalEventKey = bounded(row.finalEventKey, `accounting[${idx}].finalEventKey`, 500);
      if (!itemKeys.has(finalEventKey)) throw new Error(`accounting finalEventKey not present in items: ${finalEventKey}`);
      if (row.disposition === "retained") retainedKeys.add(finalEventKey);
    }
    return { primaryArticleId: id, disposition: row.disposition, finalEventKey, reason };
  });
  if (seenIds.size !== expectedIds.size) throw new Error("not every candidate was accounted");
  for (const key of itemKeys) if (!retainedKeys.has(key)) throw new Error(`final item lacks a retained primary candidate: ${key}`);
  const title = bounded(input.title, "title", 300);
  const reportMarkdown = bounded(input.reportMarkdown, "reportMarkdown", 100000);
  const generatedAt = normalizeIso(input.generatedAt);
  const stageId = randomUUID();
  const dir = join(stageRoot, input.edition);
  await mkdir(dir, { recursive: true, mode: 0o700 });
  await chmod(stageRoot, 0o700).catch(() => undefined);
  await chmod(dir, 0o700);
  const stored = {
    schemaVersion: 2,
    status: "staged",
    stageId,
    edition: input.edition,
    candidateRunId,
    morningBaselineId: input.edition === "afternoon" ? String(input.morningBaselineId || "") : undefined,
    generatedAt,
    savedAt: new Date().toISOString(),
    title,
    reportMarkdown,
    accounting: normalizedAccounting,
    items,
  };
  const fileName = `${candidateRunId}-${stageId}.json`;
  await atomicJson(join(dir, fileName), stored);
  return { saved: true, publicPublished: false, modifiesFreshRss: false, stageId, candidateRunId, itemCount: items.length, reportChars: reportMarkdown.length, fileName };
}
