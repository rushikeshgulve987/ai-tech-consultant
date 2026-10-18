import { AUTONOMY_ELIGIBLE, type Category } from "./schemas";

/** "Same kind" = category + primary cited article (e.g. how_to:/articles/upload-logo). Stable and auditable. */
export function kindKey(category: Category, primarySourceUrl: string | null): string {
  return `${category}:${primarySourceUrl ?? "no-source"}`;
}

/** "Without changing" = identical after trimming and collapsing whitespace and ignoring case. Any word-level change counts as an edit. */
export function normalize(text: string): string {
  return text.replace(/\s+/g, " ").trim().toLowerCase();
}
export const isUnedited = (draft: string, finalText: string): boolean => normalize(draft) === normalize(finalText);

export interface KindState {
  kind: string;
  consecutiveUnedited: number;
  eligible: boolean;
  enabled: boolean;
  disabledReason: string | null;
  recentEdited: boolean[]; // newest last, kept to the last 10 reviewed items
  autoSent: number;
}
export const newState = (kind: string): KindState => ({ kind, consecutiveUnedited: 0, eligible: false, enabled: false, disabledReason: null, recentEdited: [], autoSent: 0 });

export function editRate(recent: boolean[], window = 10): number {
  const w = recent.slice(-window);
  return w.length === 0 ? 0 : w.filter(Boolean).length / w.length;
}

/** Apply one human review outcome. Any edit/rejection resets the streak; on an enabled kind it also revokes autonomy. */
export function applyReview(s: KindState, edited: boolean, threshold: number, maxEditRate = 0.2): KindState {
  const recentEdited = [...s.recentEdited, edited].slice(-10);
  const next: KindState = { ...s, recentEdited };
  if (edited) {
    next.consecutiveUnedited = 0;
    next.eligible = false;
    if (s.enabled) { next.enabled = false; next.disabledReason = "human edited or rejected an audited draft"; }
  } else {
    next.consecutiveUnedited = s.consecutiveUnedited + 1;
    next.eligible = next.consecutiveUnedited >= threshold;
  }
  if (next.enabled && recentEdited.length >= 5 && editRate(recentEdited) > maxEditRate) {
    next.enabled = false; next.disabledReason = "edit rate over limit in last 10 reviews";
  }
  return next;
}

export interface AutoDecision { autoSend: boolean; reason: string; audit: boolean }

/** The 20-in-a-row streak only makes a kind *eligible*. Sending alone also needs: a human switched it on, low risk, evidence-backed,
 * no policy flags, not urgent, and it isn't one of the sampled audit drafts. */
export function decideAutoSend(s: KindState | undefined, c: { category: Category; risk: string; urgency: string }, ctx: { kbAnswered: boolean; flags: string[] }, auditEveryN: number): AutoDecision {
  if (!s || !s.enabled) return { autoSend: false, reason: "autonomy not enabled for this kind", audit: false };
  if (!AUTONOMY_ELIGIBLE.has(c.category)) return { autoSend: false, reason: "category always needs a human", audit: false };
  if (c.risk !== "low" || c.urgency === "high") return { autoSend: false, reason: "risk or urgency too high", audit: false };
  if (!ctx.kbAnswered) return { autoSend: false, reason: "no evidence-backed answer", audit: false };
  if (ctx.flags.length) return { autoSend: false, reason: `policy flags: ${ctx.flags.join(",")}`, audit: false };
  if ((s.autoSent + 1) % auditEveryN === 0) return { autoSend: false, reason: "sampled for human audit", audit: true };
  return { autoSend: true, reason: "kind enabled and all safety conditions met", audit: false };
}

/** Levenshtein-based distance ratio (0 = identical, 1 = totally different), used to measure how much humans edit drafts. */
export function editDistanceRatio(a: string, b: string): number {
  const x = normalize(a), y = normalize(b);
  if (x === y) return 0;
  const m = x.length, n = y.length;
  let prev = Array.from({ length: n + 1 }, (_, j) => j);
  for (let i = 1; i <= m; i++) {
    const cur = [i];
    for (let j = 1; j <= n; j++) cur[j] = Math.min((prev[j] ?? 0) + 1, (cur[j - 1] ?? 0) + 1, (prev[j - 1] ?? 0) + (x[i - 1] === y[j - 1] ? 0 : 1));
    prev = cur;
  }
  return (prev[n] ?? 0) / Math.max(m, n);
}
