# Deployment

**The live instance was deployed by the repository owner; the assistant has not deployed anything.** Everything below is prepared and locally verified
(clean-venv install + production start command against PostgreSQL, `/ready`, SIGTERM graceful shutdown,
Next.js standalone server). Docker images could not be built in the authoring sandbox (Docker Hub blocked);
the CI `docker` job builds and smoke-tests them.

## Backend on Railway (+ PostgreSQL)

1. Railway → New Project → Deploy from GitHub repo → select this repo. Set the service **Root Directory** to
   `backend` (uses `backend/Dockerfile` and `backend/railway.json`).
2. Add a **PostgreSQL** plugin. On the backend service set `DATABASE_URL=${{Postgres.DATABASE_URL}}`
   (`postgres://` URLs are normalised to `postgresql+asyncpg://`).
3. Variables (minimum for a mock-provider public demo):
   `APP_ENV=production`, `FRONTEND_URL=https://<frontend-domain>`.
   Railway provides `PORT`; the container listens on it. Migrations run on start (`AUTO_MIGRATE=true`);
   seed data is upserted idempotently.
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

Operator audit access (either option): set the **server-only** variables `OPERATOR_TOKEN` (same value as
the backend) and `OPERATOR_CONSOLE_PASSWORD` on the frontend service. They are read only by the Next.js
route handlers under `app/api/operator/*` and are never `NEXT_PUBLIC_`, so they are not inlined into client
JavaScript (`npm run check:bundle` builds with canary values and scans the browser-reachable output).

Then set the backend `FRONTEND_URL` to the frontend origin (CORS + WebSocket origin check in production).

## Current production values

Railway (backend service variables):

```env
APP_ENV=production
FRONTEND_URL=https://ai-voice-calling-collections-agent.vercel.app   # exact origin, no wildcard
DATABASE_URL=${{Postgres.DATABASE_URL}}
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
# server-only (Production environment; do NOT prefix with NEXT_PUBLIC_)
OPERATOR_TOKEN=<same value as Railway OPERATOR_TOKEN>
OPERATOR_CONSOLE_PASSWORD=<new long random password for the Sessions page sign-in>
```

Operator auth flow for protected audit detail (phone sessions):

```
browser --/api/operator/sessions/:id (same origin, HttpOnly cookie)--> Next.js route handler
        --Authorization: Bearer OPERATOR_TOKEN--> Railway /api/sessions/:id
```

The browser never receives the token; it signs in once with `OPERATOR_CONSOLE_PASSWORD` and gets an
HttpOnly, `SameSite=Strict`, 8-hour HMAC-signed cookie scoped to `/api/operator`. Browser-demo sessions open
without sign-in; the backend still refuses phone-session detail without the token.

The browser builds the WebSocket URL from this value (`https` → `wss`, same host), never from the Vercel
origin: `wss://ai-voice-calling-collections-agent-production.up.railway.app/ws/session?...`.

## Post-deploy checks

```bash
curl https://<backend>/ready          # database ok, providers listed
curl https://<backend>/api/capabilities
curl -X POST https://<backend>/api/eval/run   # expect passed == total
```
Then open the frontend, run scenario A, and open the session's audit trail. For a phone session: open it
from Sessions, sign in with `OPERATOR_CONSOLE_PASSWORD`, confirm the detail loads, and check in DevTools →
Network that the request goes to `/api/operator/sessions/<id>` on the Vercel origin with no
`Authorization` header.

## Environment reference

See [`.env.example`](../.env.example). Secrets (`CLOUDFLARE_API_TOKEN`, `CARTESIA_API_KEY`,
`TWILIO_AUTH_TOKEN`, `OPERATOR_TOKEN`) are backend variables. The only secrets the frontend holds are the
server-only `OPERATOR_TOKEN` and `OPERATOR_CONSOLE_PASSWORD` used by its route handlers; never expose either
through a `NEXT_PUBLIC_` variable.
