import { neon } from "@neondatabase/serverless";

async function sqlClient(env) {
  if (!env.DATABASE_URL) throw new Error("DATABASE_URL missing");
  const sql = neon(env.DATABASE_URL);
  await sql`
    CREATE TABLE IF NOT EXISTS amodex_p006b_events (
      marker TEXT PRIMARY KEY,
      writer_build TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
  `;
  return sql;
}

async function providerEgress() {
  try {
    const response = await fetch("https://generativelanguage.googleapis.com/v1beta/models", {
      method: "GET",
      redirect: "manual"
    });
    return response.status >= 200 && response.status < 500;
  } catch {
    return false;
  }
}

async function hashPrefix(value) {
  const bytes = new TextEncoder().encode(value || "");
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .slice(0, 8)
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function writeEvent(env, marker) {
  const sql = await sqlClient(env);
  await sql`
    INSERT INTO amodex_p006b_events (marker, writer_build)
    VALUES (${marker}, ${env.BUILD_ID || "unknown"})
    ON CONFLICT (marker) DO NOTHING
  `;
  const rows = await sql`
    SELECT marker, writer_build, created_at
    FROM amodex_p006b_events
    WHERE marker = ${marker}
  `;
  return rows[0] || null;
}

async function readEvent(env, marker) {
  const sql = await sqlClient(env);
  const rows = await sql`
    SELECT marker, writer_build, created_at
    FROM amodex_p006b_events
    WHERE marker = ${marker}
  `;
  return rows[0] || null;
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" }
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/health") {
      let database = false;
      try {
        const sql = await sqlClient(env);
        const rows = await sql`SELECT 1 AS ok`;
        database = rows?.[0]?.ok === 1;
      } catch {
        database = false;
      }

      const secretInjected = Boolean(env.AMODEX_P0_06B_SECRET);
      const egress = await providerEgress();
      return json({
        ok: database && secretInjected && egress,
        database,
        secret_injected: secretInjected,
        secret_fingerprint: secretInjected ? await hashPrefix(env.AMODEX_P0_06B_SECRET) : null,
        provider_egress: egress,
        build_id: env.BUILD_ID || null
      }, database && secretInjected && egress ? 200 : 503);
    }

    if (request.method === "POST" && url.pathname === "/event") {
      const body = await request.json().catch(() => ({}));
      const marker = typeof body.marker === "string" ? body.marker : "";
      if (!marker || marker.length > 200) return json({ error: "invalid marker" }, 400);
      return json({ event: await writeEvent(env, marker) });
    }

    if (request.method === "GET" && url.pathname === "/event") {
      const marker = url.searchParams.get("marker") || "";
      if (!marker) return json({ error: "marker required" }, 400);
      const event = await readEvent(env, marker);
      return event ? json({ event }) : json({ event: null }, 404);
    }

    return json({ error: "not found" }, 404);
  },

  async scheduled(_controller, env, ctx) {
    const marker = `cron:${env.SCHEDULE_MARKER || "missing"}`;
    ctx.waitUntil(writeEvent(env, marker));
  }
};
