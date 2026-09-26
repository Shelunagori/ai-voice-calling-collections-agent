// Framework-free voice session client. Owns the WebSocket, audio player and microphone for ONE
// session and guarantees that nothing it does can throw into React:
//   * every handler is wrapped; failures become short notices + console.error details;
//   * teardown is idempotent;
//   * a SessionManager generation guard ignores callbacks from superseded sessions.

import { MicCapture, PcmPlayer, parseAudioFrame } from "./audio";
import type { ServerEvent } from "./session";

export const MIC_UNAVAILABLE = "Microphone unavailable. Continue with typed input.";

export interface SocketLike {
  readyState: number;
  binaryType: string;
  send(data: string | ArrayBuffer): void;
  close(code?: number, reason?: string): void;
  onopen: ((e: unknown) => void) | null;
  onmessage: ((e: { data: unknown }) => void) | null;
  onerror: ((e: unknown) => void) | null;
  onclose: ((e: { code: number; reason: string }) => void) | null;
}
export interface PlayerLike {
  play(generation: number, pcm: Int16Array): void;
  clear(generation: number): void;
  close(): void;
  resume(): Promise<void>;
}
export interface MicLike {
  start(onFrame: (pcm: ArrayBuffer) => void): Promise<void>;
  stop(): void;
}
export type ClientDeps = {
  createSocket: (url: string) => SocketLike;
  createPlayer: () => PlayerLike;
  createMic: () => MicLike;
  logError: (...args: unknown[]) => void;
  onAudioClear?: () => void;
};
export type ClientCallbacks = {
  onEvent: (ev: ServerEvent) => void;
  onNotice: (message: string) => void;
  onClosed: (reason?: string) => void;
};

const OPEN = 1;

export function browserDeps(): ClientDeps {
  return {
    createSocket: (url) => new WebSocket(url) as unknown as SocketLike,
    createPlayer: () => new PcmPlayer(16000),
    createMic: () => new MicCapture(),
    logError: (...args) => console.error("[voice-demo]", ...args),
    onAudioClear: () => {
      try {
        window.speechSynthesis?.cancel();
      } catch {
        /* ignore */
      }
    },
  };
}

function describe(e: unknown): string {
  if (e instanceof Error) return e.message;
  return typeof e === "string" ? e : "unexpected error";
}

export class VoiceClient {
  private sock: SocketLike | null = null;
  private player: PlayerLike | null = null;
  private mic: MicLike | null = null;
  private disposed = false;
  private closedReported = false;

  constructor(
    private deps: ClientDeps,
    private cb: ClientCallbacks,
  ) {}

  get connected(): boolean {
    return !this.disposed && this.sock?.readyState === OPEN;
  }

  /** Never throws. Connection problems are reported through callbacks. */
  async start(url: string): Promise<void> {
    try {
      this.player = this.deps.createPlayer();
      await this.player.resume().catch((e) => this.deps.logError("audio resume failed", e));
    } catch (e) {
      this.player = null;
      this.deps.logError("audio player unavailable", e);
      this.notice("Audio playback unavailable in this browser; the transcript will still update.");
    }
    if (this.disposed) return;
    try {
      const sock = this.deps.createSocket(url);
      sock.binaryType = "arraybuffer";
      this.sock = sock;
      sock.onmessage = (m) => this.guard("message", () => this.onMessage(m.data));
      sock.onerror = (e) =>
        this.guard("socket error", () => {
          this.deps.logError("websocket error", e);
          this.notice("Connection problem with the voice service.");
        });
      sock.onclose = (e) => this.guard("socket close", () => this.finish(e?.reason || (e?.code ? `closed (${e.code})` : undefined)));
    } catch (e) {
      this.deps.logError("websocket could not be created", e);
      this.notice(`Could not connect to the voice service: ${describe(e)}`);
      this.finish("connect_failed");
    }
  }

  sendJSON(obj: Record<string, unknown>): boolean {
    if (!this.connected) return false;
    try {
      this.sock!.send(JSON.stringify(obj));
      return true;
    } catch (e) {
      this.deps.logError("send failed", e);
      return false;
    }
  }

  /** Idempotent teardown: socket, microphone, player. */
  close(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.releaseMic();
    try {
      this.player?.close();
    } catch (e) {
      this.deps.logError("player close failed", e);
    }
    this.player = null;
    const s = this.sock;
    this.sock = null;
    if (s) {
      s.onmessage = s.onerror = s.onclose = null;
      try {
        s.close();
      } catch {
        /* already closed */
      }
    }
  }

  private finish(reason?: string): void {
    if (this.closedReported) return;
    this.closedReported = true;
    const cb = this.cb;
    this.close();
    cb.onClosed(reason);
  }

  private notice(msg: string): void {
    try {
      this.cb.onNotice(msg);
    } catch (e) {
      this.deps.logError("notice handler failed", e);
    }
  }

  private guard(what: string, fn: () => void): void {
    if (this.disposed && what !== "socket close") return;
    try {
      fn();
    } catch (e) {
      this.deps.logError(`${what} handler failed`, e);
      this.notice(`Voice demo error (${what}): ${describe(e)}`);
    }
  }

  private onMessage(data: unknown): void {
    if (this.disposed) return;
    if (data instanceof ArrayBuffer) {
      const frame = parseAudioFrame(data);
      if (!frame) {
        this.deps.logError("malformed audio frame", data.byteLength);
        return;
      }
      try {
        this.player?.play(frame.generation, frame.pcm);
      } catch (e) {
        this.deps.logError("audio playback failed", e);
      }
      return;
    }
    if (typeof data !== "string") {
      this.deps.logError("unexpected websocket payload", typeof data);
      return;
    }
    let ev: unknown;
    try {
      ev = JSON.parse(data);
    } catch (e) {
      this.deps.logError("malformed JSON from server", e);
      return;
    }
    if (!ev || typeof ev !== "object" || Array.isArray(ev) || typeof (ev as ServerEvent).type !== "string") {
      this.deps.logError("unexpected event shape", ev);
      return;
    }
    const event = ev as ServerEvent;
    if (event.type === "audio.clear") {
      try {
        this.player?.clear(Number(event.generation) || 0);
      } catch (e) {
        this.deps.logError("audio clear failed", e);
      }
      this.deps.onAudioClear?.();
      return;
    }
    if (event.type === "error") this.deps.logError("server reported provider error", event.provider, event.message);
    this.cb.onEvent(event);
    if (event.type === "session.created" && event.input_mode === "voice") void this.startMic();
  }

  private async startMic(): Promise<void> {
    let mic: MicLike;
    try {
      mic = this.deps.createMic();
    } catch (e) {
      this.deps.logError("microphone unavailable", e);
      this.notice(`${MIC_UNAVAILABLE} (${describe(e)})`);
      return;
    }
    this.mic = mic;
    try {
      await mic.start((pcm) => {
        if (this.connected) {
          try {
            this.sock!.send(pcm);
          } catch (e) {
            this.deps.logError("audio send failed", e);
          }
        }
      });
    } catch (e) {
      this.deps.logError("microphone start failed", e);
      this.releaseMic();
      if (!this.disposed) this.notice(`${MIC_UNAVAILABLE} (${describe(e)})`);
    }
  }

  private releaseMic(): void {
    try {
      this.mic?.stop();
    } catch (e) {
      this.deps.logError("microphone stop failed", e);
    }
    this.mic = null;
  }
}

/** Serialises sessions: starting a new one tears the old one down first; callbacks from any
 *  superseded session are dropped by a monotonically increasing generation guard. */
export class SessionManager {
  generation = 0;
  private client: VoiceClient | null = null;

  constructor(private deps: ClientDeps) {}

  async start(opts: { url: string } & ClientCallbacks): Promise<void> {
    this.stop();
    const gen = ++this.generation;
    const live = () => gen === this.generation;
    const client = new VoiceClient(this.deps, {
      onEvent: (e) => live() && opts.onEvent(e),
      onNotice: (m) => live() && opts.onNotice(m),
      onClosed: (r) => live() && opts.onClosed(r),
    });
    this.client = client;
    await client.start(opts.url);
  }

  sendText(text: string): boolean {
    return this.client?.sendJSON({ type: "text", text }) ?? false;
  }

  send(obj: Record<string, unknown>): boolean {
    return this.client?.sendJSON(obj) ?? false;
  }

  get connected(): boolean {
    return this.client?.connected ?? false;
  }

  /** Idempotent. The closed client drops its own late callbacks (disposed flag). */
  stop(): void {
    const c = this.client;
    this.client = null;
    c?.close();
  }
}
