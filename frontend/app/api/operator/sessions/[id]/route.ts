// Same-origin proxy for the protected session/audit detail. The backend operator token is
// added here, on the server, only for a signed-in operator (see lib/server/operator.ts).
import { NextRequest, NextResponse } from "next/server";
import { OPERATOR_COOKIE, proxySessionDetail } from "@/lib/server/operator";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const r = await proxySessionDetail(id, { cookie: req.cookies.get(OPERATOR_COOKIE)?.value });
  return NextResponse.json(r.body, { status: r.status, headers: { "Cache-Control": "no-store" } });
}
