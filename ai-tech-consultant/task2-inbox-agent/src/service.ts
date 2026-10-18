import { applyReview, decideAutoSend, editDistanceRatio, isUnedited, type KindState } from "./autonomy";
import type { Config } from "./config";
import { Repo, type Row } from "./db";
import { detectPolicyFlags } from "./guards";
import type { LlmClient } from "./llm";
import type { KnowledgeClient } from "./knowledgeClient";
import { evidenceFrom, processEmail, type PipelineDeps } from "./pipeline";
import { DRAFT_SYSTEM, draftUser } from "./prompts";
import { DraftSchema, type EmailInput, type ReviewInput } from "./schemas";
import { z } from "zod";

export class InboxService {
  constructor(readonly cfg: Config, readonly repo: Repo, readonly llm: LlmClient, readonly kb: KnowledgeClient) {}

  private deps(): PipelineDeps {
    return { llm: this.llm, kb: this.kb, classifyModel: this.cfg.classifyModel, draftModel: this.cfg.draftModel,
      styleFor: (cat) => { const s = this.repo.activeStyle(cat); return s ? { id: Number(s.id), text: String(s.text) } : null; } };
  }

  async ingest(email: EmailInput): Promise<{ draftId: number; autoSent: boolean; reason: string }> {
    const emailId = this.repo.addEmail(email);
    const r = await processEmail(email, this.deps());
    const decision = r.status === "pending_review" && r.draft
      ? decideAutoSend(this.repo.kind(r.kindKey), r.classification, { kbAnswered: r.kb.kind === "answered", flags: r.flags }, this.cfg.auditEveryN)
      : { autoSend: false, reason: `status ${r.status}`, audit: false };
    const draftId = this.repo.addDraft({
      email_id: emailId, category: r.classification.category, urgency: r.classification.urgency, risk: r.classification.risk, kind_key: r.kindKey,
      summary: r.classification.summary, kb_status: r.kb.kind, sources: JSON.stringify(r.sources), evidence_text: r.evidenceText, reply: r.reply,
      flags: JSON.stringify(r.flags), status: r.status, requires_human: r.requiresHuman ? 1 : 0, audit: decision.audit ? 1 : 0,
      prompt_version: r.promptVersion, models: JSON.stringify(r.usage), next_action: r.draft?.nextAction ?? null, follow_up_date: r.draft?.followUpDate ?? null,
    });
    this.repo.audit("draft_created", { draftId, category: r.classification.category, status: r.status, flags: r.flags, autoDecision: decision.reason });
    if (decision.autoSend) {
      this.repo.send(draftId, email.from, `Re: ${email.subject}`, r.reply, "auto");
      this.repo.setDraftStatus(draftId, "auto_sent");
      const s = this.repo.kindOrNew(r.kindKey);
      this.repo.saveKind({ ...s, autoSent: s.autoSent + 1 });
      this.repo.audit("auto_sent", { draftId, kind: r.kindKey });
    } else if (decision.audit) {
      const s = this.repo.kindOrNew(r.kindKey);
      this.repo.saveKind({ ...s, autoSent: s.autoSent + 1 }); // counts toward the next audit sample
    }
    return { draftId, autoSent: decision.autoSend, reason: decision.reason };
  }

  /** Human decision on a draft. Outcome is recorded, compared with the ORIGINAL draft text, and fed to the autonomy state. */
  review(draftId: number, input: ReviewInput): { edited: boolean; sent: boolean; kind: KindState } {
    const d = this.repo.draft(draftId);
    if (!d) throw new Error("draft not found");
    if (["sent", "auto_sent"].includes(String(d.status))) throw new Error("draft already sent");
    const original = String(d.reply ?? "");
    const kindKey = String(d.kind_key);
    if (input.action === "escalate") {
      this.repo.setDraftStatus(draftId, "needs_expert");
      this.repo.addReview({ draftId, reviewer: input.reviewer, action: "escalate", finalText: null, edited: true, reason: input.reason ?? null, ratio: 1 });
      this.repo.audit("escalated", { draftId });
      return { edited: true, sent: false, kind: this.repo.kindOrNew(kindKey) };
    }
    const finalText = input.action === "approve" ? original : (input.finalText ?? "").trim();
    if (input.action !== "approve" && !finalText) throw new Error("finalText is required for edit/reject");
    if (input.action === "approve" && !original.trim()) throw new Error("nothing to approve: no draft was generated");
    const edited = input.action !== "approve" && !isUnedited(original, finalText); // clicking "edit" and changing nothing counts as unedited
    const flagsOnFinal = detectPolicyFlags(finalText, String(d.evidence_text ?? ""));
    const counts = input.action === "reject" ? true : edited;
    const tx = this.repo.db.transaction(() => {
      this.repo.addReview({ draftId, reviewer: input.reviewer, action: input.action, finalText, edited: counts, reason: input.reason ?? null, ratio: editDistanceRatio(original, finalText) });
      const next = applyReview(this.repo.kindOrNew(kindKey), counts, this.cfg.autonomyThreshold);
      this.repo.saveKind(next);
      const sent = this.repo.send(draftId, String(d.from_addr), `Re: ${String(d.subject)}`, finalText, input.reviewer);
      this.repo.setDraftStatus(draftId, "sent");
      return { next, sent };
    });
    const { next, sent } = tx();
    this.repo.audit("reviewed", { draftId, action: input.action, edited: counts, kind: kindKey, streak: next.consecutiveUnedited, eligible: next.eligible, flagsOnFinal });
    return { edited: counts, sent, kind: next };
  }

  enableAutonomy(kind: string, by: string): KindState {
    const s = this.repo.kind(kind);
    if (!s || !s.eligible) throw new Error("kind is not eligible yet (needs the unedited streak)");
    if (kind.startsWith("refund") || kind.startsWith("complaint") || kind.startsWith("quote") || kind.startsWith("project")) throw new Error("this category always needs a human");
    const n = { ...s, enabled: true, disabledReason: null, autoSent: 0 };
    this.repo.saveKind(n);
    this.repo.audit("autonomy_enabled", { kind, by });
    return n;
  }
  disableAutonomy(kind: string, by: string, reason = "manual kill switch"): KindState {
    const s = this.repo.kindOrNew(kind);
    const n = { ...s, enabled: false, eligible: false, consecutiveUnedited: 0, disabledReason: reason };
    this.repo.saveKind(n);
    this.repo.audit("autonomy_disabled", { kind, by, reason });
    return n;
  }

  // ---- learning: human edits -> candidate style rules -> regression test -> activate / rollback ----
  async proposeStyle(category: string): Promise<number> {
    const edits = this.repo.reviewedPairs(category, 30).filter((p) => p.draft !== p.final_text).slice(0, 12);
    if (edits.length < 3) throw new Error("need at least 3 edited replies in this category to learn from");
    const examples = edits.map((e, i) => `#${i + 1} MODEL DRAFT:\n${String(e.draft)}\nHUMAN FINAL:\n${String(e.final_text)}`).join("\n\n");
    const r = await this.llm.json({
      system: "You turn recurring human edits into at most 5 short, general style rules for a support-reply drafter. Rules must be about tone, length, structure or wording. NEVER include rules about prices, refunds, dates or promises. Reply ONLY JSON: {\"rules\": [str]}",
      user: examples, model: this.cfg.draftModel, schema: z.object({ rules: z.array(z.string()).max(5) }), maxTokens: 500 });
    const parent = this.repo.activeStyle(category);
    return this.repo.addPrompt(category, r.data.rules.map((x) => `- ${x}`).join("\n"), parent ? Number(parent.id) : null, `learned from ${edits.length} human edits`);
  }

  /** Regression: regenerate drafts for past reviewed emails with baseline vs candidate rules; compare distance to what humans actually sent. */
  async evaluateCandidate(id: number): Promise<{ baseline: number; candidate: number; flagsBaseline: number; flagsCandidate: number; improved: boolean; n: number }> {
    const cand = this.repo.prompt(id);
    if (!cand || cand.status !== "candidate") throw new Error("candidate not found");
    const category = String(cand.category);
    const pairs = this.repo.reviewedPairs(category, 20);
    if (pairs.length < 5) throw new Error("need at least 5 reviewed emails for a regression run");
    const base = this.repo.activeStyle(category);
    const gen = async (style: string | null, p: Row) => {
      const email = { from: String(p.from_addr), subject: String(p.subject), body: String(p.body) };
      const ev = [{ url: "evidence", text: String(p.evidence_text ?? "") }];
      const d = await this.llm.json({ system: DRAFT_SYSTEM, user: draftUser({ email, summary: String(p.summary), category: category as never, evidence: ev, style }), model: this.cfg.draftModel, schema: DraftSchema, maxTokens: 1200 });
      return d.data.reply;
    };
    let b = 0, c = 0, fb = 0, fc = 0;
    for (const p of pairs) {
      const rb = await gen(base ? String(base.text) : null, p), rc = await gen(String(cand.text), p);
      b += editDistanceRatio(rb, String(p.final_text)); c += editDistanceRatio(rc, String(p.final_text));
      fb += detectPolicyFlags(rb, String(p.evidence_text ?? "")).length ? 1 : 0; fc += detectPolicyFlags(rc, String(p.evidence_text ?? "")).length ? 1 : 0;
    }
    const n = pairs.length;
    const out = { baseline: b / n, candidate: c / n, flagsBaseline: fb, flagsCandidate: fc, improved: c / n < b / n && fc <= fb, n };
    this.repo.updatePrompt(id, { baseline_score: out.baseline, candidate_score: out.candidate });
    this.repo.audit("candidate_evaluated", { id, ...out });
    return out;
  }
  activateStyle(id: number): void {
    const cand = this.repo.prompt(id);
    if (!cand) throw new Error("prompt not found");
    if (cand.status === "candidate" && !(typeof cand.candidate_score === "number" && typeof cand.baseline_score === "number" && cand.candidate_score < cand.baseline_score))
      throw new Error("candidate has not beaten the baseline in a regression run");
    const cur = this.repo.activeStyle(String(cand.category));
    if (cur) this.repo.updatePrompt(Number(cur.id), { status: "retired" });
    this.repo.updatePrompt(id, { status: "active" });
    this.repo.audit("style_activated", { id, replaced: cur?.id ?? null });
  }
  rollbackStyle(category: string): void {
    const cur = this.repo.activeStyle(category);
    if (!cur) return;
    this.repo.updatePrompt(Number(cur.id), { status: "retired" });
    if (cur.parent_id) this.repo.updatePrompt(Number(cur.parent_id), { status: "active" });
    this.repo.audit("style_rolled_back", { category, from: cur.id, to: cur.parent_id ?? null });
  }
}
