"use client";

import { cx } from "@/components/ui/cx";
import ui from "@/components/ui/ui.module.css";
import d from "./demo.module.css";

export function Composer({
  draft,
  onDraft,
  onSend,
  voice,
  agentSpeaking,
  suggestions,
}: {
  draft: string;
  onDraft: (v: string) => void;
  onSend: (text: string) => void;
  voice: boolean;
  agentSpeaking: boolean;
  suggestions: string[];
}) {
  return (
    <div className={d.composer}>
      <form
        className={d.composeRow}
        onSubmit={(e) => {
          e.preventDefault();
          onSend(draft);
        }}
      >
        <input
          type="text"
          value={draft}
          maxLength={500}
          id="caller-input"
          aria-label="Caller says"
          placeholder={voice ? "Speak, or type to barge in…" : "Type what the caller says (typing while the agent speaks = barge-in)"}
          onChange={(e) => onDraft(e.target.value)}
          className={d.input}
        />
        <button className={cx(ui.ghostBtn, d.sendBtn, agentSpeaking && d.sendBarge)} type="submit" disabled={!draft.trim()}>
          {agentSpeaking ? "Barge in" : "Send"}
        </button>
      </form>
      {suggestions.length > 0 && (
        <div className={d.suggest} aria-label="Suggested caller lines">
          {suggestions.map((line) => (
            <button key={line} type="button" className={d.suggestChip} onClick={() => onSend(line)} title="Suggested caller line">
              {line.replace(/^\[interrupt\]\s*/, "↯ ")}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Honest note about where the orb's level comes from. */
export function CallControlsHint({ voice }: { voice: boolean }) {
  return (
    <p className={ui.fine} style={{ textAlign: "center" }}>
      {voice ? "Orb level: live microphone and agent playback." : "Typed mode · orb level is synthesized from the live voice state."}
    </p>
  );
}
