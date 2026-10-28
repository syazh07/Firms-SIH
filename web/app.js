const CLASSES = {
  industrial_persistent: { color: '#ff4d4d', name: 'Industrial – persistent source' },
  gas_flare:             { color: '#ff9f1c', name: 'Gas flare (refinery / oil & gas)' },
  mining_thermal:        { color: '#b5651d', name: 'Mining / coal-fire thermal' },
  industrial_fire:       { color: '#ff00c8', name: 'Industrial fire – ALERT' },
  agricultural_burn:     { color: '#f5e663', name: 'Agricultural burning' },
  wildfire:              { color: '#3ddc84', name: 'Wildfire / vegetation fire' },
  volcano:               { color: '#c084fc', name: 'Volcano' },
  offshore_other:        { color: '#7aa6ff', name: 'Other / unclassified (water, bare, unknown)' },
  industrial:            { color: '#ff4d4d', name: 'ML: industrial / static source' },
  natural:               { color: '#3ddc84', name: 'ML: natural fire' },
  other:                 { color: '#7aa6ff', name: 'ML: other' },
};
const RULE_CLASSES = ['industrial_persistent', 'gas_flare', 'mining_thermal', 'industrial_fire', 'agricultural_burn', 'wildfire', 'volcano', 'offshore_other'];
const state = { dataset: 'live', days: 7, end: null, label: 'label_final', classes: new Set(Object.keys(CLASSES)), sources: new Set(), night: false, day: null, playing: null };

const map = L.map('map', { zoomControl: true, worldCopyJump: false, preferCanvas: true }).setView([22.5, 80], 5);
const basemaps = {
  dark: L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', { attribution: '© OSM © CARTO', maxZoom: 19 }),
  imagery: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { attribution: 'Esri World Imagery', maxZoom: 18 }),
  osm: L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap', maxZoom: 19 }),
};
// offline vector basemap: country outlines from /api/basemap (served from local Natural Earth cache)
const offlineBase = L.layerGroup();
let offlineLoaded = false;
async function ensureOffline() {
  if (offlineLoaded) return; offlineLoaded = true;
  try {
    const gj = await fetch('/api/basemap').then(r => r.json());
    L.geoJSON(gj, { style: () => ({ color: '#3d5a80', weight: 1, fillColor: '#16263a', fillOpacity: 1 }),
      onEachFeature: (f, l) => l.bindTooltip(f.properties.name, { sticky: true, direction: 'top' }) }).addTo(offlineBase);
  } catch (e) { console.warn('offline basemap unavailable', e); }
}
basemaps.offline = offlineBase;
let currentBase = basemaps.imagery.addTo(map);
const labelsLayer = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png', { pane: 'shadowPane' }).addTo(map);
// if tiles fail (no internet), fall back to the offline vector basemap automatically
let tileErrors = 0;
function tileFail() {
  if (++tileErrors < 6 || currentBase === offlineBase) return;
  map.removeLayer(currentBase); map.removeLayer(labelsLayer);
  ensureOffline().then(() => { currentBase = offlineBase.addTo(map); offlineBase.eachLayer(l => l.bringToBack && l.bringToBack()); });
  $('basemap').value = 'offline';
  console.warn('tile server unreachable — switched to offline vector basemap');
}
for (const b of Object.values(basemaps)) if (b.on) b.on('tileerror', tileFail);

let detLayer = L.layerGroup().addTo(map);
const cluster = L.markerClusterGroup({ disableClusteringAtZoom: 9, maxClusterRadius: 40, chunkedLoading: true,
  iconCreateFunction: c => L.divIcon({ html: `<div style="background:rgba(255,77,77,.85);color:#fff;border-radius:50%;width:34px;height:34px;line-height:34px;text-align:center;font-weight:600">${c.getChildCount()}</div>`, className: '', iconSize: [34, 34] }) });
const sitesLayer = L.layerGroup();
const sourcesLayer = L.layerGroup().addTo(map);
let rawDetections = null;
const $ = id => document.getElementById(id);

map.on('mousemove', e => { $('coords').textContent = `Lat ${e.latlng.lat.toFixed(3)}°, Lon ${e.latlng.lng.toFixed(3)}°`; });

// ---------- legend / controls
const legend = $('legend');
for (const k of RULE_CLASSES) {
  const v = CLASSES[k], li = document.createElement('li');
  li.innerHTML = `<label><input type="checkbox" class="cls" value="${k}" checked><span class="sw" style="background:${v.color}"></span>${v.name}</label><span class="cnt" id="cnt-${k}">–</span>`;
  legend.appendChild(li);
}
document.querySelectorAll('.cls').forEach(cb => cb.addEventListener('change', () => { cb.checked ? state.classes.add(cb.value) : state.classes.delete(cb.value); render(); }));
const SENSOR_NAMES = { VIIRS_SNPP_NRT: 'VIIRS S-NPP [375 m]', VIIRS_NOAA20_NRT: 'VIIRS NOAA-20 [375 m]', VIIRS_NOAA21_NRT: 'VIIRS NOAA-21 [375 m]', MODIS_NRT: 'MODIS Aqua & Terra [1 km]', VIIRS_NOAA20_SP: 'VIIRS NOAA-20 standard processing [375 m]', VIIRS_SNPP_SP: 'VIIRS S-NPP standard processing [375 m]', MODIS_SP: 'MODIS standard processing [1 km]' };
function buildSensors(bySource) {
  const ul = document.getElementById('sources'); ul.innerHTML = ''; state.sources = new Set();
  for (const [k, n] of Object.entries(bySource)) {
    state.sources.add(k);
    const li = document.createElement('li');
    li.innerHTML = `<label><input type="checkbox" class="src" value="${k}" checked> ${SENSOR_NAMES[k] || k}</label><span class="cnt">${n}</span>`;
    ul.appendChild(li);
  }
  document.querySelectorAll('.src').forEach(cb => cb.addEventListener('change', () => { cb.checked ? state.sources.add(cb.value) : state.sources.delete(cb.value); render(); loadStats(); }));
}
document.querySelectorAll('input[name=label]').forEach(r => r.addEventListener('change', () => { state.label = r.value; render(); loadStats(); }));
document.querySelectorAll('.tbtn').forEach(b => b.addEventListener('click', () => { document.querySelectorAll('.tbtn').forEach(x => x.classList.remove('active')); b.classList.add('active'); state.days = +b.dataset.days; state.day = null; loadAll(); }));
$('ov-sites').addEventListener('change', e => e.target.checked ? sitesLayer.addTo(map) : map.removeLayer(sitesLayer));
$('ov-persistent').addEventListener('change', e => e.target.checked ? sourcesLayer.addTo(map) : map.removeLayer(sourcesLayer));
$('ov-cluster').addEventListener('change', render);
$('ov-night').addEventListener('change', e => { state.night = e.target.checked; render(); loadStats(); });
$('basemap').addEventListener('change', async e => {
  map.removeLayer(currentBase);
  if (e.target.value === 'offline') { await ensureOffline(); map.removeLayer(labelsLayer); currentBase = offlineBase.addTo(map); offlineBase.eachLayer(l => l.bringToBack && l.bringToBack()); }
  else { currentBase = basemaps[e.target.value].addTo(map); currentBase.bringToBack(); if (!map.hasLayer(labelsLayer)) labelsLayer.addTo(map); }
});
$('toggle-panel').addEventListener('click', () => { $('panel').hidden = true; $('panel-open').hidden = false; });
$('panel-open').addEventListener('click', () => { $('panel').hidden = false; $('panel-open').hidden = true; });
$('dataset').addEventListener('change', e => { state.dataset = e.target.value; state.day = null; state.end = null; $('end-date').value = ''; loadEverything(); });
$('end-date').addEventListener('change', e => { state.end = e.target.value || null; state.day = null; loadAll(); });
$('end-clear').addEventListener('click', () => { state.end = null; $('end-date').value = ''; loadAll(); });
$('refresh').addEventListener('click', async () => {
  const s = $('refresh-status'); s.textContent = 'requesting…';
  const r = await fetch('/api/refresh', { method: 'POST' }).then(r => r.json());
  s.textContent = r.status + (r.retry_in_s ? ` (retry in ${Math.ceil(r.retry_in_s / 60)} min)` : '');
});

// ---------- playback
let dayList = [];
$('day-slider').addEventListener('input', e => { const i = +e.target.value; state.day = i >= dayList.length ? null : dayList[i]; $('day-label').textContent = state.day || 'all days'; render(); loadStats(); });
$('play').addEventListener('click', () => {
  if (state.playing) { clearInterval(state.playing); state.playing = null; $('play').textContent = '▶'; return; }
  let i = 0; $('play').textContent = '⏸';
  state.playing = setInterval(() => { $('day-slider').value = i; $('day-slider').dispatchEvent(new Event('input')); i = (i + 1) % (dayList.length + 1); }, 700);
});

// ---------- rendering
function popup(p) {
  const cls = CLASSES[p[state.label]] || CLASSES.offshore_other;
  const rows = [['Time (UTC)', p.acq_datetime], ['Sensor', `${p.source} (${p.satellite})`], ['FRP', `${(+p.frp).toFixed(1)} MW`],
    ['Brightness', `${(+p.bt).toFixed(1)} K`], ['Day/Night', p.daynight], ['Confidence', ({n:'Nominal', l:'Low', h:'High'})[p.confidence] || p.confidence],
    ['Active days / 90d', `${p.n_days_90} cell${p.site_days_90 ? ` · ${p.site_days_90} site` : ''}`], ['Night fraction', (+p.night_frac_90).toFixed(2)],
    ['Land cover', p.landcover], ['Nearest site', p.site_name ? `${p.site_name} (${p.site_type}, ${Math.round(p.dist_site_m)} m)` : '—'],
    ['Rule label', p.label_rule], ['ML', `${p.label_ml} (p industrial = ${p.proba_industrial ?? '—'})`],
    ['Final', `${p.label_final}${p.ml_veto ? ' · rule industrial vetoed by ML' : ''}`],
    ['Source / anomaly', p.source_id != null && p.source_id >= 0 ? `#${p.source_id} · z = ${p.anomaly_score}` : '—'],
    ['SP truth type', p.sp_type != null && p.sp_type >= 0 ? ({0: '0 vegetation fire', 1: '1 volcano', 2: '2 static land source', 3: '3 offshore'})[p.sp_type] : '—']];
  return `<div class="popup"><h4><span class="badge" style="background:${cls.color}">${cls.name}</span></h4><table>${rows.map(r => `<tr><td>${r[0]}</td><td>${r[1] ?? '—'}</td></tr>`).join('')}</table><p class="small">${p.explanation || ''}</p></div>`;
}

function render() {
  if (!rawDetections) return;
  map.removeLayer(detLayer); map.removeLayer(cluster);
  detLayer = L.layerGroup(); cluster.clearLayers();
  const counts = {}; for (const k of Object.keys(CLASSES)) counts[k] = 0;
  let renderedCount = 0;
  const markers = [];
  for (const f of rawDetections.features) {
    const p = f.properties, lab = p[state.label];
    if (!state.sources.has(p.source)) continue;
    if (state.night && p.daynight !== 'N') continue;
    if (state.day && !(p.acq_datetime || '').startsWith(state.day)) continue;
    counts[lab] = (counts[lab] || 0) + 1;
    if (state.label !== 'label_ml' && !state.classes.has(lab)) continue;
    if (state.label === 'label_ml' && !state.classes.has(p.label_rule)) continue;
    
    renderedCount++;
    const c = (CLASSES[lab] || CLASSES.offshore_other).color;
    const r = Math.min(12, 4 + Math.sqrt(Math.max(0, +p.frp || 0)));
    const m = L.circleMarker([f.geometry.coordinates[1], f.geometry.coordinates[0]], { radius: r, color: c, weight: 1, fillColor: c, fillOpacity: 0.75 });
    m.bindPopup(() => popup(p), { maxWidth: 380 });
    markers.push(m);
  }
  for (const k of RULE_CLASSES) $(`cnt-${k}`).textContent = state.label !== 'label_ml' ? (counts[k] || 0) : '';
  if (state.label === 'label_ml') { $('cnt-industrial_persistent').textContent = `ML ind ${counts.industrial || 0}`; $('cnt-wildfire').textContent = `ML nat ${counts.natural || 0}`; }
  if ($('ov-cluster').checked) { cluster.addLayers(markers); cluster.addTo(map); } else { markers.forEach(m => m.addTo(detLayer)); detLayer.addTo(map); }
  
  window._renderedCount = renderedCount;
  updateRenderedCount();
}

// ---------- data loading
const q = (path, params) => fetch(`${path}?dataset=${state.dataset}&${new URLSearchParams(state.end ? { ...params, end: state.end } : params)}`).then(r => r.json());

async function loadDetections() {
  rawDetections = await q('/api/detections', { days: state.days, limit: 60000 });
  const dates = rawDetections.features.map(f => (f.properties.acq_datetime || '').slice(0, 10)).filter(Boolean).sort();
  dayList = [...new Set(dates)];
  $('day-slider').max = dayList.length; $('day-slider').value = dayList.length; $('day-label').textContent = 'all days';
  $('daterange').textContent = dates.length ? `${dates[0]} → ${dates[dates.length - 1]} · ${dates.length} detections${rawDetections.features.length >= 60000 ? ' (sampled)' : ''}` : 'no detections';
  render();
}

async function loadSites() {
  const gj = await q('/api/sites', {});
  sitesLayer.clearLayers();
  L.geoJSON(gj, {
    pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 5, color: '#00e5ff', weight: 1, fillOpacity: .3 }),
    style: () => ({ color: '#00e5ff', weight: 1, fillOpacity: .12 }),
    onEachFeature: (f, l) => l.bindPopup(`<div class="popup"><h4>${f.properties.name || '(unnamed)'}</h4><table><tr><td>Type</td><td>${f.properties.site_type}</td></tr><tr><td>Source</td><td>${f.properties.provider}</td></tr><tr><td>Notes</td><td>${f.properties.notes || ''}</td></tr></table></div>`),
  }).addTo(sitesLayer);
}

async function loadSources() {
  const gj = await q('/api/sources', {});
  sourcesLayer.clearLayers();
  L.geoJSON(gj, {
    style: f => ({ color: (CLASSES[f.properties.label] || CLASSES.offshore_other).color, weight: 2, fill: true, fillOpacity: .08, dashArray: '4 3' }),
    onEachFeature: (f, l) => { l.on('click', () => showSource(f.properties)); l.bindTooltip(`${f.properties.site_name || 'unmapped source'} · ${f.properties.active_days_90} d/90`, { sticky: true }); },
  }).addTo(sourcesLayer);
}

async function showSource(p) {
  const card = $('source-card'); card.hidden = false;
  $('src-title').textContent = `#${p.source_id} · ${(CLASSES[p.label] || {}).name || p.label}`;
  $('src-meta').innerHTML = `<b>${p.site_name || 'no mapped site'}</b> (${p.site_type}) · ${p.n_cells} cells · ${p.landcover}<br>active ${p.active_days_90} d / 90 · ${p.n_det_90} detections · night ${(+p.night_frac_90).toFixed(2)} · FRP mean ${(+p.frp_mean_90).toFixed(1)} ± ${(+p.frp_std_90).toFixed(1)} MW<br>seen ${p.first_seen} → ${p.last_seen}`;
  const ts = await q(`/api/sources/${p.source_id}/timeseries`, { days: 90 });
  const d = ts.daily, W = 300, H = 70, max = Math.max(1, ...d.map(x => x.frp_max)), nmax = Math.max(1, ...d.map(x => x.n));
  const t0 = new Date(d[0].date), span = Math.max(1, (new Date(d[d.length - 1].date) - t0) / 864e5);
  const x = s => 4 + (new Date(s) - t0) / 864e5 / span * (W - 8);
  const bars = d.map(r => `<rect x="${x(r.date) - 1.5}" y="${H - 4 - r.n / nmax * 25}" width="3" height="${r.n / nmax * 25}" fill="#3a5a80"/>`).join('');
  const line = d.map((r, i) => `${i ? 'L' : 'M'}${x(r.date).toFixed(1)},${(H - 6 - r.frp_max / max * (H - 14)).toFixed(1)}`).join(' ');
  $('sparkline').innerHTML = `${bars}<path d="${line}" fill="none" stroke="#ff9f1c" stroke-width="1.5"/><text x="4" y="10" fill="#8fa3b8" font-size="9">max ${max.toFixed(0)} MW</text>`;
  card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

let currentStatsTotal = 0;
let currentStatsDays = 0;

async function loadStats() {
  const params = { days: state.days, label: state.label };
  if (state.sources && state.sources.size) params.sources = Array.from(state.sources).join(',');
  if (state.night) params.night = 'true';
  if (state.day) params.day = state.day;

  const s = await q('/api/stats', params);
  currentStatsTotal = s.total;
  currentStatsDays = state.day ? 1 : s.days;
  
  const ind = state.label !== 'label_ml' ? ['industrial_persistent', 'gas_flare', 'mining_thermal', 'industrial_fire'].reduce((a, k) => a + (s.by_class[k] || 0), 0) : (s.by_class.industrial || 0);
  const hd = (window._health || {}).datasets?.[state.dataset];
  const fresh = hd && hd.data_age_hours != null ? `<div><b>${hd.data_age_hours < 48 ? hd.data_age_hours + ' h' : Math.round(hd.data_age_hours / 24) + ' d'}</b><span>data age${(window._health.refresh?.every_hours) ? ` · refresh ${window._health.refresh.every_hours} h` : ''}</span></div>` : '';
  
  $('topstats').innerHTML = fresh + `<div id="stats-total"><b>${s.total}</b><span>detections · ${currentStatsDays} d</span></div><div><b>${ind}</b><span>industrial / persistent</span></div><div><b>${s.by_class.industrial_fire || 0}</b><span>industrial fire alerts</span></div><div><b>${s.anomalies || 0}</b><span>FRP anomalies</span></div><div><b>${s.by_class.wildfire ?? s.by_class.natural ?? 0}</b><span>${state.label !== 'label_ml' ? 'wildfire' : 'ML natural'}</span></div><div><b>${s.by_class.agricultural_burn || 0}</b><span>agri burn</span></div><div><b>${s.latest}</b><span>latest data</span></div>`;
  
  updateRenderedCount();
}

function updateRenderedCount() {
  const el = $('stats-total');
  if (el && window._renderedCount !== undefined) {
    el.innerHTML = `<b>${window._renderedCount} / ${currentStatsTotal}</b><span>rendered / total · ${currentStatsDays} d</span>`;
  }
}

async function loadAlerts() {
  const gj = await q('/api/alerts', { days: state.days });
  const ol = $('alerts'); ol.innerHTML = '';
  if (!gj.features.length) { ol.innerHTML = '<li class="small">no industrial fire events in window</li>'; return; }
  for (const f of gj.features.slice(0, 25)) {
    const p = f.properties, li = document.createElement('li');
    const why = p.anomaly_score >= 4 ? `FRP anomaly z=${p.anomaly_score}` : 'new event at site';
    li.innerHTML = `<b>${p.site_name || 'unmapped source'}</b> <span class="small">${p.site_type} · ${why}</span><br><span class="frp">${(+p.frp).toFixed(1)} MW</span> · ${p.acq_datetime?.slice(0, 16).replace('T', ' ')} · ${p.source}`;
    li.addEventListener('click', () => map.setView([f.geometry.coordinates[1], f.geometry.coordinates[0]], 12));
    ol.appendChild(li);
  }
}

async function loadModel() {
  const e = await fetch('/api/eval').then(r => r.json());
  const el = $('model');
  if (!e.report) { el.textContent = e.model.trained_on === 'rules' ? 'ML mimics rules (no ground truth yet)' : 'no evaluation yet — run python -m frims.classify.evaluate'; return; }
  const cm = (r, title) => `<div><b>${title}</b> · n=${r.n} · acc ${(r.accuracy * 100).toFixed(1)}%</div>` +
    ['industrial', 'natural'].map(c => `<span class="metric">${c}: P <b>${(r.per_class[c].precision * 100).toFixed(1)}%</b> R <b>${(r.per_class[c].recall * 100).toFixed(1)}%</b> F1 <b>${(r.per_class[c].f1 * 100).toFixed(1)}%</b></span>`).join('') +
    `<table class="cm"><tr><th>true \\ pred</th>${r.confusion.cols_pred.map(c => `<th>${c}</th>`).join('')}</tr>${r.confusion.matrix.map((row, i) => `<tr><th>${r.confusion.rows_true[i]}</th>${row.map(v => `<td>${v}</td>`).join('')}</tr>`).join('')}</table>`;
  el.innerHTML = (e.report.combined_holdout ? cm(e.report.combined_holdout, `Combined (rules + ML veto) · held-out ${e.report.combined_holdout.period.join(' → ')}`) : '') +
    (e.report.rules_holdout ? cm(e.report.rules_holdout, `Rules · held-out ${e.report.rules_holdout.period.join(' → ')}`) : '') +
    cm(e.report.rules, `Rules · tuning period ${e.report.rules.period.join(' → ')}`) +
    (e.report.ml ? cm(e.report.ml.test, `ML · held-out ${e.report.ml.test.period.join(' → ')}`) : '') +
    `<div class="small">Truth = FIRMS VIIRS standard-processing <i>type</i>: 2 static land source → "industrial" (persistent / flare / mining); 0 vegetation fire → "natural" (transient: agri, wildfire and one-off industrial_fire events, which SP cannot distinguish).</div>`;
}

async function loadIncidents() {
  const r = await fetch('/api/incidents').then(r => r.json()).catch(() => null);
  const el = $('incidents');
  if (!r || !r.incidents) { el.textContent = 'no incident report'; return; }
  const s = r.summary;
  el.innerHTML = `<div><b>${s.n_detected}/${s.n_with_data}</b> reported incidents seen by satellite · <b>${s.n_alert_fired}</b> raised an alert · of ${s.expected_visible.n} expected-visible: ${s.expected_visible.detected} seen, ${s.expected_visible.alerted} alerted</div>` +
    `<table class="cm"><tr><th>date</th><th>incident</th><th>seen</th><th>det</th><th>FRP</th><th>z</th><th>alert</th></tr>` +
    r.incidents.map(x => `<tr class="inc" data-lat="${x.lat}" data-lon="${x.lon}" title="${(x.labels && JSON.stringify(x.labels)) || ''}"><td>${x.date}</td><td style="text-align:left">${x.name}</td><td>${x.status === 'detected' ? '✅' : x.status === 'not_detected' ? '—' : '?'}</td><td>${x.n_detections ?? ''}</td><td>${x.frp_max ? (+x.frp_max).toFixed(0) : ''}</td><td>${x.anomaly_z_max != null ? x.anomaly_z_max : ''}</td><td>${x.alert_fired ? '🚨' : ''}${x.location_confidence && x.location_confidence.startsWith('low') ? '<span title="wide search radius">≈</span>' : ''}</td></tr>`).join('') + `</table>`;
  el.querySelectorAll('tr.inc').forEach(tr => { tr.style.cursor = 'pointer'; tr.addEventListener('click', () => map.setView([+tr.dataset.lat, +tr.dataset.lon], 12)); });
}

async function loadWatch() {
  const gj = await q('/api/alerts', { days: state.days, tier: 'watch' }).catch(() => ({ features: [] }));
  const ol = $('watch'); ol.innerHTML = '';
  if (!gj.features.length) { ol.innerHTML = '<li class="small">nothing unusual</li>'; return; }
  for (const f of gj.features.slice(0, 15)) {
    const p = f.properties, li = document.createElement('li');
    li.innerHTML = `<b>${p.site_name || 'unmapped source'}</b> <span class="small">${p.site_type} · z=${p.anomaly_score}</span><br><span class="frp">${(+p.frp).toFixed(1)} MW</span> · ${p.acq_datetime?.slice(0, 16).replace('T', ' ')}`;
    li.addEventListener('click', () => map.setView([f.geometry.coordinates[1], f.geometry.coordinates[0]], 12));
    ol.appendChild(li);
  }
}

async function loadAll() { await Promise.all([loadDetections(), loadStats(), loadAlerts(), loadWatch()]); }
async function loadEverything() {
  $('source-card').hidden = true;
  const all = await q('/api/stats', { days: 400 });
  buildSensors(all.by_source);
  await Promise.all([loadAll(), loadSites(), loadSources()]);
}
(async () => {
  const h = await fetch('/api/health').then(r => r.json()).catch(() => ({ ok: false }));
  if (!h.ok) { $('topstats').textContent = 'No data yet — run scripts/run_pipeline.py'; return; }
  const names = Object.keys(h.datasets);
  window._health = h;
  $('dataset').innerHTML = names.map(n => `<option value="${n}">${n} (${h.datasets[n].min_date} → ${h.datasets[n].max_date})</option>`).join('');
  state.dataset = names.includes('live') ? 'live' : names[0]; $('dataset').value = state.dataset;
  await Promise.all([loadEverything(), loadModel(), loadIncidents()]);
})();
