import { KbResponseSchema, type KbOutcome } from "./schemas";

export interface KbClientOptions {
  baseUrl: string;
  apiKey?: string | undefined;
  timeoutMs: number;
  retries?: number;
  fetchImpl?: typeof fetch;
}

/** Typed client for Task 1's HTTP API. It never throws: failures become `{kind:"unavailable"}` so callers can't forget them. */
export class KnowledgeClient {
  constructor(private opts: KbClientOptions) {}

  async query(question: string, context?: string): Promise<KbOutcome> {
    const f = this.opts.fetchImpl ?? fetch;
    const retries = this.opts.retries ?? 2;
    let lastError = "unknown error";
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        const res = await f(`${this.opts.baseUrl}/query`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Request-ID": crypto.randomUUID().slice(0, 12),
            ...(this.opts.apiKey ? { "X-API-Key": this.opts.apiKey } : {}) },
          body: JSON.stringify({ question, context, escalate_if_unanswered: true }),
          signal: AbortSignal.timeout(this.opts.timeoutMs),
        });
        if (res.status >= 500) { lastError = `HTTP ${res.status}`; await new Promise((r) => setTimeout(r, 300 * 2 ** attempt)); continue; }
        if (!res.ok) return { kind: "unavailable", error: `HTTP ${res.status}` }; // 4xx: retrying will not help
        const parsed = KbResponseSchema.safeParse(await res.json());
        if (!parsed.success) return { kind: "unavailable", error: "unexpected response shape from knowledge service" };
        return { kind: parsed.data.status === "answered" ? "answered" : "insufficient", response: parsed.data };
      } catch (err) {
        lastError = err instanceof Error ? err.message : String(err);
        await new Promise((r) => setTimeout(r, 300 * 2 ** attempt));
      }
    }
    return { kind: "unavailable", error: lastError };
  }
}
