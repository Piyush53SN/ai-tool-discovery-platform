#!/usr/bin/env python3
"""
One-command cloud deploy: Supabase + Render + Netlify.

Creates the three production resources for this repo, wires them together,
waits for every deploy to finish, verifies the chain end-to-end, and prints
the single shareable link.

Prerequisites (on YOUR machine, not in CI):
  * Python 3.8+ (standard library only — no pip installs)
  * The three access tokens, either as environment variables:
        SUPABASE_TOKEN   (supabase.com → Account → Access tokens → Generate)
        RENDER_API_KEY   (render.com  → Account → API Keys → Create)
        NETLIFY_TOKEN    (app.netlify.com → Applications → Personal access
                         tokens → New, scopes: Account/Sites/Deployments/
                         Builds/Forms/Domains)
      or you'll be prompted for them (input is hidden).
  * Render: your GitHub account must be connected once in the dashboard
    (render.com → Account → GitHub → Connect) so Render can pull this repo.

Usage:
  python scripts/deploy_cloud.py                     # sensible defaults
  python scripts/deploy_cloud.py --branch main       # after merging to main
  python scripts/deploy_cloud.py --render-name my-api --region sg1

What it does (in order):
  1. Supabase:  new project (Mumbai by default) → wait for ACTIVE →
                enable the pgvector `vector` extension → connection URL.
  2. Render:    new free Docker web service from this GitHub branch with all
                env vars (DATABASE_URL, ALLOWED_HOSTS, CORS, SECRET_KEY…) →
                wait for deployed.
  3. Netlify:   new site from the same branch with VITE_API_BASE baked in →
                wait for the build to be ready.
  4. Reconcile: if any platform renamed something, update the cross-links
                (CORS origin / allowed hosts) and re-deploy.
  5. Verify:    SPA loads, API answers, CORS preflight passes, demo login
                works — then print THE LINK.

Idempotency: re-running never duplicates resources — it reuses existing
services/sites/projects it finds (matching by name) and only fills in what
is missing.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

REPO_OWNER = "Piyush53SN"
REPO_NAME = "ai-tool-discovery-platform"
DEFAULT_BRANCH = "arena/01a0350b-ai-tool-discovery-platform"

SUPABASE_API = "https://api.supabase.com/v1"
RENDER_API = "https://api.render.com"
NETLIFY_API = "https://api.netlify.com/api/v1"

SUPABASE_PROJECT_NAMES = ["ai-tools", "ai-tools-catalog"]
SUPABASE_REGIONS = ["in1", "sg1", "au1", "usw2", "use1"]  # Mumbai first

RENDER_NAMES = ["ai-tool-api", "ai-tool-discovery-api"]

NETLIFY_SITE_NAMES = [
    "ai-tools-discovery",
    "aitools-discovery",
    "ai-tool-radar",
    "ai-tools-finder",
]

DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo-pass-123"

DB_NAME = "postgres"  # Supabase's default database name


# ---------------------------------------------------------------------------
# Small HTTP helper (stdlib only, so the script runs anywhere)
# ---------------------------------------------------------------------------

@dataclass
class ApiError(Exception):
    status: int
    body: str


class Api:
    def __init__(self, base: str, token: str, extra_headers: dict | None = None):
        self.base = base
        self.token = token
        self.extra = extra_headers or {}

    def request(self, method: str, path: str, body: dict | None = None,
                timeout: int = 60) -> dict | list | None:
        url = path if path.startswith("http") else self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        for k, v in self.extra.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            raise ApiError(e.code, raw) from None
        except urllib.error.URLError as e:
            raise SystemExit(
                f"\n✗ Cannot reach {url} — check your internet connection.\n"
                f"  ({e.reason})\n"
            ) from None


def _extract(msg: str, key: str) -> str:
    """Best-effort pull of a useful field out of a provider error body."""
    m = re.search(rf'"{key}"\s*:\s*"([^"]+)"', msg)
    return m.group(1) if m else ""


def prompt_env(name: str, label: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        print(f"  • {label}: from ${name}")
        return value
    print(f"  • {label} — paste the token (input hidden; "
          f"Ctrl-C to abort):")
    value = getpass.getpass("    " + label + ": ").strip()
    if not value:
        raise SystemExit(f"✗ {label} is required (set ${name} or paste it).")
    return value


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

class Step:
    def __init__(self, label: str):
        self.label = label

    def __enter__(self):
        print(f"\n▶ {self.label}")
        sys.stdout.flush()
        return self

    def info(self, msg: str):
        print(f"    … {msg}")
        sys.stdout.flush()

    def done(self, msg: str = ""):
        print(f"  ✓ {self.label}" + (f" — {msg}" if msg else ""))
        sys.stdout.flush()

    def __exit__(self, *exc):
        if exc[0] is not None:
            print(f"  ✗ {self.label} failed")
            sys.stdout.flush()


def wait_for(label: str, fn, timeout: int, interval: int = 10):
    """Poll fn() (returns True when done, raises ApiError on hard errors)."""
    deadline = time.time() + timeout
    last_state = ""
    while time.time() < deadline:
        state = fn()
        if state is True:
            return
        if isinstance(state, str) and state != last_state:
            last_state = state
            print(f"    … {label}: {state}")
            sys.stdout.flush()
        time.sleep(interval)
    raise SystemExit(f"✗ Timed out waiting for {label} ({timeout}s). "
                     "Check the provider dashboard and re-run this script — "
                     "it resumes from where it left off.")


# ---------------------------------------------------------------------------
# 1) Supabase
# ---------------------------------------------------------------------------

def supabase_db_url(project: dict, db_password: str) -> str:
    ref = project["ref"]
    return (f"postgresql://postgres.{ref}:{db_password}"
            f"@db.{ref}.supabase.co:5432/{DB_NAME}?sslmode=require")


def find_supabase_project(sb: Api, orgs: list, name: str) -> dict | None:
    org = orgs[0]
    try:
        projects = sb.request("GET", f"/organizations/{org['id']}/projects") or []
    except ApiError:
        projects = sb.request("GET", f"/projects?organization_id={org['id']}") or []
    return next((p for p in projects if p.get("name") == name), None)


def step_supabase(args, sb: Api) -> tuple[dict, str]:
    with Step("Supabase: create project (Postgres + pgvector)"):
        orgs = sb.request("GET", "/orgs") or []
        if not orgs:
            raise SystemExit("✗ No Supabase organisations found for this token.")
        org = orgs[0]
        sb.info(f"organisation: {org.get('name')}")

        # Reuse an existing project if we created one before (idempotent).
        project = None
        for name in SUPABASE_PROJECT_NAMES:
            project = find_supabase_project(sb, orgs, name)
            if project:
                sb.info(f"reusing existing project '{name}' (ref {project['ref']})")
                break

        if not project:
            db_password = secrets.token_urlsafe(24)
            last_err = None
            for name in SUPABASE_PROJECT_NAMES:
                for region in SUPABASE_REGIONS:
                    if args.region and region != args.region:
                        continue
                    try:
                        sb.info(f"creating project '{name}' in region {region} …")
                        project = sb.request("POST", "/projects", {
                            "name": name,
                            "region": region,
                            "organization_id": org["id"],
                            "database_password": db_password,
                        })
                        break
                    except ApiError as e:
                        last_err = e
                        detail = _extract(e.body, "message") or e.body[:200]
                        sb.info(f"  {region}: {detail}")
                        if e.status in (401, 403):
                            raise SystemExit(
                                "✗ Supabase token rejected — check SUPABASE_TOKEN."
                            )
                        break  # name taken → try next name
                if project:
                    break
            if not project:
                raise SystemExit(f"✗ Could not create Supabase project: {last_err}")
            db_password_note = "generated by this script"
        else:
            db_password = os.environ.get("SUPABASE_DB_PASSWORD", "")
            if not db_password:
                raise SystemExit(
                    "✗ Reusing an existing project, but I don't know its database "
                    "password.\n"
                    f"  Get it at supabase.com → project '{project['name']}' → "
                    "Project Settings → Database → Connection details, then:\n"
                    "      SUPABASE_DB_PASSWORD=*** python scripts/deploy_cloud.py"
                )
        ref = project["ref"]

        def project_state():
            p = sb.request("GET", f"/projects/{ref}")
            state = (p.get("status") or {}).get("state") or p.get("state") or "?"
            return True if state == "ACTIVE" else f"provisioning ({state})"

        wait_for("project provisioning", project_state, timeout=600, interval=15)

        # Enable the pgvector extension (required by the first migration).
        def extension_state():
            try:
                exts = sb.request("GET", f"/projects/{ref}/extensions") or []
                return True if any(e.get("name") == "vector" for e in exts) else "waiting for 'vector'"
            except ApiError:
                return "listing extensions"

        try:
            sb.request("POST", f"/projects/{ref}/extensions", {"name": "vector"})
            sb.info("pgvector: extension enable requested")
        except ApiError as e:
            sb.info(f"could not enable 'vector' via API ({e.status}) — "
                    f"{_extract(e.body, 'message') or 'enable it in the dashboard'}")
            print("\n    ⚠ ONE-CLICK STEP for you: Supabase dashboard → your "
                  f"project '{project['name']}' → Project Settings → "
                  "Database → Extensions → search 'vector' → Enable.")
            ans = input("    Press Enter once you've done that (or 'skip' to "
                        "abort): ").strip().lower()
            if ans in ("skip", "s", "q"):
                raise SystemExit("✗ Aborted.")
        wait_for("pgvector extension", extension_state, timeout=300, interval=10)

        url = supabase_db_url(project, db_password)
        project_state_now = (sb.request("GET", f"/projects/{ref}") or {}).get("status", {}).get("state")
        step_supabase.done(f"project '{project['name']}' ref={ref} state={project_state_now}")
        return project, url


# ---------------------------------------------------------------------------
# 2) Render
# ---------------------------------------------------------------------------

def render_env_vars(db_url: str, render_domain: str, netlify_url: str) -> list:
    return [
        {"key": "SECRET_KEY", "generateValue": True},
        {"key": "DEBUG", "value": "False"},
        {"key": "DATABASE_URL", "value": db_url},
        {"key": "ALLOWED_HOSTS", "value": render_domain},
        {"key": "CORS_ALLOWED_ORIGINS", "value": netlify_url},
        {"key": "GUNICORN_WORKERS", "value": "1"},
    ]


def find_render_service(rd: Api, orgs: list, name: str) -> dict | None:
    for org in orgs:
        services = rd.request("GET", f"/orgs/{org['id']}/services") or []
        for s in services:
            if s.get("name") == name:
                return s
    return None


def step_render(args, rd: Api, db_url: str, netlify_url_guess: str) -> dict:
    with Step("Render: create free Docker web service (Django API)"):
        orgs = rd.request("GET", "/orgs") or []
        if not orgs:
            raise SystemExit("✗ No Render organisations found for this key.")
        org = orgs[0]
        rd.info(f"organisation: {org.get('name')}")

        service = None
        for name in RENDER_NAMES:
            if args.render_name:
                name = args.render_name
                break
            service = find_render_service(rd, orgs, name)
            if service:
                rd.info(f"reusing existing service '{name}'")
                break
        domain = (service or {}).get("publicDomain") or ""

        if not service:
            # Render names the free host <name>.onrender.com, so seed
            # ALLOWED_HOSTS with the expected domain; reconciled afterwards.
            domain = f"{name}.onrender.com"
            body = {
                "name": name,
                "runtime": "docker",
                "plan": "free",
                "repo": {
                    "type": "git",
                    "linkType": "github",
                    "owner": REPO_OWNER,
                    "name": REPO_NAME,
                    "branch": args.branch,
                },
                "healthCheckPath": "/api",
                "autoDeploy": True,
                "envVars": render_env_vars(db_url, domain, netlify_url_guess),
            }
            try:
                rd.info(f"creating service '{name}' (branch {args.branch}) …")
                service = rd.request("POST", "/services", body)
            except ApiError as e:
                detail = _extract(e.body, "message") or e.body[:300]
                raise SystemExit(
                    f"✗ Render refused to create the service ({e.status}): {detail}\n"
                    "  Most likely your GitHub is not connected to Render:\n"
                    "  render.com → avatar → Account → GitHub → Connect\n"
                    "  then re-run this script (it will reuse what exists)."
                )
        service_id = service["id"]
        domain = service.get("publicDomain") or domain
        rd.info(f"service id={service_id} domain={domain}")

        def deploy_state():
            s = rd.request("GET", f"/services/{service_id}")
            state = s.get("deploymentState") or "?"
            if state == "deployed":
                return True
            if state == "errored":
                raise SystemExit(
                    f"✗ Render deploy errored — see the build logs in the "
                    f"dashboard: https://dashboard.render.com"
                )
            return f"deploying ({state})"

        wait_for("Render deploy", deploy_state, timeout=1500, interval=15)
        step_render.done(f"https://{domain}")
        return {"id": service_id, "domain": domain, "name": service.get("name")}


# ---------------------------------------------------------------------------
# 3) Netlify
# ---------------------------------------------------------------------------

def find_netlify_site(nf: Api, name: str) -> dict | None:
    for site in nf.request("GET", "/sites?page_size=100") or []:
        if site.get("name") == name:
            return site
    return None


def step_netlify(args, nf: Api, render_domain: str) -> dict:
    with Step("Netlify: create site (React SPA — the one link)"):
        site = None
        used_name = None
        for name in NETLIFY_SITE_NAMES:
            if args.netlify_name:
                name = args.netlify_name
                break
            site = find_netlify_site(nf, name)
            if site:
                used_name = name
                nf.info(f"reusing existing site '{name}'")
                break
        if not site:
            last_err = None
            for name in NETLIFY_SITE_NAMES:
                if args.netlify_name:
                    name = args.netlify_name
                    break
                try:
                    nf.info(f"creating site '{name}' from github:{REPO_OWNER}/{REPO_NAME} ({args.branch}) …")
                    site = nf.request("POST", "/sites", {
                        "name": name,
                        "repo": {
                            "url": f"https://github.com/{REPO_OWNER}/{REPO_NAME}",
                            "branch": args.branch,
                        },
                        # Baked into the SPA at build time (Vite).
                        "build_settings": {
                            "env": {"VITE_API_BASE": f"https://{render_domain}"},
                        },
                    })
                    used_name = name
                    break
                except ApiError as e:
                    last_err = e
                    detail = _extract(e.body, "message") or e.body[:200]
                    nf.info(f"  '{name}': {detail}")
            if not site:
                raise SystemExit(f"✗ Could not create Netlify site: {last_err}")
        site_id = site["id"]
        url = site.get("ssl_url") or site.get("url") or ""

        def build_state():
            deploys = nf.request("GET", f"/sites/{site_id}/deploys") or []
            if not deploys:
                return "waiting for first deploy to start"
            state = deploys[0].get("state") or "?"
            if state == "ready":
                return True
            if state == "error":
                raise SystemExit(
                    f"✗ Netlify build failed — open the deploy in the "
                    f"dashboard to read the logs: {url}"
                )
            return f"building ({state})"

        wait_for("Netlify build", build_state, timeout=900, interval=10)
        # Refresh to get the final URL (name may have been auto-adjusted).
        site = nf.request("GET", f"/sites/{site_id}") or site
        url = site.get("ssl_url") or site.get("url") or url
        step_netlify.done(url)
        return {"id": site_id, "url": url, "name": site.get("name")}


# ---------------------------------------------------------------------------
# 4) Reconcile cross-links
# ---------------------------------------------------------------------------

def reconcile(rd: Api, nf: Api, render: dict, netlify: dict):
    with Step("Reconcile cross-links (CORS origin + allowed hosts)"):
        # Render: CORS must allow the real Netlify origin.
        service = rd.request("GET", f"/services/{render['id']}")
        envs = {e["key"]: e for e in service.get("envVars") or []}
        changed = []
        cors = envs.get("CORS_ALLOWED_ORIGINS", {}).get("value")
        if cors != netlify["url"]:
            envs["CORS_ALLOWED_ORIGINS"] = {"key": "CORS_ALLOWED_ORIGINS", "value": netlify["url"]}
            changed.append("CORS_ALLOWED_ORIGINS")
        hosts = envs.get("ALLOWED_HOSTS", {}).get("value")
        if hosts != render["domain"]:
            envs["ALLOWED_HOSTS"] = {"key": "ALLOWED_HOSTS", "value": render["domain"]}
            changed.append("ALLOWED_HOSTS")
        if changed:
            print(f"    … updating {', '.join(changed)} → redeploys Render (~1 min)")
            sys.stdout.flush()
            rd.request("PUT", f"/services/{render['id']}",
                       {"envVars": list(envs.values())})

            def deploy_state():
                s = rd.request("GET", f"/services/{render['id']}")
                state = s.get("deploymentState") or "?"
                return True if state == "deployed" else f"redeploying ({state})"

            wait_for("Render re-deploy", deploy_state, timeout=1200, interval=15)
        else:
            print("    … already in sync — nothing to do")
            sys.stdout.flush()
        reconcile.done()


# ---------------------------------------------------------------------------
# 5) Verify
# ---------------------------------------------------------------------------

def http_get(url: str, origin: str | None = None, timeout: int = 90):
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json, text/html")
    if origin:
        req.add_header("Origin", origin)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, dict(resp.headers), resp.read().decode(errors="replace")


def http_post(url: str, body: dict, origin: str | None = None, timeout: int = 90):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if origin:
        req.add_header("Origin", origin)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, dict(resp.headers), resp.read().decode(errors="replace")


def step_verify(netlify: dict, render: dict):
    with Step("End-to-end verification"):
        base_api = f"https://{render['domain']}/api"
        site = netlify["url"].rstrip("/")

        st, _, html = http_get(site + "/")
        assert st == 200 and "<div id=\"root\"" in html, "SPA shell not served"
        print("    … SPA shell: 200 OK")

        st, headers, body = http_get(base_api + "/", origin=site)
        assert st == 200 and '"api"' in body, "API root not answering"
        print(f"    … API root: 200 OK (CORS header: "
              f"{headers.get('Access-Control-Allow-Origin', 'missing')} )")

        # CORS preflight for the login POST.
        req = urllib.request.Request(base_api + "/auth/token/", method="OPTIONS")
        req.add_header("Origin", site)
        req.add_header("Access-Control-Request-Method", "POST")
        req.add_header("Access-Control-Request-Headers", "content-type, authorization")
        with urllib.request.urlopen(req, timeout=60) as resp:
            pre = dict(resp.headers)
        assert "authorization" in (pre.get("Access-Control-Allow-Headers") or "").lower(), \
            "CORS preflight did not allow Authorization header"
        print("    … CORS preflight: allowed (POST + Authorization)")

        # The SPA bundle must actually contain the Render origin (build-time env).
        m = re.search(r'src="(/assets/[^"]+\.js)"', html)
        if m:
            _, _, js = http_get(site + m.group(1))
            if render["domain"] in js:
                print(f"    … SPA bundle references https://{render['domain']} ✓")
            else:
                print("    ⚠ SPA bundle does NOT contain the Render domain — "
                      "the VITE_API_BASE build env var may be missing on "
                      "Netlify (Site settings → Environment variables). "
                      "API calls would fail; fix and redeploy Netlify.")

        # Wait for the background seed to finish (catalog may be briefly empty
        # right after the very first boot of a fresh database).
        def tools_ready():
            try:
                st, _, body = http_get(base_api + "/tools/?limit=1")
                data = json.loads(body)
                if data.get("count"):
                    return True
                return f"catalog still seeding (count={data.get('count', 0)})"
            except Exception:
                return "API warming up"

        wait_for("catalog seed", tools_ready, timeout=420, interval=10)
        st, _, body = http_get(base_api + "/tools/?limit=1")
        count = json.loads(body).get("count")
        print(f"    … catalog: {count} tools live")

        st, _, body = http_post(base_api + "/auth/token/",
                                {"username": DEMO_USERNAME, "password": DEMO_PASSWORD},
                                origin=site)
        assert st == 200 and "access" in json.loads(body), "demo login failed"
        print(f"    … demo login ({DEMO_USERNAME}): JWT issued ✓")
        step_verify.done()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--branch", default=DEFAULT_BRANCH,
                        help="Git branch to deploy (default: the session branch; "
                             "use 'main' after merging)")
    parser.add_argument("--region", default="",
                        help="Supabase region code (default: try Mumbai first)")
    parser.add_argument("--render-name", default="", help="Render service name")
    parser.add_argument("--netlify-name", default="", help="Netlify site name")
    args = parser.parse_args()

    print("=" * 72)
    print("  AI Tool Discovery Platform — one-command cloud deploy")
    print("  Supabase (Postgres+pgvector) · Render (API) · Netlify (SPA)")
    print("=" * 72)

    print("\nTokens (set as env vars, or you'll be prompted):")
    sb_token = prompt_env("SUPABASE_TOKEN", "Supabase access token")
    rd_token = prompt_env("RENDER_API_KEY", "Render API key")
    nf_token = prompt_env("NETLIFY_TOKEN", "Netlify personal access token")

    sb = Api(SUPABASE_API, sb_token)
    rd = Api(RENDER_API, rd_token)
    nf = Api(NETLIFY_API, nf_token)

    # Netlify's URL is needed as Render's CORS origin. We don't know the
    # final subdomain until Netlify creates the site, so seed Render with
    # the first candidate and reconcile after.
    netlify_url_guess = f"https://{NETLIFY_SITE_NAMES[0]}.netlify.app"

    project, db_url = step_supabase(args, sb)
    render = step_render(args, rd, db_url, netlify_url_guess)
    netlify = step_netlify(args, nf, render["domain"])
    reconcile(rd, nf, render, netlify)
    step_verify(netlify, render)

    print("\n" + "=" * 72)
    print("  🎉 DONE — your one link:")
    print()
    print(f"       {netlify['url']}")
    print()
    print(f"  Login for the demo account:   {DEMO_USERNAME} / {DEMO_PASSWORD}")
    print("=" * 72)
    print("""
  Notes:
  • Free Render instance sleeps after ~15 min idle → first visitor after a
    break waits 30–60 s. Keep a tab open during live demos.
  • Supabase's DB pauses after ~1 week with zero visits; the next visit
    wakes it (~1 min).
  • Pushing to the deployed branch auto-redeploys both services.
  • Rotate/delete the three tokens you used — they were pasted in chat.
  • To stop paying nothing is needed; to tear down: delete the Render
    service, the Netlify site and the Supabase project in their dashboards.
""")


if __name__ == "__main__":
    main()
