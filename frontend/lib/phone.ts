// Shared (client + server) phone-number helpers. No configuration or secrets here.
export const E164 = /^\+[1-9]\d{7,14}$/;

/** "+81 90-1234-5678" -> "+819012345678"; returns null when it is not E.164. */
export function normaliseE164(raw: string): string | null {
  const v = raw.replace(/[\s()-]/g, "");
  return E164.test(v) ? v : null;
}

/** For display only: keep the country code and last 3 digits. */
export function maskPhone(e164: string): string {
  return e164.length > 6 ? `${e164.slice(0, 3)}${"•".repeat(e164.length - 6)}${e164.slice(-3)}` : "•••";
}
