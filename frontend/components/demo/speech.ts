// Local stand-in voice for the agent when the server TTS is the silent mock (labelled in the UI).
// Picks a natural voice explicitly: with no voice set, Chrome on macOS falls back to the first voice for the
// language, which is often a novelty voice ("Albert", "Bad News", "Bubbles", "Zarvox", …).

const PREFERRED: Record<"en" | "ja", RegExp[]> = {
  en: [/Google US English/i, /Microsoft (Aria|Jenny|Ava|Emma).*(Natural|Online)/i, /^Samantha/i, /^Karen/i, /^Moira/i, /^Tessa/i, /^Daniel/i, /Google UK English Female/i],
  ja: [/Google 日本語/i, /Microsoft Nanami.*(Natural|Online)/i, /^Kyoko/i, /^O-?ren/i],
};
// macOS novelty / character voices: never use these for the agent.
const NOVELTY =
  /^(Albert|Bad News|Bahh|Bells|Boing|Bubbles|Cellos|Deranged|Fred|Good News|Hysterical|Jester|Junior|Organ|Pipe Organ|Ralph|Superstar|Trinoids|Whisper|Wobble|Zarvox|Eddy|Flo|Grandma|Grandpa|Reed|Rocko|Sandy|Shelley)\b/i;

function supported() {
  return typeof window !== "undefined" && "speechSynthesis" in window && typeof SpeechSynthesisUtterance !== "undefined";
}

export function pickVoice(lang: "en" | "ja"): SpeechSynthesisVoice | null {
  if (!supported()) return null;
  const all = window.speechSynthesis.getVoices();
  const prefix = lang === "ja" ? "ja" : "en";
  const candidates = all.filter((v) => v.lang.toLowerCase().startsWith(prefix) && !NOVELTY.test(v.name));
  for (const re of PREFERRED[lang]) {
    const v = candidates.find((c) => re.test(c.name));
    if (v) return v;
  }
  // otherwise any non-novelty voice for the language, preferring the user's region (e.g. en-US for "en")
  const region = lang === "ja" ? "ja-jp" : "en-us";
  return candidates.find((c) => c.lang.toLowerCase() === region) ?? candidates[0] ?? null;
}

/** Voices load asynchronously in Chrome; call once early so they are ready by the first agent turn. */
export function warmVoices() {
  if (!supported()) return;
  try {
    window.speechSynthesis.getVoices();
  } catch {
    /* ignore */
  }
}

export function speak(text: string, lang: "en" | "ja") {
  if (!supported()) return;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = lang === "ja" ? "ja-JP" : "en-US";
  const v = pickVoice(lang);
  if (v) u.voice = v;
  u.rate = 1;
  u.pitch = 1;
  window.speechSynthesis.speak(u);
}

export function stopSpeaking() {
  if (!supported()) return;
  try {
    window.speechSynthesis.cancel();
  } catch {
    /* ignore */
  }
}
