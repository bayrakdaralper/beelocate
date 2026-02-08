/* BeeLocate PRO v6.1 - Frontend Logic
   Goal: stable UI + strict API contract consumption.
   - Sends rad + month + water_managed to /analyze
   - Never crashes on missing keys
   - Renders dynamic cards in #metrics-grid
*/

let map;
let marker = null;
let roiCircle = null;
let selectedLat = null;
let selectedLng = null;
let currentLang = 'EN';
let currentUnits = (function(){
  try { return (localStorage.getItem('blp_units') || 'metric'); } catch(e) { return 'metric'; }
})();

function _normUnits(u){
  u = String(u||'').toLowerCase();
  return (u === 'imperial' || u === 'us' || u === 'uscs' || u === 'english') ? 'imperial' : 'metric';
}

function updateUnitsUI(){
  currentUnits = _normUnits(currentUnits);
  const el = document.getElementById('units-label');
  const btn = document.getElementById('units-btn');
  if(el) el.textContent = (currentUnits === 'imperial') ? 'Imperial' : 'Metric';
  if(btn) btn.setAttribute('aria-pressed', currentUnits === 'imperial' ? 'true' : 'false');
}

function toggleUnits(){
  currentUnits = (currentUnits === 'imperial') ? 'metric' : 'imperial';
  try { localStorage.setItem('blp_units', currentUnits); } catch(e) {}
  updateUnitsUI();
  if (window.lastResult) { renderResults(window.lastResult); }
}

function _numFromAny(x){
  if (x === null || x === undefined) return null;
  if (typeof x === 'number') return isFinite(x) ? x : null;
  const m = String(x).match(/-?\d+(?:\.\d+)?/);
  if(!m) return null;
  const v = parseFloat(m[0]);
  return isFinite(v) ? v : null;
}
function _fmt(v, nd){
  if(v===null || v===undefined || !isFinite(v)) return '--';
  const p = Math.max(0, Math.min(3, nd||0));
  return Number(v).toFixed(p);
}
function _kmToMi(km){ return km * 0.621371; }
function _mToFt(m){ return m * 3.28084; }
function _mmToIn(mm){ return mm / 25.4; }
function _cToF(c){ return (c * 9/5) + 32; }
function _kmhToMph(kmh){ return kmh * 0.621371; }

function formatMetricForUnits(title, metric){
  const t = String(title||'').toLowerCase();
  const m = (metric && typeof metric === 'object') ? metric : {};
  // Defaults from backend
  let main = (m._main ?? m.label ?? m.val ?? '--');
  let sub  = (m._sub ?? m.desc ?? '--');
  const us = _normUnits(currentUnits);

  // Distance-like cards (backend val is usually in km, elevation in m, precip in mm)
  if (us === 'imperial') {
    if (t.includes('road distance') || t.includes('settlement distance')) {
      const km = _numFromAny(m.val ?? m.value ?? m.label);
      if (km !== null) main = `${_fmt(_kmToMi(km), 1)} mi`;
    }
    if (t === 'elevation') {
      const meters = _numFromAny(m.val ?? m.value ?? m.label);
      if (meters !== null) main = `${_fmt(_mToFt(meters), 0)} ft`;
    }
    if (t.includes('precip')) {
      const mm = _numFromAny(m.val ?? m.value ?? m.label);
      if (mm !== null) main = `${_fmt(_mmToIn(mm), 2)} in`;
    }
    if (t.includes('temperature')) {
      const c = _numFromAny(m.val ?? m.value ?? m.label);
      if (c !== null) main = `${_fmt(_cToF(c), 0)} °F`;
    }
    if (t.includes('wind')) {
      // Many backends send km/h in label/val; convert to mph if numeric present.
      const kmh = _numFromAny(m.val ?? m.value ?? m.label);
      if (kmh !== null) main = `${_fmt(_kmhToMph(kmh), 0)} mph`;
    }
  } else {
    // metric: try to standardize display if backend sent bare numbers
    if ((t.includes('road distance') || t.includes('settlement distance')) && typeof main === 'number') main = `${_fmt(main,1)} km`;
    if (t === 'elevation' && typeof main === 'number') main = `${_fmt(main,0)} m`;
    if (t.includes('precip') && typeof main === 'number') main = `${_fmt(main,0)} mm`;
    if (t.includes('temperature') && typeof main === 'number') main = `${_fmt(main,0)} °C`;
    if (t.includes('wind') && typeof main === 'number') main = `${_fmt(main,0)} km/h`;
  }
  return { main, sub };
}

let lastResult = null;
let waterManaged = false;
let baseLayer = null;
let currentBasemap = 'esri';

// ---------------------------------
// Anonymous device identity (MVP)
// ---------------------------------
// This enables "My Reports" without requiring login.
// It is NOT a security boundary; paid checks still happen server-side.
const UID_STORAGE_KEY = 'blp_uid';

function _getCookie(name) {
  const m = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
  return m ? decodeURIComponent(m[2]) : '';
}

function _setCookie(name, value, days = 365) {
  try {
    const d = new Date();
    d.setTime(d.getTime() + (days*24*60*60*1000));
    document.cookie = `${name}=${encodeURIComponent(value)}; expires=${d.toUTCString()}; path=/; SameSite=Lax`;
  } catch(e) {}
}

function getOrCreateUid() {
  let uid = '';
  try { uid = (localStorage.getItem(UID_STORAGE_KEY) || '').trim(); } catch(e) {}
  if (!uid) uid = (_getCookie('blp_uid') || '').trim();
  if (!uid) {
    try {
      uid = (crypto?.randomUUID ? crypto.randomUUID() : (Date.now().toString(16) + Math.random().toString(16).slice(2)));
    } catch(e) {
      uid = (Date.now().toString(16) + Math.random().toString(16).slice(2));
    }
  }
  try { localStorage.setItem(UID_STORAGE_KEY, uid); } catch(e) {}
  _setCookie('blp_uid', uid);
  return uid;
}

const MAPBOX_TOKEN = (window.MAPBOX_TOKEN || '').trim();

function mapboxStyleUrl(styleId) {
  if (!MAPBOX_TOKEN) return null;
  // Raster tiles from Mapbox Styles API
  return `https://api.mapbox.com/styles/v1/mapbox/${styleId}/tiles/256/{z}/{x}/{y}?access_token=${MAPBOX_TOKEN}`;
}

const BASEMAPS = {
  mapbox_sat_labels: {
    name: 'Satellite + Labels (Mapbox)',
    url: () => mapboxStyleUrl('satellite-streets-v12'),
    options: { maxZoom: 20, attribution: '© Mapbox © OpenStreetMap' }
  },
  mapbox_sat: {
    name: 'Satellite (Mapbox)',
    url: () => mapboxStyleUrl('satellite-v9'),
    options: { maxZoom: 20, attribution: '© Mapbox © OpenStreetMap' }
  },
  mapbox_streets: {
    name: 'Map (Mapbox)',
    url: () => mapboxStyleUrl('streets-v12'),
    options: { maxZoom: 20, attribution: '© Mapbox © OpenStreetMap' }
  },
  osm: {
    name: 'Map (OSM fallback)',
    url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    options: { maxZoom: 19, attribution: '&copy; OpenStreetMap' }
  },
};


function applyLabels(on) {
  labelsOn = !!on;

  // Labels are implemented by switching between Mapbox styles.
  if (!MAPBOX_TOKEN) return;

  if (labelsOn) {
    // if on, prefer satellite+labels
    if (currentBasemap === 'mapbox_sat') {
      applyBasemap('mapbox_sat_labels');
    }
  } else {
    // if off, prefer pure satellite
    if (currentBasemap === 'mapbox_sat_labels') {
      applyBasemap('mapbox_sat');
    }
  }
}

function setLabels(on) {
  if (!map) return;
  applyLabels(on);
}



// Loading screen control (retro matrix)
let loadingTimer = null;
let loadingPct = 0;

function showLoadingScreen() {
  const screen = $('loading-screen');
  if (!screen) return;
  screen.classList.remove('hidden');
  screen.classList.add('flex');
  loadingPct = 0;
  const pctEl = $('loading-percent');
  const barEl = $('loading-bar');
  if (pctEl) pctEl.textContent = `%0`;
  if (barEl) barEl.style.width = `0%`;

  if (loadingTimer) clearInterval(loadingTimer);
  loadingTimer = setInterval(() => {
    // climb quickly to 70, then creep to 90 while waiting
    const target = loadingPct < 70 ? 70 : 90;
    if (loadingPct < target) loadingPct += (loadingPct < 70 ? 7 : 1);
    loadingPct = clamp(loadingPct, 0, 90);
    if (pctEl) pctEl.textContent = `%${loadingPct}`;
    if (barEl) barEl.style.width = `${loadingPct}%`;
  }, 120);
}

function hideLoadingScreen() {
  if (loadingTimer) {
    clearInterval(loadingTimer);
    loadingTimer = null;
  }
  const pctEl = $('loading-percent');
  const barEl = $('loading-bar');
  if (pctEl) pctEl.textContent = `%100`;
  if (barEl) barEl.style.width = `100%`;
  // small delay so it feels intentional, not a flash
  setTimeout(() => {
    const screen = $('loading-screen');
    if (!screen) return;
    screen.classList.add('hidden');
    screen.classList.remove('flex');
  }, 150);
}

function $(id) { return document.getElementById(id); }

function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }

function getRadiusMeters() {
  const el = $('rad');
  const n = Number(el?.value ?? 2000);
  return Number.isFinite(n) ? n : 2000;
}

function setText(id, value) {
  const el = $(id);
  if (!el) return;
  el.textContent = (value === undefined || value === null || value === '') ? '--' : String(value);
}

function scoreComment(score) {
  if (!Number.isFinite(score)) return '--';
  if (score >= 80) return 'High Potential';
  if (score >= 60) return 'Good Potential';
  if (score >= 40) return 'Medium Potential';
  return 'Low Potential';
}
function formatCoords(lat, lon) {
  const a = Number(lat), b = Number(lon);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return '--';
  return `📍 ${a.toFixed(5)}, ${b.toFixed(5)}`;
}

function updateSeasonOptionLabel(seasonLabelTR) {
  const sel = $('month-selector');
  if (!sel) return;
  const opt = Array.from(sel.options || []).find(o => o.value === 'season');
  if (!opt) return;

  const raw = (seasonLabelTR || '').trim();
  if (!raw) {
    opt.textContent = '🌿 RECOMMENDED SEASON (Phenology)';
    return;
  }
  // keep it short in the dropdown
  const short = raw.split(' (')[0]; // e.g., "Nisan–Haziran"
  opt.textContent = `🌿 RECOMMENDED SEASON (${short})`;
}


// ------------------------------
// ------------------------------
// Map
// ------------------------------
function waitForLeafletAndInit(retries = 80) { // ~8s
  if (window.L && typeof window.L.map === 'function') {
    initMap();
    return;
  }
  if (retries <= 0) {
    console.error('Leaflet failed to load.');
    alert('Map engine failed to load. Please refresh.');
    return;
  }
  setTimeout(() => waitForLeafletAndInit(retries - 1), 100);
}

// Map
// ------------------------------

function applyBasemap(key) {
  const cfg = BASEMAPS[key] || BASEMAPS.osm;
  try {
    if (baseLayer) {
      try { map.removeLayer(baseLayer); } catch(e) {}
      baseLayer = null;
    }
    const url = (typeof cfg.url === 'function') ? cfg.url() : cfg.url;
    if (!url) {
      // Missing token: fall back to OSM
      if (key !== 'osm') return applyBasemap('osm');
      throw new Error('Missing MAPBOX_TOKEN');
    }
    baseLayer = L.tileLayer(url, cfg.options).addTo(map);
    currentBasemap = key;
  } catch (e) {
    console.error('Basemap load failed, falling back to OSM:', e);
    if (key !== 'osm') {
      applyBasemap('osm');
    }
  }
}

function setBasemap(key) {
  if (!map) return;
  applyBasemap(key);
}
function initMap() {
  map = L.map('map').setView([39.78, 30.52], 10);

  // Basemap (default: Mapbox Satellite+Labels if token is set, otherwise OSM fallback)
  const sel = $('basemap-selector');
  const hasToken = !!MAPBOX_TOKEN;
  const defaultKey = hasToken ? 'mapbox_sat_labels' : 'osm';
  currentBasemap = (sel && sel.value) ? sel.value : defaultKey;
  if (sel) sel.value = currentBasemap;
  applyBasemap(currentBasemap);

  
  // Labels overlay (place/road names)
  const lbl = $('labels-toggle');
  const wantLabels = (lbl ? lbl.checked : true);
  applyLabels(wantLabels);
map.on('click', (e) => {
    selectedLat = e.latlng.lat;
    selectedLng = e.latlng.lng;

    if ($('lat')) $('lat').value = selectedLat;
    if ($('lng')) $('lng').value = selectedLng;

    if (!marker) marker = L.marker(e.latlng).addTo(map);
    else marker.setLatLng(e.latlng);

    const rad = getRadiusMeters();
    if (!roiCircle) roiCircle = L.circle(e.latlng, { radius: rad, color: '#F59E0B', fillOpacity: 0.12 }).addTo(map);
    else {
      roiCircle.setLatLng(e.latlng);
      roiCircle.setRadius(rad);
    }
  });

  // Sync UI
  updateRadius($('radius-slider')?.value ?? 2000);
  checkGeeHealth();
}


async function checkGeeHealth() {
  try {
    const r = await fetch('/health/gee', { cache: 'no-store' });
    const j = await r.json();
    const btn = document.querySelector('button[onclick="startAnalysis()"]');
    const lbl = document.getElementById('btn-analyze-text');
    if (!j.ok) {
      if (btn) {
        btn.disabled = true;
        btn.classList.add('opacity-50');
        btn.classList.add('cursor-not-allowed');
      }
      if (lbl) lbl.textContent = 'SERVICE UNAVAILABLE';
      // Optional: show a subtle toast in the console for MVP
      console.warn('GEE health check failed:', j.error);
    } else {
      // ensure enabled
      if (btn) {
        btn.disabled = false;
        btn.classList.remove('opacity-50');
        btn.classList.remove('cursor-not-allowed');
      }
    }
  } catch (e) {
    console.warn('GEE health check error', e);
  }
}

document.addEventListener('DOMContentLoaded', () => waitForLeafletAndInit());

// ------------------------------
// UI hooks called from HTML
// ------------------------------
function updateRadius(val) {
  const slider = $('radius-slider');
  const radHidden = $('rad');
  if (slider && val !== undefined) slider.value = val;
  if (radHidden && val !== undefined) radHidden.value = val;

  const rad = getRadiusMeters();
  const radVal = $('rad-val');
  if (radVal) radVal.textContent = `${Math.round(rad)}m`;

  if (roiCircle) roiCircle.setRadius(rad);
}

// MVP: English-only UI (i18n-ready). We keep the function for FAZ-4.
function setLang(lang) {
  currentLang = 'EN';

  const btnTR = $('btn-tr');
  const btnEN = $('btn-en');
  // Hide language toggles in EN-only mode.
  if (btnTR) btnTR.style.display = 'none';
  if (btnEN) btnEN.style.display = 'none';

  const input = $('search-input');
  if (input) input.placeholder = 'Search location...';

  const sysTitle = $('sys-title');
  if (sysTitle) sysTitle.textContent = 'PRE-ASSESSMENT';

  // Refresh managed-water button label
  const lbl = $('water-managed-label');
  if (lbl) {
    lbl.textContent = waterManaged ? 'Managed Water: ON' : 'Managed Water: OFF';
  }
}

async function handleSearch(event) {
  if (!event || event.key !== 'Enter') return;
  event.preventDefault();

  const q = ($('search-input')?.value ?? '').trim();
  if (!q) return;

  try {
    const url = `https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(q)}`;
    const res = await fetch(url, { headers: { 'Accept-Language': 'en' } });
    const arr = await res.json();
    if (!Array.isArray(arr) || arr.length === 0) {
      alert('No results found.');
      return;
    }
    const lat = Number(arr[0].lat);
    const lon = Number(arr[0].lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
      alert('Invalid result.');
      return;
    }

    selectedLat = lat;
    selectedLng = lon;

    if ($('lat')) $('lat').value = selectedLat;
    if ($('lng')) $('lng').value = selectedLng;

    const latlng = L.latLng(lat, lon);
    map.setView(latlng, 13);

    if (!marker) marker = L.marker(latlng).addTo(map);
    else marker.setLatLng(latlng);

    const rad = getRadiusMeters();
    if (!roiCircle) roiCircle = L.circle(latlng, { radius: rad, color: '#F59E0B', fillOpacity: 0.12 }).addTo(map);
    else {
      roiCircle.setLatLng(latlng);
      roiCircle.setRadius(rad);
    }
  } catch (e) {
    console.error(e);
    alert('Search error.');
  }
}

function updateMonth() {
  // called on select change
  if (selectedLat && selectedLng) startAnalysis();
}

function toggleWaterManaged() {
  waterManaged = !waterManaged;
  const btn = $('water-managed-btn');
  if (btn) {
    btn.setAttribute('aria-pressed', waterManaged ? 'true' : 'false');
    btn.classList.toggle('ring-1', waterManaged);
    btn.classList.toggle('ring-yellow-400/50', waterManaged);
  }
  const lbl = $('water-managed-label');
  if (lbl) lbl.textContent = waterManaged ? 'Managed Water: ON' : 'Managed Water: OFF';
  // If we already have a point selected, re-run analysis instantly
  if (selectedLat && selectedLng) startAnalysis();
}

function closeModal(event) {
  // overlay click closes; inner click should not
  if (event?.target && event.target.id !== 'result-modal' && !event.target.classList?.contains('close-modal')) return;
  $('result-modal')?.classList.add('hidden');
}

function openModal() {
  $('result-modal')?.classList.remove('hidden');
}

// ------------------------------
// Analysis
// ------------------------------
const FREE_DAILY_LIMIT = 5;

function _todayKey() {
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth()+1).padStart(2,'0');
  const dd = String(d.getDate()).padStart(2,'0');
  return `${yyyy}${mm}${dd}`;
}

function _getUnlimitedUntil() {
  const v = Number(localStorage.getItem('blp_unlimited_until') || 0);
  return Number.isFinite(v) ? v : 0;
}

function _hasUnlimitedNow() {
  return Date.now() < _getUnlimitedUntil();
}

function _getFreeCount() {
  const k = `blp_free_count_${_todayKey()}`;
  const v = Number(localStorage.getItem(k) || 0);
  return Number.isFinite(v) ? v : 0;
}

function _incFreeCount() {
  const k = `blp_free_count_${_todayKey()}`;
  const n = _getFreeCount() + 1;
  localStorage.setItem(k, String(n));
  return n;
}

function _limitReached() {
  if (_hasUnlimitedNow()) return false;
  return _getFreeCount() >= FREE_DAILY_LIMIT;
}

function _lastReportId() {
  return localStorage.getItem('blp_last_report_id') || '';
}

function _setLastReportId(rid) {
  if (rid) localStorage.setItem('blp_last_report_id', rid);
}

function _showPaywall() {
  const rid = _lastReportId();
  const msg = `Daily limit reached. You've used your ${FREE_DAILY_LIMIT} free analyses today.`;
  const cta = rid ? `/buy/${rid}` : '/';
  // Minimal modal fallback: use alert + redirect (keeps MVP stable)
  if (confirm(`${msg}\n\nUnlock full report + unlimited analyses for 24 hours — $9.90`)) {
    window.location.href = cta;
  }
}

async function startAnalysis() {
  if (_limitReached()) {
    _showPaywall();
    return;
  }
  if (!Number.isFinite(selectedLat) || !Number.isFinite(selectedLng)) {
    alert('Please select a location.');
    return;
  }

  const month = $('month-selector')?.value ?? 'current';
  const rad = getRadiusMeters();
  const managed = waterManaged;

  // reset header
  setText('mini-score-val', '--');
  setText('mini-score-comment', '--');
  setText('mini-coords', '--');
  setText('score-val', '0');
  setText('score-comment', '--');
  setText('main-coords', '--');
  setText('dynamic-text', 'Analyzing...');

  try {
    // immediate feedback (fixes 1-3s "nothing happens" feeling)
    showLoadingScreen();

    const payload = {
      lat: selectedLat,
      lng: selectedLng,
      lon: selectedLng,
      month: month,
      rad: rad,
      water_managed: managed,
      uid: getOrCreateUid(),
      units: currentUnits
    };

    const res = await fetch('/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept-Language': 'en' },
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    lastResult = data;

    if (!res.ok) throw new Error(data?.error || 'API error');

    // Track report id so paywall can unlock the correct report.
    if (data?.report_id) _setLastReportId(String(data.report_id));

    renderResults(data);
    openModal();

    // Count only successful analyses (free mode only)
    if (res.ok && data?.ok !== false && !_hasUnlimitedNow()) {
      _incFreeCount();
    }

  } catch (e) {
    console.error(e);
    alert('Analysis error. Check console.');
  } finally {
    hideLoadingScreen();
  }
}

function renderResults(data) {
  const d = data?.details || {};
  const score = Number.isFinite(data?.score) ? data.score : 0;

  setText('score-val', score);
  setText('mini-score-val', score);
  setText('score-comment', scoreComment(score));
  setText('mini-score-comment', scoreComment(score));

  // Coords
  const coords = formatCoords(data?.lat, data?.lon ?? data?.lng);
  setText('mini-coords', coords);
  setText('main-coords', coords);

  // Update season label in dropdown (so user sees the actual recommended window)
  const seasonLabel = d?.season_meta?.season_label_en || d?.season_meta?.season_label_tr;
  updateSeasonOptionLabel(seasonLabel);


  // System message
  const sys = data?.sys_msg || '';
  if (sys) setText('dynamic-text', sys);

  const grid = $('metrics-grid');
  if (!grid) return;
  grid.innerHTML = '';

  const topo = d.topography || {};

  const cards = [
    { title: 'Vegetation', icon: 'forest', border: 'border-green-500', metric: d.flora },
    { title: 'Water', icon: 'water_drop', border: 'border-blue-500', metric: d.water },
    { title: 'Wind', icon: 'air', border: 'border-sky-500', metric: d.climate?.wind },
    { title: 'Flight Window', icon: 'event_available', border: 'border-emerald-500', metric: d.flight },
    { title: 'Aspect', icon: 'explore', border: 'border-yellow-500', metric: topo.aspect || d.aspect },
    { title: 'Humidity', icon: 'water', border: 'border-purple-500', metric: d.climate?.humidity },
    { title: 'Slope', icon: 'landscape', border: 'border-gray-400', metric: topo.slope || d.slope },
    { title: 'Road Distance', icon: 'route', border: 'border-red-500', metric: d.transport },
    { title: 'Urban', icon: 'location_city', border: 'border-orange-500', metric: d.urban },
    { title: 'Settlement', icon: 'home_work', border: 'border-amber-500', metric: d.settlement },
    { title: 'Flight Suitability', icon: 'fact_check', border: 'border-emerald-400', metric: d.flight_suitability },
    { title: 'Precip', icon: 'rainy', border: 'border-cyan-500', metric: d.precip },
    { title: 'Elevation', icon: 'terrain', border: 'border-indigo-500', metric: topo.elevation || d.elevation },
    { title: 'Temperature', icon: 'thermostat', border: 'border-pink-500', metric: d.climate?.temp },
  ];

  for (const c of cards) {
    grid.appendChild(createCard(c.title, c.metric, c.icon, c.border));
  }
}

function createCard(title, metric, icon, border) {
  const m = (metric && typeof metric === 'object') ? metric : {};
    let main = (m._main ?? m.label ?? m.val ?? '--');
  let sub = (m._sub ?? m.desc ?? '--');

  // Ensure Flight Suitability is not a duplicate of Flight Window
  const t = (title || '').toLowerCase();
  if (t.includes('flight suitability')) {
    // If backend accidentally sends a raw day count, derive class+score here.
    const daysRaw = (m.value ?? m.val ?? null);
    const days = Number(daysRaw);
    const looksLikeDays = Number.isFinite(days) || (typeof main === 'string' && /day|year|yr/i.test(main));
    if (looksLikeDays) {
      const d = Number.isFinite(days) ? days : Number(String(main).replace(/[^0-9.]/g,''));
      let cls = '--', sc = 0;
      if (Number.isFinite(d)) {
        if (d < 120) { cls='Weak'; sc=20; }
        else if (d < 180) { cls='Fair'; sc=45; }
        else if (d < 240) { cls='Good'; sc=70; }
        else if (d < 300) { cls='Very Good'; sc=85; }
        else { cls='Excellent'; sc=95; }
        main = `${cls} (${sc}/100)`;
        sub = `Estimated flight-ready days: ${Math.round(d)} days/year`;
      }
    }
  }

  // Apply unit conversion for display (Metric/Imperial toggle).
  try {
    const fm = formatMetricForUnits(title, m);
    if (fm && fm.main !== undefined) main = fm.main;
    if (fm && fm.sub !== undefined) sub = fm.sub;
  } catch (e) {}

  const card = document.createElement('div');
  card.className = `p-3 rounded-lg border ${border} bg-white/5`;
  card.innerHTML = `
    <div class="flex items-center justify-between">
      <div class="text-xs uppercase tracking-wider text-white/70">${title}</div>
      <span class="material-symbols-outlined text-white/60" style="font-size:18px;">${icon}</span>
    </div>
    <div class="mt-1 text-xl font-bold text-white">${main}</div>
    <div class="mt-0.5 text-xs text-white/60">${sub}</div>
  `;
  return card;
}

// Safe stubs
function downloadReport() {
  if (!lastResult) {
    alert('Run an analysis first.');
    return;
  }

  // Prefer id-based report URL (enables PDF export + caching).
  const rid = lastResult.report_id;
  if (rid) {
    window.open(`/report/${rid}?units=${encodeURIComponent(currentUnits)}`, '_blank');
    return;
  }

  // Single source of truth: report_id must be present.
  alert('Report ID is missing. Please run the analysis again.');
}

async function shareResult() {
  if (!lastResult) return;
  const txt = `BeeLocate Pro score: ${lastResult.score} | Location: ${lastResult.lat}, ${lastResult.lng}`;
  try {
    if (navigator.share) { await navigator.share({ title: 'BeeLocate PRO', text: txt }); return; }
  } catch (e) {}
  try { await navigator.clipboard.writeText(txt); alert('Copied.'); }
  catch (e) { alert(txt); }
}

// Export required functions for inline handlers
window.updateRadius = updateRadius;
window.setLang = setLang;
window.toggleUnits = toggleUnits;
window.handleSearch = handleSearch;
window.startAnalysis = startAnalysis;
window.updateMonth = updateMonth;
window.toggleWaterManaged = toggleWaterManaged;
window.setBasemap = setBasemap;
window.setLabels = setLabels;
window.closeModal = closeModal;
window.downloadReport = downloadReport;
window.shareResult = shareResult;
window.toggleWaterManaged = toggleWaterManaged;
window.setBasemap = setBasemap;
window.setLabels = setLabels;


function updateUnlimitedBadge(){
  const badge = document.getElementById('unlimitedBadge');
  const cd = document.getElementById('unlimitedCountdown');
  if(!badge || !cd) return;
  const until = _getUnlimitedUntil();
  const now = Date.now();
  if(until && now < until){
    const ms = until - now;
    const s = Math.max(0, Math.floor(ms/1000));
    const h = Math.floor(s/3600);
    const m = Math.floor((s%3600)/60);
    const ss = s%60;
    cd.textContent = (h>0) ? `${h}h ${m}m` : (m>0 ? `${m}m ${ss}s` : `${ss}s`);
    badge.style.display = 'block';
  }else{
    // Expired/unset: hide and clean up so we don't show stale state in future sessions.
    try {
      localStorage.removeItem('blp_unlimited_until');
    } catch (e) {}
    badge.style.display = 'none';
  }
}
setInterval(updateUnlimitedBadge, 1000);
document.addEventListener('DOMContentLoaded', () => { updateUnlimitedBadge(); updateUnitsUI(); });