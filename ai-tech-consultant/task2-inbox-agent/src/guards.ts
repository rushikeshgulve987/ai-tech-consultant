/** Post-generation safety checks. The model is told not to make commitments; this code checks that it didn't. */
const MONEY = /(?:[$€£₹]\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?(?:usd|inr|eur|gbp|rupees|dollars)\b)/gi;

export function detectPolicyFlags(reply: string, evidenceText: string): string[] {
  const flags: string[] = [];
  const evidence = evidenceText.toLowerCase();
  for (const m of reply.match(MONEY) ?? []) {
    if (!evidence.includes(m.toLowerCase())) { flags.push(`unsupported_price:${m.trim()}`); break; }
  }
  if (/\b(we (have|'ve) (issued|processed|approved) (your|a|the) refund|your refund (has been|will be|is approved)|we will refund|you will be refunded)\b/i.test(reply)) flags.push("refund_commitment");
  if (/\b(will be (ready|delivered|live|done|completed|fixed) (by|on|within)|guarantee[sd]?|we promise|deliver(ed)? (by|within) \d)/i.test(reply)) flags.push("delivery_or_guarantee_commitment");
  if (/\b(legal(ly)?|lawsuit|liab(le|ility)|compensat(e|ion))\b/i.test(reply)) flags.push("legal_language");
  for (const url of reply.match(/https?:\/\/[^\s)>\]]+/g) ?? []) {
    if (!evidence.includes(url.toLowerCase().replace(/[.,;]+$/, ""))) { flags.push(`unverified_link:${url}`); break; }
  }
  return flags;
}
