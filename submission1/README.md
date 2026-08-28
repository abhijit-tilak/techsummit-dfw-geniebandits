# Lakebase submission — NorthPeak Retail (Genie Bandits)

This folder is the Lakebase evidence pack for the NorthPeak Stockout & Markdown
Rescue app. Every artifact here was **executed live** against the Lakebase project
`dbdemos-asset-generator` on workspace `fe-sandbox-rkm-sandbox-1` (profile
`rkm-sandbox-1`); the `results/*.json` files are the committed outputs of those runs.

## How it maps to the rubric

Legend: ✅ built **and** ran (evidence committed) · ⧗ built + prepared, one UI click
away (reverse Lakehouse Sync is UI-only).

### Lakehouse ↔ Lakebase sync

| Rubric row | Status | Code | Evidence |
|---|---|---|---|
| Lakebase instance in code + connectivity check that ran | ✅ | `01_provision_branches.sh`, `02_connectivity_check.py`, `lib/lb.py` | `results/lakebase_connectivity_result.json` |
| Governed UC table synced into Lakebase, returns rows | ✅ | `05_synced_table.sh` | `results/synced_table_result.json` (14,000 rows) |
| Operational schema modeled: related tables + keys | ✅ | `migrations/001_operational_schema.sql` | `results/operational_schema_result.json` (5 FKs) |
| Separate writable Postgres tables ≠ read-only synced | ✅ | migration 001 (`northpeak_ops.*`) vs `public.store_sku_position_synced` | `results/writable_proof.json` |
| Sync defined as code (Terraform / not UI-only) | ✅ | `sync.tf` + `05_synced_table.sh` | `results/synced_table_result.json` |
| Reverse Lakehouse Sync → UC Delta | ⧗ | `reverse_sync/` (prepared: REPLICA IDENTITY FULL set) | `reverse_sync/REVERSE_SYNC_UI_STEPS.md` → `results/reverse_sync_sample.json` |
| Reverse Delta shows SCD2 history + metadata cols | ⧗ | `reverse_sync/11_capture_reverse_sync.py` | → `results/reverse_scd2_result.json` |

### Branching

| Rubric row | Status | Code | Evidence |
|---|---|---|---|
| Dev branch off main, creation captured in code | ✅ | `01_provision_branches.sh` (`geniebandits-dev`) + git branch `feat/lakebase-rubric` | `results/branches_result.json` |
| Branch changes committed as versioned artifacts | ✅ | `migrations/`, `results/`, progressive commits | git history |
| Main stays clean until promotion (git history) | ✅ | work isolated on `feat/lakebase-rubric`; promoted via PR | git log / PR |
| Both branch uses: dev iteration + throwaway forecast | ✅ | `geniebandits-dev` (iteration) + `geniebandits-forecast` (throwaway, 4h TTL) | `results/forecast_throwaway_result.json` |
| Scale-to-zero for idle branches | ✅ | `01_provision_branches.sh` (autoscale 0.5 CU, suspend when idle) | `results/branches_result.json` |

### Agentic development

| Rubric row | Status | Code | Evidence |
|---|---|---|---|
| Agent change committed as diff / migration | ✅ | `migrations/001_*.sql`, `migrations/002_*.sql` | git diffs |
| Change validated by committed test + result | ✅ | `tests/test_operational_schema.sql`, `09_run_tests.py` | `results/test_result.json` (8/8) |
| Validated change promoted via merge / PR into main | ✅ | PR `feat/lakebase-rubric` → `main` | PR link |
| Progressive, layered build in commit history | ✅ | 8+ scoped commits (branching → schema → hybrid → sync → query → tests → forecast) | git log |

### Lakebase Search

| Rubric row | Status | Code | Evidence |
|---|---|---|---|
| Hybrid search (vector + full-text) over a text column | ✅ | `migrations/002_hybrid_search.sql` (HNSW + GIN), `07_hybrid_search.py` (RRF) | `results/hybrid_search_index_result.json` |
| Search returns relevant records for an NL query | ✅ | `07_hybrid_search.py` | `results/search_result.json`, `search_query.txt` |

### Domain question

| Rubric row | Status | Code | Evidence |
|---|---|---|---|
| Low-latency query answers a representative question | ✅ | `core_query.sql`, `08_core_query.py` | `results/core_query_result.json` (server 10.6 ms) |

## Run order

```bash
export PROFILE=rkm-sandbox-1
./01_provision_branches.sh            # dev + throwaway branches, scale-to-zero
python 02_connectivity_check.py       # connectivity proof
python 03_apply_migrations.py         # operational schema + writable proof
python 04_load_embeddings.py          # hybrid-search DDL + product embeddings
./05_synced_table.sh                  # governed UC MV -> Lakebase (forward sync as code)
python 07_hybrid_search.py            # hybrid (vector + full-text) search
python 08_core_query.py               # low-latency domain query
python 09_run_tests.py                # validate migrations (8/8)
python 10_forecast_throwaway.py       # throwaway forecasting branch
# then the one UI step:
#   follow reverse_sync/REVERSE_SYNC_UI_STEPS.md, then
#   python reverse_sync/11_capture_reverse_sync.py --catalog <C> --schema <S>
```

## Architecture in one line

Governed lakehouse MV → **forward-synced** (read-only) into Lakebase → joined with a
**writable** operational schema (managers approve recovery moves) → **hybrid-searchable**
substitutes → operational writes **reverse-synced** back to the lakehouse as SCD2 history.
Dev iteration and throwaway forecasting run on isolated, scale-to-zero branches.
