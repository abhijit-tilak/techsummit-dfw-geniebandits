# =====================================================================================
# Lakehouse -> Lakebase forward sync — declarative IaC definition (Terraform)
# =====================================================================================
# The SAME sync that 05_synced_table.sh applies via the CLI, expressed declaratively
# so the sync lives in code (rubric: "sync is defined as code, not UI-only").
#
# terraform apply -var 'profile=rkm-sandbox-1'
#
# Note on IaC parity: on current Autoscaling Lakebase the CLI flow
# (databricks postgres create-synced-table) is the path that actually executes and
# produced results/synced_table_result.json (14,000 rows). This Terraform file is the
# declarative source-of-truth for the same resource; the databricks_synced_database_table
# resource tracks the Autoscaling API as the provider catches up. Both describe one sync:
# governed UC MV gold_store_sku_position -> Lakebase public.store_sku_position_synced.
# =====================================================================================

terraform {
  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = ">= 1.94.0"
    }
  }
}

variable "profile" {
  type    = string
  default = "rkm-sandbox-1"
}

provider "databricks" {
  profile = var.profile
}

locals {
  project = "dbdemos-asset-generator"
  catalog = "rkm_sandbox_1_catalog"
  schema  = "demo_workshop_northpeak_retail_stockout_markdown_rescue"
  branch  = "geniebandits-dev"
}

# Register the Lakebase Postgres DB as a UC catalog (sync target namespace).
resource "databricks_database_catalog" "lakebase_geniebandits" {
  name              = "lakebase_geniebandits"
  database_instance = local.project
  database_name     = "databricks_postgres"
  create_database_if_not_exists = false
}

# Governed UC MV -> Lakebase read-only synced table, SNAPSHOT mode, composite PK.
resource "databricks_synced_database_table" "store_sku_position_synced" {
  name = "${databricks_database_catalog.lakebase_geniebandits.name}.public.store_sku_position_synced"

  database_instance_name = local.project
  logical_database_name  = "databricks_postgres"

  spec {
    source_table_full_name = "${local.catalog}.${local.schema}.gold_store_sku_position"
    primary_key_columns    = ["store_id", "product_id"]
    scheduling_policy      = "SNAPSHOT"
    create_database_objects_if_missing = true

    new_pipeline_spec {
      storage_catalog = local.catalog
      storage_schema  = local.schema
    }
  }
}
