# Deploying `api` and `web` on Railway

Two services in the existing **regimecast** project, next to the **recorder**, which is not touched.

| Service | URL | Deploys from branch | Region |
|---|---|---|---|
| `web` | https://web-production-210f7.up.railway.app | `railway/web` | EU West (europe-west4) |
| `api` | https://api-production-bb6a.up.railway.app (docs: `/docs`) | `railway/api` | EU West (europe-west4) |
| `recorder` | https://recorder-production-796f.up.railway.app/health | `main` (unchanged) | EU West (europe-west4) |

EU West because Binance blocks US servers (the api calls Binance for live prices).

## Why the `railway/api` and `railway/web` branches

Railway applies the repo-root `railway.json` to every service built from this repo, and it no
longer lets a new service point at a different config file (config-as-code is deprecated). The
root `railway.json` belongs to the recorder, so a plain build of `api` produced the recorder image.

So `api` and `web` deploy from **generated branches**: the same files as the source branch, with
`railway.json` replaced by `deploy/railway.api.json` or `deploy/railway.web.json`.
`scripts/sync_railway_branches.sh` builds them, and the **railway branches** GitHub workflow runs
it on every push to the source branch. Never commit to `railway/*` by hand; they are overwritten.

The recorder tracks `main`, whose `railway.json` stays exactly as it was.

## Service settings (already done)

- **api**: Dockerfile `Dockerfile.api`, healthcheck `/health`, variable `PORT=8000`, domain on port 8000.
- **web**: Dockerfile `web/Dockerfile` (build context is the repo root), healthcheck `/`,
  variables `PORT=3000`, `API_URL=https://${{api.RAILWAY_PUBLIC_DOMAIN}}`,
  `RECORDER_URL=https://recorder-production-796f.up.railway.app`, domain on port 3000.

The web site forwards `/api/*` to `API_URL` at run time, so the browser only talks to the web domain.

## After Phase 2 and Phase 3 are merged into `main`

1. In `.github/workflows/railway-branches.yml`, change `branches: [phase2-app]` to `branches: [main]`.
2. Push. The workflow regenerates `railway/api` and `railway/web` from `main`, and both services redeploy.
   (Or run `bash scripts/sync_railway_branches.sh main` once by hand.)
3. The News Mood panel lights up by itself once the api has `/news-mood`.

Before **2026-12-01** Railway stops reading `railway.json` altogether. Move all three services to
Infrastructure as Code (`.railway/railway.ts`) before then; the branches can then be deleted.

## Local alternative

`docker compose up --build` runs both on your PC: http://localhost:3000 (API docs at http://localhost:8000/docs).
