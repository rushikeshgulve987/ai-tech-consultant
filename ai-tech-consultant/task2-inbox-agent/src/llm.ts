import Anthropic from "@anthropic-ai/sdk";
import { z } from "zod";
import type { Config } from "./config";

export interface LlmCall<T> {
  system: string;
  user: string;
  model: string;
  schema: z.ZodType<T>;
  maxTokens?: number;
}
export interface LlmResult<T> {
  data: T;
  model: string;
  inputTokens: number;
  outputTokens: number;
  latencyMs: number;
}
export interface LlmClient {
  json<T>(call: LlmCall<T>): Promise<LlmResult<T>>;
}

export function extractJson(text: string): unknown {
  const s = text.indexOf("{");
  const e = text.lastIndexOf("}");
  if (s === -1 || e < s) throw new Error("no JSON object in model output");
  return JSON.parse(text.slice(s, e + 1));
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export class AnthropicLlm implements LlmClient {
  private client = new Anthropic();
  async json<T>(call: LlmCall<T>): Promise<LlmResult<T>> {
    let user = call.user;
    let lastErr: unknown;
    for (let attempt = 0; attempt < 4; attempt++) {
      const t0 = Date.now();
      try {
        const msg = await this.client.messages.create({
          model: call.model, max_tokens: call.maxTokens ?? 1200, system: call.system,
          messages: [{ role: "user", content: user }],
        });
        const text = msg.content.map((b) => (b.type === "text" ? b.text : "")).join("");
        try {
          const data = call.schema.parse(extractJson(text));
          return { data, model: call.model, inputTokens: msg.usage.input_tokens, outputTokens: msg.usage.output_tokens, latencyMs: Date.now() - t0 };
        } catch (parseErr) {
          // schema failure: ask once more, telling the model what was wrong
          user = `${call.user}\n\nYour previous reply was invalid (${String(parseErr).slice(0, 300)}). Reply with ONLY one valid JSON object.`;
          lastErr = parseErr;
        }
      } catch (err) {
        lastErr = err;
        await sleep(Math.min(20000, 2 ** attempt * 1000 + Math.random() * 500));
      }
    }
    throw new Error(`LLM call failed after retries: ${String(lastErr)}`);
  }
}

/** Deterministic offline stand-in so the whole app (and the tests) run without an API key. Not used for evals. */
export class MockLlm implements LlmClient {
  async json<T>(call: LlmCall<T>): Promise<LlmResult<T>> {
    const u = call.user.toLowerCase();
    let raw: unknown;
    if (call.system.includes("CLASSIFIER")) {
      const email = u.slice(u.indexOf("<customer_email>") + 16, u.indexOf("</customer_email>")).replace(/^\s*from:.*$/m, "");
      const category =
        /refund|cancel/.test(email) ? "refund_cancellation" :
        /broke|unacceptable|terrible|disappointed|furious/.test(email) ? "complaint" :
        /how much|quote|price|cost/.test(email) ? "quote_request" :
        /did you receive|following up|any update|previous (email|request)/.test(email) ? "follow_up" :
        /\badd a\b|new feature|\bbuild\b|\bcustom\b/.test(email) ? "project_request" :
        /not working|error|stopped|fails|can't log|cannot/.test(email) ? "troubleshooting" : "how_to";
      raw = { category, urgency: /urgent|asap/.test(email) ? "high" : "normal",
        risk: ["refund_cancellation", "complaint", "quote_request", "project_request"].includes(category) ? "high" : "low",
        summary: "Customer email (mock classification)", knowledgeQuestion: category === "quote_request" || category === "follow_up" ? null : email.slice(0, 200) };
    } else {
      const urls = [...call.user.matchAll(/<source url="([^"]+)"/g)].map((m) => m[1]);
      raw = { reply: "Hi,\n\nThanks for your email. Here is what our documentation says: please follow the steps in the linked article.\n\nBest regards,\nSupport team",
        usedSourceUrls: urls.slice(0, 1), needsHumanApproval: false, nextAction: null, followUpDate: null };
    }
    return { data: call.schema.parse(raw), model: "mock", inputTokens: 0, outputTokens: 0, latencyMs: 1 };
  }
}

export function makeLlm(cfg: Config): LlmClient {
  return cfg.llmMode === "anthropic" ? new AnthropicLlm() : new MockLlm();
}
