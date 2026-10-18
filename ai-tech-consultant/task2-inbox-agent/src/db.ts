import Database from "better-sqlite3";
import fs from "node:fs";
import path from "node:path";
import { newState, type KindState } from "./autonomy";

const SCHEMA = `
CREATE TABLE IF NOT EXISTS emails(id INTEGER PRIMARY KEY, from_addr TEXT, subject TEXT, body TEXT, received_at TEXT);
CREATE TABLE IF NOT EXISTS drafts(
  id INTEGER PRIMARY KEY, email_id INTEGER, category TEXT, urgency TEXT, risk TEXT, kind_key TEXT, summary TEXT,
  kb_status TEXT, sources TEXT, evidence_text TEXT, reply TEXT, flags TEXT, status TEXT, requires_human INTEGER,
  audit INTEGER DEFAULT 0, prompt_version TEXT, models TEXT, next_action TEXT, follow_up_date TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, draft_id INTEGER, reviewer TEXT, action TEXT, final_text TEXT, edited INTEGER, reason TEXT, edit_ratio REAL, created_at TEXT);
CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY, draft_id INTEGER UNIQUE, to_addr TEXT, subject TEXT, body TEXT, sent_by TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS kind_state(kind TEXT PRIMARY KEY, json TEXT);
CREATE TABLE IF NOT EXISTS prompt_versions(id INTEGER PRIMARY KEY, category TEXT, text TEXT, parent_id INTEGER, status TEXT, note TEXT, baseline_score REAL, candidate_score REAL, created_at TEXT);
CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY, ts TEXT, event TEXT, detail TEXT);
`;

export type Row = Record<string, unknown>;
const now = () => new Date().toISOString();

export class Repo {
  readonly db: Database.Database;
  constructor(file: string) {
    if (file !== ":memory:") fs.mkdirSync(path.dirname(file), { recursive: true });
    this.db = new Database(file);
    this.db.pragma("journal_mode = WAL");
    this.db.exec(SCHEMA);
  }
  audit(event: string, detail: unknown): void {
    this.db.prepare("INSERT INTO audit_log(ts,event,detail) VALUES(?,?,?)").run(now(), event, JSON.stringify(detail));
  }
  addEmail(e: { from: string; subject: string; body: string }): number {
    return Number(this.db.prepare("INSERT INTO emails(from_addr,subject,body,received_at) VALUES(?,?,?,?)").run(e.from, e.subject, e.body, now()).lastInsertRowid);
  }
  addDraft(d: Row): number {
    const cols = Object.keys(d);
    const sql = `INSERT INTO drafts(${cols.join(",")},created_at) VALUES(${cols.map(() => "?").join(",")},?)`;
    return Number(this.db.prepare(sql).run(...(Object.values(d) as never[]), now()).lastInsertRowid);
  }
  draft(id: number): Row | undefined {
    return this.db.prepare("SELECT d.*, e.from_addr, e.subject, e.body FROM drafts d JOIN emails e ON e.id=d.email_id WHERE d.id=?").get(id) as Row | undefined;
  }
  inbox(): Row[] {
    return this.db.prepare("SELECT d.id, d.category, d.risk, d.urgency, d.status, d.requires_human, d.audit, d.kb_status, d.flags, e.from_addr, e.subject FROM drafts d JOIN emails e ON e.id=d.email_id ORDER BY d.id DESC LIMIT 200").all() as Row[];
  }
  setDraftStatus(id: number, status: string): void {
    this.db.prepare("UPDATE drafts SET status=? WHERE id=?").run(status, id);
  }
  /** Idempotent: UNIQUE(draft_id) means a retried send can never create a second outbox row. Returns true if newly sent. */
  send(draftId: number, to: string, subject: string, body: string, by: string): boolean {
    return this.db.prepare("INSERT OR IGNORE INTO outbox(draft_id,to_addr,subject,body,sent_by,created_at) VALUES(?,?,?,?,?,?)").run(draftId, to, subject, body, by, now()).changes === 1;
  }
  outbox(): Row[] {
    return this.db.prepare("SELECT * FROM outbox ORDER BY id DESC LIMIT 100").all() as Row[];
  }
  addReview(r: { draftId: number; reviewer: string; action: string; finalText: string | null; edited: boolean; reason: string | null; ratio: number }): void {
    this.db.prepare("INSERT INTO reviews(draft_id,reviewer,action,final_text,edited,reason,edit_ratio,created_at) VALUES(?,?,?,?,?,?,?,?)")
      .run(r.draftId, r.reviewer, r.action, r.finalText, r.edited ? 1 : 0, r.reason, r.ratio, now());
  }
  kind(kind: string): KindState | undefined {
    const r = this.db.prepare("SELECT json FROM kind_state WHERE kind=?").get(kind) as { json: string } | undefined;
    return r ? (JSON.parse(r.json) as KindState) : undefined;
  }
  kindOrNew(kind: string): KindState {
    return this.kind(kind) ?? newState(kind);
  }
  saveKind(s: KindState): void {
    this.db.prepare("INSERT INTO kind_state(kind,json) VALUES(?,?) ON CONFLICT(kind) DO UPDATE SET json=excluded.json").run(s.kind, JSON.stringify(s));
  }
  kinds(): KindState[] {
    return (this.db.prepare("SELECT json FROM kind_state").all() as { json: string }[]).map((r) => JSON.parse(r.json) as KindState);
  }
  /** Pairs of (email, evidence, model draft, human final) for a category, newest first. Source for learning + regression tests. */
  reviewedPairs(category: string, limit: number): Row[] {
    return this.db.prepare(`SELECT d.id, d.summary, d.evidence_text, d.reply AS draft, r.final_text, e.from_addr, e.subject, e.body
      FROM reviews r JOIN drafts d ON d.id=r.draft_id JOIN emails e ON e.id=d.email_id
      WHERE d.category=? AND r.final_text IS NOT NULL AND r.action IN ('approve','edit','reject') ORDER BY r.id DESC LIMIT ?`).all(category, limit) as Row[];
  }
  activeStyle(category: string): Row | undefined {
    return this.db.prepare("SELECT * FROM prompt_versions WHERE category=? AND status='active' ORDER BY id DESC LIMIT 1").get(category) as Row | undefined;
  }
  addPrompt(category: string, text: string, parentId: number | null, note: string): number {
    return Number(this.db.prepare("INSERT INTO prompt_versions(category,text,parent_id,status,note,created_at) VALUES(?,?,?,'candidate',?,?)").run(category, text, parentId, note, now()).lastInsertRowid);
  }
  prompt(id: number): Row | undefined {
    return this.db.prepare("SELECT * FROM prompt_versions WHERE id=?").get(id) as Row | undefined;
  }
  prompts(): Row[] {
    return this.db.prepare("SELECT * FROM prompt_versions ORDER BY id DESC").all() as Row[];
  }
  updatePrompt(id: number, patch: { status?: string; baseline_score?: number; candidate_score?: number }): void {
    for (const [k, v] of Object.entries(patch)) this.db.prepare(`UPDATE prompt_versions SET ${k}=? WHERE id=?`).run(v as never, id);
  }
}
