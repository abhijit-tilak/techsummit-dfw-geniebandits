-- =====================================================================================
-- Core domain question (low-latency store-ops query)
-- =====================================================================================
-- "Right now, which northern stores are stocked out of a cold-weather SKU with the
--  highest lost-sales exposure — and has a recovery move already been approved?"
--
-- Joins the READ-ONLY synced analytics table (store_sku_position_synced, fed from the
-- governed lakehouse) with the WRITABLE operational tables (stores, products,
-- recovery_actions) — one query spanning both sides of the Lakebase model.
-- =====================================================================================
SELECT
  s.store_id,
  st.city,
  s.product_id,
  p.product_name,
  s.on_hand_units,
  round(s.lost_sales_exposure_usd::numeric, 2) AS lost_sales_exposure_usd,
  ra.chosen_move,
  ra.status                       AS action_status,
  ra.net_recaptured_value
FROM public.store_sku_position_synced s
JOIN northpeak_ops.stores   st ON st.store_id   = s.store_id
JOIN northpeak_ops.products p  ON p.product_id  = s.product_id
LEFT JOIN northpeak_ops.recovery_actions ra
       ON ra.store_id = s.store_id AND ra.product_id = s.product_id
WHERE s.position_status = 'stockout'
  AND st.climate_zone   = 'North'
  AND p.seasonality     = 'cold_weather'
ORDER BY s.lost_sales_exposure_usd DESC
LIMIT 10;
