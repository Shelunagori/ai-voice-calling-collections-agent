import { describe, expect, it, vi } from "vitest";
import { MIC_UNAVAILABLE, SessionManager, type SocketLike } from "../lib/voice-client";

class FakeSocket implements SocketLike {
  static all: FakeSocket[] = [];
  readyState = 0;
  binaryType = "blob";
  sent: unknown[] = [];
  closed = 0;
  onopen: ((e: unknown) => void) | null = null;
  onmessage: ((e: { data: unknown }) => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  onclose: ((e: { code: number; reason: string }) => void) | null = null;
  constructor(public url: string) {
    FakeSocket.all.push(this);
  }
  send(d: unknown) {
    this.sent.push(d);
  }
  close() {
    this.closed++;
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.({});
  }
  msg(data: unknown) {
    this.onmessage?.({ data });
  }
}

function harness(over: Partial<Parameters<typeof SessionManager.prototype.start>[0]> = {}) {
  FakeSocket.all = [];
  const events: { type: string }[] = [];
  const notices: string[] = [];
  const closed: (string | undefined)[] = [];
  const player = { play: vi.fn(), clear: vi.fn(), close: vi.fn(), resume: vi.fn(async () => {}) };
  const mic = { start: vi.fn(async () => {}), stop: vi.fn() };
  const logged: unknown[] = [];
  const mgr = new SessionManager({
    createSocket: (u) => new FakeSocket(u),
    createPlayer: () => player,
    createMic: () => mic,
    logError: (...a: unknown[]) => logged.push(a),
  });
  const cb = {
    onEvent: (e: { type: string }) => events.push(e),
    onNotice: (m: string) => notices.push(m),
    onClosed: (r?: string) => closed.push(r),
  };
  return { mgr, cb, events, notices, closed, player, mic, logged, ...over };
}

const created = (mode = "voice") => JSON.stringify({ type: "session.created", session_id: "s", input_mode: mode, providers: {} });

describe("SessionManager / VoiceClient", () => {
  it("malformed JSON and unexpected shapes are reported, never thrown", async () => {
    const h = harness();
    await h.mgr.start({ url: "wss://x/ws", ...h.cb });
    const s = FakeSocket.all[0];
    s.open();
    expect(() => s.msg("{not json")).not.toThrow();
    expect(() => s.msg(JSON.stringify([1, 2]))).not.toThrow();
    expect(() => s.msg(JSON.stringify({ no: "type" }))).not.toThrow();
    expect(() => s.msg(42)).not.toThrow();
    expect(h.events).toEqual([]);
    expect(h.logged.length).toBeGreaterThan(0);
  });

  it("malformed binary frames and player errors do not escape", async () => {
    const h = harness();
    h.player.play.mockImplementation(() => {
      throw new Error("AudioContext closed");
    });
    await h.mgr.start({ url: "wss://x/ws", ...h.cb });
    const s = FakeSocket.all[0];
    expect(() => s.msg(new ArrayBuffer(2))).not.toThrow();
    const ok = new ArrayBuffer(8);
    expect(() => s.msg(ok)).not.toThrow();
    expect(h.player.play).toHaveBeenCalledTimes(1);
  });

  it("microphone rejection keeps the session alive with a typed-input notice", async () => {
    const h = harness();
    h.mic.start.mockRejectedValue(Object.assign(new Error("Permission denied"), { name: "NotAllowedError" }));
    await h.mgr.start({ url: "wss://x/ws", ...h.cb });
    const s = FakeSocket.all[0];
    s.open();
    s.msg(created("voice"));
    await new Promise((r) => setTimeout(r, 0));
    expect(h.notices.some((n) => n.startsWith(MIC_UNAVAILABLE))).toBe(true);
    expect(h.mic.stop).toHaveBeenCalled();
    expect(s.closed).toBe(0); // socket untouched
    expect(h.events.map((e) => e.type)).toContain("session.created");
    h.mgr.sendText("yes");
    expect(s.sent).toContain(JSON.stringify({ type: "text", text: "yes" }));
  });

  it("player creation failure still connects (text transcript keeps working)", async () => {
    const h = harness();
    const mgr = new SessionManager({
      createSocket: (u) => new FakeSocket(u),
      createPlayer: () => {
        throw new Error("no AudioContext");
      },
      createMic: () => h.mic,
      logError: () => {},
    });
    await mgr.start({ url: "wss://x/ws", ...h.cb });
    expect(FakeSocket.all).toHaveLength(1);
    expect(h.notices.join(" ")).toMatch(/audio playback unavailable/i);
  });

  it("WebSocket constructor failure is a notice, not an exception", async () => {
    const h = harness();
    const mgr = new SessionManager({
      createSocket: () => {
        throw new SyntaxError("bad url");
      },
      createPlayer: () => h.player,
      createMic: () => h.mic,
      logError: () => {},
    });
    await expect(mgr.start({ url: "wss://x/ws", ...h.cb })).resolves.toBeUndefined();
    expect(h.notices.join(" ")).toMatch(/could not connect/i);
    expect(h.closed).toHaveLength(1);
  });

  it("onerror + onclose surface one closed callback and teardown is idempotent", async () => {
    const h = harness();
    await h.mgr.start({ url: "wss://x/ws", ...h.cb });
    const s = FakeSocket.all[0];
    s.onerror?.({});
    s.onclose?.({ code: 1006, reason: "" });
    s.onclose?.({ code: 1006, reason: "" });
    h.mgr.stop();
    h.mgr.stop();
    expect(h.closed).toHaveLength(1);
    expect(h.notices.join(" ")).toMatch(/connection/i);
    expect(h.player.close).toHaveBeenCalledTimes(1);
  });

  it("duplicate start: the first session is torn down and its late callbacks are ignored", async () => {
    const h = harness();
    await h.mgr.start({ url: "wss://x/1", ...h.cb });
    await h.mgr.start({ url: "wss://x/2", ...h.cb });
    const [first, second] = FakeSocket.all;
    expect(FakeSocket.all).toHaveLength(2);
    expect(first.closed).toBe(1);
    first.onmessage?.({ data: created("text") }); // handlers were detached, but even if called:
    first.onclose?.({ code: 1000, reason: "old" });
    expect(h.events).toEqual([]);
    expect(h.closed).toEqual([]);
    second.msg(created("text"));
    expect(h.events).toHaveLength(1);
    expect(h.mgr.generation).toBe(2);
  });

  it("two starts in the same tick never leave two live sockets", async () => {
    const h = harness();
    await Promise.all([h.mgr.start({ url: "wss://x/1", ...h.cb }), h.mgr.start({ url: "wss://x/2", ...h.cb })]);
    const open = FakeSocket.all.filter((s) => s.closed === 0);
    expect(open).toHaveLength(1);
    expect(open[0].url).toBe("wss://x/2");
    expect(h.player.close).toHaveBeenCalled();
  });
});
