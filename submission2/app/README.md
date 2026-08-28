# NorthPeak Store Operations — App

Live inventory view for store operations. Surfaces shortfalls and prescribes the recovery move
(transfer / expedite / substitute) for a manager to approve. Plain **Node/Express + `pg`** (no build
step): `server.js` serves the REST API and the static UI in `public/`.

- **URL:** https://dbgen-northpeak-7474644154611302.aws.databricksapps.com
- **App source (deployed from):** `/Workspace/Users/abhijit.tilak@databricks.com/dbgen-northpeak`
- **Data:** its own Lakebase (Postgres) database `dbgen_northpeak`, schema `northpeak_ops`, auto-synced
  from the Delta gold tables on boot (`gold_store_sku_position`, `gold_open_shortfalls`,
  `gold_recovery_recommendations`, `mv_store_position`).

## Tabs

| Tab | What it shows |
|---|---|
| **Operations** | The whole workflow (below): KPI row, a US map, the shortfalls panel, and the ranked recovery panel. |
| **Dashboard** | Embedded AI/BI dashboard. |
| **AI Assistant** | A chat UI backed by the **Genie Conversation API** (see below). |

There is **no separate Search tab** — a **search bar below the map** (on the Operations tab) filters
the shortfalls panel directly. (The old Search tab was redundant with the Operations shortfalls panel
and was removed; the search bar was also moved out of the header into the Operations view.)

## Operations workflow — search → shortfalls → ranked recovery

Everything happens on the Operations tab (see `public/app.js`: `runGlobalSearch` → `loadShortfalls`
→ `selectShortfall` → `loadRecovery`, and the endpoints in `server.js`):

1. **Search for an item** (e.g. "parka") in the search bar below the map → **`GET /api/search/shortfalls?q=`**
   resolves the query to matching products (Postgres full-text `search_vector` + `ILIKE`) and lists
   **open shortfalls across stores** (store×product, stockout/at-risk, by lost-sales exposure) in the
   left panel. With no query, the panel shows the top open shortfalls (`GET /api/shortfalls`).
   Clicking **clear** resets the filter.

2. **Click a shortfall (store + product).** Two things happen:
   - **The map zooms in** on that store (`flyTo`, zoom 9).
   - **`GET /api/recovery/:store/:product`** returns **ranked recovery options that mix the same
     product AND similar products** (right panel):
     - **Search** (Lakebase) builds candidates: the nearest **overstock** store for the *same*
       product, plus *similar* products (same category) that are overstock elsewhere.
     - **AI** scores how similar each candidate is — `ai_similarity(stockout text, candidate text)`
       on the SQL warehouse (best-effort; falls back to a season/category heuristic). A short
       manager rationale for the top pick is generated with `ai_query` against a foundation model.
     - **Ranking** decides same-vs-similar via one net-value score:
       `recaptured = lost_sales × coverage × similarity` **plus**
       `markdown_saved = markdown_exposure × (units moved / source on-hand)`. So a similar product
       sitting in heavy markdown can outrank a same-product transfer — exactly the "decide via a
       ranking" behavior. Each option shows a **Same product** / **Similar · sim 0.xx** badge.

3. **Approve.** Each option's button posts **`POST /api/approve`** — `recommended_move = 'transfer'`
   for same-product, `'substitute'` for similar (the substitute SKU is recorded in `notes`) — writing
   `northpeak_ops.recovery_approvals` (+ a `markdown_holds` row for transfers). The "for a manager to
   approve → action today" step.

> Candidates come from **overstock** stores on purpose: moving surplus that would otherwise be marked
> down into a stocked-out store fills demand **and** clears markdown-bound inventory — one move, two
> margin leaks closed. The `ranking_method` field reports whether AI similarity or the heuristic was used.

## AI Assistant — Genie chat (not an iframe)

The AI Assistant tab is a chat UI, not an embedded Genie iframe (Genie sets `X-Frame-Options` and
refuses to be framed — that was the "refused to connect" error). The frontend
(`renderAssistant`/`sendChat` in `public/app.js`) posts to **`POST /api/genie/ask`**, which proxies
the **Genie Conversation API** on the `GENIE_SPACE_ID` space (`server.js`):

1. `start-conversation` (first turn) or `conversations/{id}/messages` (follow-ups — Genie keeps
   context; the frontend holds `conversation_id`).
2. Poll `messages/{id}` until `COMPLETED` / `FAILED`.
3. Return the text answer, the generated **SQL**, and the **result rows** (from the attachment
   `query-result`). The UI renders the answer, a collapsible SQL block, and a result table.

Auth (important): Genie requires the **`dashboards.genie`** OAuth scope, which the app's default
`all-apis` token does **not** carry (that was the `403 … required scopes: genie`). This workspace has
**user-token forwarding disabled** (`forward_user_access_token` can't be set here), so the app can't
run Genie as the signed-in user. The fix: the app declares `dashboards.genie` in its
**`user_api_scopes`** (set via `databricks apps create-update … user_api_scopes`), and `server.js`
mints a **service-principal token requesting `scope=dashboards.genie`** for Genie calls
(`getGenieScopedToken`). An optional **`GENIE_PAT`** env var overrides this with a personal token if
ever needed. Other calls (warehouse, Lakebase) keep using the `all-apis` SP token.

> **Fixed:** the Genie space was attached to a **stale warehouse** (`a94beda3aab06fa4`, since deleted),
> so every Genie query failed. Repointed it to the live warehouse `fa7569cd826a3654`
> (`databricks genie update-space <space> --warehouse-id fa7569cd826a3654`).

## Notable UI behavior

- **Dark mode (default).** The header has a moon/sun toggle. Theme is driven by
  `data-theme` on `<html>`, backed by CSS custom properties in `styles.css` (dark on bare `:root`,
  light under `:root[data-theme="light"]`). An inline script in `index.html` `<head>` applies the
  saved theme before first paint (no flash); the choice persists in `localStorage['np-theme']` and
  **defaults to dark**. The map style swaps between Carto GL `dark-matter` / `positron` to match.
- **Map widget = MapLibre GL.** Vector basemap via keyless Carto GL styles. Store markers are colored
  by status (stockout / overstock / other); **clicking a marker or selecting a shortfall zooms in on
  that store** (`flyTo`, zoom 9). The Operations tab uses a **persistent shell** — the map lives
  outside the re-rendered panels, so selecting a shortfall updates the lists without tearing down or
  flickering the map. (Switching tabs or toggling theme disposes and rebuilds the map with the right style.)

## API routes (`server.js`)

| Route | Purpose |
|---|---|
| `GET /api/config` | Genie/dashboard ids + host for the embeds |
| `GET /api/kpis` | KPI totals (from `mv_store_position`) |
| `GET /api/positions` | Store positions for the map |
| `GET /api/shortfalls` | Default (unfiltered) shortfall list; now includes `store_lat/store_lng` for map zoom |
| `GET /api/search/shortfalls?q=` | **Step 1** — shortfalls across stores for a search (feeds the Operations panel) |
| `GET /api/recovery/:store/:product` | **Step 2** — ranked recovery: same-product transfers + similar-product substitutes (AI similarity + rationale) |
| `POST /api/genie/ask` | Genie chat proxy — `{question, conversation_id?}` → `{answer, sql, columns, rows, conversation_id}` |
| `POST /api/approve` | Approve a move → writes `recovery_approvals` (+ `markdown_holds` for transfers); substitutes recorded in `notes` |
| `GET /api/approvals` | Recent approvals |
| `POST /api/admin/sync` | Re-sync Delta gold tables → Lakebase |
| `GET /api/health` | Health check |
| `GET /api/recommendations/:store/:product` | (Legacy) heuristic recovery moves |
| `GET /api/transfers/:store/:product` | (Legacy) same-product transfer sources only |
| `GET /api/search?q=` | (Legacy) product-level match + similar-with-shortfalls |

`RATIONALE_ENDPOINT` env (default `databricks-gpt-oss-20b`) selects the FM used for the recovery
rationale; swap it for the governed `np-substitute-llm` endpoint once created.

## Config (`app.yaml` env)

`CATALOG`, `SCHEMA`, `WAREHOUSE_ID`, `GENIE_SPACE_ID`, `DASHBOARD_ID`, `LAKEBASE_*`, `DATABRICKS_HOST`.
Lakebase auth uses the app service principal's OAuth token as the Postgres password (minted in
`server.js` `getPgToken()`), refreshed before expiry.

## Deploy

Edit `server.js` / `public/*`, then:

```bash
databricks workspace import-dir ./public /Workspace/Users/abhijit.tilak@databricks.com/dbgen-northpeak/public --overwrite --profile fevmrkmsb
databricks workspace import server.js /Workspace/Users/abhijit.tilak@databricks.com/dbgen-northpeak/server.js --overwrite --profile fevmrkmsb
databricks apps deploy dbgen-northpeak --source-code-path /Workspace/Users/abhijit.tilak@databricks.com/dbgen-northpeak --profile fevmrkmsb
```
