import { describe, expect, it } from "vitest";
import { describeAction, endState, identityTimeline, latencySummary, maskDobFields, voiceRuntime } from "../lib/audit-summary";
import { phoneDetail } from "./fixtures";

describe("audit summary", () => {
  it("masks DOB values in identity events and interpretations", () => {
    expect(maskDobFields({ year: 1988, month: 4, day: null, missing: ["day"] }, "identity.partial_dob")).toEqual({ year: "•••", month: "•••", day: null, missing: ["day"] });
    expect(maskDobFields({ amount: 1988 }, "payment.proposed")).toEqual({ amount: 1988 });
    expect(describeAction({ action: "PROVIDE_DOB", dob: "1988-04-12" })).toBe("PROVIDE_DOB (complete date)");
    expect(describeAction({ action: "PROPOSE_PAYMENT", amount: 20000, date: "2026-10-15" })).toBe("PROPOSE_PAYMENT ¥20,000 · 2026-10-15");
  });
  it("builds the identity timeline in order", () => {
    expect(identityTimeline(phoneDetail().audit).map((s) => s.label)).toEqual(["name confirmed", "DOB partial", "verified", "disclosure allowed"]);
  });
  it("only reports p95 from 5 samples", () => {
    const rows = [100, 200, 300, 400, 500].map((v, i) => ({ turn_index: i, input_mode: "voice", provider_mode: "x", stages: { nlu: v } }));
    const nlu = (r: typeof rows) => latencySummary(r).find((s) => s.key === "nlu")!;
    expect(nlu(rows)).toMatchObject({ count: 5, p50: 300, p95: 500 });
    expect(nlu(rows.slice(0, 2)).p95).toBeUndefined();
  });
  it("classifies end states and reads voice runtime", () => {
    expect(endState({ ended_reason: "caller_hangup", call_status: "DISCONNECTED" }).category).toBe("caller_hangup");
    expect(endState({ ended_reason: "completed_with_promise", promise_status: "CONFIRMED", call_status: "COMPLETED" }).category).toBe("completed_with_promise");
    expect(endState({ ended_reason: "transferred_to_human", human_transfer_requested: true, call_status: "TRANSFER_REQUESTED" }).category).toBe("transfer");
    expect(endState({ ended_reason: "stt_unavailable", call_status: "FAILED" }).category).toBe("error_or_disconnect");
    const v = voiceRuntime(phoneDetail().audit);
    expect(v.bargeIns[0].cancelMs).toBe(0.92);
    expect(v.transitions).toHaveLength(2);
  });
});
