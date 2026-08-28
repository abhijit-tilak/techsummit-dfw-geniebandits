# Reverse Lakehouse Sync — one-time UI step (Postgres → UC Delta, SCD Type 2)

Reverse **Lakehouse Sync** streams changes *from* Lakebase Postgres *into* Unity
Catalog Delta tables via CDC, producing an **SCD Type 2** history table with system
metadata columns. It is **UI-only** — there is no CLI or API to configure it (confirmed
in the Databricks Lakebase docs), so this is the single step in this submission that
must be clicked through once. Everything around it is already prepared in code.

## Already prepared for you (in code, executed)

- Writable source tables live in `databricks_postgres` → schema `northpeak_ops`:
  `recovery_actions`, `action_status_history`.
- **`REPLICA IDENTITY FULL`** is already set on both (required for CDC) — verified in
  `03_apply_migrations.py`'s branch and re-confirmed before this handoff.
- All columns use reverse-sync-supported types (text / int8 / float8 / timestamptz).

> ⚠️ Do **not** select `northpeak_ops.products` (it has a `vector(1024)` column, which
> reverse sync does not support) or `public.store_sku_position_synced` (that is the
> forward-synced table). Select **only** `recovery_actions` and `action_status_history`.

## Steps (≈2 minutes)

1. **Catalog** → Autoscaling project **`dbdemos-asset-generator`** → branch
   **`geniebandits-dev`** → **Lakehouse Sync** tab → **Start Sync**.
2. **Source**: database `databricks_postgres`, schema `northpeak_ops`; select tables
   **`recovery_actions`** and **`action_status_history`**.
3. **Destination**: a UC catalog **without default storage** (default-storage catalogs
   are unsupported) and a schema you can create tables in. Note the
   `<catalog>.<schema>` you pick — pass it to the capture script below.
4. Enable and **Start**. The sync creates SCD Type 2 history tables named
   `lb_recovery_actions_history` and `lb_action_status_history_history` with the CDC
   metadata columns `_pg_change_type`, `_pg_lsn`, `_pg_xid`, `_timestamp`, `_sort_by`.

## Capture the evidence (after the sync is active)

Generating a couple of writes and reading the history table proves it streamed and
that SCD2 history + metadata columns are present:

```bash
python reverse_sync/11_capture_reverse_sync.py \
  --catalog <DEST_CATALOG> --schema <DEST_SCHEMA>
```

This writes:
- `results/reverse_sync_sample.json` — sample rows streamed Postgres → Delta.
- `results/reverse_scd2_result.json` — SCD2 change history for one action
  (insert → update_preimage/update_postimage) with the system metadata columns.

Once those two files exist, the two reverse-sync rubric rows flip to Verified.
