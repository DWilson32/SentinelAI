import { NextResponse } from "next/server";

// Server-only proxy for acknowledging an alert.
//
// Acknowledging writes, so the backend requires the admin key, and the key
// cannot travel to the browser. This route runs on the server, attaches it, and
// forwards the call — the same arrangement as the sync button.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_BASE =
  process.env.SENTINEL_API_BASE_URL ??
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  "http://127.0.0.1:8000/api";

export async function POST(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const adminKey = process.env.SENTINEL_ADMIN_KEY;

  if (!adminKey) {
    return NextResponse.json(
      { detail: "Acknowledging is unavailable: SENTINEL_ADMIN_KEY is not configured." },
      { status: 503 },
    );
  }

  try {
    const upstream = await fetch(`${API_BASE}/alerts/${encodeURIComponent(id)}/acknowledge`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": adminKey,
      },
      cache: "no-store",
    });

    const body = await upstream.text();

    return new NextResponse(body, {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("Content-Type") ?? "application/json",
      },
    });
  } catch {
    return NextResponse.json(
      { detail: "Could not reach the SentinelAI API." },
      { status: 502 },
    );
  }
}
