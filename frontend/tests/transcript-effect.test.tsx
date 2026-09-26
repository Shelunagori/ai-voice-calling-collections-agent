// @vitest-environment jsdom
/**
 * Root cause of the production crash ("This page couldn't load"): in Chrome >= 14x,
 * Element.scrollIntoView() returns a Promise. TranscriptPanel's effect was written as an
 * expression-bodied arrow, so it *returned* that Promise; React stores an effect's return
 * value as its cleanup and calls it on the next run -> "TypeError: i is not a function"
 * during commit, which unmounts the whole route.
 */
import { act, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TranscriptPanel } from "../components/console/Panels";

describe("TranscriptPanel autoscroll effect", () => {
  it("survives scrollIntoView returning a Promise (Chrome 14x behaviour)", () => {
    const spy = vi.fn(() => Promise.resolve());
    Element.prototype.scrollIntoView = spy as unknown as Element["scrollIntoView"];
    const errors: unknown[] = [];
    const onErr = (e: ErrorEvent) => errors.push(e.error);
    window.addEventListener("error", onErr);
    const item = (k: string) => ({ key: k, speaker: "agent" as const, text: "Hello" });
    const { rerender } = render(<TranscriptPanel items={[item("a0")]} partial="" lang="en" />);
    expect(() =>
      act(() => {
        rerender(<TranscriptPanel items={[item("a0"), item("a1")]} partial="" lang="en" />);
      }),
    ).not.toThrow();
    window.removeEventListener("error", onErr);
    expect(errors).toEqual([]);
    expect(spy).toHaveBeenCalledTimes(2);
  });
});

describe("effect return-value guard", () => {
  it("no component uses an expression-bodied useEffect (its value would become a cleanup)", async () => {
    const fs = await import("node:fs");
    const path = await import("node:path");
    const roots = ["app", "components"].map((d) => path.resolve(__dirname, "..", d));
    const files: string[] = [];
    const walk = (d: string) =>
      fs.readdirSync(d, { withFileTypes: true }).forEach((e) => {
        const p = path.join(d, e.name);
        if (e.isDirectory()) walk(p);
        else if (/\.tsx?$/.test(e.name)) files.push(p);
      });
    roots.forEach(walk);
    const bad = files.filter((f) => /useEffect\(\s*\(\)\s*=>\s*(?![{(\s])/.test(fs.readFileSync(f, "utf8")));
    expect(bad).toEqual([]);
  });
});
