import express from 'express';
import pg from 'pg';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const app = express();
const PORT = process.env.PORT || 8000;

// Env vars
const CATALOG = process.env.CATALOG;
const SCHEMA = process.env.SCHEMA;
const WAREHOUSE_ID = process.env.WAREHOUSE_ID;
const GENIE_SPACE_ID = process.env.GENIE_SPACE_ID;
const DASHBOARD_ID = process.env.DASHBOARD_ID;
const DATABRICKS_HOST = (process.env.DATABRICKS_HOST || '').replace('https://', '').replace(/\/$/, '');
const CLIENT_ID = process.env.DATABRICKS_CLIENT_ID;
const CLIENT_SECRET = process.env.DATABRICKS_CLIENT_SECRET;
const LAKEBASE_SCHEMA = process.env.LAKEBASE_SCHEMA || 'northpeak_ops';

// --- Lakebase Postgres pool (uses OAuth token as password) ---
let pgToken = null;
let pgTokenExpiry = 0;

async function getPgToken() {
  if (pgToken && Date.now() < pgTokenExpiry) return pgToken;
  const tokenUrl = `https://${DATABRICKS_HOST}/oidc/v1/token`;
  const resp = await fetch(tokenUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'client_credentials',
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
      scope: 'all-apis',
    }).toString(),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(`PG token error: ${JSON.stringify(data)}`);
  pgToken = data.access_token;
  pgTokenExpiry = Date.now() + (data.expires_in - 60) * 1000;
  return pgToken;
}

// Create pool with dynamic password (JWT token)
const pool = new pg.Pool({
  host: process.env.PGHOST,
  port: parseInt(process.env.PGPORT || '5432'),
  database: process.env.PGDATABASE,
  user: process.env.PGUSER,
  password: async () => await getPgToken(),
  ssl: { rejectUnauthorized: false },
  max: 5,
});

pool.on('error', (err) => console.error('PG pool error:', err.message));

app.use(express.json());
app.use(express.static(join(__dirname, 'public')));

app.use((req, res, next) => {
  if (req.path.startsWith('/api')) console.log(`${req.method} ${req.path}`);
  next();
});

// --- OAuth M2M token with explicit sql scope ---
let cachedToken = null;
let tokenExpiry = 0;

async function getToken() {
  if (cachedToken && Date.now() < tokenExpiry) return cachedToken;
  
  const tokenUrl = `https://${DATABRICKS_HOST}/oidc/v1/token`;
  console.log(`Requesting OAuth token from ${tokenUrl} with scope=sql`);
  
  const resp = await fetch(tokenUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'client_credentials',
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
      scope: 'all-apis',
    }).toString(),
  });
  
  const text = await resp.text();
  if (!resp.ok) {
    console.error(`Token error (${resp.status}): ${text.substring(0, 300)}`);
    throw new Error(`OAuth token request failed (${resp.status}): ${text.substring(0, 200)}`);
  }
  
  const data = JSON.parse(text);
  cachedToken = data.access_token;
  tokenExpiry = Date.now() + (data.expires_in - 60) * 1000;
  console.log(`Got token: scope=${data.scope}, expires_in=${data.expires_in}s`);
  return cachedToken;
}

// --- SQL Statement Execution API ---
async function querySQL(sql) {
  const token = await getToken();
  const url = `https://${DATABRICKS_HOST}/api/2.0/sql/statements`;
  const resp = await fetch(url, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      warehouse_id: WAREHOUSE_ID,
      statement: sql,
      wait_timeout: '30s',
      catalog: CATALOG,
      schema: SCHEMA,
    }),
  });
  
  const rawText = await resp.text();
  if (!resp.ok && !rawText.startsWith('{')) {
    throw new Error(`SQL API error (${resp.status}): ${rawText.substring(0, 200)}`);
  }
  
  const data = JSON.parse(rawText);
  if (data.status?.state === 'SUCCEEDED') {
    const cols = data.manifest?.schema?.columns?.map(c => c.name) || [];
    const rows = (data.result?.data_array || []).map(row => {
      const obj = {};
      cols.forEach((col, i) => { obj[col] = row[i]; });
      return obj;
    });
    return rows;
  }
  if (data.status?.state === 'FAILED') {
    throw new Error(`SQL failed: ${data.status.error?.message || JSON.stringify(data.status)}`);
  }
  throw new Error(`SQL unexpected state: ${JSON.stringify(data.status)}`);
}

// --- API: Config ---
app.get('/api/config', (req, res) => {
  res.json({
    genie_space_id: GENIE_SPACE_ID,
    dashboard_id: DASHBOARD_ID,
    databricks_host: 'https://' + DATABRICKS_HOST,
  });
});

// --- API: KPI summary (reads from Lakebase) ---
app.get('/api/kpis', async (req, res) => {
  try {
    const result = await pool.query(`
      SELECT
        SUM(lost_sales_exposure) AS lost_sales_usd,
        SUM(markdown_exposure) AS markdown_usd,
        SUM(stockout_count) AS stockout_count,
        SUM(overstock_count) AS overstock_count,
        SUM(position_count) AS total_positions
      FROM ${LAKEBASE_SCHEMA}.mv_store_position
    `);
    res.json(result.rows[0] || {});
  } catch (e) {
    console.error('/api/kpis error:', e.message);
    res.status(500).json({ error: e.message });
  }
});

// --- API: Store positions (reads from Lakebase) ---
app.get('/api/positions', async (req, res) => {
  try {
    const result = await pool.query(`
      SELECT store_id, store_name, city, climate_zone, store_lat, store_lng,
             position_status, COUNT(*)::int as position_count,
             ROUND(SUM(lost_sales_exposure_usd)::numeric) as lost_sales,
             ROUND(SUM(markdown_exposure_usd)::numeric) as markdown
      FROM ${LAKEBASE_SCHEMA}.gold_store_sku_position
      WHERE position_status IN ('stockout','at_risk','overstock')
      GROUP BY store_id, store_name, city, climate_zone, store_lat, store_lng, position_status
      ORDER BY lost_sales DESC
      LIMIT 200
    `);
    res.json(result.rows);
  } catch (e) {
    console.error('/api/positions error:', e.message);
    res.status(500).json({ error: e.message });
  }
});

// --- API: Open shortfalls (reads from Lakebase) ---
app.get('/api/shortfalls', async (req, res) => {
  try {
    const result = await pool.query(`
      SELECT store_id, store_name, city, climate_zone, store_lat, store_lng, product_id, product_name,
             on_hand_units, avg_daily_velocity, weeks_of_supply, position_status,
             ROUND(lost_sales_exposure_usd::numeric) as lost_sales_exposure_usd,
             nearest_surplus_store_id, nearest_surplus_on_hand,
             ROUND(nearest_surplus_distance_km::numeric, 1) as nearest_surplus_distance_km
      FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY lost_sales_exposure_usd DESC) as rn
        FROM ${LAKEBASE_SCHEMA}.gold_open_shortfalls
      ) ranked
      WHERE rn <= 10
      ORDER BY lost_sales_exposure_usd DESC
      LIMIT 50
    `);
    res.json(result.rows);
  } catch (e) {
    console.error('/api/shortfalls error:', e.message);
    res.status(500).json({ error: e.message });
  }
});

// --- API: Recovery recommendations (reads from Lakebase) ---
app.get('/api/recommendations/:storeId/:productId', async (req, res) => {
  try {
    const { storeId, productId } = req.params;
    const result = await pool.query(
      `SELECT recommended_move, recommended_source_store_id, recommended_units,
              predicted_recaptured_usd, move_cost_usd, predicted_net_value_usd, move_ranking
       FROM ${LAKEBASE_SCHEMA}.gold_recovery_recommendations
       WHERE store_id = $1 AND product_id = $2
       ORDER BY move_ranking`,
      [storeId, productId]
    );
    res.json(result.rows);
  } catch (e) {
    console.error('/api/recommendations error:', e.message);
    res.status(500).json({ error: e.message });
  }
});

// --- API: Approve (writes to Lakebase) ---
app.post('/api/approve', async (req, res) => {
  const client = await pool.connect();
  try {
    const { store_id, product_id, recommended_move, recommended_source_store_id,
            recommended_units, predicted_net_value_usd, approved_by, notes } = req.body;

    await client.query('BEGIN');

    // Insert approval record
    await client.query(
      `INSERT INTO ${LAKEBASE_SCHEMA}.recovery_approvals
       (store_id, product_id, recommended_move, recommended_source_store_id,
        recommended_units, predicted_net_value_usd, status, approved_by, approved_at, notes)
       VALUES ($1, $2, $3, $4, $5, $6, 'approved', $7, NOW(), $8)`,
      [store_id, product_id, recommended_move, recommended_source_store_id || null,
       parseInt(recommended_units) || 0, parseFloat(predicted_net_value_usd) || 0,
       approved_by || 'Priya Raghavan', notes || null]
    );

    // If transfer, create markdown hold on source store
    if (recommended_move === 'transfer' && recommended_source_store_id) {
      await client.query(
        `INSERT INTO ${LAKEBASE_SCHEMA}.markdown_holds
         (store_id, product_id, hold_reason, hold_until, created_by)
         VALUES ($1, $2, $3, $4, $5)`,
        [recommended_source_store_id, product_id,
         `Transfer to ${store_id} approved`,
         new Date(Date.now() + 14 * 86400000).toISOString().split('T')[0],
         approved_by || 'Priya Raghavan']
      );
    }

    await client.query('COMMIT');
    console.log('APPROVAL written to Lakebase:', { store_id, product_id, recommended_move, recommended_units });
    res.json({ success: true, message: 'Recovery move approved and saved' });
  } catch (e) {
    await client.query('ROLLBACK').catch(() => {});
    console.error('/api/approve error:', e.message);
    res.status(500).json({ error: e.message });
  } finally {
    client.release();
  }
});

// --- API: Recent approvals from Lakebase ---
app.get('/api/approvals', async (req, res) => {
  try {
    const result = await pool.query(
      `SELECT * FROM ${LAKEBASE_SCHEMA}.recovery_approvals ORDER BY created_at DESC LIMIT 20`
    );
    res.json(result.rows);
  } catch (e) {
    console.error('/api/approvals error:', e.message);
    res.status(500).json({ error: e.message });
  }
});

// --- API: Product Search (full-text search with shortfall/markdown prioritization) ---
app.get('/api/search', async (req, res) => {
  try {
    const q = (req.query.q || '').trim();
    if (!q) return res.json([]);

    // Convert search terms to tsquery format (prefix matching)
    const terms = q.split(/\s+/).map(t => t + ':*').join(' & ');

    const result = await pool.query(`
      WITH matched_products AS (
        SELECT DISTINCT product_id, product_name, category, seasonality
        FROM ${LAKEBASE_SCHEMA}.gold_store_sku_position
        WHERE search_vector @@ to_tsquery('english', $1)
           OR product_name ILIKE '%' || $2 || '%'
      ),
      product_status AS (
        SELECT
          mp.product_id, mp.product_name, mp.category, mp.seasonality,
          COUNT(*) FILTER (WHERE gsp.position_status = 'stockout') AS stockout_stores,
          COUNT(*) FILTER (WHERE gsp.position_status = 'at_risk') AS at_risk_stores,
          COUNT(*) FILTER (WHERE gsp.position_status = 'overstock') AS overstock_stores,
          ROUND(SUM(gsp.lost_sales_exposure_usd)::numeric) AS total_lost_sales,
          ROUND(SUM(gsp.markdown_exposure_usd)::numeric) AS total_markdown,
          BOOL_OR(gsp.position_status IN ('stockout','at_risk')) AS has_shortfall,
          BOOL_OR(gsp.markdown_exposure_usd > 0) AS has_markdown
        FROM matched_products mp
        JOIN ${LAKEBASE_SCHEMA}.gold_store_sku_position gsp USING (product_id)
        GROUP BY mp.product_id, mp.product_name, mp.category, mp.seasonality
      )
      SELECT * FROM product_status
      ORDER BY has_markdown DESC, has_shortfall DESC, total_lost_sales DESC
      LIMIT 20
    `, [terms, q]);

    // For each matched product with shortfall, find similar products (same category+seasonality) that also have shortfalls
    const products = result.rows;
    if (products.length > 0) {
      const topProduct = products[0];
      const similar = await pool.query(`
        SELECT product_id, product_name, category, seasonality,
               COUNT(*) FILTER (WHERE position_status = 'stockout') AS stockout_stores,
               ROUND(SUM(lost_sales_exposure_usd)::numeric) AS total_lost_sales,
               ROUND(SUM(markdown_exposure_usd)::numeric) AS total_markdown
        FROM ${LAKEBASE_SCHEMA}.gold_store_sku_position
        WHERE category = $1 AND seasonality = $2
          AND product_id != $3
          AND position_status IN ('stockout','at_risk')
        GROUP BY product_id, product_name, category, seasonality
        HAVING COUNT(*) > 0
        ORDER BY SUM(markdown_exposure_usd) DESC, SUM(lost_sales_exposure_usd) DESC
        LIMIT 5
      `, [topProduct.category, topProduct.seasonality, topProduct.product_id]);

      res.json({ matches: products, similar_with_shortfalls: similar.rows });
    } else {
      res.json({ matches: [], similar_with_shortfalls: [] });
    }
  } catch (e) {
    console.error('/api/search error:', e.message);
    res.status(500).json({ error: e.message });
  }
});

// --- API: Search -> shortfalls across stores (workflow step 1) ---
// Given a query, list open shortfalls (stockout/at_risk) at the store×product grain
// for every product matching the search. This is the clickable list in the Search tab.
app.get('/api/search/shortfalls', async (req, res) => {
  try {
    const q = (req.query.q || '').trim();
    if (!q) return res.json([]);
    const terms = q.split(/\s+/).map(t => t + ':*').join(' & ');

    const result = await pool.query(`
      WITH matched AS (
        SELECT DISTINCT product_id
        FROM ${LAKEBASE_SCHEMA}.gold_store_sku_position
        WHERE search_vector @@ to_tsquery('english', $1)
           OR product_name ILIKE '%' || $2 || '%'
      )
      SELECT g.store_id, g.store_name, g.city, g.climate_zone, g.store_lat, g.store_lng,
             g.product_id, g.product_name, g.category, g.seasonality,
             g.on_hand_units,
             ROUND(g.avg_daily_velocity::numeric, 1) AS avg_daily_velocity,
             g.position_status,
             ROUND(g.lost_sales_exposure_usd::numeric) AS lost_sales_exposure_usd
      FROM ${LAKEBASE_SCHEMA}.gold_store_sku_position g
      JOIN matched m USING (product_id)
      WHERE g.position_status IN ('stockout','at_risk')
      ORDER BY g.lost_sales_exposure_usd DESC
      LIMIT 100
    `, [terms, q]);

    res.json(result.rows);
  } catch (e) {
    console.error('/api/search/shortfalls error:', e.message);
    res.status(500).json({ error: e.message });
  }
});

// --- API: Transfer sources for a shortfall (workflow step 2) ---
// Other stores holding surplus (overstock) of the same product that could be
// transferred into the shortfall store, nearest first, with a suggested quantity.
app.get('/api/transfers/:storeId/:productId', async (req, res) => {
  try {
    const { storeId, productId } = req.params;
    const result = await pool.query(`
      WITH target AS (
        SELECT store_lat, store_lng, avg_daily_velocity
        FROM ${LAKEBASE_SCHEMA}.gold_store_sku_position
        WHERE store_id = $1 AND product_id = $2
        LIMIT 1
      )
      SELECT g.store_id, g.store_name, g.city, g.climate_zone,
             g.on_hand_units, g.position_status,
             ROUND((111.0 * sqrt(power(g.store_lat - t.store_lat, 2) + power(g.store_lng - t.store_lng, 2)))::numeric, 1) AS distance_km,
             LEAST(g.on_hand_units, GREATEST(CEIL(COALESCE(t.avg_daily_velocity, 0) * 14)::int, 1)) AS suggested_units
      FROM ${LAKEBASE_SCHEMA}.gold_store_sku_position g
      CROSS JOIN target t
      WHERE g.product_id = $2 AND g.store_id <> $1
        AND g.position_status = 'overstock' AND g.on_hand_units > 0
      ORDER BY distance_km ASC, g.on_hand_units DESC
      LIMIT 10
    `, [storeId, productId]);

    res.json(result.rows);
  } catch (e) {
    console.error('/api/transfers error:', e.message);
    res.status(500).json({ error: e.message });
  }
});

// --- API: Recovery options for a shortfall — same product AND similar products, ranked ---
// Search (Lakebase) builds the candidate set: nearest overstock source for the SAME product,
// plus similar products (same category) that are overstock elsewhere. AI (ai_similarity on the
// warehouse) scores how similar each candidate is; a net-value ranking then decides whether the
// best move is a same-product transfer or a similar-product substitute. AI steps are best-effort
// (wrapped) so the endpoint always returns a heuristic-ranked list if AI/permissions are unavailable.
const RATIONALE_ENDPOINT = process.env.RATIONALE_ENDPOINT || 'databricks-gpt-oss-20b';
const sqlLit = (s) => "'" + String(s == null ? '' : s).replace(/'/g, "''") + "'";

app.get('/api/recovery/:storeId/:productId', async (req, res) => {
  try {
    const { storeId, productId } = req.params;

    // 1) shortfall context
    const ctxq = await pool.query(
      `SELECT store_id, store_name, city, product_id, product_name, category, seasonality,
              store_lat, store_lng, avg_daily_velocity,
              ROUND(lost_sales_exposure_usd::numeric) AS lost_sales_exposure_usd
       FROM ${LAKEBASE_SCHEMA}.gold_store_sku_position
       WHERE store_id = $1 AND product_id = $2 LIMIT 1`,
      [storeId, productId]
    );
    if (ctxq.rows.length === 0) return res.json({ context: null, options: [] });
    const ctx = ctxq.rows[0];

    // 2) candidate sources: nearest overstock store per product (same product OR same category)
    const srcq = await pool.query(`
      WITH c AS (
        SELECT store_lat, store_lng FROM ${LAKEBASE_SCHEMA}.gold_store_sku_position
        WHERE store_id = $1 AND product_id = $2 LIMIT 1
      ),
      src AS (
        SELECT g.product_id, g.product_name, g.category, g.seasonality,
               g.store_id AS source_store_id, g.store_name AS source_store_name, g.city AS source_city,
               g.on_hand_units, ROUND(g.markdown_exposure_usd::numeric) AS markdown_exposure_usd,
               ROUND((111.0 * sqrt(power(g.store_lat - c.store_lat, 2) + power(g.store_lng - c.store_lng, 2)))::numeric, 1) AS distance_km,
               ROW_NUMBER() OVER (PARTITION BY g.product_id ORDER BY (power(g.store_lat - c.store_lat, 2) + power(g.store_lng - c.store_lng, 2)) ASC, g.on_hand_units DESC) AS rn
        FROM ${LAKEBASE_SCHEMA}.gold_store_sku_position g, c
        WHERE g.position_status = 'overstock' AND g.on_hand_units > 0 AND g.store_id <> $1
          AND (g.product_id = $3 OR g.category = $4)
      )
      SELECT product_id, product_name, category, seasonality, source_store_id, source_store_name,
             source_city, on_hand_units, markdown_exposure_usd, distance_km,
             CASE WHEN product_id = $3 THEN 'same' ELSE 'similar' END AS match_type
      FROM src WHERE rn = 1
    `, [storeId, productId, productId, ctx.category]);

    let same = srcq.rows.filter(r => r.match_type === 'same');
    let similar = srcq.rows.filter(r => r.match_type === 'similar')
      .sort((a, b) => (b.markdown_exposure_usd || 0) - (a.markdown_exposure_usd || 0))
      .slice(0, 8);

    // 3) AI similarity for the similar candidates (best-effort; heuristic fallback)
    let ranking_method = 'heuristic';
    const stockText = [ctx.product_name, ctx.category, ctx.seasonality].filter(Boolean).join(' ');
    const simById = {};
    if (similar.length > 0) {
      try {
        const ids = similar.map(s => sqlLit(s.product_id)).join(',');
        const rows = await querySQL(
          `SELECT product_id, ROUND(ai_similarity(${sqlLit(stockText)}, concat_ws(' ', product_name, category, subcategory, seasonality))::numeric, 4) AS sim
           FROM raw_products WHERE product_id IN (${ids})`
        );
        rows.forEach(r => { simById[r.product_id] = Number(r.sim); });
        if (rows.length > 0) ranking_method = 'ai (ai_similarity)';
      } catch (e) {
        console.warn('ai_similarity unavailable, using heuristic:', e.message);
      }
    }
    const heuristicSim = (s) => (s.seasonality === ctx.seasonality ? 0.85 : 0.65);

    // 4) net-value scoring — decides same-vs-similar via a single ranking
    const velocity = Number(ctx.avg_daily_velocity) || 0;
    const lost = Number(ctx.lost_sales_exposure_usd) || 0;
    const score = (o, sim) => {
      const expected = Math.max(1, Math.min(Math.ceil(velocity * 14), Number(o.on_hand_units) || 0));
      const coverage = Math.min(1, expected / Math.max(velocity * 30, 1));
      const recaptured = lost * coverage * sim;
      const md = Number(o.markdown_exposure_usd) || 0;
      const markdown_saved = o.on_hand_units ? md * (expected / o.on_hand_units) : 0;
      return {
        match_type: o.match_type, product_id: o.product_id, product_name: o.product_name,
        category: o.category, seasonality: o.seasonality,
        source_store_id: o.source_store_id, source_store_name: o.source_store_name,
        source_city: o.source_city, distance_km: o.distance_km, on_hand_units: o.on_hand_units,
        suggested_units: expected, similarity: Number(sim.toFixed ? sim.toFixed(3) : sim),
        recaptured_usd: Math.round(recaptured), markdown_saved_usd: Math.round(markdown_saved),
        score_usd: Math.round(recaptured + markdown_saved),
      };
    };

    const options = []
      .concat(same.map(o => score(o, 1.0)))
      .concat(similar.map(o => score(o, simById[o.product_id] != null ? simById[o.product_id] : heuristicSim(o))))
      .sort((a, b) => b.score_usd - a.score_usd)
      .slice(0, 8);

    // 5) one-line rationale for the top option (best-effort, single bounded FM call)
    let rationale = null;
    if (options.length > 0) {
      const top = options[0];
      try {
        const prompt = `You are a retail store-ops assistant. In ONE sentence, tell the manager why this is the best recovery for a stockout of ${ctx.product_name} at ${ctx.store_name}. Option: ${top.match_type === 'same' ? 'transfer the same product' : 'substitute a similar product (' + top.product_name + ')'} from ${top.source_store_name}, ${top.suggested_units} units, similarity ${top.similarity}, recapturing ~$${top.recaptured_usd} and clearing ~$${top.markdown_saved_usd} of markdown risk.`;
        const rows = await querySQL(`SELECT ai_query(${sqlLit(RATIONALE_ENDPOINT)}, ${sqlLit(prompt)}) AS r`);
        if (rows.length > 0) rationale = String(rows[0].r || '').trim();
      } catch (e) {
        console.warn('rationale FM call unavailable:', e.message);
      }
    }

    res.json({ context: ctx, options, ranking_method, rationale });
  } catch (e) {
    console.error('/api/recovery error:', e.message);
    res.status(500).json({ error: e.message });
  }
});

// --- API: Admin Sync (pulls Delta Lake -> Lakebase) ---
app.post('/api/admin/sync', async (req, res) => {
  console.log('Starting Delta Lake -> Lakebase sync...');
  const client = await pool.connect();
  try {
    const tables = [
      { pg: 'mv_store_position', sql: 'SELECT * FROM mv_store_position' },
      { pg: 'gold_open_shortfalls', sql: 'SELECT * FROM gold_open_shortfalls' },
      { pg: 'gold_recovery_recommendations', sql: 'SELECT * FROM gold_recovery_recommendations' },
      { pg: 'gold_store_sku_position', sql: 'SELECT * FROM gold_store_sku_position' },
    ];
    const results = {};
    for (const { pg, sql } of tables) {
      console.log(`  Syncing ${pg}...`);
      const rows = await querySQL(sql);
      // Truncate
      await client.query(`TRUNCATE ${LAKEBASE_SCHEMA}.${pg}`);
      // Batch insert
      if (rows.length > 0) {
        const cols = Object.keys(rows[0]);
        const batchSize = 200;
        let inserted = 0;
        for (let i = 0; i < rows.length; i += batchSize) {
          const batch = rows.slice(i, i + batchSize);
          const valueParts = batch.map(row => {
            const vals = cols.map(c => {
              const v = row[c];
              if (v === null || v === undefined) return 'NULL';
              if (typeof v === 'number') return String(v);
              return `'${String(v).replace(/'/g, "''")}'`;
            });
            return `(${vals.join(',')})`;
          });
          await client.query(`INSERT INTO ${LAKEBASE_SCHEMA}.${pg} (${cols.join(',')}) VALUES ${valueParts.join(',')}`);
          inserted += batch.length;
        }
        results[pg] = inserted;
        console.log(`  ✓ ${pg}: ${inserted} rows`);
      } else {
        results[pg] = 0;
      }
    }
    res.json({ success: true, synced: results });
  } catch (e) {
    console.error('Sync error:', e.message);
    res.status(500).json({ error: e.message });
  } finally {
    client.release();
  }
});

// --- API: Genie chat (Conversation API) — powers the AI Assistant tab ---
// Proxies the Genie Conversation API so the tab can render a chat UI instead of an
// (un-embeddable) iframe. Prefers the on-behalf-of user token so Genie runs as the
// logged-in user; falls back to the app service principal token.
// Genie needs the `dashboards.genie` OAuth scope (the app's all-apis token doesn't carry it).
// The app declares it in user_api_scopes, so we mint a dedicated SP token requesting that scope.
let genieScopedToken = null, genieScopedExpiry = 0;
async function getGenieScopedToken() {
  if (genieScopedToken && Date.now() < genieScopedExpiry) return genieScopedToken;
  const resp = await fetch(`https://${DATABRICKS_HOST}/oidc/v1/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'client_credentials', client_id: CLIENT_ID, client_secret: CLIENT_SECRET,
      scope: 'dashboards.genie',
    }).toString(),
  });
  const text = await resp.text();
  if (!resp.ok) throw new Error(`Genie token error (${resp.status}): ${text.substring(0, 200)}`);
  const data = JSON.parse(text);
  genieScopedToken = data.access_token;
  genieScopedExpiry = Date.now() + (data.expires_in - 60) * 1000;
  console.log(`Got Genie-scoped token: scope=${data.scope}`);
  return genieScopedToken;
}

async function genieToken(req) {
  if (process.env.GENIE_PAT) return process.env.GENIE_PAT;              // optional explicit override
  const userTok = req.headers['x-forwarded-access-token'];
  if (userTok) return userTok;                                          // on-behalf-of (if ever enabled)
  try { return await getGenieScopedToken(); }                          // SP token WITH genie scope
  catch (e) { console.warn('genie-scoped token failed, falling back to all-apis:', e.message); return await getToken(); }
}

async function genieApi(method, path, token, body) {
  const resp = await fetch(`https://${DATABRICKS_HOST}${path}`, {
    method,
    headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await resp.text();
  let data; try { data = JSON.parse(text); } catch (e) { data = { raw: text }; }
  if (!resp.ok) throw new Error(`Genie API ${resp.status}: ${text.substring(0, 300)}`);
  return data;
}

app.post('/api/genie/ask', async (req, res) => {
  try {
    if (!GENIE_SPACE_ID) return res.status(400).json({ error: 'GENIE_SPACE_ID not configured' });
    const question = (req.body && req.body.question || '').trim();
    const conversationId = req.body && req.body.conversation_id;
    if (!question) return res.status(400).json({ error: 'question required' });

    const token = await genieToken(req);
    const base0 = `/api/2.0/genie/spaces/${GENIE_SPACE_ID}`;

    // start a conversation, or add a message to the existing one
    let convId = conversationId, msgId;
    if (convId) {
      const m = await genieApi('POST', `${base0}/conversations/${convId}/messages`, token, { content: question });
      msgId = m.message_id || m.id || (m.message && m.message.id);
    } else {
      const c = await genieApi('POST', `${base0}/start-conversation`, token, { content: question });
      convId = c.conversation_id || (c.conversation && (c.conversation.conversation_id || c.conversation.id));
      msgId = c.message_id || (c.message && (c.message.message_id || c.message.id));
    }
    if (!convId || !msgId) return res.status(502).json({ error: 'Genie did not return conversation/message id' });

    // poll for completion
    const base = `${base0}/conversations/${convId}/messages/${msgId}`;
    const terminal = ['COMPLETED', 'FAILED', 'CANCELLED', 'QUERY_RESULT_EXPIRED'];
    let msg;
    for (let i = 0; i < 40; i++) {
      msg = await genieApi('GET', base, token);
      if (terminal.includes(msg.status)) break;
      await new Promise(r => setTimeout(r, 1500));
    }
    if (!msg || msg.status !== 'COMPLETED') {
      const errMsg = msg && msg.error ? (msg.error.error || msg.error.message || JSON.stringify(msg.error)) : null;
      return res.json({ conversation_id: convId, message_id: msgId, status: msg ? msg.status : 'TIMEOUT',
        answer: errMsg || `Genie did not complete (${msg ? msg.status : 'timeout'}).` });
    }

    // collect the text answer + (if any) the generated SQL and its result rows
    let answer = '', sql = null, columns = [], rows = [];
    for (const att of (msg.attachments || [])) {
      if (att.text && att.text.content) answer += (answer ? '\n\n' : '') + att.text.content;
      if (att.query) {
        sql = att.query.query || sql;
        if (!answer && att.query.description) answer = att.query.description;
        try {
          let qr;
          try {
            qr = await genieApi('GET', `${base}/attachments/${att.attachment_id}/query-result`, token);
          } catch (e1) {
            qr = await genieApi('GET', `${base}/query-result`, token); // older path fallback
          }
          const sr = qr.statement_response || qr;
          const cols = (sr.manifest && sr.manifest.schema && sr.manifest.schema.columns) || [];
          columns = cols.map(c => c.name);
          rows = (sr.result && sr.result.data_array) || [];
        } catch (e) { console.warn('genie query-result error:', e.message); }
      }
    }
    res.json({ conversation_id: convId, message_id: msgId, status: 'COMPLETED',
      answer: answer || '(Genie returned no text answer)', sql, columns, rows });
  } catch (e) {
    console.error('/api/genie/ask error:', e.message);
    res.status(500).json({ error: e.message });
  }
});

// --- API: Health ---
app.get('/api/health', (req, res) => {
  res.json({ status: 'ok', host: DATABRICKS_HOST, warehouse: WAREHOUSE_ID, catalog: CATALOG, hasClientId: !!CLIENT_ID, hasSecret: !!CLIENT_SECRET });
});

// --- Serve SPA ---
app.get('*', (req, res) => {
  res.sendFile(join(__dirname, 'public', 'index.html'));
});

app.listen(PORT, async () => {
  console.log(`NorthPeak Store Operations running on port ${PORT}`);
  console.log(`  Catalog: ${CATALOG}.${SCHEMA}`);
  console.log(`  Warehouse: ${WAREHOUSE_ID}`);
  console.log(`  Host: ${DATABRICKS_HOST}`);
  console.log(`  OAuth M2M: client_id=${CLIENT_ID?.substring(0,8)}...`);

  // Auto-sync on startup if Lakebase tables are empty
  try {
    const check = await pool.query(`SELECT COUNT(*) as cnt FROM ${LAKEBASE_SCHEMA}.mv_store_position`);
    if (parseInt(check.rows[0].cnt) === 0) {
      console.log('Lakebase tables are empty — starting auto-sync from Delta Lake...');
      const tables = [
        { pg: 'mv_store_position', sql: 'SELECT * FROM mv_store_position' },
        { pg: 'gold_open_shortfalls', sql: 'SELECT * FROM gold_open_shortfalls' },
        { pg: 'gold_recovery_recommendations', sql: 'SELECT * FROM gold_recovery_recommendations' },
        { pg: 'gold_store_sku_position', sql: 'SELECT * FROM gold_store_sku_position' },
      ];
      const client = await pool.connect();
      for (const { pg, sql } of tables) {
        console.log(`  Syncing ${pg}...`);
        const rows = await querySQL(sql);
        await client.query(`TRUNCATE ${LAKEBASE_SCHEMA}.${pg}`);
        if (rows.length > 0) {
          const cols = Object.keys(rows[0]);
          const batchSize = 200;
          for (let i = 0; i < rows.length; i += batchSize) {
            const batch = rows.slice(i, i + batchSize);
            const valueParts = batch.map(row => {
              const vals = cols.map(c => {
                const v = row[c];
                if (v === null || v === undefined) return 'NULL';
                if (typeof v === 'number') return String(v);
                return `'${String(v).replace(/'/g, "''")}'`;
              });
              return `(${vals.join(',')})`;
            });
            await client.query(`INSERT INTO ${LAKEBASE_SCHEMA}.${pg} (${cols.join(',')}) VALUES ${valueParts.join(',')}`);
          }
          console.log(`  \u2713 ${pg}: ${rows.length} rows synced`);
        }
      }
      client.release();
      console.log('\u2713 Auto-sync complete!');
    } else {
      console.log(`Lakebase tables already populated (${check.rows[0].cnt} KPI rows). Skipping sync.`);
    }
  } catch (e) {
    console.error('Auto-sync error:', e.message);
  }
});
