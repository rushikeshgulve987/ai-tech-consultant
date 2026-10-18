import { detectPolicyFlags } from "./guards";
import { kindKey } from "./autonomy";
import type { LlmClient } from "./llm";
import type { KnowledgeClient } from "./knowledgeClient";
import { CLASSIFY_SYSTEM, CLASSIFY_VERSION, DRAFT_SYSTEM, DRAFT_VERSION, HOLDING_REPLY, draftUser } from "./prompts";
import { ALWAYS_HUMAN, ClassificationSchema, DraftSchema, NEEDS_EVIDENCE, type Classification, type Draft, type EmailInput, type KbOutcome } from "./schemas";

export type DraftStatus = "pending_review" | "needs_expert" | "knowledge_unavailable";

export interface PipelineResult {
  classification: Classification;
  kb: KbOutcome;
  draft: Draft | null;
  reply: string;
  flags: string[];
  status: DraftStatus;
  requiresHuman: boolean;
  kindKey: string;
  evidenceText: string;
  sources: { title: string; url: string }[];
  promptVersion: string;
  usage: { model: string; inputTokens: number; outputTokens: number; latencyMs: number }[];
}

export interface PipelineDeps {
  llm: LlmClient;
  kb: KnowledgeClient;
  classifyModel: string;
  draftModel: string;
  styleFor: (category: string) => { id: number; text: string } | null;
}

export function evidenceFrom(kb: KbOutcome): { url: string; text: string }[] {
  if (kb.kind !== "answered") return [];
  const answer = kb.response.answer ? [{ url: kb.response.sources[0]?.url ?? "knowledge-base", text: `Knowledge-base answer: ${kb.response.answer}` }] : [];
  return [...answer, ...kb.response.sources.map((s) => ({ url: s.url, text: s.excerpt }))];
}

export async function processEmail(email: EmailInput, deps: PipelineDeps): Promise<PipelineResult> {
  const usage: PipelineResult["usage"] = [];
  const cls = await deps.llm.json({ system: CLASSIFY_SYSTEM, user: `<customer_email>\nFrom: ${email.from}\nSubject: ${email.subject}\n\n${email.body}\n</customer_email>`, model: deps.classifyModel, schema: ClassificationSchema, maxTokens: 400 });
  usage.push({ model: cls.model, inputTokens: cls.inputTokens, outputTokens: cls.outputTokens, latencyMs: cls.latencyMs });
  const c = cls.data;

  const kb: KbOutcome = c.knowledgeQuestion ? await deps.kb.query(c.knowledgeQuestion, `${email.subject}\n${email.body}`.slice(0, 1500)) : { kind: "skipped" };
  const evidence = evidenceFrom(kb);
  const evidenceText = evidence.map((e) => `${e.url}\n${e.text}`).join("\n");
  const primary = kb.kind === "answered" ? kb.response.sources[0]?.url ?? null : null;
  const base = { classification: c, kb, kindKey: kindKey(c.category, primary), evidenceText, sources: kb.kind === "answered" ? kb.response.sources.map((s) => ({ title: s.title, url: s.url })) : [], usage };
  const style = deps.styleFor(c.category);
  const promptVersion = `${CLASSIFY_VERSION}/${DRAFT_VERSION}${style ? `+style#${style.id}` : ""}`;

  // Never fabricate a reply when the knowledge service failed or has no evidence for a product question.
  if (NEEDS_EVIDENCE.has(c.category) && kb.kind === "unavailable") {
    return { ...base, draft: null, reply: "", flags: [], status: "knowledge_unavailable", requiresHuman: true, promptVersion };
  }
  if (NEEDS_EVIDENCE.has(c.category) && kb.kind !== "answered") {
    return { ...base, draft: null, reply: HOLDING_REPLY, flags: [], status: "needs_expert", requiresHuman: true, promptVersion };
  }

  const d = await deps.llm.json({ system: DRAFT_SYSTEM, user: draftUser({ email, summary: c.summary, category: c.category, evidence, style: style?.text ?? null }), model: deps.draftModel, schema: DraftSchema, maxTokens: 1200 });
  usage.push({ model: d.model, inputTokens: d.inputTokens, outputTokens: d.outputTokens, latencyMs: d.latencyMs });
  const flags = detectPolicyFlags(d.data.reply, evidenceText);
  const requiresHuman = ALWAYS_HUMAN.has(c.category) || c.risk !== "low" || c.urgency === "high" || d.data.needsHumanApproval || flags.length > 0;
  return { ...base, draft: d.data, reply: d.data.reply, flags, status: "pending_review", requiresHuman, promptVersion };
}
