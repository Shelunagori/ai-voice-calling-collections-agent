// Browser audio I/O for the voice demo.
//
// Playback: server frames are [4-byte big-endian generation][PCM16 16 kHz]. Frames from a
// generation older than the latest `audio.clear` are dropped, and `clear()` stops every
// scheduled buffer immediately — so interrupted agent audio can never resume.

export function parseAudioFrame(buf: ArrayBuffer): { generation: number; pcm: Int16Array } {
  const view = new DataView(buf);
  const generation = view.getUint32(0, false);
  return { generation, pcm: new Int16Array(buf.slice(4)) };
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
  readonly gate = new GenerationGate();
  muted = false;

  constructor(private sampleRate = 16000) {
    this.ctx = new AudioContext({ sampleRate });
  }

  async resume(): Promise<void> {
    if (this.ctx.state !== "running") await this.ctx.resume();
  }

  play(generation: number, pcm: Int16Array): void {
    if (!this.gate.accept(generation) || this.muted || pcm.length === 0) return;
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
    this.clear(this.gate.minGeneration);
    void this.ctx.close();
  }
}

export class MicCapture {
  private ctx?: AudioContext;
  private stream?: MediaStream;
  private node?: AudioWorkletNode;

  async start(onFrame: (pcm16: ArrayBuffer) => void): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    this.ctx = new AudioContext();
    await this.ctx.audioWorklet.addModule("/audio-worklet.js");
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "capture-16k");
    this.node.port.onmessage = (ev: MessageEvent<ArrayBuffer>) => onFrame(ev.data);
    src.connect(this.node);
    // Keep the worklet in the rendered graph (some browsers skip unconnected nodes) without echoing the mic.
    const mute = this.ctx.createGain();
    mute.gain.value = 0;
    this.node.connect(mute).connect(this.ctx.destination);
  }

  stop(): void {
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.ctx?.close();
  }
}
