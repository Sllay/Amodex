import { neon } from "@neondatabase/serverless";

interface Env {
  DATABASE_URL: string;
  BUILD_ID: string;
}

type RunRow = Record<string, unknown>;
type EventRow = Record<string, unknown>;

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8" };
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}

function uuidv7(): string {
  const bytes = new Uint8Array(16);
  let ts = BigInt(Date.now());
  for (let i = 5; i >= 0; i -= 1) {
    bytes[i] = Number(ts & 0xffn);
    ts >>= 8n;
  }
  crypto.getRandomValues(bytes.subarray(6));
  bytes[6] = (bytes[6] & 0x0f) | 0x70;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function asIso(value: unknown): string | null {
  if (value == null) return null;
  if (value instanceof Date) return value.toISOString();
  return String(value);
}

function mapRun(row: RunRow) {
  return {
    schema_version: 1,
    run_id: String(row.run_id),
    prompt: String(row.prompt),
    state: String(row.state),
    provider: row.provider == null ? null : String(row.provider),
    model: row.model == null ? null : String(row.model),
    result_text: row.result_text == null ? null : String(row.result_text),
    last_error_code: row.last_error_code == null ? null : String(row.last_error_code),
    provider_request_key: row.provider_request_key == null ? null : String(row.provider_request_key),
    provider_request_state: String(row.provider_request_state ?? "NONE"),
    revision: Number(row.revision),
    fencing_token: Number(row.fencing_token),
    lease_owner: row.lease_owner == null ? null : String(row.lease_owner),
    lease_expires_at: asIso(row.lease_expires_at),
    created_at: asIso(row.created_at),
    updated_at: asIso(row.updated_at),
  };
}

function mapEvent(row: EventRow) {
  return {
    schema_version: 1,
    event_id: String(row.event_id),
    run_id: String(row.run_id),
    seq: Number(row.seq),
    kind: String(row.kind),
    payload: row.payload ?? {},
    created_at: asIso(row.created_at),
  };
}

function validRunId(value: string): boolean {
  return UUID_RE.test(value);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/health") {
      return json({ status: "ok" });
    }
    if (request.method === "GET" && url.pathname === "/__deployment") {
      return json({ build_id: env.BUILD_ID, runtime: "cloudflare-ts-neon-edge" });
    }
    if (!env.DATABASE_URL?.startsWith("postgres")) {
      return json({ detail: "database binding unavailable" }, 503);
    }

    const sql = neon(env.DATABASE_URL);

    try {
      if (request.method === "POST" && url.pathname === "/v1/runs") {
        let body: unknown;
        try {
          body = await request.json();
        } catch {
          return json({ detail: "invalid JSON" }, 422);
        }
        if (body == null || typeof body !== "object") {
          return json({ detail: "request body must be an object" }, 422);
        }
        const record = body as Record<string, unknown>;
        const schemaVersion = record.schema_version ?? 1;
        const prompt = record.prompt;
        if (schemaVersion !== 1 || typeof prompt !== "string" || prompt.length < 1 || prompt.length > 20_000) {
          return json({ detail: "invalid run request" }, 422);
        }

        const runId = uuidv7();
        const eventId = uuidv7();
        const rows = await sql`
          WITH new_run AS (
            INSERT INTO amodex_runs (run_id, prompt, state, event_seq)
            VALUES (${runId}::uuid, ${prompt}, 'QUEUED', 1)
            RETURNING *
          ), new_event AS (
            INSERT INTO amodex_run_events (event_id, run_id, seq, kind, payload)
            SELECT ${eventId}::uuid, run_id, 1, 'RUN_CREATED', '{}'::jsonb
            FROM new_run
          )
          SELECT * FROM new_run
        `;
        return json(mapRun(rows[0] as RunRow), 201);
      }

      const eventMatch = url.pathname.match(/^\/v1\/runs\/([^/]+)\/events$/);
      if (request.method === "GET" && eventMatch) {
        const runId = eventMatch[1];
        if (!validRunId(runId)) return json({ detail: "run not found" }, 404);
        const exists = await sql`SELECT run_id FROM amodex_runs WHERE run_id = ${runId}::uuid`;
        if (exists.length === 0) return json({ detail: "run not found" }, 404);
        const rows = await sql`
          SELECT event_id, run_id, seq, kind, payload, created_at
          FROM amodex_run_events
          WHERE run_id = ${runId}::uuid
          ORDER BY seq ASC
        `;
        return json(rows.map((row) => mapEvent(row as EventRow)));
      }

      const runMatch = url.pathname.match(/^\/v1\/runs\/([^/]+)$/);
      if (request.method === "GET" && runMatch) {
        const runId = runMatch[1];
        if (!validRunId(runId)) return json({ detail: "run not found" }, 404);
        const rows = await sql`SELECT * FROM amodex_runs WHERE run_id = ${runId}::uuid`;
        if (rows.length === 0) return json({ detail: "run not found" }, 404);
        return json(mapRun(rows[0] as RunRow));
      }

      return json({ detail: "not found" }, 404);
    } catch (error) {
      console.error("stage1 edge adapter database error", error instanceof Error ? error.message : "unknown");
      return json({ detail: "internal server error" }, 500);
    }
  },
};
