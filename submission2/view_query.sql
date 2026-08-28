-- =====================================================================================
-- Live decision view (Build 2, Step 1 — Visualize)
-- =====================================================================================
-- Ranks and FLAGS the important thing so it is obvious: open cold-weather stockouts in
-- the North, ordered so un-actioned, highest-exposure rows surface first. Reads the
-- READ-ONLY Build 1 synced table and LEFT JOINs the writable action table so a committed
-- decision is reflected on the next read (closed loop). This is a decision surface, not a
-- dashboard: every row is either NEEDS_DECISION or shows the action already taken.
-- =====================================================================================
SELECT
  s.store_id,
  st.city,
  s.product_id,
  p.product_name,
  s.on_hand_units,
  round(s.lost_sales_exposure_usd::numeric, 2) AS lost_sales_exposure_usd,
  round(s.lost_sales_exposure_usd::numeric, 2) AS priority_score,   -- higher = act first
  CASE
    WHEN ra.action_id IS NULL THEN 'NEEDS_DECISION'
    ELSE 'COMMITTED_' || upper(ra.status)
  END AS flag,
  ra.action_id,
  ra.chosen_move,
  ra.status        AS action_status,
  ra.committed_at
FROM public.store_sku_position_synced s
JOIN northpeak_ops.stores   st ON st.store_id  = s.store_id
JOIN northpeak_ops.products p  ON p.product_id = s.product_id
-- only a COMMITTED decision resolves a row; uncommitted proposals still surface
LEFT JOIN northpeak_ops.recovery_actions ra
       ON ra.store_id = s.store_id AND ra.product_id = s.product_id
      AND ra.committed_at IS NOT NULL
WHERE s.position_status = 'stockout'
  AND st.climate_zone   = 'North'
  AND p.seasonality     = 'cold_weather'
ORDER BY (ra.action_id IS NULL) DESC, s.lost_sales_exposure_usd DESC
LIMIT 15;
