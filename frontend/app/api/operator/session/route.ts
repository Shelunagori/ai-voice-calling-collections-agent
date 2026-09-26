// Operator console sign-in. POST {password} -> HttpOnly signed cookie; DELETE -> sign out;
// GET -> {configured, signed_in}. Neither the password nor OPERATOR_TOKEN is ever returned.
import { NextRequest, NextResponse } from "next/server";
import {
  OPERATOR_COOKIE,
  SESSION_TTL_S,
  consoleConfigured,
  issueSession,
  loginAllowed,
  passwordMatches,
  serverConfig,
  verifySession,
} from "@/lib/server/operator";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const NO_STORE = { "Cache-Control": "no-store" };

function sameOrigin(req: NextRequest): boolean {
  const origin = req.headers.get("origin");
  return !origin || origin === req.nextUrl.origin;
}

const cookieOpts = {
  httpOnly: true,
  secure: process.env.NODE_ENV === "production",
  sameSite: "strict" as const,
  path: "/api/operator",
};

export async function GET(req: NextRequest) {
  const cfg = serverConfig();
  return NextResponse.json(
    { configured: consoleConfigured(cfg), signed_in: consoleConfigured(cfg) && verifySession(req.cookies.get(OPERATOR_COOKIE)?.value, cfg.token) },
    { headers: NO_STORE },
  );
}

export async function POST(req: NextRequest) {
  if (!sameOrigin(req)) return NextResponse.json({ ok: false, code: "bad_origin" }, { status: 403, headers: NO_STORE });
  const cfg = serverConfig();
  if (!consoleConfigured(cfg))
    return NextResponse.json({ ok: false, code: "operator_not_configured", detail: "Operator sign-in is not configured on this deployment." }, { status: 503, headers: NO_STORE });
  const ip = (req.headers.get("x-forwarded-for") || "").split(",").pop()?.trim() || "local";
  if (!loginAllowed(ip)) return NextResponse.json({ ok: false, code: "rate_limited", detail: "Too many attempts; wait a minute." }, { status: 429, headers: NO_STORE });
  let password: unknown;
  try {
    password = ((await req.json()) as { password?: unknown }).password;
  } catch {
    password = undefined;
  }
  if (!passwordMatches(password, cfg.password))
    return NextResponse.json({ ok: false, code: "invalid_credentials", detail: "Wrong operator password." }, { status: 401, headers: NO_STORE });
  const res = NextResponse.json({ ok: true, signed_in: true }, { headers: NO_STORE });
  res.cookies.set(OPERATOR_COOKIE, issueSession(cfg.token), { ...cookieOpts, maxAge: SESSION_TTL_S });
  return res;
}

export async function DELETE(req: NextRequest) {
  if (!sameOrigin(req)) return NextResponse.json({ ok: false, code: "bad_origin" }, { status: 403, headers: NO_STORE });
  const res = NextResponse.json({ ok: true, signed_in: false }, { headers: NO_STORE });
  res.cookies.set(OPERATOR_COOKIE, "", { ...cookieOpts, maxAge: 0 });
  return res;
}
