import { describe, expect, it } from "vitest";
import { applyReview, decideAutoSend, editDistanceRatio, isUnedited, kindKey, newState } from "../src/autonomy";

const low = { category: "how_to" as const, risk: "low", urgency: "normal" };
const ok = { kbAnswered: true, flags: [] as string[] };

describe("unedited comparison", () => {
  it("ignores whitespace/case only", () => {
    expect(isUnedited("Hi  there,\nBye", "hi there, bye")).toBe(true);
    expect(isUnedited("Hi there", "Hello there")).toBe(false);
  });
  it("distance ratio", () => { expect(editDistanceRatio("abc", "abc")).toBe(0); expect(editDistanceRatio("abc", "xyz")).toBe(1); });
  it("kind key", () => expect(kindKey("how_to", "u")).toBe("how_to:u"));
});

describe("20-in-a-row rule", () => {
  it("becomes eligible exactly at 20 and resets on one edit", () => {
    let s = newState("k");
    for (let i = 0; i < 19; i++) s = applyReview(s, false, 20);
    expect(s.eligible).toBe(false);
    s = applyReview(s, false, 20);
    expect(s.eligible).toBe(true);
    s = applyReview(s, true, 20);
    expect(s.consecutiveUnedited).toBe(0);
    expect(s.eligible).toBe(false);
  });
  it("an edit revokes enabled autonomy", () => {
    let s = { ...newState("k"), eligible: true, enabled: true, consecutiveUnedited: 25 };
    s = applyReview(s, true, 20);
    expect(s.enabled).toBe(false);
    expect(s.disabledReason).toMatch(/edited/);
  });
  it("high edit rate revokes autonomy even without the latest being an edit", () => {
    let s = { ...newState("k"), enabled: true, eligible: true, recentEdited: [true, false, true, false] };
    s = applyReview(s, false, 20);
    expect(s.enabled).toBe(false);
  });
});

describe("auto-send gate", () => {
  const on = { ...newState("k"), enabled: true, eligible: true };
  it("eligible is not enough: needs enabled", () => expect(decideAutoSend({ ...on, enabled: false }, low, ok, 5).autoSend).toBe(false));
  it("sends when every condition holds", () => expect(decideAutoSend(on, low, ok, 5).autoSend).toBe(true));
  it("never for refund/complaint categories", () => expect(decideAutoSend(on, { ...low, category: "refund_cancellation" }, ok, 5).autoSend).toBe(false));
  it("blocked by policy flags, high risk, urgency, missing evidence", () => {
    expect(decideAutoSend(on, low, { ...ok, flags: ["refund_commitment"] }, 5).autoSend).toBe(false);
    expect(decideAutoSend(on, { ...low, risk: "medium" }, ok, 5).autoSend).toBe(false);
    expect(decideAutoSend(on, { ...low, urgency: "high" }, ok, 5).autoSend).toBe(false);
    expect(decideAutoSend(on, low, { ...ok, kbAnswered: false }, 5).autoSend).toBe(false);
  });
  it("samples every Nth for audit", () => {
    const d = decideAutoSend({ ...on, autoSent: 4 }, low, ok, 5);
    expect(d.autoSend).toBe(false);
    expect(d.audit).toBe(true);
  });
});
