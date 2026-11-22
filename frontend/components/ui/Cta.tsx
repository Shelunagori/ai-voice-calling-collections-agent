import Link from "next/link";
import type { AnchorHTMLAttributes, ButtonHTMLAttributes } from "react";
import { cx } from "./cx";
import ui from "./ui.module.css";

/** The glowing primary call-to-action (conic glow ring). */
export function Cta({ className, children, ...rest }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button className={cx(ui.cta, className)} {...rest}>
      <span className={ui.ctaGlow} aria-hidden="true" />
      {children}
    </button>
  );
}

export function MicIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <path d="M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3Zm-7 9a7 7 0 0 0 14 0M12 19v2" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

/** Same glowing CTA, as a navigation link. */
export function CtaLink({ href, className, children, ...rest }: { href: string } & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href">) {
  return (
    <Link href={href} className={cx(ui.cta, className)} {...rest}>
      <span className={ui.ctaGlow} aria-hidden="true" />
      {children}
    </Link>
  );
}
