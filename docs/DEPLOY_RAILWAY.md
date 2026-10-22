# Deploying `api` and `web` on Railway

Two new services in the existing **regimecast** project. The **recorder** service is not touched:
it keeps its own `railway.json` at the repo root, and its watch patterns mean pushes to other
files never redeploy it.

| Service | Root directory | Config file | Dockerfile | Branch (for the P1 Mid demo) |
|---|---|---|---|---|
| `api` | `/` (repo root) | `/deploy/api/railway.json` | `Dockerfile.api` | `phase2-app` (switch to `main` after merge) |
| `web` | `/web` | `/web/railway.json` | `web/Dockerfile` | `phase2-app` (switch to `main` after merge) |

Both run in **EU West (europe-west4)**, because Binance blocks US servers.

## Steps (dashboard, about 10 minutes)

**api**
1. Open railway.com → project **regimecast** → **+ Create** → **GitHub Repo** → `algorian-commits/regimecast`.
2. Railway starts a first build straight away using the root `railway.json` (the recorder's). Open the new
   service → **Deployments** → remove/cancel that first deployment. Nothing breaks if it runs for a minute.
3. **Settings** of the new service:
   - Name: `api`
   - Source → Branch: `phase2-app`
   - Config-as-code → Railway config file: `/deploy/api/railway.json` (**important**: without this it builds the recorder)
   - Deploy → Regions: **EU West (Amsterdam)** only, 1 replica
   - Networking → **Generate Domain** (port 8000 if asked)
4. **Deploy**. Check `https://<api-domain>/health` shows `{"status":"ok"}` and `/market/BTCUSDT` shows a price.

**web**
1. **+ Create** → **GitHub Repo** → the same repo again. Cancel the first deployment as above.
2. **Settings**:
   - Name: `web`
   - Source → Root directory: `/web`, Branch: `phase2-app`
   - Config-as-code → Railway config file: `/web/railway.json`
   - Deploy → Regions: **EU West (Amsterdam)**
   - Networking → **Generate Domain** (port 3000 if asked)
3. **Variables** → add `API_URL` = `https://${{api.RAILWAY_PUBLIC_DOMAIN}}`
   (optional: `RECORDER_URL` = `https://recorder-production-796f.up.railway.app`, which is already the default)
4. **Deploy**. Open the web domain on your phone.

## After Phase 2 and 3 are merged into `main`

Switch both services' branch from `phase2-app` to `main`. The News Mood panel lights up by itself once
the api has `/news-mood` (Phase 3).

## Local alternative

`docker compose up --build` runs both on your PC: http://localhost:3000 (API docs at http://localhost:8000/docs).
