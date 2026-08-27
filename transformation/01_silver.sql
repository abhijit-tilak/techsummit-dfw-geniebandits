-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Silver Layer — NorthPeak Retail
-- MAGIC Ingest raw Parquet from the landing volume into governed, typed streaming tables.

-- COMMAND ----------

CREATE OR REFRESH STREAMING TABLE raw_stores
COMMENT "Store master — 400 US locations with lat/lng and climate zone."
AS SELECT *
FROM STREAM read_files(
  '/Volumes/${catalog}/${schema}/raw_data/stores/',
  format => 'parquet'
);

-- COMMAND ----------

CREATE OR REFRESH STREAMING TABLE raw_products
COMMENT "Product catalog — ~40K SKUs with category, seasonality, and unit cost/price."
AS SELECT *
FROM STREAM read_files(
  '/Volumes/${catalog}/${schema}/raw_data/products/',
  format => 'parquet'
);

-- COMMAND ----------

CREATE OR REFRESH STREAMING TABLE raw_inventory_snapshots
COMMENT "Daily inventory snapshot per store × SKU — on_hand, in_transit, allocated."
AS SELECT *
FROM STREAM read_files(
  '/Volumes/${catalog}/${schema}/raw_data/inventory_snapshots/',
  format => 'parquet'
);

-- COMMAND ----------

CREATE OR REFRESH STREAMING TABLE raw_sales
COMMENT "Transaction-level sales — store, SKU, units, revenue, timestamp."
AS SELECT *
FROM STREAM read_files(
  '/Volumes/${catalog}/${schema}/raw_data/sales/',
  format => 'parquet'
);
