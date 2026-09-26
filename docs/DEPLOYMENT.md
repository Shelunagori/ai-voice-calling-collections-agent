# Deployment

**The live instance was deployed by the repository owner; the assistant has not deployed anything.** Everything below is prepared and locally verified
(clean-venv install + production start command against PostgreSQL, `/ready`, SIGTERM graceful shutdown,
Next.js standalone server). Docker images could not be built in the authoring sandbox (Docker Hub blocked);
the CI `docker` job builds and smoke-tests them.

## Backend on Railway (+ PostgreSQL)

1. Railway → New Project → Deploy from GitHub repo → select this repo. Set the service **Root Directory** to
   `backend` (uses `backend/Dockerfile` and `backend/railway.json`).
2. Add a **PostgreSQL** service. On the backend service set `DATABASE_URL=${{Postgres.DATABASE_URL}}`
   (`Postgres` is the database service's name in Railway; `postgres://` URLs are normalised to
   `postgresql+asyncpg://`, and `sslmode=` is translated for asyncpg).
3. Variables (minimum for a mock-provider public demo):
   `APP_ENV=production`, `FRONTEND_URL=https://<frontend-domain>`.
   Railway provides `PORT`; the container listens on it. Migrations run on start (`AUTO_MIGRATE=true`);
   seed data is upserted idempotently, and existing flags are not reset.

**Durable persistence is required in production.** With `APP_ENV=production` or `staging`, or when Railway
environment variables are present, the backend **refuses to start** unless `DATABASE_URL` points to PostgreSQL.
A failed start fails the `/ready` health check, so Railway keeps the previous deployment serving.

The SQLite fallback is for local development and tests only. `ALLOW_EPHEMERAL_DATABASE=true` overrides the
check for emergencies; it is logged CRITICAL, and all data is lost on the next redeploy.

`/ready` shows `dependencies.database = {status, mode, backend, durable, revision}` and never the URL, host
or credentials.

With PostgreSQL attached, sessions, transcripts, the audit trail, latency, promises, debtor/account flags and
contact-point suppression survive restarts and redeploys. Live calls in progress, pending Twilio call contexts
and rate-limit counters are in-process and do not (D6).

### Moving an existing deployment to PostgreSQL

1. In the Railway project: **+ New → Database → PostgreSQL**, and keep the service name `Postgres`.
2. On the backend service → **Variables → New Variable**: name `DATABASE_URL`, value
   `${{Postgres.DATABASE_URL}}` (Railway autocompletes the reference). Delete any empty `DATABASE_URL`.
3. Keep `APP_ENV=production` and `AUTO_MIGRATE=true`. Do not set `ALLOW_EPHEMERAL_DATABASE`. No other new
   variables are needed.
4. Deploy the backend. Order: Postgres service first, then the variable, then the backend deploy. The
   frontend needs no change.
5. Check migrations:
   - `/ready` shows `"backend": "postgresql", "durable": true, "revision": "0002"` and `"mode":
     "alembic_upgrade_head"`;
   - the deploy log's `startup` line shows `db_backend=postgresql` and `db_revision=0002`;
   - optionally, locally with the public URL: `DATABASE_URL=<DATABASE_PUBLIC_URL> alembic current` → `0002 (head)`.
6. Check persistence:
   1. Run a browser demo session (for example scenario E, "Please don't call me again").
   2. Note the session in **Sessions**.
   3. Trigger **Redeploy** of the backend.
   4. After it is healthy, the session, its audit trail and E's "not eligible" flag are still there.
7. Data recorded before this change was in the old container's SQLite file and cannot be recovered.
4. Generate a public domain. Health check: `/ready` (configured in `railway.json`).
5. Keep **1 replica** (sessions are stateful in-process; see DECISIONS.md D6). `drainingSeconds: 20` +
   uvicorn `--timeout-graceful-shutdown 20` let live sessions end cleanly on redeploy.
6. Optional providers: add the Cloudflare / Cartesia / Twilio variables from `.env.example`. For Twilio set
   `TWILIO_WEBHOOK_BASE_URL=https://<backend-domain>`.

Railway terminates TLS; WebSockets work over `wss://<backend-domain>/ws/session`. Rate limiting keys on the
right-most `X-Forwarded-For` entry (appended by Railway's proxy) when `TRUST_PROXY_HEADERS=true` (default);
set it to `false` wherever the backend port is reachable without that proxy.

## Frontend

Option A — Railway: second service, Root Directory `frontend`, build variable
`NEXT_PUBLIC_API_BASE_URL=https://<backend-domain>` (build-time, public). Uses `frontend/Dockerfile`
(standalone output) on `PORT`.

Option B — Vercel: import the repo, root `frontend`, env `NEXT_PUBLIC_API_BASE_URL`.

Operator features (either option): set the **server-only** variable `OPERATOR_TOKEN` (same value as the
backend) on the frontend service. It is read only by the Next.js route handlers under `app/api/operator/*`
and is never `NEXT_PUBLIC_`, so it is not inlined into client JavaScript (`npm run check:bundle` builds with
a canary value and scans the browser-reachable output).

Then set the backend `FRONTEND_URL` to the frontend origin (CORS + WebSocket origin check in production).

## Current production values

Railway (backend service variables):

```env
APP_ENV=production
FRONTEND_URL=https://ai-voice-calling-collections-agent.vercel.app   # exact origin, no wildcard
DATABASE_URL=${{Postgres.DATABASE_URL}}
AUTO_MIGRATE=true
POLICY_COUNTRY=JP            # simulated Asia/Tokyo calling window for outbound demo calls
POLICY_TIMEZONE=Asia/Tokyo
POLICY_CALLING_START_HOUR=8
POLICY_CALLING_END_HOUR=21
# POLICY_COUNTRY=            # (empty) instead, to test outbound calls outside Tokyo hours
```

Vercel (frontend):

```env
# build-time, public
NEXT_PUBLIC_API_BASE_URL=https://ai-voice-calling-collections-agent-production.up.railway.app
# server-only (do NOT prefix with NEXT_PUBLIC_)
OPERATOR_TOKEN=<same value as Railway OPERATOR_TOKEN>
# optional server-only backend URL; defaults to NEXT_PUBLIC_API_BASE_URL
# BACKEND_API_BASE_URL=https://ai-voice-calling-collections-agent-production.up.railway.app
```

Operator flow (no browser login, reviewer-friendly):

```
browser --GET  /api/operator/sessions/:id (same origin)--> Next.js route --Bearer OPERATOR_TOKEN--> Railway GET  /api/sessions/:id
browser --POST /api/operator/calls        (same origin)--> Next.js route --Bearer OPERATOR_TOKEN--> Railway POST /api/operator/calls --> Twilio
```

- Only these two backend routes are proxied; session ids must be UUIDs; Start Call forwards only `to`,
  `scenario`, `language`. Browser headers (including any `Authorization`) are never forwarded; backend
  error bodies are replaced by fixed messages.
- Start Call accepts only same-origin `application/json` requests (blocks cross-site forms / CSRF), rejects
  a repeat call to the same number within 30 s and allows 3 calls per client per 10 minutes (per server
  instance). Railway still enforces the allowlist (`DEMO_CALL_ALLOWED_NUMBERS`), its own hourly limit, the
  calling window, attempt limit and stop-contact; Twilio webhooks stay signed and the media stream keeps
  its per-call HMAC.
- **Trade-off (accepted for this POC):** anyone who can open the console can read phone-session audit
  detail and start calls to allowlisted numbers. The token itself never leaves the server, and the Railway
  endpoints still reject requests without it. Put Vercel Deployment Protection (or an auth layer) in front
  of the console before using it with anything but your own test numbers.

The browser builds the WebSocket URL from this value (`https` → `wss`, same host), never from the Vercel
origin: `wss://ai-voice-calling-collections-agent-production.up.railway.app/ws/session?...`.

## Post-deploy checks

```bash
curl https://<backend>/ready          # database ok, providers listed
curl https://<backend>/api/capabilities
curl -X POST https://<backend>/api/eval/run   # expect passed == total
```
Then open the frontend, run scenario A, and open the session's audit trail. For a phone session: open it
from Sessions (no prompt), confirm the detail loads, and check in DevTools → Network that the request goes
to `/api/operator/sessions/<id>` on the Vercel origin with no `Authorization` header. On **Telephony**, start
a call to an allowlisted number and use **Open Session**.

## Environment reference

See [`.env.example`](../.env.example). Secrets (`CLOUDFLARE_API_TOKEN`, `CARTESIA_API_KEY`,
`TWILIO_AUTH_TOKEN`, `OPERATOR_TOKEN`) are backend variables. The only secret the frontend holds is the
server-only `OPERATOR_TOKEN` used by its route handlers; never expose it through a `NEXT_PUBLIC_` variable.
