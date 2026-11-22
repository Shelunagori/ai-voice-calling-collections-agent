// Readable labels for backend enums. The raw value is still available (title attributes, detail key/values),
// so nothing audited is hidden; this only changes how the value reads at a glance.
import type { ChipTone } from "@/components/ui/Chip";
import { humanize } from "@/lib/format";

type L = [string, ChipTone];

const IDENTITY: Record<string, L> = {
  VERIFIED: ["Verified", "ok"],
  NAME_CONFIRMED: ["Name confirmed", "info"],
  UNVERIFIED: ["Not verified", "plain"],
  FAILED: ["Failed check", "bad"],
  WRONG_PARTY: ["Wrong party", "bad"],
};
const PROMISE: Record<string, L> = {
  CONFIRMED: ["Promise confirmed", "ok"],
  PENDING_CONFIRMATION: ["Awaiting yes", "warn"],
  REJECTED: ["Declined", "bad"],
  NONE: ["No promise", "plain"],
};
const CALL: Record<string, L> = {
  COMPLETED: ["Completed", "plain"],
  DISCONNECTED: ["Disconnected", "plain"],
  FAILED: ["Failed", "bad"],
  TRANSFER_REQUESTED: ["Transfer requested", "info"],
  IN_PROGRESS: ["Live", "ok"],
  ACTIVE: ["Live", "ok"],
};
const END: Record<string, L> = {
  completed_with_promise: ["Completed with promise", "ok"],
  caller_hangup: ["Caller hung up", "plain"],
  transfer: ["Transferred to a human", "info"],
  stop_contact: ["Stop-contact recorded", "warn"],
  error_or_disconnect: ["Error or disconnect", "bad"],
  completed: ["Completed", "plain"],
  in_progress: ["In progress", "ok"],
};
const REASON: Record<string, string> = {
  completed_with_promise: "Completed with promise",
  caller_ended: "Caller ended",
  caller_hangup: "Caller hung up",
};

const pick = (map: Record<string, L>, v: unknown): L => map[String(v)] ?? [humanize(String(v ?? "—")), "plain"];
export const identityLabel = (v: unknown) => pick(IDENTITY, v);
export const promiseLabel = (v: unknown) => pick(PROMISE, v);
export const callLabel = (v: unknown) => pick(CALL, v);
export const endLabel = (v: unknown) => pick(END, v);
export const reasonLabel = (v: unknown) => (v ? (REASON[String(v)] ?? humanize(String(v))) : "—");
export const channelLabel = (v: unknown) => (v === "phone" ? "Phone (PSTN)" : v === "browser" ? "Browser" : humanize(String(v ?? "—")));
export const languageLabel = (v: unknown) => (v === "ja" ? "日本語" : v === "en" ? "English" : String(v ?? "—"));

/** Backend timestamps come without a zone and are UTC. */
export function parseUtc(iso: unknown): Date | null {
  if (!iso) return null;
  const s = String(iso);
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : `${s}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}
export function fmtWhen(iso: unknown) {
  const d = parseUtc(iso);
  return d ? d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
}
export function fmtDuration(a: unknown, b: unknown) {
  const s = parseUtc(a);
  const e = parseUtc(b);
  if (!s || !e) return "—";
  const sec = Math.max(0, Math.round((e.getTime() - s.getTime()) / 1000));
  return sec >= 60 ? `${Math.floor(sec / 60)}m ${String(sec % 60).padStart(2, "0")}s` : `${sec}s`;
}

/** Audit event families, used for colour and filtering in the trail. */
export type Family = "policy" | "identity" | "payment" | "voice" | "session" | "other";
export function familyOf(type: string): Family {
  if (type.startsWith("policy.")) return "policy";
  if (type.startsWith("identity.") || type.startsWith("disclosure.")) return "identity";
  if (type.startsWith("payment.") || type.startsWith("promise.")) return "payment";
  if (type.startsWith("voice.")) return "voice";
  if (type.startsWith("session.") || type.startsWith("call.") || type.startsWith("notification")) return "session";
  return "other";
}
