import express from "express";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { loadConfig, type Config } from "./config";
import { Repo } from "./db";
import { KnowledgeClient } from "./knowledgeClient";
import { makeLlm, type LlmClient } from "./llm";
import { EmailInputSchema, ReviewSchema } from "./schemas";
import { InboxService } from "./service";

export function createApp(svc: InboxService) {
  const app = express();
  app.use(express.json({ limit: "200kb" }));
  app.use(express.static(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "public")));
  const wrap = (fn: (req: express.Request, res: express.Response) => unknown) => async (req: express.Request, res: express.Response) => {
    try { res.json(await fn(req, res)); } catch (e) { res.status(400).json({ error: e instanceof Error ? e.message : String(e) }); }
  };

  app.get("/health", (_q, res) => res.json({ status: "ok", llm: svc.cfg.llmMode, knowledge: svc.cfg.kbUrl }));
  app.post("/api/emails", wrap(async (req) => svc.ingest(EmailInputSchema.parse(req.body))));
  app.get("/api/inbox", wrap(() => svc.repo.inbox()));
  app.get("/api/drafts/:id", wrap((req) => { const d = svc.repo.draft(Number(req.params.id)); if (!d) throw new Error("not found"); return d; }));
  app.post("/api/drafts/:id/review", wrap((req) => svc.review(Number(req.params.id), ReviewSchema.parse(req.body))));
  app.get("/api/outbox", wrap(() => svc.repo.outbox()));
  app.get("/api/autonomy", wrap(() => svc.repo.kinds()));
  app.post("/api/autonomy/enable", wrap((req) => svc.enableAutonomy(String(req.body.kind), String(req.body.by ?? "agent"))));
  app.post("/api/autonomy/disable", wrap((req) => svc.disableAutonomy(String(req.body.kind), String(req.body.by ?? "agent"))));
  app.get("/api/prompts", wrap(() => svc.repo.prompts()));
  app.post("/api/learning/propose", wrap(async (req) => ({ id: await svc.proposeStyle(String(req.body.category)) })));
  app.post("/api/learning/:id/evaluate", wrap((req) => svc.evaluateCandidate(Number(req.params.id))));
  app.post("/api/learning/:id/activate", wrap((req) => { svc.activateStyle(Number(req.params.id)); return { ok: true }; }));
  app.post("/api/learning/rollback", wrap((req) => { svc.rollbackStyle(String(req.body.category)); return { ok: true }; }));
  return app;
}

export function buildService(cfg: Config = loadConfig(), llm?: LlmClient): InboxService {
  return new InboxService(cfg, new Repo(cfg.dbPath), llm ?? makeLlm(cfg), new KnowledgeClient({ baseUrl: cfg.kbUrl, apiKey: cfg.kbApiKey, timeoutMs: cfg.kbTimeoutMs }));
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const svc = buildService();
  createApp(svc).listen(svc.cfg.port, () => console.log(`Inbox Agent on http://localhost:${svc.cfg.port} (llm=${svc.cfg.llmMode}, knowledge=${svc.cfg.kbUrl})`));
}
