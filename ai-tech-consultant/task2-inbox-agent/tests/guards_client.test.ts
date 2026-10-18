import { describe, expect, it } from "vitest";
import { detectPolicyFlags } from "../src/guards";
import { KnowledgeClient } from "../src/knowledgeClient";

describe("policy guard", () => {
  it("flags invented prices, refund promises, delivery promises", () => {
    expect(detectPolicyFlags("That will cost $500.", "")).toContain("unsupported_price:$500");
    expect(detectPolicyFlags("We will refund you today.", "")).toContain("refund_commitment");
    expect(detectPolicyFlags("It will be ready by Friday.", "")).toContain("delivery_or_guarantee_commitment");
  });
  it("allows prices that appear in evidence and clean replies", () => {
    expect(detectPolicyFlags("The plan is $29.", "plans start at $29")).toEqual([]);
    expect(detectPolicyFlags("Open Design > Logo to upload it.", "")).toEqual([]);
  });
  it("flags links not found in evidence", () => {
    expect(detectPolicyFlags("See https://evil.example/x", "https://good.example/a").some((f) => f.startsWith("unverified_link"))).toBe(true);
  });
});

const body = { request_id: "r", status: "answered", answer: "ok", sources: [{ title: "t", url: "u", relevance: 0.9, kind: "text", excerpt: "e" }], evidence_score: 0.9, needs_human: false };
const mk = (impl: typeof fetch, retries = 1) => new KnowledgeClient({ baseUrl: "http://kb", timeoutMs: 500, retries, fetchImpl: impl });

describe("knowledge client", () => {
  it("parses a good response", async () => {
    const r = await mk((async () => new Response(JSON.stringify(body))) as typeof fetch).query("q");
    expect(r.kind).toBe("answered");
  });
  it("maps insufficient evidence", async () => {
    const r = await mk((async () => new Response(JSON.stringify({ ...body, status: "insufficient_evidence", answer: null, sources: [] }))) as typeof fetch).query("q");
    expect(r.kind).toBe("insufficient");
  });
  it("retries 5xx then reports unavailable instead of throwing", async () => {
    let n = 0;
    const r = await mk((async () => { n++; return new Response("x", { status: 503 }); }) as typeof fetch).query("q");
    expect(r.kind).toBe("unavailable");
    expect(n).toBe(2);
  });
  it("does not retry 4xx", async () => {
    let n = 0;
    const r = await mk((async () => { n++; return new Response("x", { status: 401 }); }) as typeof fetch).query("q");
    expect(r).toEqual({ kind: "unavailable", error: "HTTP 401" });
    expect(n).toBe(1);
  });
  it("network failure -> unavailable", async () => {
    const r = await mk((async () => { throw new Error("ECONNREFUSED"); }) as typeof fetch).query("q");
    expect(r.kind).toBe("unavailable");
  });
  it("rejects malformed bodies", async () => {
    const r = await mk((async () => new Response(JSON.stringify({ nope: 1 }))) as typeof fetch).query("q");
    expect(r.kind).toBe("unavailable");
  });
});
