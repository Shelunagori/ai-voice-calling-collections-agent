import { Chip } from "./Chip";
import ui from "./ui.module.css";

export function SiteFooter() {
  return (
    <footer className={ui.footer}>
      <div className={ui.footerChips}>
        <Chip tone="warn" dot>
          Synthetic identities &amp; accounts only
        </Chip>
        <Chip>Inspired by production work</Chip>
        <Chip>Simulated demo policy · not legal advice</Chip>
        <Chip>Not for contacting real debtors</Chip>
      </div>
    </footer>
  );
}
