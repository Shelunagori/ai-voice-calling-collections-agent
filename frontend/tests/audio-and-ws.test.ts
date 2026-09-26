import { describe, expect, it, vi } from "vitest";
import { wsUrl } from "../lib/api";
import { GenerationGate, MicCapture, PcmPlayer, detectMicSupport, parseAudioFrame } from "../lib/audio";
import { downsampleToInt16 } from "../lib/dsp";

describe("wsUrl()", () => {
  const prod = "https://ai-voice-calling-collections-agent-production.up.railway.app";
  it("maps the HTTPS API base to WSS on the backend host (never the frontend origin)", () => {
    expect(wsUrl("/ws/session?scenario=A&lang=en&mode=voice", prod)).toBe(
      "wss://ai-voice-calling-collections-agent-production.up.railway.app/ws/session?scenario=A&lang=en&mode=voice",
    );
  });
  it("maps HTTP to WS", () => {
    expect(wsUrl("/ws/session?x=1", "http://localhost:8000")).toBe("ws://localhost:8000/ws/session?x=1");
  });
  it("does not duplicate slashes and preserves the query string", () => {
    expect(wsUrl("/ws/session?a=1&b=2", prod + "/")).toBe(
      "wss://ai-voice-calling-collections-agent-production.up.railway.app/ws/session?a=1&b=2",
    );
    expect(wsUrl("ws/session?a=1", prod)).toContain(".app/ws/session?a=1");
  });
  it("rejects a non-http base instead of producing a bogus URL", () => {
    expect(() => wsUrl("/ws/session", "ftp://x")).toThrow();
  });
});

describe("parseAudioFrame", () => {
  it("returns null for frames shorter than the 4-byte header", () => {
    expect(parseAudioFrame(new ArrayBuffer(0))).toBeNull();
    expect(parseAudioFrame(new ArrayBuffer(3))).toBeNull();
  });
  it("drops a trailing odd byte instead of throwing RangeError", () => {
    const buf = new ArrayBuffer(4 + 5);
    new DataView(buf).setUint32(0, 9, false);
    const f = parseAudioFrame(buf)!;
    expect(f.generation).toBe(9);
    expect(f.pcm.length).toBe(2);
  });
  it("handles a header-only frame", () => {
    const buf = new ArrayBuffer(4);
    expect(parseAudioFrame(buf)!.pcm.length).toBe(0);
  });
  it("rejects non-ArrayBuffer input", () => {
    expect(parseAudioFrame("nope" as unknown as ArrayBuffer)).toBeNull();
  });
});

describe("downsampleToInt16", () => {
  it("converts 48 kHz float to 16 kHz int16 with clipping", () => {
    const input = new Float32Array(480).fill(0.5);
    input[0] = 2; // out of range
    const out = downsampleToInt16(input, 48000, 16000);
    expect(out.length).toBe(160);
    expect(out[5]).toBe(Math.round(0.5 * 0x7fff));
    expect(Math.abs(out[0])).toBeLessThanOrEqual(0x7fff);
  });
  it("passes 16 kHz through and handles empty input", () => {
    expect(downsampleToInt16(new Float32Array(10), 16000, 16000).length).toBe(10);
    expect(downsampleToInt16(new Float32Array(0), 44100, 16000).length).toBe(0);
  });
  it("rejects upsampling / invalid rates", () => {
    expect(() => downsampleToInt16(new Float32Array(4), 8000, 16000)).toThrow();
  });
});

describe("feature detection", () => {
  it("reports missing mediaDevices / AudioContext / AudioWorklet", () => {
    expect(detectMicSupport({} as never)).toMatch(/microphone API/);
    expect(detectMicSupport({ navigator: { mediaDevices: { getUserMedia: () => 0 } } } as never)).toMatch(/Web Audio/);
    const Ctx = function () {} as unknown as typeof AudioContext;
    expect(
      detectMicSupport({ navigator: { mediaDevices: { getUserMedia: () => 0 } }, AudioContext: Ctx } as never),
    ).toMatch(/AudioWorklet/);
  });
});

function fakeCtx(opts: { closeRejects?: boolean } = {}) {
  const sources: { stop: () => void; start: () => void }[] = [];
  const ctx = {
    state: "running",
    currentTime: 0,
    destination: {},
    createBuffer: (_c: number, n: number) => ({ duration: n / 16000, getChannelData: () => new Float32Array(n) }),
    createBufferSource: () => {
      const s = { buffer: null, connect: () => {}, start: vi.fn(), stop: vi.fn(() => { throw new Error("already stopped"); }), onended: null };
      sources.push(s);
      return s;
    },
    resume: vi.fn(async () => {}),
    close: vi.fn(async () => {
      ctx.state = "closed";
      if (opts.closeRejects) throw new Error("InvalidStateError");
    }),
  };
  return { ctx, sources };
}

describe("PcmPlayer", () => {
  it("never throws on play after close, and close/clear are idempotent", async () => {
    const { ctx } = fakeCtx({ closeRejects: true });
    const p = new PcmPlayer(16000, () => ctx as never);
    p.play(1, new Int16Array(160));
    p.clear(2);
    p.close();
    p.close();
    await Promise.resolve();
    expect(() => p.play(3, new Int16Array(160))).not.toThrow();
    expect(ctx.close).toHaveBeenCalledTimes(1);
  });
  it("drops stale generations", () => {
    const g = new GenerationGate();
    g.clear(4);
    expect(g.accept(3)).toBe(false);
  });
});

describe("MicCapture", () => {
  it("permission denial rejects with a readable error and releases nothing twice", async () => {
    const env = {
      navigator: { mediaDevices: { getUserMedia: vi.fn(async () => { throw Object.assign(new Error("denied"), { name: "NotAllowedError" }); }) } },
      AudioContext: function () {} as never,
      AudioWorkletNode: function () {} as never,
    };
    const m = new MicCapture(env as never);
    await expect(m.start(() => {})).rejects.toThrow(/permission/i);
    m.stop();
    m.stop();
  });
  it("stops acquired tracks when AudioWorklet setup fails", async () => {
    const track = { stop: vi.fn() };
    const stream = { getTracks: () => [track] };
    const ctx = { audioWorklet: { addModule: vi.fn(async () => { throw new Error("worklet 404"); }) }, close: vi.fn(async () => {}) };
    const env = {
      navigator: { mediaDevices: { getUserMedia: vi.fn(async () => stream) } },
      AudioContext: function () { return ctx; } as never,
      AudioWorkletNode: function () {} as never,
    };
    const m = new MicCapture(env as never);
    await expect(m.start(() => {})).rejects.toThrow(/worklet/);
    expect(track.stop).toHaveBeenCalledTimes(1);
    expect(ctx.close).toHaveBeenCalledTimes(1);
    m.stop(); // idempotent after failure
    expect(track.stop).toHaveBeenCalledTimes(1);
  });
  it("stop() before getUserMedia resolves releases the late stream", async () => {
    const track = { stop: vi.fn() };
    let resolve!: (s: unknown) => void;
    const env = {
      navigator: { mediaDevices: { getUserMedia: () => new Promise((r) => (resolve = r)) } },
      AudioContext: function () { return { audioWorklet: { addModule: async () => {} }, close: async () => {} }; } as never,
      AudioWorkletNode: function () {} as never,
    };
    const m = new MicCapture(env as never);
    const p = m.start(() => {});
    m.stop();
    resolve({ getTracks: () => [track] });
    await expect(p).rejects.toThrow(/stopped/);
    expect(track.stop).toHaveBeenCalled();
  });
});
