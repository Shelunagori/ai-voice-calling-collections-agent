// Same-origin proxy for session/audit detail. OPERATOR_TOKEN is added here, on the server.
import { NextRequest, NextResponse } from "next/server";
import { proxySessionDetail } from "@/lib/server/operator";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const r = await proxySessionDetail(id);
  return NextResponse.json(r.body, { status: r.status, headers: { "Cache-Control": "no-store" } });
}
