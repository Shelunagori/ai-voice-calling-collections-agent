// Start Call: same-origin JSON only, rate-limited, then POST /api/operator/calls on the
// backend with the server-side OPERATOR_TOKEN. Backend allowlist + contact policy decide.
import { NextRequest, NextResponse } from "next/server";
import { proxyStartCall, sameOriginJson } from "@/lib/server/operator";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const NO_STORE = { "Cache-Control": "no-store" };

function clientKey(req: NextRequest): string {
  // Vercel sets x-real-ip / appends to x-forwarded-for; the right-most XFF entry is the proxy-observed client.
  return req.headers.get("x-real-ip") || (req.headers.get("x-forwarded-for") || "").split(",").pop()?.trim() || "local";
}

export async function POST(req: NextRequest) {
  if (!sameOriginJson(req.headers, req.nextUrl.origin)) {
    return NextResponse.json({ ok: false, code: "bad_origin", detail: "Start Call only accepts same-origin JSON requests." }, { status: 403, headers: NO_STORE });
  }
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, code: "invalid_request", detail: "Request body must be JSON." }, { status: 400, headers: NO_STORE });
  }
  const r = await proxyStartCall(body, { client: clientKey(req) });
  return NextResponse.json(r.body, { status: r.status, headers: NO_STORE });
}
