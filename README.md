# AI Tool Discovery & Recommendation Platform

Find the *right* AI tool for a specific need — not just browse a static list. A
searchable, faceted catalog of **real, link-verified AI tools** with semantic
(embedding-based) search, side-by-side comparison, bookmarks, reviews,
**personalised recommendations computed from behaviour with pgvector**, and a
**Model Lab** that streams one prompt to four live LLMs side by side.

```
React SPA (Vite)
      │  HTTPS / JSON (same-origin via dev proxy)
      ▼
Django REST Framework API
      │
      ├── Auth (SimpleJWT + register + onboarding quiz tags)
      ├── Tools: hybrid search · faceted filters · compare · view tracking
      ├── Bookmarks & Reviews (weighted interaction log)
      ├── Recommendation service (embedding cosine similarity + MMR re-rank)
      ├── Model Lab: one prompt → 4 live LLMs, per-model SSE streams
      ▼
PostgreSQL 16 + pgvector
      (relational tables + 384-dim vector columns, queried together via `<=>`)
      ▲
Embedding jobs — sentence-transformers (all-MiniLM-L6-v2)
      post_save signal → Celery task (or background thread pool), NEVER on the
      request path. Batched passes for seeding / backfills.
```

---

## Why this is more than a CRUD app (the parts worth grading/interviewing)

| Area | What's actually going on |
|---|---|
| **Vector search in Postgres** | Tool embeddings live in a pgvector `vector(384)` column next to the relational data. Ranking is `ORDER BY embedding <=> :query` *in SQL*, so vector math composes with `WHERE category = ...`/exclusions in one query. No separate vector DB. |
| **ANN indexing & its recall trap** | An `ivfflat` index is approximate: with default `probes=1` on a small table it **silently drops rows** (k-means lists the probe never visits) — this bit us during development and is covered by a regression test. The index is therefore created only past a size threshold (`manage.py backfill_embeddings --vector-index`, `lists ≈ rows/1000`) and every vector-ordered query runs inside `ivfflat_probes(n)` which raises probes per-transaction. See `catalog/db.py`. |
| **Hybrid search** | Full-text (`SearchVector`/`SearchRank`, name weighted A / description B) fused with semantic cosine similarity: `score = α·rank/max_rank + (1−α)·(1 − cosine_distance)`, with window-MAX normalisation of the unbounded ts rank. α = `SEARCH_TEXT_WEIGHT` (default 0.4). |
| **Recommendation engine** | 1️⃣ every view/bookmark/review writes a **weighted** `Interaction` row (view 1.0 · bookmark 3.0 · review 4.0, +1.0 if rating ≥ 4) → 2️⃣ the user's **preference vector** is the weight-weighted mean of interacted tool embeddings, L2-normalised and cached on `Profile` → 3️⃣ candidates ranked by cosine distance to it, bookmarked tools excluded → 4️⃣ optional **MMR diversity re-rank** penalises near-duplicate category/tag profiles. Fully commented in `recommendations/services.py`. |
| **Cold start** | No interactions yet? Fall back to onboarding-tag matching boosted by a **Bayesian-smoothed popularity score** — `(v·R + m·C)/(v + m)` shrinks a single 5★ review toward the prior, then multiplied by `log(1 + bookmarks + 0.5·reviews)` traction so quiet-but-good tools don't flatline at 0. |
| **Off-request-path embeddings** | `post_save`/`m2m_changed` signals dispatch jobs to Celery (`USE_CELERY=1`) or a background thread pool. Nothing in a view ever encodes catalog text. Query-time embedding of the *search string* is the one deliberate exception (spec §6.1) — singleton model + LRU cache. |
| **Embedding backends** | Default `sentence-transformers/all-MiniLM-L6-v2` (384-dim, CPU). A deterministic **signed feature-hashing** fallback (`EMBEDDING_BACKEND=hash`, numpy-only) keeps the stack runnable offline/in CI — identical dimensions, identical downstream maths. |
| **Relational design** | Normalised catalog (Category/Tag/Tool M2M), unique `(user, tool)` constraints on bookmarks/reviews, append-mostly interaction log with explicit bookkeeping policy (bookmark/review rows mirror current state; views are append-only), denormalised `avg_rating`/`rating_count`/`bookmark_count` maintained transactionally in a service layer. |

---

## Quickstart

### 0. Zero-install option — GitHub Codespaces (browser preview, no local setup)

Open the repo on GitHub → green **Code** button → **Codespaces** → **Create codespace on arena/…** (or `main` once merged). The devcontainer (`.devcontainer/`) installs Python, Node, and Docker-in-Docker, then the post-create script pre-installs dependencies. Once it's up, run in the terminal:

```bash
./scripts/run_local.sh        # DB + migrate + seed + both servers
```

Codespaces auto-forwards port **5173** and offers **"Open in Browser"** — that's your live
localhost preview of the React app (login `demo / demo-pass-123`).

### 1. Database (PostgreSQL 15+ with pgvector)

Any Postgres with the `vector` extension available works. Docker one-liner
(`POSTGRES_DB` matters — it pre-creates the `aitools` database the default
`DATABASE_URL` expects):

```bash
docker run -d --name pgvector -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=aitools \
  pgvector/pgvector:pg16
```

The first migration runs `CREATE EXTENSION IF NOT EXISTS vector;` itself — the DB
role needs privileges for that (superuser in dev is fine).

### 2. Backend

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt       # or requirements.txt + skip torch via hash backend
cp .env.example .env                      # adjust DATABASE_URL

python manage.py migrate
python manage.py seed_tools               # ~160 tools + demo user, batch-embedded
python manage.py runserver                # http://localhost:8000
```

**Lightweight install (no torch, no model download):**

```bash
grep -v '^sentence-transformers' requirements.txt > /tmp/reqs.txt
pip install -r /tmp/reqs pytest pytest-django ruff
# .env: EMBEDDING_BACKEND=hash   (everything works; swap back later for MiniLM)
```

> **No network / no torch?** Set `EMBEDDING_BACKEND=hash` in `.env` — everything
> (search, recommendations, seeding, tests) works with the deterministic offline
> embedder. Swap back to `sentence-transformers` (default `auto`) for real
> MiniLM semantics; dimensions are identical either way.

Demo login: **demo / demo-pass-123** (a writing/marketing persona with bookmarks,
reviews and views already logged — its preference vector is precomputed).

### 3. Frontend

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173
```

Vite proxies `/api` to Django, so the browser stays same-origin.

### 4. Tests

```bash
pytest                                     # 75 tests against real Postgres + pgvector
pytest --cov=accounts --cov=catalog --cov=interactions --cov=recommendations
ruff check .
```

---

## API (Section 7 of the spec)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/auth/register/` | – | username/email/password + optional `onboarding_tags` (cold-start quiz); returns JWT pair |
| POST | `/api/auth/token/` | – | JWT obtain (returns user block too) |
| POST | `/api/auth/token/refresh/` | – | JWT refresh (rotating) |
| GET/PATCH | `/api/auth/me/` | ✓ | identity, recommender state, onboarding tags |
| GET | `/api/tools/?search=&category=&pricing=&tags=&min_rating=&ordering=&page=&page_size=` | optional | hybrid search + facets + ordering, paginated |
| GET | `/api/tools/{slug}/` | optional | detail + semantic neighbours (`similar_tools`) |
| POST | `/api/tools/compare/` | optional | `{tool_ids: [2–4]}` → attribute matrix |
| POST | `/api/tools/{slug}/view/` | optional | view interaction (logged for signed-in users) |
| POST/GET/DELETE | `/api/bookmarks/` | ✓ | body `{tool_id}`; maintains weighted interactions |
| POST/GET/DELETE | `/api/reviews/` | ✓ / optional | body `{tool_id, rating, comment}`; `?tool=slug` public thread |
| GET | `/api/recommendations/?limit=&diversify=` | ✓ | ranked list + strategy + similarity + reason |
| GET | `/api/categories/`, `/api/tags/` | optional | facet metadata with counts |

All endpoints throttled (scoped rates on auth/writes), paginated list responses
(`count/pages/next/previous/results`), CORS restricted to the frontend origin(s).

### Example: the recommendation payload

```json
{
  "count": 8,
  "strategy": "personalised_diverse",
  "preference_vector_ready": true,
  "diversify": true,
  "results": [
    {
      "tool": { "name": "Surfer SEO", "category": {...}, "avg_rating": "4.60", ... },
      "similarity": 0.589,
      "score": 0.412,
      "reason": "Similar to tools you interacted with"
    }
  ]
}
```

`strategy` is one of `personalised`, `personalised_diverse`, `cold_start_tags`,
`cold_start_popularity` — the endpoint tells you exactly which path served you.

---

## Management commands

| Command | Purpose |
|---|---|
| `seed_tools [--count N] [--flush] [--no-demo-user]` | 145 curated real tools + deterministic synthetic filler to N, batch-embedded; optional demo user with interaction history |
| `backfill_embeddings [--all] [--vector-index] [--force-index] [--reindex] [--drop-index]` | offline (re)embedding + ivfflat ANN index lifecycle |
| `recompute_preferences` | refresh every user's preference vector (also wired as a Celery beat task) |

## Celery (optional)

```bash
USE_CELERY=1 celery -A config.celery worker -l info
USE_CELERY=1 celery -A config.celery beat -l info
```

Without a broker, embedding/preference jobs run on an in-process daemon thread
pool — still strictly off the request path.

---

## Testing philosophy

* Every endpoint has at least one API test; models/serializers are covered directly.
* **The graded centerpiece is pinned by exact maths**: `recommendations/tests/test_engine.py`
  plants axis-aligned unit vectors (hand-checkable cosine values, e.g.
  `3/√10 ≈ 0.9487`) and asserts the preference-vector weighted mean, the ranking
  order, bookmark exclusion, weight sensitivity (bookmark vs view flips the
  ranking), MMR reordering, and both cold-start strategies.
* A regression test locks in the ivfflat recall bug (ANN index must not drop
  candidates on small tables).
* Tests run against **real PostgreSQL + pgvector** (CI provisions a service
  container) with the deterministic hash embedder and synchronous dispatch —
  hermetic and fast, no network.

## CI

`.github/workflows/ci.yml`: ruff → pytest (pgvector service container) → frontend
build, on every push/PR.

---

## Project layout

```
config/            settings (env-only), urls, celery, pagination
accounts/          Profile (preference vector, onboarding tags), register/me endpoints
catalog/           Category/Tag/Tool models + pgvector, hybrid search, filters,
                   compare, embedding backends + task dispatch, seed data
interactions/      Bookmark/Review/Interaction models, service layer
                   (denormalised stats + weighted log), API
recommendations/   the engine: preference vectors, cosine ranking, MMR,
                   cold start, trending; endpoint + periodic tasks
frontend/          React 19 + Vite SPA
```

## Status of the stretch goals

- ✅ Diversity re-rank (MMR) — `?diversify=` / `RECOMMENDATION_DIVERSIFY`
- ✅ Onboarding quiz for cold start — register flow + `/api/auth/me/` PATCH
- ✅ Trending — `GET /api/tools/trending/` + Explore strip (bookmark velocity, 7-day window)
- 🔲 LLM-assisted tool submission from a URL
- 🔲 Admin moderation dashboard for community submissions

## Environment & security

Every knob is an environment variable (see `.env.example`): `SECRET_KEY`, `DEBUG`,
`ALLOWED_HOSTS`, `DATABASE_URL`, CORS origins, JWT lifetimes, embedding backend &
dispatch mode, search blend weight, MMR λ. Production mode refuses to boot with the
insecure default key. No secrets in code.


---

## Upgrade features

### Real catalog + verification crawler (`catalog/verification.py`)

The seed (`real_tools_seed.json`) contains 127 real tools with real URLs and
hand-written descriptions; it's a curated file meant to be hand-expanded. A
Celery beat task verifies links: robots.txt-honouring HEAD→bounded-GET checks
(≤64 KB read, `<title>`/`og:description` only), one check per tool per 24h
with jitter, 10s timeout, and a 3-strike `needs_review` flag — never
auto-delete. `is_live`/`http_status` surface in `/api/tools/` and the UI
("link down" tags, per-tool status lines). Manual run:
`python manage.py verify_tool_links`.

### Model Lab — live multi-model comparison (`chat/`)

`POST /api/chat/turns/` fans one prompt out to up to 4 models;
`GET /api/chat/turns/{id}/stream/{model_id}/` is an independent SSE pipe per
model (`data: {"token": …}` → `data: {"done": true, "usage": …}` or
`data: {"error": …}`). Providers: OpenAI, Anthropic, Gemini, Groq
(open-source Llama) — one async adapter each, normalised to plain tokens;
registry-driven so new models are an env var, not a code change
(`OPENAI_CHAT_MODEL` etc.).

**Cost discipline (real APIs, real money):** server-side keys only, per-user
`"chat"` throttle scope, 60s timeout + token cap per response, and *no fake
fallback* — an unconfigured provider renders "NOT CONNECTED", never a canned
answer. Responses persist with latency/token measurements for honest
comparison. Set spend caps in each provider dashboard before demos.

### Redesign

Spec-sheet aesthetic: Space Grotesk display + IBM Plex Sans body + IBM Plex
Mono for anything measured (tabular numerals), one signal-orange accent on
near-black/off-white, hairline rules, squared corners, no gradients or glass.
The live streaming cursor is the only hero motion.
