# Deploy: Netlify + Render + Supabase (one shareable link)

Host the platform with a single public URL anyone can open and try:

```
Visitor's browser
      │  one link: https://<site>.netlify.app
      ▼
Netlify (free)        — serves the built React SPA
      │  fetch /api/... (VITE_API_BASE, baked in at build time)
      ▼
Render (Docker)       — Django REST API (gunicorn)
      │  PostgreSQL wire protocol
      ▼
Supabase (free)       — Postgres 15+ with the pgvector `vector` extension
```

| Piece      | What it hosts            | Cost                                  |
|------------|--------------------------|---------------------------------------|
| Netlify    | `frontend/` (built SPA)  | Free tier (100 GB bandwidth/month)    |
| Render     | Django API (Docker)      | **Free** Hobby web service — $0       |
| Supabase   | Postgres + pgvector      | Free tier (500 MB DB)                 |

**Total: $0/month.** The free Render web service (0.1 vCPU / 512 MB)
**sleeps after ~15 min of inactivity**; the first visitor after a sleep
waits ~30–60 s for wake-up (plus one background re-seed check). It also has
a 5 GB/month workspace bandwidth cap — fine for a demo, since Netlify
serves the SPA and only JSON API traffic hits Render. If you outgrow the
sleep, the same service moves to the Starter plan ($7/mo, always-on) with
one dashboard click — no code changes.

> **Why Supabase and not Render's Postgres?** The app *requires* the
> pgvector `vector` extension (384-dim embeddings next to the relational
> rows). Supabase offers it as a one-click managed extension; Render's
> managed Postgres does not.

You need free accounts on all three (each ~2 min, GitHub login works
everywhere). Estimated total time: **10–15 minutes**.

---

## Step 1 — Supabase (the database) · ~3 min

1. Sign in at **supabase.com** → **New project**.
   - Name it `ai-tools` (any name works).
   - Set a **database password** (note it down — you'll see it in the
     connection string, but good to have).
   - Pick the region closest to you, wait ~1–2 min for provisioning.
2. **Enable the `vector` extension** (required — the app's first migration
   does `CREATE EXTENSION vector` and Supabase only lets *you*, via the
   dashboard, enable extensions):
   - **Project Settings → Database → Extensions**
   - search for **`vector`** (pgvector) → **Enable**.
3. **Copy the connection string**:
   - **Project Settings → Database → Connection details**
   - switch the **Connection pooler** segment to **Session** (port `6543`)
   - copy the **URI** (pooled) value, e.g.
     `postgresql://postgres.<project-ref>:<password>@aws-0-eu-west-1.pooler.supabase.com:6543/postgres`

   > Use the **pooler** port (6543), not the direct port (5432): Render's
   > two gunicorn workers + migrate/seed each open their own connection, and
   > Supabase's free plan caps direct connections low. The pooler accepts
   > many.

   Keep the string safe — it's the database password in plain text.

## Step 2 — Render (the API) · ~5 min

1. Sign in at **render.com** → **New → Web Service**.
2. **Connect your GitHub** (if not already) → pick the repository
   **`Piyush53SN/ai-tool-discovery-platform`** → select the branch that
   contains this deployment setup (`main` after merge, or the session
   branch).
3. Configure:
   - **Runtime: Docker** (Render finds the `Dockerfile` at the repo root).
   - **Plan: Free** (Hobby — 0.1 vCPU / 512 MB, no card usually needed;
     some accounts are asked for one, and the 5 GB/month bandwidth cap
     applies). The lightweight build is sized for exactly this instance
     (1 gunicorn worker, no torch).
   - **Health Check Path: `/api/`** (a JSON endpoint that returns 200).
   - Optional, only for real embeddings: **Docker build args** →
     `INSTALL_MLM=1` (bakes in sentence-transformers + CPU torch; image
     grows ~1.5 GB — on the free 512 MB instance the MiniLM model will
     OOM, so use this **only** on a paid plan with ≥ 1 GB RAM).
4. **Environment** tab — set exactly these (values in `‹…›` are yours):

   | Key                    | Value                                                                 |
   |------------------------|-----------------------------------------------------------------------|
   | `SECRET_KEY`           | any long random string (e.g. `openssl rand -base64 48`)                |
   | `DATABASE_URL`         | the Supabase pooler URI from Step 1                                     |
   | `ALLOWED_HOSTS`        | the service's public URL, e.g. `ai-tool-api.onrender.com`              |
   | `CORS_ALLOWED_ORIGINS` | the Netlify URL you'll get in Step 3, e.g. `https://my-tools.netlify.app` |
   | `DEBUG`                | `False`                                                                 |
   | `GUNICORN_WORKERS`     | `1` (free 512 MB instance) — raise to `2`+ only on paid plans          |

   (`ALLOWED_HOSTS` and `CORS_ALLOWED_ORIGINS` need the final URLs, so set
   them now with the Render URL from this page's preview, and update
   `CORS_ALLOWED_ORIGINS` in Step 3 once Netlify gives you the domain.)

   Optional — Model Lab (live LLM chat). Without these keys the Model Lab
   page honestly says "not connected"; with them it streams four models
   side by side. Add any of: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
   `GOOGLE_API_KEY`, `GROQ_API_KEY` (⚠️ these hit **paid** APIs — set
   spend caps in each provider's dashboard).

5. **Deploy**. First build ~3–6 min (lightweight) or 10+ min (with MLM).
   Watch the logs: you should see the migrations run, then
   `Catalog ready: 127 tools` (seeding continues in the background —
   give it a minute or two on the free 0.1-vCPU instance if
   `/api/tools/` returns an empty list right after deploy).
   Note for the free plan: after this deploy, the service will **sleep
   after ~15 min with no traffic** and wake in 30–60 s on the next visit.
   To keep it warm during a live demo, just keep the page open or hit
   `https://‹service›.onrender.com/api/` in a browser tab.
6. Sanity-check from your machine:
   ```bash
   curl https://‹service›.onrender.com/api/        # JSON service index
   curl "https://‹service›.onrender.com/api/tools/?search=chat"
   ```

## Step 3 — Netlify (the one link) · ~3 min

1. Sign in at **app.netlify.com** → **Add new site → Import an existing project**
   → **Git provider → GitHub** → select the same repository (and branch).
2. Netlify auto-detects `netlify.toml`:
   - Build command: `cd frontend && npm ci && npm run build`
   - Publish directory: `frontend/dist`
   - (You shouldn't need to change anything — just confirm.)
3. **Site settings → Build & environment → Environment variables** → add:

   | Key             | Value                                             |
   |-----------------|---------------------------------------------------|
   | `VITE_API_BASE` | `https://‹service›.onrender.com` (no trailing slash) |

   This is baked into the SPA at build time so the browser calls the
   Render API.
4. **Deploy**.
5. Back in Render, make sure `CORS_ALLOWED_ORIGINS` on the web service is
   exactly your Netlify URL (e.g. `https://my-tools.netlify.app`) →
   **Apply** (redeploys; takes ~30 s).

## Done — your one link

Open **`https://‹site›.netlify.app`** and share it with anyone.

- Browse, search (semantic + full-text hybrid), filter, compare,
  bookmark, review, and get personalised recommendations.
- **Demo account** (pre-seeded with bookmarks/reviews so the
  recommendations page shows real output):
  **username `demo` · password `demo-pass-123`** — or anyone can register
  a free account in the app.
- The Model Lab page works only if you added provider API keys in Step 2.

---

## Updating later

Push commits to the connected branch — **Render rebuilds the API** and
**Netlify rebuilds the SPA** automatically (auto-deploy is on by default).
No re-migration needed: the container runs `migrate` on every boot, and
`seed_tools` is idempotent.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Site loads but tools list is empty | Seed is still running in the background — wait ~60 s after deploy; check Render logs for `Catalog ready: 127 tools`. |
| `DisallowedHost` 400 in Render logs | `ALLOWED_HOSTS` must contain the Render domain exactly (no scheme, no port). |
| Browser console: CORS errors | `CORS_ALLOWED_ORIGINS` on Render must equal the **exact** Netlify origin, including `https://` and with no trailing slash. Changing env vars redeploys — wait for it. |
| `permission denied to create extension "vector"` | You didn't enable `vector` in Supabase (Step 1.2) — enable it in the dashboard. |
| `password authentication failed` on Render deploy | `DATABASE_URL` must be the **pooler (6543)** URI from Supabase's Connection details, copied in full. |
| OOM / service crashes after enabling `INSTALL_MLM=1` | The MiniLM model needs more memory — move the service to the Standard plan (2 GB). |
| `502` right after a Render deploy | First boot = migrate + gunicorn startup (~20–40 s); retry in a moment. |
| First request after idle is slow (30–60 s) | Normal on the free Render tier — the instance slept and is waking up; also the Supabase DB may need ~1 min to un-pause after a week without visits. |
| OOM kills on Render (free tier) | Keep `INSTALL_MLM` off and `GUNICORN_WORKERS=1` on the 512 MB free instance. |
| Bandwidth warning from Render | The free workspace is capped at 5 GB/month — expected only if the site goes viral; move to Starter ($7/mo, 25 GB+). |

## Cost summary (monthly, at demo scale)

- Netlify free tier: **$0**
- Supabase free tier: **$0** (auto-pauses after ~1 week without any visit;
  the next visit reactivates it in ~1 min)
- Render free (Hobby) web service: **$0** (sleeps after ~15 min idle;
  5 GB/month bandwidth)
- **Total: $0/month**

Want it always-on with no cold starts? Same setup, no code changes:
switch the Render service to the **Starter** plan ($7/mo). Or go fully
VM-based on **Oracle Cloud's always-free tier** (4 ARM cores / 24 GB,
Mumbai region, no card) — run the whole stack incl. real MiniLM
embeddings on one machine; that's a manual-ops route (systemd + nginx),
not one-click.
