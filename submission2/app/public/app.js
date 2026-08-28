// NorthPeak Store Operations — vanilla JS
(function() {
  var content = document.getElementById('content');
  var state = {
    tab: 'operations', kpis: null, positions: [], shortfalls: [],
    selected: null, recovery: null, config: {}, errors: [],
    searchQuery: '', mapLoaded: false,
    chat: { conversationId: null, messages: [], busy: false }
  };
  var map = null;
  var markers = [];

  function api(path) {
    return fetch(path).then(function(resp) {
      if (!resp.ok) return resp.json().catch(function() { return { error: resp.statusText }; }).then(function(err) {
        state.errors.push(path + ': ' + (err.error || resp.statusText));
        return null;
      });
      return resp.json();
    }).catch(function(e) { state.errors.push(path + ': ' + e.message); return null; });
  }

  function postJson(path, body) {
    return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function(r) { return r.json().catch(function() { return { error: 'Bad response (' + r.status + ')' }; }); })
      .catch(function(e) { return { error: e.message }; });
  }

  function fmt(v) {
    if (!v || v === '0' || v === 'null' || v === null) return '$0';
    return '$' + Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 });
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // ── Theme (dark default, persisted) ──
  function currentTheme() { return document.documentElement.getAttribute('data-theme') || 'dark'; }
  function applyTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    try { localStorage.setItem('np-theme', t); } catch (e) {}
    var btn = document.getElementById('theme-toggle');
    if (btn) btn.innerHTML = (t === 'dark') ? '&#9790;' : '&#9728;';
  }
  function bindThemeToggle() {
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    applyTheme(currentTheme());
    btn.addEventListener('click', function() {
      applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
      if (map) { try { map.remove(); } catch (e) {} map = null; state.mapLoaded = false; }
      render();
    });
  }

  // ── Operations search (below the map) → filters the shortfalls panel ──
  function setNavActive(tab) {
    document.querySelectorAll('.nav-tab').forEach(function(t) {
      t.classList.toggle('active', t.dataset.tab === tab);
    });
  }
  function runSearch(q) {
    state.searchQuery = (q || '').trim();
    state.selected = null;
    state.recovery = null;
    var input = document.getElementById('ops-search-input');
    if (input) input.value = state.searchQuery;   // keep the box in sync (e.g. on clear)
    render();
    loadShortfalls();
  }

  // ── Tab navigation ──
  function bindNav() {
    document.querySelectorAll('.nav-tab').forEach(function(tab) {
      tab.addEventListener('click', function() {
        setNavActive(tab.dataset.tab);
        state.tab = tab.dataset.tab;
        if (map) { try { map.remove(); } catch (e) {} map = null; state.mapLoaded = false; }
        render();
      });
    });
  }

  function render() {
    if (state.tab === 'operations') renderOperations();
    else if (state.tab === 'dashboard') renderDashboard();
    else if (state.tab === 'assistant') renderAssistant();
  }

  // ── Operations: a persistent shell (map survives panel updates) ──
  function renderOperations() {
    if (!document.getElementById('ops-shell')) {
      var errHtml = state.errors.length > 0
        ? '<div style="background:rgba(229,72,77,0.12);border:1px solid var(--red);padding:12px;border-radius:6px;margin-bottom:16px;font-size:13px;color:var(--red)"><b>API Errors:</b><br>' + state.errors.map(esc).join('<br>') + '</div>'
        : '';
      content.innerHTML =
        '<div id="ops-shell">' + errHtml +
          '<div class="kpi-row" id="kpi-row"></div>' +
          '<div class="map-container"><div id="map"></div></div>' +
          '<form class="ops-search" id="ops-search" role="search">' +
            '<span class="ops-search-icon" aria-hidden="true">&#128269;</span>' +
            '<input id="ops-search-input" type="search" autocomplete="off" placeholder="Search products — name, category, season…" value="' + esc(state.searchQuery) + '" />' +
            '<button type="submit">Search</button>' +
          '</form>' +
          '<div class="panel-grid">' +
            '<div class="panel"><div class="panel-header" id="shortfall-title">Open Shortfalls</div><div class="panel-body" id="shortfall-list"></div></div>' +
            '<div class="panel"><div class="panel-header" id="recovery-title">Select a shortfall</div><div class="panel-body" id="recovery-body"></div></div>' +
          '</div>' +
        '</div>';
      var sf = document.getElementById('ops-search');
      if (sf) sf.addEventListener('submit', function(e) { e.preventDefault(); runSearch(document.getElementById('ops-search-input').value); });
    }
    if (!map || !mapAttached()) initMap();
    updateKpis();
    updateShortfalls();
    updateRecovery();
  }

  function updateKpis() {
    var el = document.getElementById('kpi-row');
    if (!el) return;
    var k = state.kpis || {};
    el.innerHTML =
      '<div class="kpi-card"><div class="label">Lost-Sales Exposure</div><div class="value red">' + (state.kpis ? fmt(k.lost_sales_usd) : '…') + '</div></div>' +
      '<div class="kpi-card"><div class="label">Markdown Exposure</div><div class="value amber">' + (state.kpis ? fmt(k.markdown_usd) : '…') + '</div></div>' +
      '<div class="kpi-card"><div class="label">Stockout Positions</div><div class="value red">' + (state.kpis ? Number(k.stockout_count || 0).toLocaleString() : '…') + '</div></div>' +
      '<div class="kpi-card"><div class="label">Overstock Positions</div><div class="value amber">' + (state.kpis ? Number(k.overstock_count || 0).toLocaleString() : '…') + '</div></div>';
  }

  function updateShortfalls() {
    var title = document.getElementById('shortfall-title');
    var list = document.getElementById('shortfall-list');
    if (!title || !list) return;
    title.innerHTML = state.searchQuery
      ? 'Shortfalls for &ldquo;' + esc(state.searchQuery) + '&rdquo; <a href="#" id="clear-search" style="font-weight:normal;font-size:12px;color:var(--accent);margin-left:8px">clear</a>'
      : 'Open Shortfalls (by lost-sales exposure)';

    if (state.shortfalls.length === 0) {
      list.innerHTML = '<div class="loading">' + (state.errors.length > 0 ? 'Failed to load' : (state.searchQuery ? 'No open shortfalls for that search.' : 'Loading shortfalls…')) + '</div>';
    } else {
      list.innerHTML = state.shortfalls.map(function(s, i) {
        var sel = state.selected && state.selected.store_id === s.store_id && state.selected.product_id === s.product_id;
        return '<div class="shortfall-row' + (sel ? ' selected' : '') + '" data-idx="' + i + '">' +
          '<div class="store">' + esc(s.store_name) + ' <span class="sku">(' + esc(s.store_id) + ')</span></div>' +
          '<div class="product">' + esc(s.product_name) + ' &mdash; ' + esc(s.position_status) + '</div>' +
          '<div class="exposure">Lost: ' + fmt(s.lost_sales_exposure_usd) + ' | Velocity: ' + Number(s.avg_daily_velocity || 0).toFixed(1) + '/day</div>' +
        '</div>';
      }).join('');
      list.querySelectorAll('.shortfall-row').forEach(function(row) {
        row.addEventListener('click', function() { selectShortfall(state.shortfalls[parseInt(row.dataset.idx)]); });
      });
    }
    var clr = document.getElementById('clear-search');
    if (clr) clr.addEventListener('click', function(e) { e.preventDefault(); runSearch(''); });
  }

  function selectShortfall(s) {
    state.selected = s;
    state.recovery = null;
    updateShortfalls();
    updateRecovery();
    flyToStore(s.store_lat, s.store_lng);   // zoom the map in on the store
    loadRecovery();
  }

  function updateRecovery() {
    var title = document.getElementById('recovery-title');
    var body = document.getElementById('recovery-body');
    if (!title || !body) return;
    var s = state.selected;
    title.innerHTML = s ? 'Recovery: ' + esc(s.product_name) + ' &mdash; ' + esc(s.store_name) : 'Select a shortfall';

    if (!s) { body.innerHTML = '<div class="loading">Click a shortfall to see same-product transfers and similar-product substitutes, ranked.</div>'; return; }
    if (state.recovery === null) { body.innerHTML = '<div class="loading">Ranking same-product transfers &amp; similar substitutes…</div>'; return; }

    var opts = (state.recovery.options || []);
    var html = '';
    if (state.recovery.rationale) {
      html += '<div class="rationale">&#129504; ' + esc(state.recovery.rationale) + '</div>';
    }
    if (state.recovery.ranking_method) {
      html += '<div class="rank-method">Ranked by net recaptured value + markdown cleared &middot; similarity via ' + esc(state.recovery.ranking_method) + '</div>';
    }
    if (opts.length === 0) {
      html += '<div class="loading">No transfer or substitute source found. Consider an expedite from DC-CENTRAL.</div>';
    } else {
      html += opts.map(function(o, i) {
        var badge = o.match_type === 'same'
          ? '<span class="match-badge match-same">Same product</span>'
          : '<span class="match-badge match-similar">Similar &middot; sim ' + Number(o.similarity).toFixed(2) + '</span>';
        var verb = o.match_type === 'same' ? 'Transfer' : 'Substitute';
        return '<div class="rec-card">' +
          '<div class="move-type"><span class="rank">' + (i + 1) + '</span>' + verb + ': ' + esc(o.product_name) + ' &nbsp;' + badge + '</div>' +
          '<div class="details">From ' + esc(o.source_store_name) + ' <span class="sku">(' + esc(o.source_store_id) + ')</span> &bull; ' + esc(o.source_city || '') + ' &bull; ' + Number(o.distance_km || 0).toFixed(1) + ' km &bull; ' + (o.suggested_units || 0) + ' units</div>' +
          '<div class="net-value">Net value: ' + fmt(o.score_usd) + '</div>' +
          '<div class="metric-line">Recaptured ' + fmt(o.recaptured_usd) + ' &bull; Markdown cleared ' + fmt(o.markdown_saved_usd) + '</div>' +
          '<button class="btn-approve" data-idx="' + i + '">Approve ' + (o.match_type === 'same' ? 'transfer' : 'substitute') + '</button>' +
        '</div>';
      }).join('');
    }
    body.innerHTML = html;
    body.querySelectorAll('.btn-approve').forEach(function(btn) {
      btn.addEventListener('click', function(e) {
        e.stopPropagation();
        btn.disabled = true; btn.textContent = 'Approving…';
        handleApprove(opts[parseInt(btn.dataset.idx)], btn);
      });
    });
  }

  // ── MapLibre GL map ──
  function mapAttached() {
    return map && map.getContainer && document.body.contains(map.getContainer());
  }
  function initMap() {
    var el = document.getElementById('map');
    if (!el || typeof maplibregl === 'undefined') return;
    if (map) { try { map.remove(); } catch (e) {} map = null; }
    state.mapLoaded = false;
    var style = currentTheme() === 'dark'
      ? 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'
      : 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json';
    try {
      map = new maplibregl.Map({ container: 'map', style: style, center: [-98.5, 39.5], zoom: 3.4, attributionControl: true });
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
      map.on('load', function() {
        state.mapLoaded = true;
        map.resize();
        addMarkers();
        if (state.selected) flyToStore(state.selected.store_lat, state.selected.store_lng);
      });
    } catch (e) { console.warn('Map init error:', e); }
  }
  function addMarkers() {
    if (!map || !state.mapLoaded) return;
    markers.forEach(function(m) { m.remove(); });
    markers = [];
    (state.positions || []).forEach(function(p) {
      if (!p.store_lat || !p.store_lng) return;
      var color = p.position_status === 'stockout' ? '#E5484D' : p.position_status === 'overstock' ? '#FFB020' : '#3C6997';
      var m = new maplibregl.Marker({ color: color, scale: 0.7 })
        .setLngLat([parseFloat(p.store_lng), parseFloat(p.store_lat)])
        .setPopup(new maplibregl.Popup({ offset: 16 }).setHTML('<b>' + esc(p.store_name) + '</b><br>' + esc(p.city || '') + '<br>' + esc(p.position_status) + '<br>Lost: ' + fmt(p.lost_sales)))
        .addTo(map);
      m.getElement().style.cursor = 'pointer';
      m.getElement().addEventListener('click', function() { flyToStore(p.store_lat, p.store_lng); });
      markers.push(m);
    });
  }
  function flyToStore(lat, lng) {
    if (!map || !state.mapLoaded || !lat || !lng) return;
    map.flyTo({ center: [parseFloat(lng), parseFloat(lat)], zoom: 9, speed: 1.4, essential: true });
  }

  // ── Data loads ──
  function loadShortfalls() {
    var path = state.searchQuery ? '/api/search/shortfalls?q=' + encodeURIComponent(state.searchQuery) : '/api/shortfalls';
    api(path).then(function(s) { state.shortfalls = s || []; if (state.tab === 'operations') updateShortfalls(); });
  }
  function loadRecovery() {
    if (!state.selected) return;
    var s = state.selected;
    api('/api/recovery/' + encodeURIComponent(s.store_id) + '/' + encodeURIComponent(s.product_id)).then(function(data) {
      state.recovery = data || { options: [] };
      updateRecovery();
    });
  }

  function handleApprove(opt, btn) {
    if (!opt || !state.selected) { if (btn) { btn.disabled = false; } return; }
    var move = opt.match_type === 'same' ? 'transfer' : 'substitute';
    var label = 'Approve ' + move;
    var notes = opt.match_type === 'similar'
      ? ('Substitute ' + opt.product_id + ' — ' + opt.product_name + ' from ' + opt.source_store_id)
      : null;
    var sel = state.selected;
    fetch('/api/approve', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        store_id: sel.store_id, product_id: sel.product_id,
        recommended_move: move, recommended_source_store_id: opt.source_store_id,
        recommended_units: opt.suggested_units, predicted_net_value_usd: opt.score_usd,
        approved_by: 'Priya Raghavan', notes: notes
      })
    }).then(function(resp) {
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      return resp.json();
    }).then(function() {
      if (btn) { btn.textContent = 'Approved ✓'; btn.classList.add('approved'); }
      var b = document.getElementById('recovery-body');
      if (b) { b.querySelectorAll('.btn-approve').forEach(function(x) { x.disabled = true; }); }
      showToast('Approved ' + move + ': ' + opt.suggested_units + ' units of ' + opt.product_name);
      confirmCommitted(sel, move, opt);
    }).catch(function(e) {
      if (btn) { btn.disabled = false; btn.textContent = label; }
      showToast('Error approving: ' + (e && e.message ? e.message : 'unknown'));
    });
  }

  // Closed loop: read the committed decision back from Lakebase and show it in-panel.
  function confirmCommitted(sel, move, opt) {
    api('/api/approvals').then(function(rows) {
      var body = document.getElementById('recovery-body');
      if (!body) return;
      var rec = (rows || []).filter(function(r) {
        return r.store_id === sel.store_id && r.product_id === sel.product_id;
      })[0];
      var when = rec && rec.approved_at ? new Date(rec.approved_at).toLocaleString() : 'just now';
      var id = rec ? rec.approval_id : '';
      var units = (rec && rec.recommended_units) || opt.suggested_units;
      var who = (rec && rec.approved_by) || 'Priya Raghavan';
      var banner = document.createElement('div');
      banner.className = 'committed-banner';
      banner.innerHTML = '✅ <b>Committed decision</b>' + (id ? ' (approval #' + esc(String(id)) + ')' : '') +
        ' &mdash; ' + esc(move) + ' ' + esc(String(units)) + ' units, approved by ' + esc(who) +
        ' at ' + esc(when) + '. Written to Lakebase and reflected on the next read (closed loop).';
      body.insertBefore(banner, body.firstChild);
    }).catch(function() {});
  }

  function renderDashboard() {
    content.innerHTML = '<iframe class="embed-frame" src="' + (state.config.databricks_host || '') + '/embed/dashboardsv3/' + (state.config.dashboard_id || '') + '"></iframe>';
  }
  // ── AI Assistant: a chat UI backed by the Genie Conversation API ──
  var CHAT_SUGGESTIONS = [
    'How much are we losing to stockouts and markdowns right now?',
    'Which products are driving both problems?',
    'Which southern stores are the biggest markdown risk?',
    "What's the best recovery move for Store 214?"
  ];

  function renderAssistant() {
    content.innerHTML =
      '<div class="chat">' +
        '<div class="chat-head">&#129504; Ask NorthPeak <span class="chat-sub">— natural-language answers over governed data, powered by Genie</span></div>' +
        '<div class="chat-messages" id="chat-messages"></div>' +
        '<div class="chat-suggestions" id="chat-suggestions"></div>' +
        '<form class="chat-input" id="chat-form">' +
          '<input id="chat-text" type="text" autocomplete="off" placeholder="Ask about stockouts, markdowns, or recovery moves…" />' +
          '<button type="submit" id="chat-send">Send</button>' +
        '</form>' +
      '</div>';
    renderChatMessages();
    renderChatSuggestions();
    var form = document.getElementById('chat-form');
    form.addEventListener('submit', function(e) {
      e.preventDefault();
      var input = document.getElementById('chat-text');
      var q = input.value; input.value = '';
      sendChat(q);
    });
    document.getElementById('chat-text').focus();
  }

  function renderChatSuggestions() {
    var el = document.getElementById('chat-suggestions');
    if (!el) return;
    if (state.chat.messages.length > 0) { el.innerHTML = ''; return; }
    el.innerHTML = CHAT_SUGGESTIONS.map(function(q) {
      return '<button class="chip" type="button" data-q="' + esc(q) + '">' + esc(q) + '</button>';
    }).join('');
    el.querySelectorAll('.chip').forEach(function(b) {
      b.addEventListener('click', function() { sendChat(b.dataset.q); });
    });
  }

  function chatTable(cols, rows) {
    if (!cols || !cols.length || !rows || !rows.length) return '';
    var head = '<tr>' + cols.map(function(c) { return '<th>' + esc(c) + '</th>'; }).join('') + '</tr>';
    var body = rows.slice(0, 20).map(function(r) {
      return '<tr>' + r.map(function(v) { return '<td>' + esc(v == null ? '' : v) + '</td>'; }).join('') + '</tr>';
    }).join('');
    var more = rows.length > 20 ? '<div class="chat-more">' + (rows.length - 20) + ' more rows…</div>' : '';
    return '<div class="chat-table-wrap"><table class="chat-table">' + head + body + '</table></div>' + more;
  }

  function renderChatMessages() {
    var box = document.getElementById('chat-messages');
    if (!box) return;
    if (state.chat.messages.length === 0) {
      box.innerHTML = '<div class="chat-empty">Ask a question about inventory, stockouts, markdowns, or recovery moves. Genie answers over the governed lakehouse tables and shows the SQL it ran.</div>';
      return;
    }
    box.innerHTML = state.chat.messages.map(function(m) {
      if (m.role === 'user') return '<div class="bubble user">' + esc(m.text) + '</div>';
      if (m.pending) return '<div class="bubble bot pending"><span></span><span></span><span></span></div>';
      var cls = 'bubble bot' + (m.error ? ' error' : '');
      var html = '<div class="' + cls + '">' + esc(m.text).replace(/\n/g, '<br>');
      if (m.sql) html += '<details class="chat-sql"><summary>SQL</summary><pre>' + esc(m.sql) + '</pre></details>';
      html += chatTable(m.columns, m.rows);
      html += '</div>';
      return html;
    }).join('');
    box.scrollTop = box.scrollHeight;
  }

  function sendChat(q) {
    q = (q || '').trim();
    if (!q || state.chat.busy) return;
    state.chat.busy = true;
    state.chat.messages.push({ role: 'user', text: q });
    state.chat.messages.push({ role: 'assistant', pending: true });
    renderChatMessages();
    renderChatSuggestions();
    postJson('/api/genie/ask', { question: q, conversation_id: state.chat.conversationId }).then(function(data) {
      state.chat.messages.pop(); // drop the pending bubble
      if (!data || data.error) {
        state.chat.messages.push({ role: 'assistant', text: (data && data.error) || 'Request failed', error: true });
      } else {
        if (data.conversation_id) state.chat.conversationId = data.conversation_id;
        state.chat.messages.push({
          role: 'assistant', text: data.answer || '(no answer)',
          sql: data.sql, columns: data.columns, rows: data.rows,
          error: data.status && data.status !== 'COMPLETED'
        });
      }
      state.chat.busy = false;
      renderChatMessages();
    });
  }

  function showToast(msg) {
    var el = document.getElementById('toast');
    el.textContent = msg; el.style.display = 'block';
    setTimeout(function() { el.style.display = 'none'; }, 4000);
  }

  function init() {
    bindThemeToggle();
    bindNav();
    api('/api/config').then(function(c) { state.config = c || {}; });
    api('/api/kpis').then(function(k) { state.kpis = k; render(); });
    api('/api/positions').then(function(p) { state.positions = p || []; if (map && state.mapLoaded) addMarkers(); render(); });
    loadShortfalls();
    render();
  }

  init();
})();
