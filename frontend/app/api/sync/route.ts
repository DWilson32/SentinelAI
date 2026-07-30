import { NextResponse } from "next/server";

// Server-only proxy for the admin ingest endpoint.
//
// The dashboard's "Sync live news" button runs in the browser, so it cannot
// hold the admin key — anything shipped to the client is public. This route
// runs on the server, attaches the key from a non-public env var, and forwards
// the call to the backend.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_BASE =
  process.env.SENTINEL_API_BASE_URL ??
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  "http://127.0.0.1:8000/api";

export async function POST() {
  const adminKey = process.env.SENTINEL_ADMIN_KEY;

  if (!adminKey) {
    return NextResponse.json(
      { detail: "Sync is unavailable: SENTINEL_ADMIN_KEY is not configured." },
      { status: 503 },
    );
  }

  try {
    const upstream = await fetch(`${API_BASE}/incidents/ingest/real`, {
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
      { detail: "Sync failed: could not reach the SentinelAI API." },
      { status: 502 },
    );
  }
}
