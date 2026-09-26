// Browser audio I/O for the voice demo. Every browser API is feature-detected and every
// failure is reported to the caller instead of thrown into React.
//
// Playback: server frames are [4-byte big-endian generation][PCM16LE 16 kHz mono]. Frames from a
// generation older than the latest `audio.clear` are dropped, and `clear()` stops every
// scheduled buffer immediately — so interrupted agent audio can never resume.

type AudioCtor = new (opts?: AudioContextOptions) => AudioContext;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Env = any;

export function audioContextCtor(env: Env = typeof window !== "undefined" ? window : {}): AudioCtor | null {
  return (env?.AudioContext as AudioCtor) || (env?.webkitAudioContext as AudioCtor) || null;
}

/** null when the microphone path is supported, otherwise a short human-readable reason. */
export function detectMicSupport(env: Env = typeof window !== "undefined" ? window : {}): string | null {
  if (!env?.navigator?.mediaDevices || typeof env.navigator.mediaDevices.getUserMedia !== "function")
    return "this browser does not expose the microphone API (needs HTTPS and a modern browser)";
  const Ctor = audioContextCtor(env);
  if (!Ctor) return "Web Audio (AudioContext) is not available";
  if (typeof env.AudioWorkletNode !== "function") return "AudioWorklet is not supported by this browser";
  return null;
}

export function parseAudioFrame(buf: ArrayBuffer): { generation: number; pcm: Int16Array } | null {
  if (!(buf instanceof ArrayBuffer) || buf.byteLength < 4) return null;
  const generation = new DataView(buf).getUint32(0, false);
  const samples = Math.floor((buf.byteLength - 4) / 2); // a trailing odd byte is dropped, never a RangeError
  return { generation, pcm: new Int16Array(buf.slice(4, 4 + samples * 2)) };
}

export class GenerationGate {
  minGeneration = 0;
  accept(generation: number): boolean {
    return generation >= this.minGeneration;
  }
  clear(generation: number): void {
    this.minGeneration = Math.max(this.minGeneration, generation);
  }
}

export class PcmPlayer {
  private ctx: AudioContext;
  private nextTime = 0;
  private sources = new Set<AudioBufferSourceNode>();
  private closed = false;
  readonly gate = new GenerationGate();
  muted = false;

  /** Throws only if Web Audio is missing; callers treat that as "playback unavailable". */
  constructor(
    private sampleRate = 16000,
    factory?: () => AudioContext,
  ) {
    if (factory) this.ctx = factory();
    else {
      const Ctor = audioContextCtor();
      if (!Ctor) throw new Error("Web Audio (AudioContext) is not available");
      // Default device rate; AudioBuffers are created at 16 kHz and resampled by the browser.
      this.ctx = new Ctor();
    }
  }

  async resume(): Promise<void> {
    if (!this.closed && this.ctx.state === "suspended") await this.ctx.resume();
  }

  play(generation: number, pcm: Int16Array): void {
    if (this.closed || this.ctx.state === "closed" || !this.gate.accept(generation) || this.muted || pcm.length === 0) return;
    const buf = this.ctx.createBuffer(1, pcm.length, this.sampleRate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 0x8000;
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.ctx.destination);
    const t = Math.max(this.ctx.currentTime + 0.02, this.nextTime);
    src.start(t);
    this.nextTime = t + buf.duration;
    this.sources.add(src);
    src.onended = () => this.sources.delete(src);
  }

  clear(generation: number): void {
    this.gate.clear(generation);
    for (const s of this.sources) {
      try {
        s.stop();
      } catch {
        /* already stopped */
      }
    }
    this.sources.clear();
    this.nextTime = 0;
  }

  close(): void {
    if (this.closed) return;
    this.clear(this.gate.minGeneration);
    this.closed = true;
    try {
      void this.ctx.close().catch(() => undefined);
    } catch {
      /* already closed */
    }
  }
}

export class MicCapture {
  private ctx?: AudioContext;
  private stream?: MediaStream;
  private node?: AudioWorkletNode;
  private stopped = false;

  constructor(private env: Env = typeof window !== "undefined" ? window : {}) {}

  async start(onFrame: (pcm16: ArrayBuffer) => void): Promise<void> {
    const unsupported = detectMicSupport(this.env);
    if (unsupported) throw new Error(unsupported);
    let stream: MediaStream;
    try {
      stream = await this.env.navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      });
    } catch (e) {
      const name = (e as { name?: string })?.name;
      if (name === "NotAllowedError" || name === "SecurityError") throw new Error("microphone permission was denied");
      if (name === "NotFoundError") throw new Error("no microphone was found");
      throw new Error(`microphone could not start (${name || (e as Error)?.message || "unknown error"})`);
    }
    this.stream = stream;
    if (this.stopped) {
      this.release();
      throw new Error("microphone stopped before it finished starting");
    }
    try {
      const Ctor = audioContextCtor(this.env)!;
      this.ctx = new Ctor();
      await this.ctx.audioWorklet.addModule("/audio-worklet.js");
      if (this.stopped) throw new Error("microphone stopped before it finished starting");
      const src = this.ctx.createMediaStreamSource(stream);
      this.node = new this.env.AudioWorkletNode(this.ctx, "capture-16k") as AudioWorkletNode;
      this.node.port.onmessage = (ev: MessageEvent<ArrayBuffer>) => {
        if (!this.stopped && ev.data instanceof ArrayBuffer && ev.data.byteLength > 0) onFrame(ev.data);
      };
      src.connect(this.node);
      // Keep the worklet in the rendered graph (some browsers skip unconnected nodes) without echoing the mic.
      const mute = this.ctx.createGain();
      mute.gain.value = 0;
      this.node.connect(mute).connect(this.ctx.destination);
    } catch (e) {
      this.release();
      throw e instanceof Error ? e : new Error(String(e));
    }
  }

  /** Idempotent; safe before, during or after start(). */
  stop(): void {
    this.stopped = true;
    this.release();
  }

  private release(): void {
    try {
      if (this.node) this.node.port.onmessage = null;
      this.node?.disconnect();
    } catch {
      /* ignore */
    }
    this.node = undefined;
    try {
      this.stream?.getTracks().forEach((t) => t.stop());
    } catch {
      /* ignore */
    }
    this.stream = undefined;
    const ctx = this.ctx;
    this.ctx = undefined;
    if (ctx) {
      try {
        void ctx.close().catch(() => undefined);
      } catch {
        /* ignore */
      }
    }
  }
}
