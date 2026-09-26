import { describe, expect, it } from "vitest";
import { GenerationGate, parseAudioFrame } from "../lib/audio";
import { ms, yen } from "../lib/format";
import { initialState, percentile, reduce, turnTotal } from "../lib/session";

const ev = (event: Record<string, unknown>) => ({ kind: "event" as const, event: event as { type: string } });

describe("console reducer", () => {
  it("builds state only from server events", () => {
    let s = reduce(initialState, { kind: "connecting" });
    s = reduce(s, ev({ type: "session.created", session_id: "abc", input_mode: "text", providers: { llm: "mock" } }));
    expect(s.status).toBe("live");
    s = reduce(s, ev({ type: "lifecycle", from: "IDLE", to: "PROCESSING", cause: "session_start", at: 1 }));
    expect(s.voiceState).toBe("PROCESSING");
    s = reduce(s, ev({ type: "agent.speaking", index: 0, text: "Hello", acts: "GREETING", realizer: "template" }));
    s = reduce(s, ev({ type: "turn.agent", index: 0, text: "Hello", acts: "GREETING", interrupted: true, spoken_text: "He" }));
    expect(s.transcript).toHaveLength(1); // speaking event is updated in place, not duplicated
    expect(s.transcript[0].interrupted).toBe(true);
    s = reduce(s, ev({ type: "audit", event_type: "identity.challenge", event_id: "1", at: "t", turn_index: 1, data: {} }));
    expect(s.policy).toHaveLength(0);
    expect(s.audit[0].type).toBe("identity.challenge");
    s = reduce(s, ev({ type: "session.ended", reason: "caller_ended" }));
    expect(s.status).toBe("ended");
    expect(reduce(s, { kind: "socket_closed" }).endedReason).toBe("caller_ended");
  });

  it("extracts policy decisions from audit events", () => {
    const s = reduce(
      initialState,
      ev({
        type: "audit",
        event_type: "policy.decision",
        event_id: "e",
        at: "t",
        turn_index: 3,
        data: { decision_id: "d", rule: "PAYMENT_DATE_WITHIN_MAX_EXTENSION", decision: "BLOCK", reason: "60 days", timestamp: "t" },
      }),
    );
    expect(s.audit).toHaveLength(1);
    expect(s.policy[0]).toMatchObject({ rule: "PAYMENT_DATE_WITHIN_MAX_EXTENSION", decision: "BLOCK", turn_index: 3 });
  });
});

describe("audio", () => {
  it("parses generation-prefixed frames", () => {
    const buf = new ArrayBuffer(8);
    const v = new DataView(buf);
    v.setUint32(0, 7, false);
    v.setInt16(4, 1000, true);
    const f = parseAudioFrame(buf)!;
    expect(f.generation).toBe(7);
    expect(f.pcm[0]).toBe(1000);
  });

  it("drops frames from an interrupted generation", () => {
    const g = new GenerationGate();
    expect(g.accept(1)).toBe(true);
    g.clear(3);
    expect(g.accept(2)).toBe(false);
    expect(g.accept(3)).toBe(true);
    g.clear(2); // never moves backwards
    expect(g.accept(2)).toBe(false);
  });
});

describe("format + stats", () => {
  it("formats yen per language", () => {
    expect(yen(30000, "en")).toBe("¥30,000");
    expect(yen(30000, "ja")).toBe("30,000円");
    expect(ms(1520)).toBe("1.52 s");
  });
  it("computes nearest-rank percentiles", () => {
    expect(percentile([100, 200, 300, 400], 50)).toBe(200);
    expect(percentile([100, 200, 300, 400], 95)).toBe(400);
    expect(percentile([], 50)).toBeUndefined();
    expect(turnTotal({ speech_end_to_first_audio: 900, input_to_first_audio: 900 })).toBe(900);
  });
});

describe("degradation events", () => {
  it("switches input mode when speech recognition is lost", () => {
    let s = reduce(initialState, ev({ type: "session.created", session_id: "x", input_mode: "voice", providers: {} }));
    s = reduce(s, ev({ type: "input_mode", input_mode: "text", reason: "speech recognition unavailable" }));
    expect(s.inputMode).toBe("text");
    expect(s.errors.at(-1)).toContain("speech recognition unavailable");
  });
});
