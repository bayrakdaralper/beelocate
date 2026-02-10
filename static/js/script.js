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
let currentUnits = 'METRIC';

// ------------------------------
// Minimal static i18n (UI-only)
// Core computations MUST remain language-agnostic.
// ------------------------------
const UI_I18N = {
  EN: {
    search_ph: 'Search location... (e.g., Sarıcakaya)',
    analyze_btn: 'RUN ANALYSIS',
    scan_radius: 'Scan radius (buffer)',
    analysis_period: 'Analysis period:',
    main_score_title: 'LAND SUITABILITY SCORE',
    sys_title: 'PRE-ASSESSMENT',
    loading: 'INITIALIZING SYSTEM...',
    months: {
      season: '🌿 RECOMMENDED SEASON (Phenology)',
      current: '⚡ LIVE / NOW',
      1: 'JAN (Simulation)',
      2: 'FEB (Simulation)',
      3: 'MAR (Simulation)',
      4: 'APR (Simulation)',
      5: 'MAY (Simulation)',
      6: 'JUN (Simulation)',
      7: 'JUL (Simulation)',
      8: 'AUG (Simulation)',
      9: 'SEP (Simulation)',
      10: 'OCT (Simulation)',
      11: 'NOV (Simulation)',
      12: 'DEC (Simulation)'
    },
    managed_water_on: 'Managed Water: ON',
    managed_water_off: 'Managed Water: OFF'
  },
  TR: {
    search_ph: 'Konum ara... (örn. Sarıcakaya)',
    analyze_btn: 'ANALİZİ BAŞLAT',
    scan_radius: 'Tarama yarıçapı (buffer)',
    analysis_period: 'Analiz dönemi:',
    main_score_title: 'ARAZİ UYGUNLUK PUANI',
    sys_title: 'ÖN DEĞERLENDİRME',
    loading: 'SİSTEM BAŞLATILIYOR...',
    months: {
      season: '🌿 ÖNERİLEN SEZON (Fenoloji)',
      current: '⚡ CANLI / ŞU AN',
      1: 'OCAK (Simülasyon)',
      2: 'ŞUBAT (Simülasyon)',
      3: 'MART (Simülasyon)',
      4: 'NİSAN (Simülasyon)',
      5: 'MAYIS (Simülasyon)',
      6: 'HAZİRAN (Simülasyon)',
      7: 'TEMMUZ (Simülasyon)',
      8: 'AĞUSTOS (Simülasyon)',
      9: 'EYLÜL (Simülasyon)',
      10: 'EKİM (Simülasyon)',
      11: 'KASIM (Simülasyon)',
      12: 'ARALIK (Simülasyon)'
    },
    managed_water_on: 'Yapay Su Desteği: AÇIK',
    managed_water_off: 'Yapay Su Desteği: KAPALI'
  }
};

function applyStaticI18n() {
  const t = UI_I18N[currentLang] || UI_I18N.EN;

  // HTML lang attribute
  try { document.documentElement.lang = (currentLang === 'TR') ? 'tr' : 'en'; } catch (e) {}

  const input = $('search-input');
  if (input) input.placeholder = t.search_ph;

  const btn = $('btn-analyze-text');
  if (btn) btn.textContent = t.analyze_btn;

  const lblRad = $('lbl-rad');
  if (lblRad) lblRad.textContent = t.scan_radius;

  const lblPeriod = $('lbl-analysis-period');
  if (lblPeriod) lblPeriod.textContent = t.analysis_period;

  const mainTitle = $('main-score-title');
  if (mainTitle) mainTitle.textContent = t.main_score_title;

  const sysTitle = $('sys-title');
  if (sysTitle) sysTitle.textContent = t.sys_title;

  const loading = $('loading-text');
  if (loading) loading.textContent = t.loading;

  // Month selector options (except 'season' which may be overwritten by phenology label)
  const sel = $('month-selector');
  if (sel && sel.options) {
    for (const opt of Array.from(sel.options)) {
      const v = opt.value;
      if (v === 'season') continue; // handled by updateSeasonOptionLabel()
      if (v === 'current') { opt.textContent = t.months.current; continue; }
      const n = Number(v);
      if (Number.isFinite(n) && t.months[n]) opt.textContent = t.months[n];
    }
  }

  // Managed water label
  const mw = $('water-managed-label');
  if (mw) mw.textContent = waterManaged ? t.managed_water_on : t.managed_water_off;
}

function _getSavedLang() {
  try {
    const v = localStorage.getItem('blp_lang');
    if (!v) return null;
    const up = String(v).toUpperCase();
    return (up === 'TR' || up === 'EN') ? up : null;
  } catch (e) {
    return null;
  }
}

function _saveLang(v) {
  try { localStorage.setItem('blp_lang', v); } catch (e) {}
}

function _getSavedUnits() {
  try {
    const v = localStorage.getItem('blp_units');
    if (!v) return null;
    const up = String(v).toUpperCase();
    return (up === 'METRIC' || up === 'IMPERIAL') ? up : null;
  } catch (e) {
    return null;
  }
}

function _saveUnits(v) {
  try { localStorage.setItem('blp_units', v); } catch (e) {}
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
    // climb quickly to 70, then creep to 90, then keep moving (90→99)
    // so users don't think the app froze during server-side processing.
    let target = 70;
    if (loadingPct >= 70) target = 90;
    if (loadingPct >= 90) target = 99;

    if (loadingPct < 70) loadingPct += 7;
    else if (loadingPct < 90) loadingPct += 1;
    else if (loadingPct < 99) loadingPct += 0.3;

    loadingPct = clamp(loadingPct, 0, 99);
    const shown = Math.floor(loadingPct);
    if (pctEl) pctEl.textContent = `%${shown}`;
    if (barEl) barEl.style.width = `${shown}%`;

    // Update loading text after 90% to reduce "stuck" perception.
    const txt = $('loading-text');
    if (txt) {
      if (shown >= 90) {
        txt.textContent = (currentLang === 'TR') ? 'SON İŞLEMLER YAPILIYOR...' : 'FINALIZING RESULTS...';
      }
    }
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
  if (score >= 80) return currentLang === 'TR' ? 'Yüksek Potansiyel' : 'High Potential';
  if (score >= 60) return currentLang === 'TR' ? 'İyi Potansiyel' : 'Good Potential';
  if (score >= 40) return currentLang === 'TR' ? 'Orta Potansiyel' : 'Medium Potential';
  return currentLang === 'TR' ? 'Düşük Potansiyel' : 'Low Potential';
}
function formatCoords(lat, lon) {
  const a = Number(lat), b = Number(lon);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return '--';
  return `📍 ${a.toFixed(5)}, ${b.toFixed(5)}`;
}

function updateSeasonOptionLabel(seasonLabelTR, seasonLabelEN) {
  const sel = $('month-selector');
  if (!sel) return;
  const opt = Array.from(sel.options || []).find(o => o.value === 'season');
  if (!opt) return;

  const raw = ((currentLang === 'EN' ? seasonLabelEN : seasonLabelTR) || '').trim();
  if (!raw) {
    opt.textContent = currentLang === 'EN' ? '🌿 RECOMMENDED SEASON (Phenology)' : '🌿 ÖNERİLEN SEZON (Fenoloji)';
    return;
  }
  // keep it short in the dropdown
  const short = raw.split(' (')[0]; // e.g., "Nisan–Haziran"
  opt.textContent = currentLang === 'EN' ? `🌿 RECOMMENDED SEASON (${short})` : `🌿 ÖNERİLEN SEZON (${short})`;
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
    alert(currentLang === 'EN' ? 'Map engine failed to load. Please refresh.' : 'Harita motoru yüklenemedi. Sayfayı yenileyin.');
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

document.addEventListener('DOMContentLoaded', () => {
  // Restore preferred UI language (TR/EN). Only presentation uses this.
  setLang(_getSavedLang() || 'EN');
  setUnits(_getSavedUnits() || 'METRIC');
  waitForLeafletAndInit();
});

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

// UI language toggle (presentation-only; core computation does not depend on language)
function setLang(lang) {
  const up = String(lang || 'EN').toUpperCase();
  currentLang = (up === 'TR') ? 'TR' : 'EN';
  _saveLang(currentLang);

  const btnTR = $('btn-tr');
  const btnEN = $('btn-en');
  if (btnTR && btnEN) {
    if (currentLang === 'TR') {
      btnTR.classList.add('bg-primary', 'text-black');
      btnTR.classList.remove('text-gray-400');
      btnEN.classList.remove('bg-primary', 'text-black');
      btnEN.classList.add('text-gray-400');
    } else {
      btnEN.classList.add('bg-primary', 'text-black');
      btnEN.classList.remove('text-gray-400');
      btnTR.classList.remove('bg-primary', 'text-black');
      btnTR.classList.add('text-gray-400');
    }
  }

  // Apply static UI translations
  applyStaticI18n();
}

// Units toggle (presentation-only). Core computations remain metric.
function setUnits(units) {
  const up = String(units || 'METRIC').toUpperCase();
  currentUnits = (up === 'IMPERIAL') ? 'IMPERIAL' : 'METRIC';
  _saveUnits(currentUnits);

  const pairs = [
    ['btn-metric', 'btn-imperial'],
    ['btn-metric-modal', 'btn-imperial-modal'],
  ];
  for (const [idM, idI] of pairs) {
    const btnM = $(idM);
    const btnI = $(idI);
    if (!btnM || !btnI) continue;
    if (currentUnits === 'IMPERIAL') {
      btnI.classList.add('bg-primary', 'text-black');
      btnI.classList.remove('text-gray-400');
      btnM.classList.remove('bg-primary', 'text-black');
      btnM.classList.add('text-gray-400');
    } else {
      btnM.classList.add('bg-primary', 'text-black');
      btnM.classList.remove('text-gray-400');
      btnI.classList.remove('bg-primary', 'text-black');
      btnI.classList.add('text-gray-400');
    }
  }

  // If a result is already visible, re-render to reflect unit conversion.
  try { if (lastResult) renderResults(lastResult); } catch (e) {}
}

async function handleSearch(event) {
  if (!event || event.key !== 'Enter') return;
  event.preventDefault();

  const q = ($('search-input')?.value ?? '').trim();
  if (!q) return;

  try {
    const url = `https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(q)}`;
    const res = await fetch(url, { headers: { 'Accept-Language': currentLang === 'EN' ? 'en' : 'tr' } });
    const arr = await res.json();
    if (!Array.isArray(arr) || arr.length === 0) {
      alert(currentLang === 'EN' ? 'No results found.' : 'Sonuç bulunamadı.');
      return;
    }
    const lat = Number(arr[0].lat);
    const lon = Number(arr[0].lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
      alert(currentLang === 'EN' ? 'Invalid result.' : 'Geçersiz sonuç.');
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
    alert(currentLang === 'EN' ? 'Search error.' : 'Arama hatası.');
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
  if (lbl) lbl.textContent = waterManaged ? (currentLang==='EN' ? 'Managed Water: ON' : 'Yapay Su Desteği: AÇIK')
                                          : (currentLang==='EN' ? 'Managed Water: OFF' : 'Yapay Su Desteği');
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
    alert(currentLang === 'EN' ? 'Please select a location.' : 'Lütfen haritadan bir konum seçin.');
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
  setText('dynamic-text', currentLang === 'EN' ? 'Analyzing...' : 'Analiz yapılıyor...');

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
      lang: (currentLang === 'EN' ? 'en' : 'tr'),
      units: (currentUnits === 'IMPERIAL' ? 'imperial' : 'metric'),
      uid: getOrCreateUid()
    };

    const res = await fetch('/analyze', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept-Language': (currentLang === 'EN' ? 'en' : 'tr')
      },
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
    alert(currentLang === 'EN' ? 'Analysis error. Check console.' : 'Analiz hatası. Konsolu kontrol et.');
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
  const seasonLabelTR = d?.season_meta?.season_label_tr;
  const seasonLabelEN = d?.season_meta?.season_label_en;
  updateSeasonOptionLabel(seasonLabelTR, seasonLabelEN);


  // System message
  const sys = data?.sys_msg || '';
  if (sys) setText('dynamic-text', sys);

  const grid = $('metrics-grid');
  if (!grid) return;
  grid.innerHTML = '';

  const topo = d.topography || {};

  // Elevation sometimes comes back as a plain number (not a {label,val} object).
  // Keep core metric as-is; wrap only for presentation so the card always shows a value.
  const elevRaw = (topo.elevation ?? d.elevation);
  const elevMetric = (typeof elevRaw === 'number' && Number.isFinite(elevRaw))
    ? { _main: `${Math.round(elevRaw)}m`, _sub: 'NASA SRTM' }
    : elevRaw;

  const cards = [
    { title: currentLang==='EN' ? 'Vegetation' : 'Vejetasyon', icon: 'forest', border: 'border-green-500', metric: d.flora },
    { title: currentLang==='EN' ? 'Water' : 'Su Kaynağı', icon: 'water_drop', border: 'border-blue-500', metric: d.water },
    { title: currentLang==='EN' ? 'Wind' : 'Rüzgar', icon: 'air', border: 'border-sky-500', metric: d.climate?.wind },
    { title: currentLang==='EN' ? 'Flight Window' : 'Uçuş Penceresi', icon: 'event_available', border: 'border-emerald-500', metric: d.flight },
    { title: currentLang==='EN' ? 'Aspect' : 'Bakı / Yön', icon: 'explore', border: 'border-yellow-500', metric: topo.aspect || d.aspect },
    { title: currentLang==='EN' ? 'Humidity' : 'Nem', icon: 'water', border: 'border-purple-500', metric: d.climate?.humidity },
    { title: currentLang==='EN' ? 'Slope' : 'Eğim', icon: 'landscape', border: 'border-gray-400', metric: topo.slope || d.slope },
    { title: currentLang==='EN' ? 'Road Distance' : 'Yol Uzaklığı', icon: 'route', border: 'border-red-500', metric: d.transport },
    { title: currentLang==='EN' ? 'Urban' : 'Şehirleşme', icon: 'location_city', border: 'border-orange-500', metric: d.urban },
    { title: currentLang==='EN' ? 'Settlement' : 'Yerleşim', icon: 'home_work', border: 'border-amber-500', metric: d.settlement },
    // Flight suitability is a different concept than raw flight window
    { title: currentLang==='EN' ? 'Flight Suitability' : 'Uçuş Uygunluğu', icon: 'fact_check', border: 'border-emerald-400', metric: d.flight_suitability },
    { title: currentLang==='EN' ? 'Precip' : 'Yağış', icon: 'rainy', border: 'border-cyan-500', metric: d.precip },
    // (removed duplicated flight cards)
    { title: currentLang==='EN' ? 'Elevation' : 'Rakım', icon: 'terrain', border: 'border-indigo-500', metric: elevMetric },
    { title: currentLang==='EN' ? 'Temperature' : 'Sıcaklık', icon: 'thermostat', border: 'border-pink-500', metric: d.climate?.temp },
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
  if (t.includes('uçuş uygunluğu') || t.includes('flight suitability')) {
    // If backend accidentally sends a raw day count, derive class+score here.
    const daysRaw = (m.value ?? m.val ?? null);
    const days = Number(daysRaw);
    const looksLikeDays = (typeof main === 'string' && main.includes('gün')) || Number.isFinite(days);
    if (looksLikeDays) {
      const d = Number.isFinite(days) ? days : Number(String(main).replace(/[^0-9.]/g,''));
      let cls = '--', sc = 0;
      if (Number.isFinite(d)) {
        const EN = (currentLang === 'EN');
        if (d < 120) { cls = EN ? 'Poor' : 'Zayıf'; sc=20; }
        else if (d < 180) { cls = EN ? 'Moderate' : 'Orta'; sc=45; }
        else if (d < 240) { cls = EN ? 'Good' : 'İyi'; sc=70; }
        else if (d < 300) { cls = EN ? 'Very Good' : 'Çok İyi'; sc=85; }
        else { cls = EN ? 'Excellent' : 'Mükemmel'; sc=95; }
        main = `${cls} (${sc}/100)`;
        sub = EN ? `Flight window: ${Math.round(d)} days/year` : `Uçuş penceresi: ${Math.round(d)} gün/yıl`;
      }
    }

    // Even if backend sends Turkish labels, enforce English in EN mode (presentation-only).
    if (currentLang === 'EN') {
      if (typeof main === 'string') {
        main = main
          .replace(/Mükemmel/g, 'Excellent')
          .replace(/Çok İyi/g, 'Very Good')
          .replace(/İyi/g, 'Good')
          .replace(/Orta/g, 'Moderate')
          .replace(/Zayıf/g, 'Poor');
      }
      if (typeof sub === 'string') {
        sub = sub
          .replace(/Uçuş penceresi/g, 'Flight window')
          .replace(/Uçuş uygun gün(ler)?/g, 'Suitable flight days')
          .replace(/gün\/yıl/g, 'days/year')
          .replace(/Ort\.?/g, 'Avg');
      }
    }
  }

  // Unit conversion (presentation-only). Core values remain metric.
  if (currentUnits === 'IMPERIAL') {
    const tt = (title || '').toLowerCase();
    const toNum = (s) => {
      const m = String(s).match(/-?\d+(?:\.\d+)?/);
      return m ? Number(m[0]) : NaN;
    };
    const replNum = (s, newNum) => String(s).replace(/-?\d+(?:\.\d+)?/, newNum);
    const fmt1 = (x) => (Math.round(x*10)/10).toFixed(1);

    // Wind: km/h -> mph
    if (tt.includes('wind') || tt.includes('rüzgar')) {
      const v = toNum(main);
      if (Number.isFinite(v)) {
        const mph = v * 0.621371;
        main = replNum(main, fmt1(mph)).replace('km/h', 'mph');
      }
    }

    // Distances: km -> mi
    if (tt.includes('road distance') || tt.includes('yol') || tt.includes('settlement') || tt.includes('yerleşim')) {
      const v = toNum(main);
      if (Number.isFinite(v) && String(main).includes('km')) {
        const mi = v * 0.621371;
        main = replNum(main, fmt1(mi)).replace('km', 'mi');
      }
      const v2 = toNum(sub);
      if (Number.isFinite(v2) && String(sub).includes('km')) {
        const mi2 = v2 * 0.621371;
        sub = replNum(sub, fmt1(mi2)).replace('km', 'mi');
      }
    }

    // Elevation: m -> ft
    if (tt.includes('elevation') || tt.includes('rakım')) {
      const v = toNum(main);
      if (Number.isFinite(v) && /m\b/.test(String(main))) {
        const ft = v * 3.28084;
        main = replNum(main, String(Math.round(ft))).replace(/m\b/, 'ft');
      }
    }

    // Temperature: C -> F
    if (tt.includes('temperature') || tt.includes('sıcaklık')) {
      const v = toNum(main);
      if (Number.isFinite(v) && String(main).includes('°C')) {
        const f = (v * 9/5) + 32;
        main = replNum(main, fmt1(f)).replace('°C', '°F');
      }
    }

    // Precip: mm -> in
    if (tt.includes('precip') || tt.includes('yağış')) {
      const v = toNum(main);
      if (Number.isFinite(v) && String(main).includes('mm')) {
        const inch = v / 25.4;
        main = replNum(main, fmt1(inch)).replace('mm', 'in');
      }
    }
  }

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
    alert(currentLang === 'EN' ? 'Run an analysis first.' : 'Önce analiz yap.');
    return;
  }

  // Prefer id-based report URL (enables PDF export + caching).
  const rid = lastResult.report_id;
  if (rid) {
    window.open(`/report/${rid}`, '_blank');
    return;
  }

  // Single source of truth: report_id must be present.
  alert(currentLang === 'EN'
    ? 'Report id is missing. Please run the analysis again.'
    : 'Rapor kimliği bulunamadı. Lütfen analizi yeniden çalıştırın.');
}

async function shareResult() {
  if (!lastResult) return;
  const txt = `BeeLocate PRO skor: ${lastResult.score} | Konum: ${lastResult.lat}, ${lastResult.lng}`;
  try {
    if (navigator.share) { await navigator.share({ title: 'BeeLocate PRO', text: txt }); return; }
  } catch (e) {}
  try { await navigator.clipboard.writeText(txt); alert(currentLang==='EN' ? 'Copied.' : 'Kopyalandı.'); }
  catch (e) { alert(txt); }
}

// Export required functions for inline handlers
window.updateRadius = updateRadius;
window.setLang = setLang;
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
document.addEventListener('DOMContentLoaded', updateUnlimitedBadge);
