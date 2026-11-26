"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useId, useState } from "react";
import { Chip } from "./ui/Chip";
import { cx } from "./ui/cx";
import { ThemeToggle } from "./ui/ThemeToggle";
import ui from "./ui/ui.module.css";

const LINKS = [
  { href: "/demo", label: "Voice demo" },
  { href: "/architecture", label: "Architecture" },
  { href: "/evaluation", label: "Evaluation" },
  { href: "/model-training", label: "Model training" },
  { href: "/sessions", label: "Sessions & audit" },
  { href: "/telephony", label: "Telephony" },
];

export function Nav() {
  const path = usePathname();
  // The menu is "open for" a path, so navigating anywhere closes it without an effect.
  const [openAt, setOpenAt] = useState<string | null>(null);
  const open = openAt === path;
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpenAt(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const links = LINKS.map((l) => {
    const active = !!path?.startsWith(l.href);
    return (
      <Link key={l.href} href={l.href} className={cx(ui.navLink, active && ui.navLinkActive)} aria-current={active ? "page" : undefined}>
        {l.label}
      </Link>
    );
  });

  return (
    <header className={cx(ui.header, open && ui.headerOpen)}>
      <div className={ui.headerRow}>
        <Link href="/" className={ui.brand}>
          <span className={ui.brandOrb} aria-hidden="true" />
          <span className={ui.brandText}>
            AI Voice Collections Agent
            {/* Compact honesty line, shown when the header pills don't fit (tablet / mobile). */}
            <small className={ui.brandSub}>
              <span className={ui.brandSubWarn}>Synthetic data only</span>
              <span className={ui.brandSubSep}> · </span>
              <span>inspired by production work</span>
            </small>
          </span>
        </Link>
        <nav className={ui.navLinks} aria-label="Primary">
          {links}
        </nav>
        <span className={ui.headerPills}>
          <Chip tone="warn" dot>
            Synthetic data only
          </Chip>
          <Chip>Inspired by production work</Chip>
        </span>
        <ThemeToggle />
        <button
          type="button"
          className={cx(ui.iconBtn, ui.menuBtn)}
          aria-expanded={open}
          aria-controls={menuId}
          aria-label={open ? "Close menu" : "Open menu"}
          onClick={() => setOpenAt(open ? null : path)}
        >
          <span className={ui.burger} aria-hidden="true" />
        </button>
      </div>
      <nav id={menuId} className={ui.menu} aria-label="Primary (mobile)" hidden={!open}>
        {links}
      </nav>
    </header>
  );
}
