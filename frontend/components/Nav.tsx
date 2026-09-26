"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/demo", label: "Voice demo" },
  { href: "/architecture", label: "Architecture" },
  { href: "/evaluation", label: "Evaluation" },
  { href: "/sessions", label: "Sessions & audit" },
  { href: "/telephony", label: "Telephony" },
];

export function Nav() {
  const path = usePathname();
  return (
    <header className="topbar">
      <Link href="/" className="brand">
        AI Voice Collections Agent<small>portfolio POC</small>
      </Link>
      <nav className="nav">
        {LINKS.map((l) => (
          <Link key={l.href} href={l.href} className={path?.startsWith(l.href) ? "active" : ""}>
            {l.label}
          </Link>
        ))}
      </nav>
      <span className="spacer" />
      <span className="pill warn">Synthetic data only</span>
    </header>
  );
}
