"use client";

import { AnimatePresence, m } from "framer-motion";
import { toneOf } from "../orb/types";
import { cx } from "./cx";
import ui from "./ui.module.css";

export const STATE_LABEL: Record<string, string> = {
  IDLE: "Ready",
  LISTENING: "Listening",
  USER_SPEAKING: "Caller speaking",
  PROCESSING: "Thinking",
  AGENT_SPEAKING: "Agent speaking",
  INTERRUPTED: "Barge-in",
  TRANSFER_REQUESTED: "Transfer requested",
  ENDED: "Call ended",
  ERROR: "Error",
};

/** Glowing voice-state pill: five bars that move differently per state; the label cross-fades in one grid cell. */
export function StateIndicator({ state, big, label }: { state: string; big?: boolean; label?: string }) {
  const text = label ?? STATE_LABEL[state] ?? state;
  return (
    <div className={cx(ui.indicator, big && ui.indicatorBig)} data-tone={toneOf(state)} role="status" aria-live="polite">
      <span className={ui.eq} aria-hidden="true">
        <i />
        <i />
        <i />
        <i />
        <i />
      </span>
      <span className={ui.indLabel}>
        <AnimatePresence initial={false}>
          <m.span
            key={text}
            initial={{ opacity: 0, y: 8, filter: "blur(4px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            exit={{ opacity: 0, y: -8, filter: "blur(4px)" }}
            transition={{ duration: 0.25 }}
          >
            {text}
          </m.span>
        </AnimatePresence>
      </span>
    </div>
  );
}
