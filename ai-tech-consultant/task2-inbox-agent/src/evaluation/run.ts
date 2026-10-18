/** Evaluate the Inbox Agent on data/emails.json. Usage: npm run eval -- --split dev --draft-model claude-sonnet-5-5 --out results/inbox_sonnet.csv
 * Needs the Knowledge Brain running (KB_URL) and ANTHROPIC_API_KEY. Writes one CSV row per email; nothing is estimated. */
import fs from "node:fs";
import path from "node:path";
import { loadConfig } from "../config";
import { KnowledgeClient } from "../knowledgeClient";
import { AnthropicLlm } from "../llm";
import { processEmail } from "../pipeline";
import { EmailInputSchema } from "../schemas";

interface Case { id: string; split: string; from: string; subject: string; body: string; category: string; risk: string; shouldEscalate: boolean }
const arg = (n: string, d: string) => { const i = process.argv.indexOf(`--${n}`); return i > -1 ? process.argv[i + 1] ?? d : d; };

async function main() {
  const cfg = loadConfig();
  if (cfg.llmMode !== "anthropic") throw new Error("Set ANTHROPIC_API_KEY (evals never run on the mock model).");
  const split = arg("split", "dev"), draftModel = arg("draft-model", cfg.draftModel), classifyModel = arg("classify-model", cfg.classifyModel);
  const out = arg("out", `results/inbox_${split}.csv`);
  const llm = new AnthropicLlm();
  const kb = new KnowledgeClient({ baseUrl: cfg.kbUrl, apiKey: cfg.kbApiKey, timeoutMs: cfg.kbTimeoutMs });
  const cases = (JSON.parse(fs.readFileSync("data/emails.json", "utf8")) as Case[]).filter((c) => split === "all" || c.split === split);
  const rows: string[] = ["task,case_id,classify_model,draft_model,expected_category,actual_category,category_ok,status,flags,unsafe_commitment,escalation_ok,latency_ms,cost_usd,passed,failure_reason"];
  for (const c of cases) {
    const t0 = Date.now();
    let line: string;
    try {
      const email = EmailInputSchema.parse({ from: c.from, subject: c.subject, body: c.body });
      const r = await processEmail(email, { llm, kb, classifyModel, draftModel, styleFor: () => null });
      const catOk = r.classification.category === c.category;
      const unsafe = r.flags.some((f) => /commitment|unsupported_price/.test(f));
      const escalatedOrHuman = r.status !== "pending_review" || r.requiresHuman;
      const escOk = c.shouldEscalate ? escalatedOrHuman : true;
      const cost = r.usage.reduce((s, u) => { const p = cfg.prices[u.model]; return p ? s + (u.inputTokens / 1e6) * p[0] + (u.outputTokens / 1e6) * p[1] : s; }, 0);
      const passed = catOk && !unsafe && escOk;
      const why = [!catOk && "wrong category", unsafe && "unsafe commitment", !escOk && "should have needed a human"].filter(Boolean).join("; ");
      line = ["inbox", c.id, classifyModel, draftModel, c.category, r.classification.category, catOk, r.status, `"${r.flags.join("|")}"`, unsafe, escOk, Date.now() - t0, Object.keys(cfg.prices).length ? cost.toFixed(6) : "", passed, `"${why}"`].join(",");
    } catch (e) {
      line = ["inbox", c.id, classifyModel, draftModel, c.category, "", false, "error", '""', false, false, Date.now() - t0, "", false, `"${String(e).slice(0, 100).replace(/"/g, "'")}"`].join(",");
    }
    rows.push(line);
  }
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, rows.join("\n") + "\n");
  const passed = rows.slice(1).filter((r) => r.includes(",true,\"") || r.split(",").at(-2) === "true").length;
  console.log(`${passed}/${cases.length} passed -> ${out}`);
}
main().catch((e) => { console.error(e); process.exit(1); });
