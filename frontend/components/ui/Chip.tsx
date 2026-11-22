import type { HTMLAttributes } from "react";
import { cx } from "./cx";
import ui from "./ui.module.css";

export type ChipTone = "plain" | "ok" | "warn" | "bad" | "info" | "mock";

/**
 * Small mono capsule for status and honesty labels. `dot` adds a glowing status dot; `live` makes it pulse.
 * tone="mock" is dashed: use it for anything simulated, so mock vs live is visible at a glance.
 */
export function Chip({
  tone = "plain",
  dot,
  live,
  className,
  children,
  ...rest
}: { tone?: ChipTone; dot?: boolean; live?: boolean } & HTMLAttributes<HTMLSpanElement>) {
  return (
    <span className={cx(ui.chip, ui[`chip_${tone}`], className)} {...rest}>
      {dot && <i className={cx(ui.chipDot, live && ui.chipDotLive)} aria-hidden="true" />}
      {children}
    </span>
  );
}

/** Label/value status chip, e.g. "LLM · Cloudflare". */
export function StatusChip({ label, value, tone = "plain", live, title }: { label: string; value: string; tone?: ChipTone; live?: boolean; title?: string }) {
  return (
    <span className={cx(ui.status, ui[`chip_${tone}`])} title={title}>
      <i className={cx(ui.chipDot, live && ui.chipDotLive)} aria-hidden="true" />
      <span className={ui.statusLabel}>{label}</span>
      <b>{value}</b>
    </span>
  );
}
