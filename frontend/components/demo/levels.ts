// Real audio levels for the orb in voice mode. Wraps the existing PcmPlayer / MicCapture (unchanged) so every mic
// frame and every played chunk is metered on its way through. Nothing here alters what is sent or played.
import { MicCapture, PcmPlayer } from "@/lib/audio";
import { browserDeps, type ClientDeps, type MicLike, type PlayerLike } from "@/lib/voice-client";

const SAMPLE_RATE = 16000;

function rms(pcm: Int16Array): number {
  if (!pcm.length) return 0;
  let sum = 0;
  for (let i = 0; i < pcm.length; i += 2) sum += pcm[i] * pcm[i];
  const r = Math.sqrt(sum / Math.ceil(pcm.length / 2)) / 0x8000;
  return Math.min(1, Math.sqrt(r) * 1.6); // perceptual-ish curve: quiet speech still moves the orb
}

export class LevelMeter {
  private micLevel = 0;
  private micAt = -1e9;
  /** Played chunks on the client clock, so the level follows what is audible now, not what just arrived. */
  private queue: { start: number; end: number; level: number }[] = [];
  private cursor = 0;

  micFrame(buf: ArrayBuffer) {
    this.micLevel = rms(new Int16Array(buf, 0, Math.floor(buf.byteLength / 2)));
    this.micAt = performance.now();
  }

  outChunk(pcm: Int16Array) {
    const now = performance.now();
    const start = Math.max(now + 20, this.cursor);
    const end = start + (pcm.length / SAMPLE_RATE) * 1000;
    this.cursor = end;
    this.queue.push({ start, end, level: rms(pcm) });
    if (this.queue.length > 400) this.queue.splice(0, this.queue.length - 400);
  }

  clearOut() {
    this.queue = [];
    this.cursor = 0;
  }

  /** Microphone level, decaying to 0 when frames stop. */
  mic(now = performance.now()) {
    return now - this.micAt > 160 ? 0 : this.micLevel;
  }

  /** Level of the chunk audible right now (0 between chunks or after a clear). */
  out(now = performance.now()) {
    while (this.queue.length && this.queue[0].end < now) this.queue.shift();
    const c = this.queue[0];
    return c && c.start <= now ? c.level : 0;
  }
}

/**
 * browserDeps() with metering wrappers around the real player and microphone.
 * `holdMic()` returning true withholds microphone frames from the server (still metered locally): the
 * "Noisy place" option uses it while the agent is talking, so street noise cannot interrupt the agent.
 */
export function meteredDeps(meter: LevelMeter, holdMic: () => boolean = () => false): ClientDeps {
  const base = browserDeps();
  return {
    ...base,
    createPlayer: (): PlayerLike => {
      const p = new PcmPlayer(SAMPLE_RATE);
      return {
        play(generation, pcm) {
          p.play(generation, pcm);
          if (!p.muted && p.gate.accept(generation)) meter.outChunk(pcm);
        },
        clear(generation) {
          p.clear(generation);
          meter.clearOut();
        },
        close: () => p.close(),
        resume: () => p.resume(),
      };
    },
    createMic: (): MicLike => {
      const mic = new MicCapture();
      return {
        start: (onFrame) =>
          mic.start((buf) => {
            meter.micFrame(buf);
            if (!holdMic()) onFrame(buf);
          }),
        stop: () => mic.stop(),
      };
    },
  };
}

/** Typed mode has no audio to measure: a speech-like envelope derived only from the voice state (labelled in the UI). */
export function synthesizedLevel(state: string, now: number) {
  if (state === "AGENT_SPEAKING") return 0.35 + 0.45 * Math.abs(Math.sin(now / 95)) * (0.6 + 0.4 * Math.sin(now / 410));
  if (state === "USER_SPEAKING") return 0.3 + 0.4 * Math.abs(Math.sin(now / 80));
  return 0;
}
