"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { cx } from "../ui/cx";
import { usePrefersReducedMotion, useTheme } from "../ui/hooks";
import ui from "../ui/ui.module.css";
import { toneOf, type OrbSignal } from "./types";

// three.js lives in its own chunk, fetched after first paint. Until it is ready, and wherever WebGL is unavailable,
// a CSS orb stands in; it follows the same voice state through data-tone.
const Orb = dynamic(() => import("./Orb"), { ssr: false });

/**
 * The voice orb. `signal` is read every animation frame (no React re-render per frame), so it can be backed by
 * live mic/playback levels or by anything else that knows the voice state.
 * `tintPage` mirrors the state onto <html data-voice> so the aurora background follows the call.
 */
export function VoiceOrb({
  signal,
  className,
  tintPage = false,
}: {
  signal: RefObject<() => OrbSignal>;
  className?: string;
  tintPage?: boolean;
}) {
  const [theme] = useTheme();
  const reduced = usePrefersReducedMotion();
  const [ready, setReady] = useState<boolean | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const onReady = useCallback((ok: boolean) => setReady(ok), []);

  // Cheap per-frame state mirror: writes attributes only when the tone changes.
  useEffect(() => {
    let raf = 0;
    let prev = "";
    const loop = () => {
      const tone = toneOf(signal.current?.().state ?? "IDLE");
      if (tone !== prev) {
        prev = tone;
        box.current?.setAttribute("data-tone", tone);
        if (tintPage) document.documentElement.setAttribute("data-voice", tone);
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      if (tintPage) document.documentElement.removeAttribute("data-voice");
    };
  }, [signal, tintPage]);

  return (
    <div ref={box} className={cx(ui.orbBox, className)} data-tone="listen" data-webgl={ready === true ? "on" : "off"}>
      <div className={cx(ui.cssOrb, ready === true && ui.cssOrbHidden)} aria-hidden="true" />
      <Orb signal={signal} light={theme === "light"} reduced={reduced} onReady={onReady} />
    </div>
  );
}
