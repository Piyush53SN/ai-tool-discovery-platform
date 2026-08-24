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
| Render     | Django API (Docker)      | Web Starter — $7/mo (no free tier)    |
| Supabase   | Postgres + pgvector      | Free tier (500 MB DB; auto-pause rules apply) |

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
   - **Plan: Starter** is enough for the lightweight build (no torch).
     If you enable real embeddings (below), use **Standard** (2 GB RAM).
   - **Health Check Path: `/api/`** (a JSON endpoint that returns 200).
   - Optional, only for real embeddings: **Docker build args** →
     `INSTALL_MLM=1` (bakes in sentence-transformers + CPU torch; image
     grows ~1.5 GB, build takes a few minutes longer).
4. **Environment** tab — set exactly these (values in `‹…›` are yours):

   | Key                    | Value                                                                 |
   |------------------------|-----------------------------------------------------------------------|
   | `SECRET_KEY`           | any long random string (e.g. `openssl rand -base64 48`)                |
   | `DATABASE_URL`         | the Supabase pooler URI from Step 1                                     |
   | `ALLOWED_HOSTS`        | the service's public URL, e.g. `ai-tool-api.onrender.com`              |
   | `CORS_ALLOWED_ORIGINS` | the Netlify URL you'll get in Step 3, e.g. `https://my-tools.netlify.app` |
   | `DEBUG`                | `False`                                                                 |

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
   give it a minute if `/api/tools/` returns an empty list right after
   deploy).
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

## Cost summary (monthly, at demo scale)

- Netlify free tier: $0
- Supabase free tier: $0 (auto-pauses after 1 week of inactivity — any
  visit reactivates it in ~1 min; a scheduled visit from any page view
  keeps it warm)
- Render Web Starter: $7
- **Total: $7/mo** (or $0 + $7 while Render bills)
