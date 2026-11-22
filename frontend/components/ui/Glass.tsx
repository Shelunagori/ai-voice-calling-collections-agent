import type { HTMLAttributes } from "react";
import { cx } from "./cx";
import ui from "./ui.module.css";

type Tag = "div" | "section" | "article" | "aside" | "li";

/** Frosted glass surface. `glow` adds a soft accent glow on hover; `pad={false}` for edge-to-edge content. */
export function Glass({
  as: El = "div",
  glow,
  pad = true,
  className,
  ...rest
}: { as?: Tag; glow?: boolean; pad?: boolean } & HTMLAttributes<HTMLElement>) {
  return <El className={cx(ui.glass, pad && ui.glassPad, glow && ui.glassGlow, className)} {...rest} />;
}
