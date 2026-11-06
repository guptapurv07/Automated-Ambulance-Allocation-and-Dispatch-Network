# Dashboard

The web dashboard of the dispatch system: a live map of the fleet, reporting an emergency, following each
incident, the history of finished calls, and the state of the server. React, TypeScript and Vite; the map is
Leaflet on OpenStreetMap.

## Run it

It needs Node 20.19 or newer, and the dispatch server running on port 8000 (see the main README).

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The dev server forwards every `/api/…` request to the server at
`http://127.0.0.1:8000`, so the dashboard and the API share one address and need no CORS setup.

## Pages

| Page | What it shows |
|---|---|
| Live | The city map with every ambulance and hospital, the active dispatches, the fleet by status, the engine settings, and the call simulator |
| Report emergency | The call taker's form: place, severity, type, patients; the location can be picked on the map |
| Incidents | Every call, filtered by status and severity; each one opens its own live page |
| History | Finished dispatches with their response time against the target for the severity |
| Fleet | Every ambulance with its status and position, and the hospitals |
| System | Database, Redis, clock, queue and workers, and the settings the server runs with |

The simulator on Live sends random calls through the normal API, either one every few seconds or a burst of
more calls than there are ambulances, so some have to wait.

## Scripts

| Command | |
|---|---|
| `npm run dev` | Dev server with hot reload |
| `npm run build` | Type-check and build into `dist/` |
| `npm run preview` | Serve the build (also forwards `/api`) |
| `npm test` | Unit tests (routing and movement along roads) |
| `npm run gen:api` | Regenerate `src/api/schema.d.ts` from the running server's OpenAPI schema |

## Ambulances on the map

The server moves an ambulance only when it reaches the patient and the hospital. In between, the dashboard
draws it driving along real roads: `public/roads.json` is the road network of the service area, from
OpenStreetMap (© OpenStreetMap contributors, ODbL), and `node scripts/build-roads.mjs` builds it again. The
timing still comes from the server; the roads only decide the line the ambulance follows.
