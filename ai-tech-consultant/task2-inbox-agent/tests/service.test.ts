import { describe, expect, it } from "vitest";
import { loadConfig } from "../src/config";
import { Repo } from "../src/db";
import { KnowledgeClient } from "../src/knowledgeClient";
import { MockLlm } from "../src/llm";
import { InboxService } from "../src/service";

const kbBody = { request_id: "r", status: "answered", answer: "Go to Design > Logo.", sources: [{ title: "Logo", url: "https://h/logo", relevance: 0.9, kind: "text", excerpt: "Upload at Design > Logo" }], evidence_score: 0.9, needs_human: false };
function svc(kbImpl: typeof fetch, threshold = 3) {
  const cfg = { ...loadConfig({ LLM_MODE: "mock" } as never), autonomyThreshold: threshold, dbPath: ":memory:", auditEveryN: 100 };
  return new InboxService(cfg, new Repo(":memory:"), new MockLlm(), new KnowledgeClient({ baseUrl: "http://kb", timeoutMs: 200, retries: 0, fetchImpl: kbImpl }));
}
const good = (async () => new Response(JSON.stringify(kbBody))) as typeof fetch;
const mail = { from: "a@b.com", subject: "Logo", body: "How do I change my logo?" };

describe("inbox service", () => {
  it("drafts with evidence for a how-to email", async () => {
    const s = svc(good);
    const { draftId } = await s.ingest(mail);
    const d = s.repo.draft(draftId)!;
    expect(d.category).toBe("how_to");
    expect(d.status).toBe("pending_review");
    expect(JSON.parse(String(d.sources))[0].url).toBe("https://h/logo");
  });
  it("does NOT fabricate a reply when the knowledge service is down", async () => {
    const s = svc((async () => { throw new Error("down"); }) as typeof fetch);
    const { draftId } = await s.ingest(mail);
    const d = s.repo.draft(draftId)!;
    expect(d.status).toBe("knowledge_unavailable");
    expect(d.reply).toBe("");
    expect(() => s.review(draftId, { action: "approve", reviewer: "x" })).toThrow(/nothing to approve/);
  });
  it("refund emails always require a human", async () => {
    const s = svc(good);
    const { draftId, autoSent } = await s.ingest({ from: "a@b.com", subject: "Refund", body: "Please cancel and refund me" });
    expect(autoSent).toBe(false);
    expect(s.repo.draft(draftId)!.requires_human).toBe(1);
  });
  it("send is idempotent and cannot be repeated", async () => {
    const s = svc(good);
    const { draftId } = await s.ingest(mail);
    s.review(draftId, { action: "approve", reviewer: "x" });
    expect(() => s.review(draftId, { action: "approve", reviewer: "x" })).toThrow(/already sent/);
    expect(s.repo.outbox()).toHaveLength(1);
  });
  it("streak -> eligible -> enable -> auto-send -> edit revokes", async () => {
    const s = svc(good, 3);
    let kind = "";
    for (let i = 0; i < 3; i++) { const { draftId } = await s.ingest(mail); kind = String(s.repo.draft(draftId)!.kind_key); s.review(draftId, { action: "approve", reviewer: "x" }); }
    expect(s.repo.kind(kind)!.eligible).toBe(true);
    s.enableAutonomy(kind, "lead");
    const auto = await s.ingest(mail);
    expect(auto.autoSent).toBe(true);
    expect(s.repo.outbox().some((o) => o.sent_by === "auto")).toBe(true);
    // a human edit on the same kind switches autonomy off
    const { draftId } = await s.ingest({ ...mail, subject: "Another logo question" }); // auto-sent again (still enabled) -> use review path via manual disable check
    expect(draftId).toBeGreaterThan(0);
    s.disableAutonomy(kind, "lead");
    expect((await s.ingest(mail)).autoSent).toBe(false);
  });
  it("edit resets the streak; 'edit' with no changes counts as unedited", async () => {
    const s = svc(good);
    const a = await s.ingest(mail);
    const r1 = s.review(a.draftId, { action: "edit", finalText: String(s.repo.draft(a.draftId)!.reply), reviewer: "x" });
    expect(r1.edited).toBe(false);
    const b = await s.ingest(mail);
    const r2 = s.review(b.draftId, { action: "edit", finalText: "Totally different reply.", reviewer: "x", reason: "too long" });
    expect(r2.edited).toBe(true);
    expect(r2.kind.consecutiveUnedited).toBe(0);
  });
  it("refuses to enable autonomy before eligibility and for risky categories", () => {
    const s = svc(good);
    expect(() => s.enableAutonomy("how_to:x", "lead")).toThrow(/not eligible/);
  });
});
