import { z } from "zod";

export const CATEGORIES = [
  "how_to", "troubleshooting", "project_request", "refund_cancellation", "complaint", "quote_request", "follow_up",
] as const;
export type Category = (typeof CATEGORIES)[number];

/** Categories where money, reputation or scope is at stake: a human always reviews, however many drafts went unedited. */
export const ALWAYS_HUMAN: ReadonlySet<Category> = new Set(["refund_cancellation", "complaint", "quote_request", "project_request"]);
/** Only these can ever earn autonomy. */
export const AUTONOMY_ELIGIBLE: ReadonlySet<Category> = new Set(["how_to", "troubleshooting"]);
/** Product questions that must be grounded in evidence; with no evidence we do not draft an answer. */
export const NEEDS_EVIDENCE: ReadonlySet<Category> = new Set(["how_to", "troubleshooting"]);

export const EmailInputSchema = z.object({
  from: z.string().email().or(z.string().min(3)),
  subject: z.string().min(1).max(300),
  body: z.string().min(1).max(20000),
});
export type EmailInput = z.infer<typeof EmailInputSchema>;

export const ClassificationSchema = z.object({
  category: z.enum(CATEGORIES),
  urgency: z.enum(["low", "normal", "high"]),
  risk: z.enum(["low", "medium", "high"]),
  summary: z.string().min(1),
  knowledgeQuestion: z.string().nullable(),
});
export type Classification = z.infer<typeof ClassificationSchema>;

export const DraftSchema = z.object({
  reply: z.string().min(1),
  usedSourceUrls: z.array(z.string()),
  needsHumanApproval: z.boolean(),
  nextAction: z.string().nullable(),
  followUpDate: z.string().nullable(),
});
export type Draft = z.infer<typeof DraftSchema>;

export const KbResponseSchema = z.object({
  request_id: z.string(),
  status: z.enum(["answered", "insufficient_evidence"]),
  answer: z.string().nullable(),
  sources: z.array(z.object({ title: z.string(), url: z.string(), relevance: z.number(), kind: z.string(), excerpt: z.string() })),
  evidence_score: z.number(),
  needs_human: z.boolean(),
  escalation_id: z.number().nullable().optional(),
  note: z.string().nullable().optional(),
});
export type KbResponse = z.infer<typeof KbResponseSchema>;

export type KbOutcome =
  | { kind: "answered"; response: KbResponse }
  | { kind: "insufficient"; response: KbResponse }
  | { kind: "unavailable"; error: string }
  | { kind: "skipped" };

export const ReviewSchema = z.object({
  action: z.enum(["approve", "edit", "reject", "escalate"]),
  finalText: z.string().optional(),
  reason: z.string().max(500).optional(),
  reviewer: z.string().min(1).default("agent"),
});
export type ReviewInput = z.infer<typeof ReviewSchema>;
