# RegimeCast web app (M2)

Next.js 16 + TypeScript + Tailwind. Pages: Risk Radar (`/`, live dashboard), Risk Check, Data Health, Live Status,
Log in / Sign up and Account (Phase 8).

```bash
npm install
npm run dev            # http://localhost:3000, expects the API at API_URL (default http://127.0.0.1:8000)
npm test               # unit tests for the logic in src/lib
npm run gen:api        # regenerate src/api/schema.d.ts after docs/openapi.json changes (CI checks this)
```

How it talks to the backend: the browser only calls this site. `src/app/api/[...path]/route.ts`
forwards `/api/*` to the FastAPI backend at `API_URL` (read at run time), and
`src/app/recorder/health/route.ts` reads the live recorder's `/health` at `RECORDER_URL`.
Endpoints that answer `503 {"status":"not_ready"}` (or 404 because they are not deployed yet)
show a friendly placeholder, never an error.

Login cookies: the forwarder passes `Cookie` to the API and its `Set-Cookie` back, so the session cookie
belongs to this site. Live prices: the browser opens a WebSocket to the API itself (route handlers cannot
forward WebSockets); `/live-config` returns its address from `PUBLIC_API_URL` or `API_URL`.
