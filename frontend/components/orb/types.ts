// Voice states the backend lifecycle emits (backend/app/voice/lifecycle.py). The orb accepts any string and
// falls back to IDLE for anything it doesn't know, so a new backend state can never crash the visual.
export type OrbState =
  | "IDLE"
  | "LISTENING"
  | "USER_SPEAKING"
  | "PROCESSING"
  | "AGENT_SPEAKING"
  | "INTERRUPTED"
  | "TRANSFER_REQUESTED"
  | "ENDED"
  | "ERROR";

/** What the orb reads every animation frame: the voice state plus a 0..1 audio level. */
export type OrbSignal = { state: OrbState | (string & {}); amp: number };

/** Coarse tone used to tint the aurora and indicators (set as data-voice on <html>). */
export const ORB_TONE: Record<OrbState, string> = {
  IDLE: "listen",
  LISTENING: "listen",
  USER_SPEAKING: "caller",
  PROCESSING: "think",
  AGENT_SPEAKING: "agent",
  INTERRUPTED: "barge",
  TRANSFER_REQUESTED: "think",
  ENDED: "ended",
  ERROR: "barge",
};
export const toneOf = (state: string) => ORB_TONE[state as OrbState] ?? "listen";
