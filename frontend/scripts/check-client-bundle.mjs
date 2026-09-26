// Builds the app with a canary value for the server-only OPERATOR_TOKEN, then fails if it
// appears anywhere the browser can receive it (static JS/CSS chunks, prerendered
// HTML/RSC payloads). Usage: npm run check:bundle
import { spawnSync } from "node:child_process";
import { readdirSync, readFileSync, statSync, existsSync } from "node:fs";
import path from "node:path";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const canaries = {
  OPERATOR_TOKEN: `canary-operator-token-${process.pid}-${Date.now()}`,
};

const build = spawnSync("npx", ["next", "build"], { cwd: root, stdio: "inherit", env: { ...process.env, ...canaries } });
if (build.status !== 0) process.exit(build.status ?? 1);

function walk(dir, out = []) {
  if (!existsSync(dir)) return out;
  for (const n of readdirSync(dir)) {
    const p = path.join(dir, n);
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}
const browserReachable = [
  ...walk(path.join(root, ".next", "static")),
  ...walk(path.join(root, ".next", "server", "app")).filter((p) => /\.(html|rsc|body|meta)$/.test(p)),
];
let leaks = 0;
for (const p of browserReachable) {
  const src = readFileSync(p, "utf8");
  for (const [name, value] of Object.entries(canaries)) {
    if (src.includes(value)) {
      console.error(`LEAK: ${name} value found in ${path.relative(root, p)}`);
      leaks++;
    }
  }
}
console.log(`scanned ${browserReachable.length} browser-reachable build files: ${leaks ? `${leaks} leak(s)` : "no server secrets found"}`);
process.exit(leaks ? 1 : 0);
