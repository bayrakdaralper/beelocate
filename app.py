import math
from datetime import datetime, timedelta
import ai_premium

import os

import ee
import requests
import presentation as pres
from flask import Flask, render_template, request, jsonify, make_response, redirect, url_for


# ----------------------------
# Small geo helpers
# ----------------------------
def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance (meters)."""
    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
    return 2*r*math.atan2(math.sqrt(a), math.sqrt(1-a))


# ----------------------------
# GEE INIT (robust)
# ----------------------------
GEE_OK = False
GEE_ERR = ''


def init_gee():
    global GEE_OK, GEE_ERR
    project = os.environ.get('EE_PROJECT', 'beelocatepro-ee')

    # 1) Preferred (production): Service Account JSON from a FILE (Render Secret File)
    # In containers, putting large JSON blobs into env vars is fragile (line breaks / quoting).
    # We therefore support reading the service account key from a mounted secret file.
    #
    # Accepted env vars (first match wins):
    # - GEE_SA_PATH (our app-specific path)
    # - GOOGLE_APPLICATION_CREDENTIALS (Google standard)
    sa_path = os.environ.get('GEE_SA_PATH') or os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    if sa_path and os.path.exists(sa_path):
        try:
            import json as _json
            with open(sa_path, 'r', encoding='utf-8') as f:
                info = _json.load(f)
            client_email = info.get('client_email')
            project_id = info.get('project_id') or project
            if not client_email:
                raise ValueError('Service account JSON missing client_email')

            credentials = ee.ServiceAccountCredentials(client_email, sa_path)
            ee.Initialize(credentials, project=project_id)
            GEE_OK = True
            GEE_ERR = ''
            print(f'GEE: Service Account Auth OK (project={project_id})')
            return
        except Exception as e_sa:
            GEE_OK = False
            GEE_ERR = str(e_sa)
            print(f"GEE Service Account Auth Failed (file): {e_sa}")

    # 1b) Backward compatible: Service Account JSON via env (NOT recommended)
    # Keeps older deployments working, but prefer the file-based method above.
    sa_json = os.environ.get('GEE_SERVICE_ACCOUNT_JSON')
    if sa_json:
        try:
            import json as _json
            import tempfile as _tempfile
            info = _json.loads(sa_json)
            client_email = info.get('client_email')
            project_id = info.get('project_id') or project
            if not client_email:
                raise ValueError('GEE_SERVICE_ACCOUNT_JSON missing client_email')

            # Earth Engine expects a key *file*. We write the JSON into a temp file.
            with _tempfile.NamedTemporaryFile('w+', suffix='.json', delete=False) as tf:
                tf.write(sa_json)
                tf.flush()
                key_path = tf.name

            credentials = ee.ServiceAccountCredentials(client_email, key_path)
            ee.Initialize(credentials, project=project_id)
            GEE_OK = True
            GEE_ERR = ''
            print(f'GEE: Service Account Auth OK (project={project_id})')
            return
        except Exception as e_sa:
            GEE_OK = False
            GEE_ERR = str(e_sa)
            print(f"GEE Service Account Auth Failed: {e_sa}")

    # 2) Fallback: attempt default project auth (works on machines with cached creds)
    try:
        ee.Initialize(project=project)
        GEE_OK = True
        GEE_ERR = ''
        print(f'GEE: Project Auth OK (project={project})')
        return
    except Exception as e:
        GEE_OK = False
        GEE_ERR = str(e)
        print(f"GEE Project Auth Failed: {e}")

    # 3) Last resort: interactive auth (LOCAL ONLY)
    # Disable by default in server environments.
    if os.environ.get('ALLOW_EE_INTERACTIVE', '0') == '1':
        try:
            ee.Authenticate()
            ee.Initialize()
            GEE_OK = True
            GEE_ERR = ''
            print("GEE: Default Auth OK")
            return
        except Exception as e2:
            GEE_OK = False
            GEE_ERR = str(e2)
            print(f"GEE Critical: {e2}")
    else:
        print("GEE: Interactive auth disabled (set ALLOW_EE_INTERACTIVE=1 to enable locally)")


init_gee()

import platform
import io
import uuid
import time
import threading
import tempfile
import subprocess
import shutil
import hashlib
import hmac
from pathlib import Path

# Optional: load .env automatically in local/dev
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

app = Flask(__name__)



# ----------------------------
# Local persistence (SQLite)
# ----------------------------
# We persist report payloads and payment state in SQLite so:
# - server restarts do NOT lose paid/unpaid state
# - report links remain valid (within TTL)
# This is still MVP-friendly and deployable.

import sqlite3
import json as _json

# ---- Simple file lock (used for PDF generation) ----
# Avoids concurrent PDF renders stepping on each other in small instances.
# Uses fcntl on Unix; degrades to a no-op on platforms without it.
from contextlib import contextmanager as _contextmanager
try:
    import fcntl as _fcntl
except Exception:  # pragma: no cover
    _fcntl = None

@_contextmanager
def _FileLock(path: str):
    f = None
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        f = open(path, "a+")
        if _fcntl is not None:
            _fcntl.flock(f.fileno(), _fcntl.LOCK_EX)
        yield
    finally:
        try:
            if f is not None and _fcntl is not None:
                _fcntl.flock(f.fileno(), _fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            if f is not None:
                f.close()
        except Exception:
            pass


# Optional AI commentary for paid reports
try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None

def _default_data_dir() -> Path:
    """Choose a persistence directory.

    Render disks are commonly mounted at /var/data. If present and writable, use it.
    Falls back to the app directory for local/dev.
    """
    env_dir = (os.environ.get("BLP_DATA_DIR") or "").strip()
    if env_dir:
        try:
            p = Path(env_dir)
            p.mkdir(parents=True, exist_ok=True)
            test = p / ".__blp_write_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink(missing_ok=True)
            return p
        except Exception:
            pass

    p = Path("/var/data")
    try:
        if p.exists():
            p.mkdir(parents=True, exist_ok=True)
            test = p / ".__blp_write_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink(missing_ok=True)
            return p
    except Exception:
        pass

    return Path(app.root_path)


DATA_DIR = _default_data_dir()
DB_PATH = os.environ.get("BLP_DB_PATH", str(DATA_DIR / "blp.sqlite"))
REPORT_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days
PAID_TTL_SECONDS = 60 * 60 * 24 * 30   # 30 days


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _db_init():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _db() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                rid TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                payload_json TEXT NOT NULL,
                uid TEXT
            );
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                rid TEXT PRIMARY KEY,
                paid_at REAL NOT NULL,
                provider TEXT NOT NULL,
                email TEXT,
                ip TEXT
            );
            """
        )

        # Daily free-usage quota (MVP). Keyed primarily by IP.
        # This prevents trivial abuse like switching browsers to reset client-side counters.
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS free_quota (
                k TEXT PRIMARY KEY,
                day_bucket INTEGER NOT NULL,
                count INTEGER NOT NULL
            );
            """
        )
        # Last computed report per day (MVP). Helps paywall flow when client storage is cleared.
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS last_reports (
                k TEXT PRIMARY KEY,
                day_bucket INTEGER NOT NULL,
                rid TEXT NOT NULL
            );
            """
        )

        # Very small rate-limit table (MVP). Keyed by window bucket.
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS rate_limits (
                k TEXT PRIMARY KEY,
                window_start INTEGER NOT NULL,
                count INTEGER NOT NULL
            );
            """
        )
        con.commit()


def _db_migrate_columns():
    """Best-effort SQLite migrations (additive only).

    We keep migrations extremely conservative for MVP:
    - Only ADD COLUMN when missing.
    - Never drop/rename columns.
    """
    try:
        with _db() as con:
            cols = {r[1] for r in con.execute("PRAGMA table_info(reports)").fetchall()}
            if "uid" not in cols:
                con.execute("ALTER TABLE reports ADD COLUMN uid TEXT")

            pcols = {r[1] for r in con.execute("PRAGMA table_info(payments)").fetchall()}
            if "ip" not in pcols:
                con.execute("ALTER TABLE payments ADD COLUMN ip TEXT")
            con.commit()
    except Exception:
        # Never fail app startup due to migration issues.
        pass


def _db_gc():
    now = time.time()
    # expire old reports
    with _db() as con:
        con.execute("DELETE FROM reports WHERE expires_at < ?", (now,))
        # keep payments for PAID_TTL_SECONDS
        con.execute("DELETE FROM payments WHERE paid_at < ?", (now - PAID_TTL_SECONDS,))
        # Keep rate-limit buckets for ~2 days only.
        con.execute("DELETE FROM rate_limits WHERE window_start < ?", (int(now) - 2 * 86400,))
        # Keep free quota buckets for ~3 days only.
        con.execute("DELETE FROM free_quota WHERE day_bucket < ?", (int(now // 86400) - 3,))
        con.commit()


_db_init()
_db_migrate_columns()


# ----------------------------
# PDF cache + concurrency guard
# ----------------------------
# We keep a short in-memory cache (fast), plus an optional disk cache (survives restarts).
# We also serialize PDF generation using a file lock to avoid headless-Chrome stampedes.

PDF_CACHE: dict[str, dict] = {}
PDF_TTL_SECONDS = 60 * 60  # 1 hour

PDF_CACHE_DIR = Path(os.environ.get('PDF_CACHE_DIR', str(Path(DB_PATH).parent / 'pdf_cache')))
PDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)

PDF_LOCK_FILE = Path(os.environ.get('PDF_LOCK_FILE', str(Path(DB_PATH).parent / 'pdf_generation.lock')))

# Simple process-local lock for PDF generation.
# Note: In production, prefer a proper inter-process lock (or job queue) if you
# run multiple workers. For local/dev and single-worker deployments this is
# sufficient and prevents overlapping PDF renders.
_PDF_GEN_LOCK = threading.Lock()


class _FileLock:
    """Minimal lock context manager.

    We keep the API shape (used as `with _FileLock(PDF_LOCK_FILE):`) but use a
    simple in-process mutex to avoid NameError/runtime issues.
    """

    def __init__(self, _path: Path):
        self.path = _path

    def __enter__(self):
        _PDF_GEN_LOCK.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        _PDF_GEN_LOCK.release()
        return False


def _pdf_disk_path(rid: str) -> Path:
    safe = ''.join([c for c in rid if c.isalnum()])
    return PDF_CACHE_DIR / f"{safe}.pdf"


def _pdf_disk_get(rid: str) -> bytes | None:
    p = _pdf_disk_path(rid)
    if not p.exists():
        return None
    try:
        return p.read_bytes()
    except Exception:
        return None


def _pdf_disk_put(rid: str, pdf_bytes: bytes) -> None:
    p = _pdf_disk_path(rid)
    tmp = p.with_suffix('.pdf.tmp')
    try:
        tmp.write_bytes(pdf_bytes)
        tmp.replace(p)
    except Exception:
        # best-effort
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def _pdf_cache_get(rid: str) -> bytes | None:
    # 1) disk cache first (survives restarts)
    d = _pdf_disk_get(rid)
    if d:
        return d

    # 2) in-memory hot cache
    now = time.time()
    item = PDF_CACHE.get(rid)
    if not item:
        return None
    if (now - float(item.get('ts', 0))) > PDF_TTL_SECONDS:
        PDF_CACHE.pop(rid, None)
        return None
    return item.get('pdf')


def _pdf_cache_put(rid: str, pdf_bytes: bytes) -> None:
    PDF_CACHE[rid] = {'ts': time.time(), 'pdf': pdf_bytes}
    _pdf_disk_put(rid, pdf_bytes)


# ----------------------------
# Paid-only AI insights (OpenAI)
# ----------------------------
# Safety knobs:
# - AI_REPORTS=0 disables AI entirely.
# - OPENAI_MODEL defaults to gpt-5-mini.

AI_REPORTS = os.environ.get("AI_REPORTS", "1").strip()  # 1=enabled
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini").strip()


def _ai_enabled() -> bool:
    return (AI_REPORTS not in ("0", "false", "False", "no", "NO")) and bool(os.environ.get("OPENAI_API_KEY")) and (OpenAI is not None)


def _ai_compose_prompt(payload: dict) -> str:
    """Build the premium AI prompt for paid reports (English-only for MVP).

    Requirements:
    - Ground every claim in the provided metrics; don't hallucinate species or local facts.
    - Be practical and calm: no hype, no doom, no overpromising yields.
    - Prefer short paragraphs + bullets; avoid academic jargon.
    - Assume the reader is a working beekeeper who wants a clear decision.

    The model must return a JSON object matching our schema (see _ai_generate_insights).
    """
    payload = payload or {}
    details = (payload.get('details') or {})
    season = (details.get('season_meta') or {})
    score = payload.get('score', 0)

    flora = details.get('flora') or {}
    water = details.get('water') or {}
    precip = details.get('precip') or {}
    flight = details.get('flight') or {}
    topo = details.get('topography') or {}

    climate = details.get('climate') or {}
    temp = climate.get('temp') or {}
    wind = climate.get('wind') or {}
    humidity = climate.get('humidity') or {}

    transport = details.get('transport') or details.get('road') or details.get('roads') or {}
    urban = details.get('urban') or details.get('urbanization') or details.get('human') or {}
    settlement = details.get('settlement') or details.get('settlements') or details.get('residential') or {}

    aspect = (topo.get('aspect') or {})
    slope = (topo.get('slope') or {})
    elevation = (topo.get('elevation') or {})

    def _g(d, k, default='--'):
        if not isinstance(d, dict):
            return default
        v = d.get(k)
        return default if v is None or str(v).strip()=='' else v

    # Season strings
    season_label = _season_label_en(season) if season and season.get('peak_month') else '--'

    # Compact landcover top classes
    landcover = (flora.get('landcover') or flora.get('land_cover') or {})
    lc_txt = ''
    if isinstance(landcover, dict) and landcover:
        try:
            items = sorted(landcover.items(), key=lambda x: -float(x[1]))[:5]
        except Exception:
            items = list(landcover.items())[:5]
        parts = []
        for k,v in items:
            try:
                parts.append(f"{k}:{float(v):.0f}%")
            except Exception:
                parts.append(f"{k}:{v}")
        lc_txt = ', '.join(parts)

    # Build prompt
    lines = []
    lines.append("You are BeeLocate PRO. Write premium decision-support commentary for a PAID beekeeping site report.")
    lines.append("Write in clear English. Be direct. No marketing fluff. No guarantees.")
    lines.append("Use only the data below. If a value is missing, say it's missing and explain the implication.")
    lines.append("Return JSON only (no markdown, no code fences).")
    lines.append("")
    lines.append("DATA")
    lines.append(f"Score_0_100: {score}")
    lines.append(f"Recommended_season_label: {season_label}")
    lines.append(f"Greening_onset_SOS: {_g(season,'sos')}")
    lines.append(f"Peak_month: {_g(season,'peak')}")
    lines.append(f"Confidence: {_g(payload,'confidence','--')}")
    lines.append("")
    lines.append(f"Vegetation_NDVI: {_g(flora,'val')}")
    lines.append(f"Vegetation_class: {_g(flora,'label')}")
    if lc_txt:
        lines.append(f"Landcover_top: {lc_txt}")
    lines.append(f"Water_signal: {_g(water,'label', _g(water,'val'))}")
    lines.append(f"Precip_total_mm: {_g(precip,'val')}")
    lines.append(f"Flight_days_per_year: {_g(flight,'days')}")
    lines.append(f"Flight_rule: 10-36C and <=8m/s")
    lines.append("")
    lines.append(f"Wind: {_g(wind,'val', _g(wind,'label'))}")
    lines.append(f"Temperature: {_g(temp,'val', _g(temp,'label'))}")
    lines.append(f"Humidity_percent: {_g(humidity,'val', _g(humidity,'label'))}")
    lines.append("")
    lines.append(f"Slope: {_g(slope,'val', _g(slope,'value'))}")
    lines.append(f"Aspect: {_g(aspect,'label', _g(aspect,'val'))}")
    lines.append(f"Elevation_m: {_g(elevation,'val', _g(topo.get('elevation',{}),'val'))}")
    lines.append("")
    lines.append(f"Road_distance_km: {_g(transport,'val', _g(transport,'distance'))}")
    lines.append(f"Settlement_distance_km: {_g(settlement,'val', _g(settlement,'distance'))}")
    lines.append(f"Urban_pressure_label: {_g(urban,'label', _g(urban,'val'))}")
    lines.append("")
    lines.append("OUTPUT REQUIREMENTS")
    lines.append("- executive_summary: 120-180 words. Summarize suitability, biggest limiter, and what makes it workable (if anything).")
    lines.append("- why_this_score: 3 bullet strings. Explain the score logic in human terms (what pushed it up/down).")
    lines.append("- general_interpretation: 80-130 words. Practical framing: how to use this score + seasonal caveat.")
    lines.append("- key_drivers: 4 bullet strings. The top factors (mix of positives/negatives).")
    lines.append("- risks: 4 bullet strings. Real-world risks and what would trigger them.")
    lines.append("- field_checks: 5 bullet strings. On-site validations before placing hives.")

    return "\n".join(lines)




def _extract_json_candidate(s: str):
    """Try to pull a JSON object out of a messy model output."""
    if not s:
        return None
    s = s.strip()
    if s.startswith("{") and s.endswith("}"):
        return s
    first = s.find("{")
    last = s.rfind("}")
    if first != -1 and last != -1 and last > first:
        cand = s[first:last+1].strip()
        if cand.startswith("{") and cand.endswith("}"):
            return cand
    return None


def _deterministic_insights(payload: dict) -> dict:
    """Rule-based premium insights when AI is unavailable or output is unusable.
    Designed to feel 'paid' by explicitly referencing card values and giving actionable interpretation.
    """

    def g(path, default=None):
        cur = payload
        try:
            for k in path:
                if cur is None:
                    return default
                cur = cur.get(k)
            return cur if cur is not None else default
        except Exception:
            return default

    def fnum(x, nd=1):
        try:
            return round(float(x), nd)
        except Exception:
            return None

    score = g(["score"], g(["overall_score"], 0)) or 0

    cards = g(["cards"], {}) or {}

    # Vegetation details (as seen in UI)
    veg = cards.get("vegetation") or {}
    ndvi = veg.get("ndvi", veg.get("val"))
    ndvi_f = fnum(ndvi, 2)
    veg_label = veg.get("label") or veg.get("class") or "Vegetation"
    lc = veg.get("landcover") or veg.get("land_cover") or {}
    # support both dict and list formats
    lc_items = []
    if isinstance(lc, dict):
        for k, v in lc.items():
            try:
                lc_items.append((k, float(v)))
            except Exception:
                pass
    elif isinstance(lc, list):
        for it in lc:
            if isinstance(it, dict) and "name" in it and "pct" in it:
                lc_items.append((it["name"], float(it["pct"])))
    lc_items = sorted(lc_items, key=lambda t: t[1], reverse=True)[:3]
    lc_text = ", ".join([f"{name}: {pct:.1f}%" for name, pct in lc_items]) if lc_items else None

    # Seasonality / phenology
    sos = veg.get("sos") or veg.get("SOS")
    peak = veg.get("peak") or veg.get("Peak")
    window = g(["analysis_window_label"], g(["window_label"], None))

    # Water
    water = cards.get("water") or {}
    water_label = (water.get("label") or water.get("status") or "").strip()
    water_lower = water_label.lower()

    # Aspect / slope
    aspect = cards.get("aspect") or {}
    aspect_label = aspect.get("label") or aspect.get("name")
    aspect_deg = aspect.get("deg") or aspect.get("val")
    slope = cards.get("slope") or {}
    slope_val = slope.get("val", slope.get("slope_pct", slope.get("pct")))
    slope_f = fnum(slope_val, 0)

    # Wind / flight / temperature
    wind = cards.get("wind") or {}
    wind_val = wind.get("val")
    wind_dir = wind.get("dir") or wind.get("direction") or wind.get("label")
    wind_f = fnum(wind_val, 1)

    flight = cards.get("flight_window") or {}
    flight_days = flight.get("days", flight.get("val"))
    flight_f = fnum(flight_days, 0)

    temp = cards.get("temperature") or {}
    temp_val = temp.get("val")
    temp_f = fnum(temp_val, 1)

    # Access / pressure
    road = cards.get("road_distance") or {}
    road_km = fnum(road.get("val"), 1)
    settlement = cards.get("settlement") or {}
    settlement_km = fnum(settlement.get("val"), 1)
    urban = cards.get("urban") or {}
    urban_label = urban.get("label") or urban.get("class")

    # Build a premium executive summary (120–180 words-ish)
    parts = []
    parts.append(f"Overall, this site scores {int(score)}/100, which suggests a {'moderate' if score>=50 else 'limited'} suitability profile in local context.")

    # Vegetation interpretation
    if ndvi_f is not None:
        if ndvi_f >= 0.70:
            veg_interp = f"Vegetation looks strong (NDVI {ndvi_f:.2f})."
        elif ndvi_f >= 0.40:
            veg_interp = f"Vegetation is moderate (NDVI {ndvi_f:.2f}) and may be seasonal."
        else:
            veg_interp = f"Vegetation signal is weak (NDVI {ndvi_f:.2f}), which is a limiting factor unless nearby forage exists."
        if lc_text:
            veg_interp += f" Land cover is dominated by {lc_text}."
        if sos or peak:
            veg_interp += f" Phenology indicates greening onset around {sos or 'N/A'} and peak around {peak or 'N/A'}."
        parts.append(veg_interp)

    # Water interpretation
    if water_label:
        if any(x in water_lower for x in ['no', 'none', 'yok']):
            parts.append("Surface-water signal is limited. If you proceed, plan managed water (tanks/troughs) and verify seasonal persistence of streams/ponds.")
        else:
            parts.append("Water availability appears present/nearby, which supports colony cooling and foraging during warm periods.")

    # Terrain / aspect
    terr = []
    if slope_f is not None:
        if slope_f <= 10:
            terr.append(f"Slope (~{int(slope_f)}%) is generally manageable for setup and access.")
        elif slope_f <= 25:
            terr.append(f"Slope (~{int(slope_f)}%) may constrain micro-site options and increases setup effort.")
        else:
            terr.append(f"Slope (~{int(slope_f)}%) is steep and a major operational constraint.")
    if aspect_label:
        terr.append(f"Aspect trends {aspect_label}{(' ('+str(int(fnum(aspect_deg,0)))+'°)') if aspect_deg is not None and fnum(aspect_deg,0) is not None else ''}; morning sun exposure and wind sheltering should be evaluated on site.")
    if terr:
        parts.append(" ".join(terr))

    # Climate/flight window signals
    clim = []
    if flight_f is not None:
        clim.append(f"Estimated flight window is about {int(flight_f)} days/year; shorter windows increase timing sensitivity.")
    if wind_f is not None:
        clim.append(f"Wind is around {wind_f} km/h{(' ('+str(wind_dir)+')') if wind_dir else ''}; exposed ridges can reduce flight activity.")
    if temp_f is not None:
        clim.append(f"Current temperature indicator is ~{temp_f}°C; interpret alongside the seasonal window.")
    if clim:
        parts.append(" ".join(clim))

    # Human pressure / access
    access = []
    if road_km is not None:
        access.append(f"Road distance is ~{road_km} km (logistics vs disturbance trade‑off).")
    if settlement_km is not None:
        access.append(f"Nearest settlement is ~{settlement_km} km; closer sites may have higher pesticide/conflict risk.")
    if urban_label:
        access.append(f"Local human pressure class: {urban_label}.")
    if access:
        parts.append(" ".join(access))

    executive_summary = " ".join(parts).strip()

    # Why this score: 3 bullets tied to top signals
    why = []
    if ndvi_f is not None:
        why.append(f"Vegetation signal (NDVI {ndvi_f:.2f}) and land‑cover composition are key drivers of forage potential.")
    if water_label:
        why.append(f"Water availability status ('{water_label}') materially shifts operational risk and summer resilience.")
    if slope_f is not None or road_km is not None:
        why.append("Terrain and access conditions affect practical placement options and the true cost of operating the site.")

    # Key drivers: choose 4, specific
    drivers = []
    if ndvi_f is not None:
        drivers.append(f"Vegetation: {veg_label} (NDVI {ndvi_f:.2f})" + (f"; {lc_text}" if lc_text else ""))
    if water_label:
        drivers.append(f"Water: {water_label}")
    if flight_f is not None:
        drivers.append(f"Flight window: ~{int(flight_f)} days/year")
    if slope_f is not None:
        drivers.append(f"Slope: ~{int(slope_f)}%")

    # Risks: 4, specific
    risks = []
    if water_label and any(x in water_lower for x in ['no', 'none', 'yok']):
        risks.append("Water dependence in dry months; verify persistence and plan supplementation.")
    if slope_f is not None and slope_f > 20:
        risks.append("Steeper terrain reduces placement flexibility and increases setup/maintenance effort.")
    if wind_f is not None and wind_f > 25:
        risks.append("High wind exposure can reduce foraging time; prioritize sheltered micro‑sites.")
    if settlement_km is not None and settlement_km < 0.5:
        risks.append("Close settlement increases conflict/pesticide risk; confirm local spraying practices.")
    if not risks:
        risks.append("No dominant single risk is flagged; the limiting factors are likely seasonal forage continuity and site‑specific constraints.")

    # Field checks: actionable and tied to the cards
    checks = [
        "Confirm dominant flowering species and continuity within 1–3 km (field walk + local beekeeper input).",
        "Verify water availability across the peak season; identify a backup water source if needed.",
        "Check wind exposure at hive height; use natural/constructed windbreaks where appropriate.",
        "Select micro‑site placement based on slope, drainage, and shade; avoid cold‑air pockets.",
        "Validate access and permissions (roads, spraying schedules, livestock activity, nearby dwellings).",
    ]

    # General interpretation: short but meaningful
    general = (
        "Interpret the score in context: prioritize consistent forage (not only peak greenness), reliable water access, and practical placement conditions. "
        "Validate limiting factors on site before scaling."
    )

    return {
        "executive_summary": executive_summary,
        "why_this_score": why[:3],
        "general_interpretation": general,
        "key_drivers": drivers[:4],
        "risks": risks[:4],
        "field_checks": checks[:5],
        "mode": "fallback_rich",
    }

def _ai_generate_insights(payload: dict) -> dict:
    """Call OpenAI to generate premium insights.

    Uses a strict JSON schema via the Responses API. We still keep robust extraction logic
    for cases where output_text is empty or the SDK returns output_json blocks.
    """
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    prompt = _ai_compose_prompt(payload)

    schema = {
        "name": "beelocate_ai_insights_v1",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "executive_summary": {"type": "string"},
                "why_this_score": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 5},
                "general_interpretation": {"type": "string"},
                "key_drivers": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 6},
                "risks": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 6},
                "field_checks": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 8},
            },
            "required": ["executive_summary", "why_this_score", "general_interpretation", "key_drivers", "risks", "field_checks"],
        },
        "strict": True,
    }

    # Some OpenAI Python SDK versions do not support `response_format` in `responses.create`.
    # We try strict JSON-schema mode first; if unsupported, we fall back to
    # "JSON-only" prompting and parse the text response.
    try:
        resp = client.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
            response_format={"type": "json_schema", "json_schema": schema},
            max_output_tokens=1100,
            store=False,
        )
    except TypeError as e:
        # Older SDK: no response_format
        if "response_format" not in str(e):
            raise
        json_only_prompt = (
            prompt
            + "\n\nIMPORTANT: Return ONLY a valid JSON object with keys: executive_summary, why_this_score, general_interpretation, key_drivers, risks, field_checks."
            + " Do not wrap in markdown. Do not include any extra keys."
        )
        resp = client.responses.create(
            model=OPENAI_MODEL,
            input=json_only_prompt,
            max_output_tokens=1100,
            store=False,
        )

    def _extract_json_from_response(r):
        # 1) Some SDKs expose a parsed object directly
        for attr in ("output_json", "parsed", "json"):
            v = getattr(r, attr, None)
            if isinstance(v, dict) and v:
                return v

        out = getattr(r, "output", None)
        if isinstance(out, list):
            for item in out:
                content = None
                if isinstance(item, dict):
                    content = item.get("content")
                else:
                    content = getattr(item, "content", None)
                if isinstance(content, list):
                    for block in content:
                        # dict blocks
                        if isinstance(block, dict):
                            btype = block.get("type")
                            if btype == "output_json" and isinstance(block.get("json"), dict):
                                return block.get("json")
                            if btype in ("output_text", "text") and isinstance(block.get("text"), str):
                                # may still be JSON text
                                pass
                        else:
                            btype = getattr(block, "type", None)
                            bjson = getattr(block, "json", None)
                            if btype == "output_json" and isinstance(bjson, dict):
                                return bjson

        return None

    j = _extract_json_from_response(resp)

    def _extract_text_fallback(r) -> str:
        t = getattr(r, "output_text", None)
        if isinstance(t, str) and t.strip():
            return t.strip()
        out = getattr(r, "output", None)
        parts = []
        if isinstance(out, list):
            for item in out:
                content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            if isinstance(block.get("text"), str) and block.get("text").strip():
                                parts.append(block.get("text").strip())
                        else:
                            txt = getattr(block, "text", None)
                            if isinstance(txt, str) and txt.strip():
                                parts.append(txt.strip())
        return "\n".join(parts).strip()

    if not isinstance(j, dict):
        # Try parsing as JSON text
        cleaned = _extract_text_fallback(resp).strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.replace("json\n", "", 1).strip()
        if cleaned:
            # Trim to the first JSON object if needed
            if "{" in cleaned and "}" in cleaned:
                cleaned2 = cleaned[cleaned.find("{"): cleaned.rfind("}") + 1]
            else:
                cleaned2 = cleaned
            try:
                j = _json.loads(cleaned2)
            except Exception:
                j = None

    if not isinstance(j, dict):
        return _deterministic_insights(payload)

    # Normalize + guardrails
    def _s(x):
        return x.strip() if isinstance(x, str) else ""

    outj = {
        "executive_summary": _s(j.get("executive_summary")),
        "why_this_score": [ _s(x) for x in (j.get("why_this_score") or []) if _s(x) ],
        "general_interpretation": _s(j.get("general_interpretation")),
        "key_drivers": [ _s(x) for x in (j.get("key_drivers") or []) if _s(x) ],
        "risks": [ _s(x) for x in (j.get("risks") or []) if _s(x) ],
        "field_checks": [ _s(x) for x in (j.get("field_checks") or []) if _s(x) ],
    }

    # If anything critical is empty, treat as error so our caller can fall back (no blank premium box).
    if not outj["executive_summary"]:
        raise RuntimeError("OpenAI returned empty executive_summary")

    # Guardrails: if list sections come back empty (can happen with weak models / partial outputs),
    # fill missing parts from deterministic fallback so the premium layout never shows empty cards.
    fb = None
    if len(outj["key_drivers"]) < 2 or len(outj["risks"]) < 2 or len(outj["field_checks"]) < 3 or len(outj["why_this_score"]) < 2 or not outj["general_interpretation"]:
        try:
            fb = _ai_fallback_insights(payload)
        except Exception:
            fb = None
    if fb:
        if len(outj["why_this_score"]) < 2:
            outj["why_this_score"] = fb.get("why_this_score", [])
        if not outj["general_interpretation"]:
            outj["general_interpretation"] = fb.get("general_interpretation", "")
        if len(outj["key_drivers"]) < 2:
            outj["key_drivers"] = fb.get("key_drivers", [])
        if len(outj["risks"]) < 2:
            outj["risks"] = fb.get("risks", [])
        if len(outj["field_checks"]) < 3:
            outj["field_checks"] = fb.get("field_checks", [])

    return outj



def _ai_fallback_insights(payload: dict) -> dict:
    """Deterministic fallback when AI is unavailable.

    Keeps the paid report from looking broken. Uses the already computed card values.
    """
    cards = payload.get("cards") or payload.get("analysis") or {}
    score = payload.get("score") or payload.get("overall_score") or payload.get("suitability")
    # Best-effort grabs; keys vary a bit across versions.
    veg = cards.get("vegetation") or cards.get("ndvi") or {}
    water = cards.get("water") or {}
    slope = cards.get("slope") or {}
    settle = cards.get("settlement") or {}
    road = cards.get("road_distance") or cards.get("road") or {}
    flight = cards.get("flight_window") or cards.get("flight") or {}

    def _v(x, keys=("val", "value")):
        for k in keys:
            if isinstance(x, dict) and x.get(k) not in (None, "--", ""):
                return x.get(k)
        return None

    ndvi = _v(veg)
    w = (water.get("label") if isinstance(water, dict) else None) or _v(water)
    s = _v(slope)
    st = _v(settle)
    rd = _v(road)
    fw = _v(flight)

    summary_parts = []
    if score is not None:
        summary_parts.append(f"This report summarizes the suitability analysis (score: {score}/100).")
    if ndvi is not None:
        summary_parts.append(f"Vegetation signal (NDVI) is {ndvi}, which helps estimate forage potential.")
    if w:
        summary_parts.append(f"Water: {w}. If surface water is limited, plan managed water support.")
    if s is not None:
        summary_parts.append(f"Slope is {s}; steeper terrain can reduce practical micro-site options.")
    if fw is not None:
        summary_parts.append(f"Flight window is about {fw} days/year; wider windows reduce seasonal risk.")
    if st is not None or rd is not None:
        summary_parts.append("Access and local pressure should be validated on site (roads, settlement distance, and land use).")

    drivers = []
    if ndvi is not None:
        drivers.append("Vegetation continuity / NDVI")
    if w:
        drivers.append("Water availability")
    if fw is not None:
        drivers.append("Flight window")
    if s is not None:
        drivers.append("Slope")
    if st is not None:
        drivers.append("Settlement distance")
    if rd is not None:
        drivers.append("Road access")

    risks = []
    if w and isinstance(w, str) and "no" in w.lower():
        risks.append("No detected surface water nearby; consider managed water.")
    if s is not None:
        risks.append("Steep terrain can limit placement and increase operational effort.")

    checks = [
        "Confirm forage sources in a 1–3 km radius (field walk / local knowledge).",
        "Check water availability seasonally (streams may be intermittent).",
        "Verify access, permissions, and local constraints (spraying, livestock, proximity).",
    ]

    why = []
    if ndvi is not None:
        try:
            nd = float(ndvi)
            if nd >= 0.5:
                why.append("Vegetation signal is strong in the selected window, supporting forage availability.")
            elif nd >= 0.3:
                why.append("Vegetation signal is moderate; forage may be patchy and season-dependent.")
            else:
                why.append("Low vegetation signal is a primary limiter unless a different season performs better.")
        except Exception:
            why.append("Vegetation (NDVI) is a key driver of forage potential and should be interpreted seasonally.")
    if w:
        if isinstance(w, str) and ("no" in w.lower() or "yok" in w.lower()):
            why.append("Surface-water signal is limited; managed water can reduce operational risk.")
        else:
            why.append("Water availability appears present/nearby, supporting colony thermoregulation and activity.")
    if s is not None:
        try:
            if float(s) >= 25:
                why.append("Steeper terrain can reduce practical micro-site options and increase setup effort.")
        except Exception:
            pass
    why = why[:3] or ["The score reflects a balance of forage potential, water/logistics, and terrain constraints."]

    gen = ("Use this score as a decision-support summary, not a guarantee. "
           "Best outcomes usually happen when vegetation continuity aligns with the recommended season, "
           "and practical constraints (access, slope, and local pressure) are acceptable. "
           "Validate the limiting factors on-site before scaling.")

    return {
        "executive_summary": " ".join(summary_parts).strip() or "This report summarizes the suitability analysis for the selected location.",
        "why_this_score": why[:3],
        "general_interpretation": gen,
        "key_drivers": drivers[:4],
        "risks": risks[:4],
        "field_checks": checks[:5],
    }




def _ensure_ai_cached(report_id: str, payload: dict) -> dict:
    """Ensure premium AI insights are generated once and cached.

    IMPORTANT: This function must return the *full report payload*, not only the
    AI section. Returning only the AI dict will silently break the report render
    (score becomes 0/100, indicators become '---', etc.).

    When AI is generated (or an error is recorded), the updated payload is also
    persisted back into SQLite (payload_json) so subsequent requests reuse it.
    """

    if not isinstance(payload, dict):
        return payload

    ai = payload.get("ai") or {}
    if isinstance(ai, dict) and ai.get("executive_summary"):
        return payload

    updated = False
    try:
        insights = ai_premium.generate_ai_insights(payload)
        payload["ai"] = insights
        payload.pop("ai_error", None)
        updated = True
    except Exception as e:
        # Do not break the report if AI fails; store error and fall back.
        payload["ai_error"] = str(e)
        try:
            # existing deterministic fallback in this codebase (rich cards)
            if "_deterministic_insights" in globals():
                fb = _deterministic_insights(payload)
                payload["ai"] = fb
                updated = True
                return _report_store_update(report_id, payload) or payload
        except Exception:
            pass
        payload["ai"] = {
            "executive_summary": "",
            "key_drivers": "",
            "risks": "",
            "field_checks": "",
            "full_text": "",
            "mode": "ai_error",
        }
        updated = True

    if updated:
        _report_store_update(report_id, payload)
    return payload


# ----------------------------
# Non-blocking AI generation
# ----------------------------

AI_BG_LOCK = threading.Lock()
AI_BG_TASKS: dict = {}  # rid -> {'ts': float}


def _ai_status_from_payload(payload: dict) -> str:
    """Return a stable status string for UI/polling."""
    if not isinstance(payload, dict):
        return "unknown"
    s = str(payload.get("ai_status") or "").strip().lower()
    if s in ("done", "running", "pending", "error"):
        return s
    ai = payload.get("ai")
    if isinstance(ai, dict) and (ai.get("verdict") or ai.get("executive_summary")):
        return "done"
    if payload.get("ai_error"):
        return "error"
    return "pending"


def _kickoff_ai_generation(report_id: str, payload: dict) -> dict:
    """Start AI generation in the background (best-effort) and return immediately.

    This prevents /report from blocking on an OpenAI call.
    """
    if not report_id or not isinstance(payload, dict):
        return payload

    # If already done, no-op.
    if _ai_status_from_payload(payload) == "done":
        payload["ai_status"] = "done"
        return payload

    # If report is not paid, never generate premium AI.
    try:
        if not _is_paid(report_id):
            return payload
    except Exception:
        return payload

    # Mark as pending/running in persisted payload (so UI can show progress).
    payload.setdefault("ai_status", "pending")
    payload.setdefault("ai_started_at", time.time())
    _report_store_update(report_id, payload)

    with AI_BG_LOCK:
        if report_id in AI_BG_TASKS:
            return payload
        AI_BG_TASKS[report_id] = {"ts": time.time()}

    def _worker():
        try:
            p = _report_store_get(report_id) or payload
            if not isinstance(p, dict):
                return
            p["ai_status"] = "running"
            _report_store_update(report_id, p)

            # Generate and persist full payload with AI.
            p2 = _ensure_ai_cached(report_id, p)
            if isinstance(p2, dict):
                p2["ai_status"] = "done"
                p2.pop("ai_error", None)
                _report_store_update(report_id, p2)
        except Exception as e:
            try:
                p = _report_store_get(report_id) or payload
                if isinstance(p, dict):
                    p["ai_status"] = "error"
                    p["ai_error"] = str(e)
                    _report_store_update(report_id, p)
            except Exception:
                pass
        finally:
            with AI_BG_LOCK:
                AI_BG_TASKS.pop(report_id, None)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return payload


@app.get("/api/ai-status/<rid>")
def api_ai_status(rid: str):
    """Lightweight status endpoint used by thank-you and report UI."""
    payload = _report_store_get(rid) or {}
    status = _ai_status_from_payload(payload)
    # Provide a minimal hint text for UI (never block here).
    ai_error = payload.get("ai_error") if isinstance(payload, dict) else None
    return jsonify({
        "ok": True,
        "report_id": rid,
        "paid": bool(_is_paid(rid)),
        "ai_status": status,
        "ai_error": ai_error,
    })


# ----------------------------
# Optional in-memory hot cache
# ----------------------------
#
# This cache was added as a micro-optimization (avoid a SQLite read right after
# /analyze). In practice it adds failure modes (globals not defined after
# refactors, multi-process deployments, stale state) that are not worth it at
# this stage.
#
# Default: OFF. SQLite is the source of truth.
USE_REPORT_HOT_CACHE = os.getenv("USE_REPORT_HOT_CACHE", "0") == "1"
REPORT_HOT: dict = {} if USE_REPORT_HOT_CACHE else {}
HOT_TTL_SECONDS = int(os.getenv("REPORT_HOT_TTL_SECONDS", "900"))  # 15 min


def _report_hot_gc():
    now = time.time()
    dead = [k for k,v in REPORT_HOT.items() if (now - float(v.get('ts', 0))) > HOT_TTL_SECONDS]
    for k in dead:
        REPORT_HOT.pop(k, None)


def _report_store_put(payload: dict, uid: str = "") -> str:
    _db_gc()
    rid = uuid.uuid4().hex
    created = time.time()
    expires = created + REPORT_TTL_SECONDS
    payload_json = _json.dumps(payload, ensure_ascii=False)
    with _db() as con:
        con.execute(
            "INSERT OR REPLACE INTO reports (rid, created_at, expires_at, payload_json, uid) VALUES (?, ?, ?, ?, ?)",
            (rid, created, expires, payload_json, uid or ""),
        )
        con.commit()
    REPORT_HOT[rid] = {'ts': time.time(), 'payload': payload}
    _report_hot_gc()
    return rid


def _report_store_get(rid: str) -> dict | None:
    _db_gc()
    _report_hot_gc()
    hot = REPORT_HOT.get(rid)
    if hot:
        return hot.get('payload')
    with _db() as con:
        row = con.execute("SELECT payload_json FROM reports WHERE rid = ?", (rid,)).fetchone()
    if not row:
        return None
    try:
        payload = _json.loads(row['payload_json'])
    except Exception:
        return None
    REPORT_HOT[rid] = {'ts': time.time(), 'payload': payload}
    return payload


def _report_store_update(rid: str, payload: dict) -> None:
    """Persist an updated payload_json for an existing report (e.g., after AI enrichment)."""
    if not rid:
        return
    _db_gc()
    payload_json = _json.dumps(payload or {}, ensure_ascii=False)
    with _db() as con:
        con.execute(
            "UPDATE reports SET payload_json = ? WHERE rid = ?",
            (payload_json, rid),
        )
        con.commit()
    REPORT_HOT[rid] = {'ts': time.time(), 'payload': payload}


# ----------------------------
# Payment store (SQLite)
# ----------------------------

def _paid_set(rid: str, provider: str, email: str = "", ip: str = "") -> None:
    _db_gc()
    with _db() as con:
        con.execute(
            "INSERT OR REPLACE INTO payments (rid, paid_at, provider, email, ip) VALUES (?, ?, ?, ?, ?)",
            (rid, time.time(), provider, email or "", ip or ""),
        )
        con.commit()


def _paid_bind_ip(rid: str, ip: str) -> None:
    """Bind an IP to a paid purchase (best-effort).

    Purpose: allow the same buyer to use another browser/device on the same network
    during the 24h unlimited window without a full login system.
    """
    if not (rid and ip):
        return
    try:
        with _db() as con:
            con.execute("UPDATE payments SET ip=? WHERE rid=?", (ip, rid))
            con.commit()
    except Exception:
        pass


def _ip_has_active_paid(ip: str) -> bool:
    """Is there any paid purchase tied to this IP within the 24h window?"""
    if not ip:
        return False
    now = time.time()
    try:
        with _db() as con:
            row = con.execute(
                "SELECT paid_at FROM payments WHERE ip = ? ORDER BY paid_at DESC LIMIT 1",
                (ip,)
            ).fetchone()
        if not row:
            return False
        return (float(row[0]) + 60*60*24) > now
    except Exception:
        return False


def _is_paid(report_id: str) -> bool:
    _db_gc()
    with _db() as con:
        row = con.execute("SELECT 1 FROM payments WHERE rid = ? LIMIT 1", (report_id,)).fetchone()
    return bool(row)


def _paid_until_ts(report_id: str) -> float | None:
    """Unlimited analyses expiration for a paid report.

    Product rule: a successful purchase unlocks unlimited analyses for 24 hours.
    We anchor the window to the payment timestamp stored in SQLite.
    """
    _db_gc()
    with _db() as con:
        row = con.execute("SELECT paid_at FROM payments WHERE rid = ? LIMIT 1", (report_id,)).fetchone()
    if not row:
        return None
    paid_at = float(row[0])
    return paid_at + (60 * 60 * 24)


# ----------------------------
# HTML -> PDF (Headless Chrome)
# ----------------------------
def _find_chrome_executable() -> str | None:
    """Find a Chrome/Chromium executable path across macOS/Linux/Windows."""
    candidates = [
        # macOS
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        # Windows
        os.path.expandvars(r"%ProgramFiles%\\Google\\Chrome\\Application\\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\\Google\\Chrome\\Application\\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\\Google\\Chrome\\Application\\chrome.exe"),
    ]

    for c in candidates:
        if c and os.path.isabs(c) and os.path.exists(c):
            return c

    # Linux/any OS PATH
    from shutil import which
    for name in ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"]:
        p = which(name)
        if p:
            return p
    return None


def _render_report_html(payload: dict, report_id: str | None = None, pdf_mode: bool = False, is_paid: bool = False) -> str:
    ctx = build_report_context(payload)
    ctx["ai_error"] = (payload or {}).get("ai_error")
    ctx["ai_status"] = _ai_status_from_payload(payload or {})
    if report_id:
        ctx["report_id"] = report_id
        if is_paid:
            ctx["paid_until"] = _paid_until_ts(report_id)
    ctx["pdf_mode"] = bool(pdf_mode)
    ctx["is_paid"] = bool(is_paid)
    ctx["price_label"] = os.environ.get("BLP_PRICE_LABEL", "$9.90")
    if report_id:
        ctx["buy_url"] = url_for("buy_report", rid=report_id)

    # AI preview flags (UI only; unpaid users never receive AI text)
    ctx["ai_preview_mode"] = (not pdf_mode) and (not is_paid)
    ctx["ai_available"] = bool((ctx.get("ai") or {}).get("executive_summary"))

    # Paid-gate for HTML view: show a short preview only.
    # IMPORTANT: do not send full KPI values to the client when unpaid, otherwise users can print/save.
    if (not pdf_mode) and (not is_paid):
        full = list(ctx.get("kpis") or [])
        preview = full[:3]
        locked = []
        for c in full[3:]:
            locked.append({
                "label": c.get("label", "Locked"),
                "value": "Locked",
                "unit": "",
                "source": c.get("source", "--"),
                "why": "Unlock to view this section in the PDF report.",
                "how": "Purchase unlocks the full report + unlimited analyses for the day.",
                "extra": [],
                "_locked": True,
            })
        ctx["preview_kpis"] = preview
        ctx["locked_kpis"] = locked

    if pdf_mode:
        css_path = Path(app.root_path) / "static" / "css" / "report.css"
        try:
            ctx["inline_css"] = css_path.read_text(encoding="utf-8")
        except Exception:
            ctx["inline_css"] = ""

    return render_template("report.html", **ctx)


def _generate_pdf_from_html(html: str) -> bytes:
    chrome = _find_chrome_executable()
    if not chrome:
        raise RuntimeError("Chrome/Chromium not found. Install Google Chrome to enable PDF export.")

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        html_path = td_path / "report.html"
        pdf_path = td_path / "report.pdf"
        html_path.write_text(html, encoding="utf-8")

        cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
            "--print-to-pdf-no-header",
            f"--print-to-pdf={str(pdf_path)}",
            str(html_path.as_uri()),
        ]

        # Linux containers typically require this
        if platform.system().lower() == "linux":
            cmd.insert(1, "--no-sandbox")

        subprocess.run(cmd, check=True)
        return pdf_path.read_bytes()




# ----------------------------
# Helpers
# ----------------------------
def deg_to_cardinal(deg):
    if deg is None:
        return "--"
    dirs = ["Kuzey", "Kuzeydoğu", "Doğu", "Güneydoğu", "Güney", "Güneybatı", "Batı", "Kuzeybatı"]
    ix = int((float(deg) + 22.5) / 45.0)
    return dirs[ix % 8]


def resolve_date_window(month_arg):
    """
    month_arg:
      - 'current' veya o ay numarası (1-12)
    Dönüş: (start_date, end_date, date_info_str)
    """
    now = datetime.now()

    if month_arg == "current" or month_arg == now.month:
        end_date = now
        start_date = now - timedelta(days=30)
        return start_date, end_date, "Son 30 Gün"

    # geçmiş ay analizi: ilgili ayın ortası +/- 15 gün
    try:
        m = int(month_arg)
    except Exception:
        m = now.month

    target_year = now.year
    if m > now.month:
        target_year = now.year - 1

    mid_date = datetime(target_year, m, 15)
    start_date = mid_date - timedelta(days=15)
    end_date = mid_date + timedelta(days=15)

    return start_date, end_date, mid_date.strftime("%Y-%m")


# ----------------------------
# Phenology (NDVI seasonal signature)
# ----------------------------
MONTH_NAMES_TR = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık"
}

# Minimal EN month names for i18n (keep TR as default)
MONTH_NAMES_EN = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
}



def get_ndvi_phenology(roi, years=3):
    """Global-safe phenology summary from the last N years of Sentinel‑2 NDVI.

    Goals:
      - Reduce false peaks caused by clouds/snow/low data months
      - Provide a confidence score for the recommended window
      - Stay global (no fixed month rules)

    Returns dict:
      - peak_month (1-12) or None
      - sos_month  (1-12) or None
      - start_month, end_month (recommended window)
      - confidence: "High" | "Medium" | "Low"
      - monthly_ndvi: {"1": 0.23, ...}
      - monthly_quality: {"1": {"n": 6, "valid_ratio": 0.71}, ...}

    Method (MVP but robust):
      1) Use Sentinel‑2 SR Harmonized (COPERNICUS/S2_SR_HARMONIZED)
      2) Mask clouds/shadows/snow using the SCL band
      3) For each month: compute NDVI mean + valid pixel ratio
      4) Smooth NDVI with a 3‑month moving average (circular)
      5) Peak: max of smoothed NDVI among months passing minimum quality
      6) Recommended window: months with NDVI >= baseline + 0.70*amplitude around peak
      7) SOS: first month crossing baseline + 0.25*amplitude with positive slope

    Notes:
      - This does not assume a hemisphere or a fixed season.
      - If data quality is low, we still return a best-effort window with Low confidence.
    """
    try:
        end = datetime.now()
        start = end - timedelta(days=int(365 * years))

        # Sentinel‑2 SR Harmonized
        ic = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
              .filterDate(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
              .filterBounds(roi)
              .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 60)))

        # SCL classes to mask (clouds, shadows, snow/ice)
        # SCL: 0 no data, 1 saturated/defective, 2 dark, 3 shadow, 4 vegetation, 5 bare,
        # 6 water, 7 unclassified, 8 cloud medium prob, 9 cloud high prob, 10 cirrus, 11 snow/ice
        def _mask_s2(img):
            scl = img.select("SCL")
            good = (scl.eq(4)  # vegetation
                    .Or(scl.eq(5))  # bare
                    .Or(scl.eq(6))  # water
                    .Or(scl.eq(7)))  # unclassified
            return img.updateMask(good)

        def _ndvi(img):
            img = _mask_s2(img)
            nd = img.normalizedDifference(["B8", "B4"]).rename("ndvi")
            return nd.copyProperties(img, ["system:time_start"])

        ndvi_ic = ic.map(_ndvi)

        months = ee.List.sequence(1, 12)

        # For valid_ratio we need a stable denominator: total pixels in ROI at target scale.
        scale = 20
        total_px = ee.Image.constant(1).rename("ones").reduceRegion(
            reducer=ee.Reducer.count(),
            geometry=roi,
            scale=scale,
            maxPixels=1e8
        ).get("ones")

        def _per_month(m):
            m = ee.Number(m)
            month_ic = ndvi_ic.filter(ee.Filter.calendarRange(m, m, "month"))
            n = month_ic.size()

            # If empty, return marker values
            month_img = ee.Image(ee.Algorithms.If(n.gt(0), month_ic.median(), ee.Image.constant(-999).rename("ndvi")))
            mean_ndvi = month_img.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=roi,
                scale=scale,
                maxPixels=1e8
            ).get("ndvi")

            valid_px = month_img.reduceRegion(
                reducer=ee.Reducer.count(),
                geometry=roi,
                scale=scale,
                maxPixels=1e8
            ).get("ndvi")

            # valid_ratio in [0,1]
            vr = ee.Number(valid_px).divide(ee.Number(total_px))
            return ee.Feature(None, {"month": m, "ndvi": mean_ndvi, "n": n, "valid_ratio": vr})

        fc = ee.FeatureCollection(months.map(_per_month))
        feats = fc.getInfo().get("features", [])

        monthly = {}
        quality = {}
        for f in feats:
            p = f.get("properties", {}) or {}
            m_i = p.get("month", None)
            v = p.get("ndvi", None)
            n = p.get("n", 0)
            vr = p.get("valid_ratio", None)

            try:
                m_i = int(m_i)
            except Exception:
                continue

            try:
                v_f = float(v)
            except Exception:
                continue

            if v_f <= -900:
                continue

            # Clamp NDVI sanity
            if v_f < -0.2: v_f = -0.2
            if v_f >  0.95: v_f = 0.95

            monthly[m_i] = v_f
            try:
                vr_f = float(vr)
                if vr_f < 0: vr_f = 0.0
                if vr_f > 1: vr_f = 1.0
            except Exception:
                vr_f = 0.0
            quality[m_i] = {"n": int(n), "valid_ratio": vr_f}

        if not monthly:
            return {
                "peak_month": None,
                "sos_month": None,
                "start_month": None,
                "end_month": None,
                "confidence": "Low",
                "monthly_ndvi": {},
                "monthly_quality": {},
                "status": "Pasif",
                "desc": "Fenoloji için yeterli Sentinel‑2 verisi yok",
            }

        # Helpers: circular moving average (3-month)
        def _get(m):
            return monthly.get(m, None)

        def _ma3(m):
            # circular neighbors
            prev_m = 12 if m == 1 else m - 1
            next_m = 1 if m == 12 else m + 1
            vals = [monthly.get(prev_m), monthly.get(m), monthly.get(next_m)]
            vals = [x for x in vals if x is not None]
            if not vals:
                return None
            return sum(vals) / len(vals)

        smoothed = {m: _ma3(m) for m in range(1, 13)}
        # Quality gate per month: enough images and enough valid pixels
        def _ok(m):
            q = quality.get(m, {"n": 0, "valid_ratio": 0.0})
            return (q["n"] >= 2) and (q["valid_ratio"] >= 0.15)

        candidates = [(m, smoothed[m]) for m in range(1, 13) if (smoothed[m] is not None and _ok(m))]
        if not candidates:
            # no month passes quality gate -> fallback to any available month, low confidence
            candidates = [(m, smoothed[m] if smoothed[m] is not None else monthly.get(m)) for m in range(1, 13) if (monthly.get(m) is not None)]
            confidence = "Low"
        else:
            # confidence from average quality across candidates
            avg_vr = sum(quality[m]["valid_ratio"] for m, _ in candidates if m in quality) / max(1, len(candidates))
            avg_n = sum(quality[m]["n"] for m, _ in candidates if m in quality) / max(1, len(candidates))
            if avg_vr >= 0.45 and avg_n >= 6:
                confidence = "High"
            elif avg_vr >= 0.25 and avg_n >= 3:
                confidence = "Medium"
            else:
                confidence = "Low"

        peak_month = max(candidates, key=lambda kv: kv[1] if kv[1] is not None else -999)[0]

        # Baseline: robust low (10th percentile approximation by min of 3 lowest months we have)
        vals_sorted = sorted([v for v in smoothed.values() if v is not None])
        if len(vals_sorted) >= 3:
            baseline = sum(vals_sorted[:3]) / 3.0
        else:
            baseline = min(vals_sorted) if vals_sorted else min(monthly.values())
        vmax = smoothed.get(peak_month) if smoothed.get(peak_month) is not None else monthly[peak_month]
        amp = max(0.0, vmax - baseline)

        # If amplitude is tiny, seasonality is weak (tropics/evergreen) -> return a broader but lower-confidence window
        if amp < 0.06:
            # choose a 4-month window centered on peak
            start_m = peak_month - 1
            end_m = peak_month + 2
            # wrap
            start_m = start_m if start_m >= 1 else 12 + start_m
            end_m = end_m if end_m <= 12 else end_m - 12
            # onset undefined in weak seasonality
            return {
                "peak_month": int(peak_month),
                "sos_month": None,
                "start_month": int(start_m),
                "end_month": int(end_m),
                "confidence": "Low" if confidence == "Low" else "Medium",
                "monthly_ndvi": {str(k): round(v, 3) for k, v in sorted(monthly.items())},
                "monthly_quality": {str(k): {"n": int(quality[k]["n"]), "valid_ratio": round(quality[k]["valid_ratio"], 3)} for k in sorted(quality.keys())},
                "status": "Aktif",
                "desc": "Fenoloji (küresel): sezonluk sinyal zayıf, geniş pencere",
            }

        # Thresholds
        thr_onset = baseline + 0.25 * amp
        thr_season = baseline + 0.70 * amp

        # SOS: first month crossing onset threshold with positive slope
        sos_month = None
        for m in range(1, 13):
            v = smoothed.get(m)
            if v is None: 
                continue
            if v >= thr_onset:
                next_m = 1 if m == 12 else m + 1
                v_next = smoothed.get(next_m)
                if v_next is None or v_next >= v - 0.01:
                    sos_month = m
                    break

        # Season window: contiguous months around peak above thr_season
        above = [m for m in range(1, 13) if (smoothed.get(m) is not None and smoothed[m] >= thr_season)]
        if not above:
            # fallback to peak±1
            start_m = 12 if peak_month == 1 else peak_month - 1
            end_m = 1 if peak_month == 12 else peak_month + 1
        else:
            # build circular contiguous segments
            # duplicate list for wrap handling
            above_sorted = sorted(above)
            # Find best segment containing peak
            def segments(months):
                segs=[]
                seg=[months[0]]
                for x,y in zip(months, months[1:]):
                    if y==x+1:
                        seg.append(y)
                    else:
                        segs.append(seg); seg=[y]
                segs.append(seg)
                return segs
            segs = segments(above_sorted)
            # handle wrap (e.g. [11,12] and [1,2])
            if segs and segs[0][0] == 1 and segs[-1][-1] == 12:
                segs[0] = segs[-1] + segs[0]
                segs = segs[:-1]
            # pick segment that contains peak; else closest
            seg_with_peak = None
            for seg in segs:
                if peak_month in seg:
                    seg_with_peak = seg; break
            if seg_with_peak is None:
                seg_with_peak = max(segs, key=len) if segs else [peak_month]
            start_m = seg_with_peak[0]
            end_m = seg_with_peak[-1]

        return {
            "peak_month": int(peak_month),
            "sos_month": int(sos_month) if sos_month else None,
            "start_month": int(start_m),
            "end_month": int(end_m),
            "confidence": confidence,
            "monthly_ndvi": {str(k): round(v, 3) for k, v in sorted(monthly.items())},
            "monthly_quality": {str(k): {"n": int(quality[k]["n"]), "valid_ratio": round(quality[k]["valid_ratio"], 3)} for k in sorted(quality.keys())},
            "status": "Aktif",
            "desc": "Fenoloji (küresel): bulut/kar maskeli + kalite kontrollü",
        }

    except Exception as e:
        print(f"Phenology Error: {e}")
        return {
            "peak_month": None,
            "sos_month": None,
            "start_month": None,
            "end_month": None,
            "confidence": "Low",
            "monthly_ndvi": {},
            "monthly_quality": {},
            "status": "Pasif",
            "desc": "Fenoloji hesaplamasında hata oluştu",
        }



def clamp(n, lo, hi):
    return max(lo, min(hi, n))


# ----------------------------
# "API sigortası"
# ----------------------------
def ensure_schema(data, default_val=0, default_label="--"):
    """
    Frontend'in beklediği tüm anahtarların dolu olduğundan emin olur.

    En kritik kural:
    - Ölçüm değerlerinden "score uydurma" (özellikle derece / metre gibi).
    - Score ancak metrik fonksiyonu açıkça verdiyse taşınır.
    """
    if not isinstance(data, dict):
        data = {}

    schema = {
        # display / compat
        "val": default_val,      # bazen string, bazen numeric
        "value": default_val,    # numeric (opsiyonel)
        "label": default_label,  # ana metin
        "desc": "--",            # alt metin
        "status": "Pasif",
        # scoring
        "score": None,           # 0-100 (opsiyonel)
        # legacy / ui
        "_main": default_label,
        "_sub": "--",
        "_score": None,
    }

    merged = schema.copy()

    for k, v in data.items():
        if v is not None:
            merged[k] = v

    v = merged.get("val")
    if isinstance(v, str) and v.strip() and v.strip() != "--":
        merged["_main"] = v
    else:
        merged["_main"] = merged.get("label", default_label)

    merged["_sub"] = merged.get("desc", "--")
    merged["_score"] = merged.get("score", None)

    return merged


# ----------------------------
# Sentinel-2 Safe fetch
# ----------------------------
def get_sentinel_collection_safe(roi, start_date, end_date):
    """
    Sentinel-2 koleksiyonunu güvenli şekilde çeker.
    Plan A: tarih aralığı + %20 bulut
    Plan B: 60 gün genişlet + %40 bulut
    Boşsa -> None
    """
    try:
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        s2 = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(roi)
            .filterDate(start_str, end_str)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
        )

        count = s2.size().getInfo()
        if count and count > 0:
            return s2.median()

        print("Sentinel-2 Fallback Triggered")
        start_fb = (start_date - timedelta(days=60)).strftime("%Y-%m-%d")
        s2_fb = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(roi)
            .filterDate(start_fb, end_str)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40))
        )

        count_fb = s2_fb.size().getInfo()
        if count_fb and count_fb > 0:
            return s2_fb.median()

        return None
    except Exception as e:
        print(f"Sentinel Collection Error: {e}")
        return None


# ----------------------------
# 1) Water (hybrid)
# ----------------------------
def _water_core(roi):
    """Core water detection. Language-agnostic."""
    jrc_val = None
    try:
        jrc = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence")
        jrc_val = jrc.reduceRegion(ee.Reducer.max(), roi, 30).get("occurrence").getInfo()
        if jrc_val is not None:
            jrc_val = float(jrc_val)
    except Exception:
        jrc_val = None

    ndwi_max = 0.0
    try:
        s2_img = get_sentinel_collection_safe(roi, datetime.now() - timedelta(days=60), datetime.now())
        if s2_img:
            ndwi = s2_img.normalizedDifference(["B3", "B8"])
            ndwi_val = ndwi.reduceRegion(ee.Reducer.max(), roi, 20).get("nd").getInfo()
            if ndwi_val is not None:
                ndwi_max = float(ndwi_val)
    except Exception:
        ndwi_max = 0.0

    has_jrc = (jrc_val is not None and jrc_val > 50)
    has_ndwi = (ndwi_max > 0.1)

    if has_jrc:
        return {"has_water": True, "source": "jrc", "jrc_occurrence_max": jrc_val, "ndwi_max": ndwi_max}
    if has_ndwi:
        return {"has_water": True, "source": "ndwi", "jrc_occurrence_max": jrc_val, "ndwi_max": ndwi_max}
    return {"has_water": False, "source": "none", "jrc_occurrence_max": jrc_val, "ndwi_max": ndwi_max}


def get_water_hybrid(roi, lang="tr"):
    """Presentation wrapper (keeps old signature stable)."""
    try:
        core = _water_core(roi)
        return pres.present_water(core, lang=lang)
    except Exception as e:
        print(f"Water Error: {e}")
        return {"val": 0, "score": None, "label": "--", "desc": "Analysis error", "status": "Pasif"}


# ----------------------------
# 2) Climate (Open-Meteo live)
# ----------------------------
# ----------------------------
def _climate_core(lat, lon):
    """Core climate fetch from Open-Meteo. Language-agnostic."""
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m"
            "&timezone=auto"
        )
        headers = {"User-Agent": "BeeLocatePro/1.0"}
        r = requests.get(url, headers=headers, timeout=7)
        live = r.json().get("current", {}) if r.status_code == 200 else {}

        def _f(x):
            try:
                return float(x)
            except Exception:
                return None

        return {
            "status": "Aktif",
            "temperature_c": _f(live.get("temperature_2m")),
            "humidity_pct": _f(live.get("relative_humidity_2m")),
            "wind_kmh": _f(live.get("wind_speed_10m")),
            "wind_dir_deg": _f(live.get("wind_direction_10m")),
            "source": "Open-Meteo",
            "reason": None,
        }
    except Exception as e:
        print(f"Climate Error: {e}")
        return {
            "status": "Pasif",
            "temperature_c": None,
            "humidity_pct": None,
            "wind_kmh": None,
            "wind_dir_deg": None,
            "source": "Open-Meteo",
            "reason": "exception",
        }


def get_climate_smart(lat, lon, lang="tr"):
    """Presentation wrapper (keeps original behavior stable)."""
    try:
        core = _climate_core(lat, lon)
        return pres.present_climate(core, lang=lang)
    except Exception as e:
        print(f"Climate Present Error: {e}")
        return {"temp": {}, "wind": {}, "humidity": {}, "status": "Pasif"}


# ----------------------------
# 3) Flora (NDVI + landcover)
# ----------------------------
def _worldcover_distribution(roi):
    """Return land cover distribution (%) in the ROI using ESA WorldCover.

    Output example:
      {"Ağaçlık Alan": 62.1, "Çalılık": 18.4, "Tarım Arazisi": 7.3, ...}
    """
    land_types = {
        10: "Ağaçlık Alan",
        20: "Çalılık",
        30: "Çayır/Mera",
        40: "Tarım Arazisi",
        50: "Kentsel/Yapılaşma",
        60: "Çıplak/Seyrek",
        70: "Kar/Buz",
        80: "Su Kütlesi",
        90: "Otsu Sulak",
        95: "Mangrov",
        100: "Yosun/Liken",
    }

    try:
        wc = ee.ImageCollection("ESA/WorldCover/v200").first().select("Map")
        hist = wc.reduceRegion(
            reducer=ee.Reducer.frequencyHistogram(),
            geometry=roi,
            scale=10,
            maxPixels=1e8,
        ).get("Map").getInfo()

        if not isinstance(hist, dict) or not hist:
            return {}

        total = float(sum(hist.values())) if sum(hist.values()) else 0.0
        if total <= 0:
            return {}

        out = {}
        for k, v in hist.items():
            try:
                cls = int(float(k))
                cnt = float(v)
            except Exception:
                continue
            name = land_types.get(cls, f"Bilinmeyen({cls})")
            out[name] = round((cnt / total) * 100.0, 1)

        # Sort by percent desc
        out = dict(sorted(out.items(), key=lambda kv: kv[1], reverse=True))
        return out
    except Exception:
        return {}


def _flora_core(roi, month_arg, season_meta=None):
    """Core flora/forage signal from NDVI + landcover. Language-agnostic."""
    try:
        peak_m = None
        sos_m = None

        if month_arg == "season":
            if not isinstance(season_meta, dict):
                season_meta = get_ndvi_phenology(roi)
            peak_m = season_meta.get("peak_month") if season_meta else None
            sos_m = season_meta.get("sos_month") if season_meta else None
            if peak_m is None:
                peak_m = 4
            start_date, end_date, date_info = resolve_date_window(int(peak_m))
            window_type = "season"
        else:
            start_date, end_date, date_info = resolve_date_window(month_arg)
            window_type = "month"

        s2_img = get_sentinel_collection_safe(roi, start_date, end_date)
        if not s2_img:
            return {
                "status": "Pasif",
                "reason": "no_sentinel",
                "ndvi": None,
                "score": None,
                "class_code": None,
                "date_info": date_info,
                "window_type": window_type,
                "peak_month": peak_m,
                "sos_month": sos_m,
                "landcover_top": [],
            }

        ndvi = s2_img.normalizedDifference(["B8", "B4"])
        val = ndvi.reduceRegion(ee.Reducer.mean(), roi, 20).get("nd").getInfo()
        if val is None:
            val = 0.0
        val = float(val)

        final_score = clamp(int(val * 120), 0, 100)

        if val > 0.65:
            class_code = "very_dense"
        elif val > 0.45:
            class_code = "dense"
        elif val > 0.25:
            class_code = "moderate"
        elif val > 0.10:
            class_code = "sparse"
        else:
            class_code = "bare"

        dist = _worldcover_distribution(roi) or {}
        top = list(dist.items())[:3]
        landcover_top = [{"name": k, "pct": v} for k, v in top]

        return {
            "status": "Aktif",
            "reason": None,
            "ndvi": val,
            "score": final_score,
            "class_code": class_code,
            "date_info": date_info,
            "window_type": window_type,
            "peak_month": peak_m,
            "sos_month": sos_m,
            "landcover_top": landcover_top,
        }

    except Exception as e:
        print(f"Flora Core Error: {e}")
        return {
            "status": "Pasif",
            "reason": "exception",
            "ndvi": None,
            "score": None,
            "class_code": None,
            "date_info": "--",
            "window_type": "unknown",
            "peak_month": None,
            "sos_month": None,
            "landcover_top": [],
        }


def get_flora(roi, month_arg, season_meta=None, lang="tr"):
    """Presentation wrapper (keeps old signature stable)."""
    try:
        core = _flora_core(roi, month_arg, season_meta=season_meta)
        return pres.present_flora(core, lang=lang)
    except Exception as e:
        print(f"Flora Error: {e}")
        return {"val": 0, "score": None, "label": "--", "desc": "System error", "status": "Pasif"}


# ----------------------------
# 4) Precipitation (CHIRPS)
# ----------------------------
# ----------------------------
def precip_score_from_mm(mm):
    if mm is None:
        return None
    mm = float(mm)
    if 30 <= mm <= 90:
        return 100
    if 10 <= mm < 30 or 90 < mm <= 130:
        return 70
    if 0 <= mm < 10 or 130 < mm <= 180:
        return 40
    if mm > 180:
        return 20
    return 50


def _precip_core(roi, month_arg):
    """Core precipitation (CHIRPS). Language-agnostic."""
    try:
        start_date, end_date, date_info = resolve_date_window(month_arg)
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        chirps = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").filterDate(start_str, end_str).filterBounds(roi)
        count = chirps.size().getInfo()
        if not count or int(count) == 0:
            return {"status": "Pasif", "reason": "no_data", "mm": None, "score": None, "date_info": date_info, "source": "CHIRPS"}

        total = chirps.sum().rename("precip")
        mm = total.reduceRegion(ee.Reducer.mean(), roi, 5500).get("precip").getInfo()
        if mm is None:
            return {"status": "Pasif", "reason": "no_value", "mm": None, "score": None, "date_info": date_info, "source": "CHIRPS"}

        mm = float(mm)
        score = precip_score_from_mm(mm)
        return {"status": "Aktif", "reason": None, "mm": mm, "score": score, "date_info": date_info, "source": "CHIRPS"}

    except Exception as e:
        print(f"Precip Core Error: {e}")
        return {"status": "Pasif", "reason": "exception", "mm": None, "score": None, "date_info": "--", "source": "CHIRPS"}


def get_precipitation(roi, month_arg, lang="tr"):
    """Presentation wrapper."""
    try:
        core = _precip_core(roi, month_arg)
        return pres.present_precip(core, lang=lang)
    except Exception as e:
        print(f"Precip Present Error: {e}")
        return {"val": 0, "score": None, "label": "--", "desc": "Analysis error", "status": "Pasif"}


# ----------------------------
# 5) Topography (SRTM)
# ----------------------------
def elevation_score_m(elev_m):
    if elev_m is None:
        return None
    e = float(elev_m)
    if e <= 1200:
        return 100
    if e <= 1600:
        return 70
    if e <= 2000:
        return 40
    return 15


def slope_score_pct(pct):
    if pct is None:
        return None
    p = float(pct)
    if p <= 5:
        return 100
    if p <= 10:
        return 80
    if p <= 15:
        return 60
    if p <= 25:
        return 40
    if p <= 35:
        return 20
    return 0


def aspect_score_deg(deg):
    if deg is None:
        return None
    d = float(deg)
    diff = abs(d - 135.0)
    diff = min(diff, 360.0 - diff)
    s = 100.0 - (diff / 90.0) * 60.0
    return int(clamp(round(s), 0, 100))


def get_elevation_full(roi):
    """Return elevation stats for ROI.

    IMPORTANT:
      - Never coerce missing elevation to 0. A missing DEM sample is *unknown*, not sea level.
      - Use point sampling first (most robust), then fall back to ROI mean.

    Returns dict like:
      {"val": <float meters> or None, "min":..., "max":..., "mean":..., "src": "SRTM"/"NASADEM"/...}
    """
    try:
        # Prefer centroid sampling. ROI comes in as an ee.Geometry.
        try:
            pt = roi.centroid(1)
        except Exception:
            pt = roi

        def _sample(img, band='elevation', scale=30):
            try:
                # sample() is generally more reliable than reduceRegion over complex geoms.
                fc = img.select([band]).sample(region=pt, scale=scale, numPixels=1, geometries=False)
                f = fc.first()
                if f is None:
                    return None
                v = f.get(band)
                if v is None:
                    return None
                # getInfo() at the very end to keep server-side work in EE.
                vv = v.getInfo()
                if vv is None:
                    return None
                return float(vv)
            except Exception:
                return None

        # Try a small ladder of DEMs (coverage + resilience)
        candidates = [
            (ee.Image('USGS/SRTMGL1_003'), 'SRTM', 30),
            (ee.Image('NASA/NASADEM_HGT/001'), 'NASADEM', 30),
            (ee.Image('CGIAR/SRTM90_V4'), 'SRTM90', 90),
        ]

        elev = None
        src = None
        for img, name, sc in candidates:
            elev = _sample(img, 'elevation', sc)
            if elev is not None:
                src = name
                break

        # If point sampling failed, attempt ROI mean as last resort.
        mean = None
        mn = None
        mx = None
        if elev is None:
            try:
                img = ee.Image('USGS/SRTMGL1_003').select('elevation')
                stats = img.reduceRegion(
                    reducer=ee.Reducer.mean().combine(ee.Reducer.min(), True).combine(ee.Reducer.max(), True),
                    geometry=roi,
                    scale=90,
                    bestEffort=True,
                    maxPixels=1e9,
                )
                # These keys can vary slightly; be defensive.
                mean = stats.get('elevation_mean').getInfo() if stats.get('elevation_mean') is not None else None
                mn = stats.get('elevation_min').getInfo() if stats.get('elevation_min') is not None else None
                mx = stats.get('elevation_max').getInfo() if stats.get('elevation_max') is not None else None
                if mean is not None:
                    elev = float(mean)
                    src = 'SRTM_mean'
            except Exception:
                pass

        # Normalize outputs
        def _f(x):
            try:
                return float(x) if x is not None else None
            except Exception:
                return None

        out = {
            'val': _f(elev),
            'mean': _f(mean),
            'min': _f(mn),
            'max': _f(mx),
            'src': src or 'unknown',
        }
        return out
    except Exception:
        return {'val': None, 'mean': None, 'min': None, 'max': None, 'src': 'error'}

def urban_score_from_viirs(val):
    if val is None:
        return None
    v = float(val)
    if v < 1:
        return 100
    if v < 5:
        return 70
    if v < 15:
        return 40
    return 10


def get_urban(roi):
    try:
        viirs = (
            ee.ImageCollection("NOAA/VIIRS/DNB/MONTHLY_V1/VCMSLCFG")
            .filterDate("2023-01-01", "2023-12-31")
            .median()
            .select("avg_rad")
        )

        val = viirs.reduceRegion(ee.Reducer.mean(), roi, 500).get("avg_rad").getInfo()
        val = float(val) if val is not None else 0.0

        if val < 1:
            lbl = "Kırsal"
        elif val > 5:
            lbl = "Şehirleşmiş"
        else:
            lbl = "Banliyö"

        return {
            "val": lbl,
            "raw_val": val,
            "score": urban_score_from_viirs(val),
            "label": lbl,
            "desc": f"Işık Endeksi: {round(val, 1)}",
            "status": "Aktif",
        }
    except Exception as e:
        print(f"Urban Error: {e}")
        return {"val": "--", "raw_val": 0, "score": None, "label": "--", "desc": "--", "status": "Pasif"}


# ----------------------------
# 7) Settlement proximity (WorldCover Built-up)
# ----------------------------
def settlement_score_from_dist_km(dist_km):
    if dist_km is None:
        return None
    d = float(dist_km)
    if d <= 0.5:
        return 0
    if d <= 2:
        return 25
    if d <= 5:
        return 50
    if d <= 10:
        return 75
    return 90


def get_settlement(lon, lat):
    """
    En yakın yerleşimi kaba bir şekilde WorldCover built-up sınıfına göre arar.
    """
    try:
        pt = ee.Geometry.Point([lon, lat])

        wc = ee.ImageCollection("ESA/WorldCover/v200").first().select("Map")
        built = wc.eq(50)

        radii_m = [500, 2000, 5000, 10000]
        found_m = None

        for r in radii_m:
            v = built.reduceRegion(ee.Reducer.max(), pt.buffer(r), 10, maxPixels=1e8).get("Map").getInfo()
            if v is not None and float(v) > 0:
                found_m = r
                break

        if found_m is None:
            dist_km = 11.0
            label = ">10 km"
            desc = "10 km içinde yerleşim tespit edilmedi (WorldCover)"
        else:
            dist_km = round(found_m / 1000.0, 1)
            label = f"{dist_km} km"
            desc = f"En yakın yerleşim ~{label} içinde (WorldCover)"

        score = settlement_score_from_dist_km(dist_km)
        return {"val": dist_km, "score": score, "label": label, "desc": desc, "status": "Aktif"}

    except Exception as e:
        print(f"Settlement Error: {e}")
        return {"val": 0, "score": None, "label": "--", "desc": "Analiz Hatası", "status": "Pasif"}


# ----------------------------
# 8) Transport (placeholder)
# ----------------------------
def road_score_from_dist_m(dist_m):
    """Beşeri baskı yaklaşımı: yoldan uzaklaşmak daha iyi (1-9 -> 0-100)."""
    if dist_m is None:
        return None
    d = float(dist_m)
    if d <= 1000: rank = 1
    elif d <= 2000: rank = 2
    elif d <= 3000: rank = 3
    elif d <= 4000: rank = 4
    elif d <= 5000: rank = 5
    elif d <= 6000: rank = 6
    elif d <= 7000: rank = 7
    elif d <= 8000: rank = 8
    else: rank = 9
    return int(round((rank - 1) * 100 / 8))


def get_transport_overpass(lon, lat):
    """Internet dependent: find nearest OSM highway (meters)."""
    try:
        url = "https://overpass-api.de/api/interpreter"
        # First try within 5km, then widen if needed
        for radius in (5000, 15000):
            query = f"""
[out:json][timeout:8];
(
  way[highway](around:{radius},{lat},{lon});
);
out center 120;
"""
            r = requests.post(url, data=query.encode("utf-8"), headers={"User-Agent": "BeeLocatePro/1.0"}, timeout=10)
            if r.status_code != 200:
                continue
            js = r.json()
            elems = js.get("elements", [])
            if not elems:
                continue

            best = None
            for e in elems:
                c = e.get("center")
                if not c:
                    continue
                d = haversine_m(lat, lon, float(c["lat"]), float(c["lon"]))
                if best is None or d < best:
                    best = d

            if best is not None:
                label = f"{round(best/1000, 1)} km"
                return {
                    "val": best,
                    "value": best,
                    "score": road_score_from_dist_m(best),
                    "label": label,
                    "desc": "En yakın yol (OSM/Overpass)",
                    "status": "Aktif",
                }

        return {"val": 0, "score": None, "label": "--", "desc": "Yol bulunamadı (OSM)", "status": "Pasif"}

    except Exception as e:
        print(f"Transport Error: {e}")
        return {"val": 0, "score": None, "label": "--", "desc": "Yol analizi hatası", "status": "Pasif"}


def get_transport(lon, lat):
    # Real distance (OSM). Fallback to passive if network fails.
    return get_transport_overpass(lon, lat)


# ----------------------------
# 9) Microclimate (ERA5-Land)
# ----------------------------
def get_era5_flight_stats(roi):
    """Compute flight-suitable days in last 365 days using ERA5-Land Daily Agg.
    Criteria (simple, editable):
      - 10°C <= mean temp <= 36°C
      - mean wind speed <= 8 m/s
    Returns dict with temp_avg_c, wind_avg_ms, flight_days.
    """
    try:
        # --- Band detection / availability cache ---
        # Some environments/accounts may not have access to ERA5-Land in GEE,
        # or band names may differ. We detect bands once and reuse.
        global _ERA5_BANDNAMES_CACHE
        if "_ERA5_BANDNAMES_CACHE" not in globals() or _ERA5_BANDNAMES_CACHE is None:
            try:
                _ERA5_BANDNAMES_CACHE = (
                    ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR")
                    .first()
                    .bandNames()
                    .getInfo()
                )
            except Exception as e_meta:
                _ERA5_BANDNAMES_CACHE = []
                return {
                    "val": 0,
                    "score": None,
                    "label": "--",
                    "desc": f"ERA5 erişimi yok / metadata alınamadı: {str(e_meta)[:120]}",
                    "status": "Pasif",
                }

        end = datetime.now()
        start = end - timedelta(days=365)
        ic = (
            ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR")
            .filterDate(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        )

        def _pick_band(candidates, contains=None):
            # 1) direct hit
            for c in candidates:
                if c in _ERA5_BANDNAMES_CACHE:
                    return c
            # 2) substring fallback
            if contains:
                for n in _ERA5_BANDNAMES_CACHE:
                    if contains in n:
                        return n
            return None

        # ---- Temperature band (Kelvin -> C)
        # ERA5-Land daily bands vary by environment; commonly:
        # - temperature_2m (K)
        # - temperature_2m_mean (K)
        temp_band = _pick_band(
            ["temperature_2m_mean", "temperature_2m"],
            contains="temperature_2m",
        )
        if not temp_band:
            preview = ",".join(_ERA5_BANDNAMES_CACHE[:10])
            return {
                "val": 0,
                "score": None,
                "label": "--",
                "desc": f"ERA5 band yok: temperature_2m (mevcut: {preview})",
                "status": "Pasif",
            }

        t = ic.select(temp_band).mean().subtract(273.15).rename("t_c")

        # ---- Wind band(s)
        wind_band = _pick_band(
            ["wind_speed_10m_mean", "wind_speed_10m"],
            contains="wind_speed_10m",
        )

        u_band = _pick_band(
            ["u_component_of_wind_10m_mean", "u_component_of_wind_10m"],
            contains="u_component_of_wind_10m",
        )
        v_band = _pick_band(
            ["v_component_of_wind_10m_mean", "v_component_of_wind_10m"],
            contains="v_component_of_wind_10m",
        )

        if wind_band:
            wind = ic.select(wind_band).mean().rename("wind_ms")
        else:
            if not (u_band and v_band):
                preview = ",".join(_ERA5_BANDNAMES_CACHE[:10])
                return {
                    "val": 0,
                    "score": None,
                    "label": "--",
                    "desc": f"ERA5 wind band yok (wind_speed_10m veya u/v) (mevcut: {preview})",
                    "status": "Pasif",
                }
            u = ic.select(u_band).mean()
            v = ic.select(v_band).mean()
            wind = u.pow(2).add(v.pow(2)).sqrt().rename("wind_ms")

        temp_avg = t.reduceRegion(ee.Reducer.mean(), roi, 10000, maxPixels=1e8).get("t_c").getInfo()
        wind_avg = wind.reduceRegion(ee.Reducer.mean(), roi, 10000, maxPixels=1e8).get("wind_ms").getInfo()

        # flight days mask per day, then sum
        def per_day(img):
            tc = img.select(temp_band).subtract(273.15)
            if wind_band:
                ws = img.select(wind_band)
            else:
                ws = img.select(u_band).pow(2).add(img.select(v_band).pow(2)).sqrt()
            ok = tc.gte(10).And(tc.lte(36)).And(ws.lte(8)).rename("ok")
            return ok

        ok_sum = ic.map(per_day).sum().rename("ok_days")
        flight_days = ok_sum.reduceRegion(ee.Reducer.mean(), roi, 10000, maxPixels=1e8).get("ok_days").getInfo()

        if temp_avg is None or wind_avg is None or flight_days is None:
            return {"val": 0, "score": None, "label": "--", "desc": "ERA5 verisi alınamadı", "status": "Pasif"}

        temp_avg = float(temp_avg)
        wind_avg = float(wind_avg)
        flight_days = float(flight_days)

        # score: 0-100 based on flight_days (rough)
        # 0-120 bad, 120-200 mid, 200-260 good, 260+ very good
        if flight_days < 120:
            sc = 20
        elif flight_days < 200:
            sc = 50
        elif flight_days < 260:
            sc = 80
        else:
            sc = 95

        return {
            "val": int(round(flight_days)),
            "value": flight_days,
            "score": sc,
            "label": f"{int(round(flight_days))} gün/yıl",
            "desc": f"Uçuş uygun gün (ERA5-Land, 10–36°C & ≤8 m/s) | Ort: {temp_avg:.1f}°C, {wind_avg:.1f} m/s",
            "status": "Aktif",
            "temp_avg_c": temp_avg,
            "wind_avg_ms": wind_avg,
        }

    except Exception as e:
        print(f"ERA5 Error: {e}")
        return {"val": 0, "score": None, "label": "--", "desc": f"ERA5 analizi hatası: {str(e)[:120]}", "status": "Pasif"}


# ----------------------------
# Overall scoring (weighted overlay, v6 skeleton)
# ----------------------------
WEIGHTS = {
    "flora": 0.440,
    "water": 0.146,
    "aspect": 0.120,
    "elevation": 0.100,
    "precip": 0.076,
    "slope": 0.044,
    "roads": 0.039,
    "settlement": 0.033,
}


def metric_score(obj):
    if not isinstance(obj, dict):
        return None
    if obj.get("status") == "Pasif":
        return None
    s = obj.get("score", None)
    if s is None:
        return None
    try:
        return float(s)
    except Exception:
        return None


def weighted_score(score_map):
    num = 0.0
    den = 0.0
    for k, w in WEIGHTS.items():
        s = score_map.get(k, None)
        if s is None:
            continue
        num += float(s) * w
        den += w
    if den <= 0:
        return 0
    return clamp(int(round(num / den)), 0, 100)


def _season_window_from_peak(peak_month: int):
    peak = int(peak_month) if peak_month else 4
    start = max(1, peak - 1)
    end = min(12, peak + 1)
    return start, end, peak


def _season_label_tr(season_meta: dict):
    """Human label for recommended season, derived from phenology meta.
    Uses explicit start/end months when available; falls back to peak±1.
    Appends confidence when present.
    """
    if not isinstance(season_meta, dict):
        season_meta = {}

    def _to_int(x, default=None):
        try:
            return int(x)
        except Exception:
            return default

    peak = _to_int(season_meta.get("peak_month", 4), 4)
    start_m = _to_int(season_meta.get("start_month"), None)
    end_m = _to_int(season_meta.get("end_month"), None)
    if start_m is None or end_m is None:
        start_m, end_m, peak = _season_window_from_peak(peak)

    label = f"{MONTH_NAMES_TR.get(start_m,'--')}–{MONTH_NAMES_TR.get(end_m,'--')} (Zirve: {MONTH_NAMES_TR.get(peak,'--')})"

    sos = _to_int(season_meta.get("sos_month", None), None)
    if sos is not None:
        label += f" | Yeşerme: {MONTH_NAMES_TR.get(sos,'--')}"

    conf = str(season_meta.get("confidence", "") or "").strip()
    if conf:
        # Keep short in UI
        label += f" | Güven: {conf}"

    return label


def build_sys_msg_en(flora, water, precip, settlement, urban, transport, flight, water_managed=False, month_req="season", season_meta=None):
    """English variant of the pre-assessment message."""
    lines = []
    season_meta = season_meta or {}
    season_label = _season_label_en(season_meta) if season_meta else None

    f_score = flora.get("score")
    w_score = water.get("score")
    u_lbl = urban.get("label", "--")
    road_lbl = transport.get("label", "--")
    set_lbl = settlement.get("label", "--")
    p_lbl = precip.get("label", "--")
    flight_lbl = flight.get("label", "--")

    # 0) Period context
    try:
        if month_req == "season":
            lines.append(f"Score computed for the recommended window: {season_label}." if season_label else
                         "Score computed for the recommended window (phenology).")
        elif month_req == "current":
            lines.append(f"Score computed for the last 30 days (current conditions). Recommended window: {season_label}." if season_label else
                         "Score computed for the last 30 days (current conditions).")
        else:
            try:
                m = int(month_req)
                m_lbl = MONTH_NAMES_EN.get(m, str(m))
                lines.append(f"Score computed for the {m_lbl} simulation window. Recommended window: {season_label}." if season_label else
                             f"Score computed for the {m_lbl} simulation window.")
            except Exception:
                lines.append(f"Score computed for the selected period. Recommended window: {season_label}." if season_label else
                             "Score computed for the selected period.")
    except Exception:
        pass

    # 1) Water headline
    if water.get("status") == "Aktif" and (w_score or 0) < 50 and not water_managed:
        lines.append("Key limiter: natural water availability is weak. Without water, colonies often shift from productivity to survival.")
    elif water.get("status") == "Aktif" and (w_score or 0) < 50 and water_managed:
        lines.append("Natural water is weak, but 'managed water' is ON, so this is not an automatic rejection. Cost and operational risk still increase.")
    else:
        lines.append("Overall, the layers do not severely contradict each other.")

    # 2) Flora
    if flora.get("status") == "Aktif" and f_score is not None:
        try:
            fs = float(f_score)
        except Exception:
            fs = None
        if fs is None:
            lines.append("Vegetation signal is unclear; interpret the result conservatively.")
        elif fs < 30:
            lines.append("Vegetation looks weak; nectar flow may be short and fragile.")
        elif fs < 60:
            lines.append("Vegetation is moderate; it can work in the right window, but sustained flow is not guaranteed.")
        else:
            lines.append("Vegetation looks strong; in the right window this area can support good nectar/pollen potential.")
    else:
        lines.append("Vegetation data is not reliable; interpret the result conservatively.")

    # 3) Microclimate
    if flight.get("status") == "Aktif" and flight_lbl != "--":
        lines.append(f"Microclimate proxy: flight-suitable days ≈ {flight_lbl}. Low values slow colony development.")

    # 4) Human pressure + precip
    lines.append(f"Settlement: {set_lbl} | Human pressure: {u_lbl} | Road: {road_lbl}.")
    lines.append(f"Precipitation: {p_lbl}.")

    return " ".join(lines)


def build_sys_msg(flora, water, precip, settlement, urban, transport, flight, water_managed=False, month_req="season", season_meta=None, lang="tr"):
    """Short, human pre-assessment. Explains *why* and anchors to the recommended season.
    lang: 'tr' or 'en' (best-effort)
    """
    if str(lang).lower().startswith("en"):
        return build_sys_msg_en(flora, water, precip, settlement, urban, transport, flight,
                                water_managed=water_managed, month_req=month_req, season_meta=season_meta)

    lines = []

    season_meta = season_meta or {}
    season_label = _season_label_tr(season_meta) if season_meta else None

    f_score = flora.get("score")
    w_score = water.get("score")
    u_lbl = urban.get("label", "--")
    road_lbl = transport.get("label", "--")
    set_lbl = settlement.get("label", "--")
    p_lbl = precip.get("label", "--")
    flight_lbl = flight.get("label", "--")

    # 0) Period context (critical for trust)
    try:
        if month_req == "season":
            if season_label:
                lines.append(f"Bu skor önerilen sezona göre hesaplandı: {season_label}.")
            else:
                lines.append("Bu skor önerilen sezona göre hesaplandı (fenoloji).")
        elif month_req == "current":
            if season_label:
                lines.append(f"Bu skor son 30 güne göre hesaplandı (anlık durum). Önerilen sezon: {season_label}.")
            else:
                lines.append("Bu skor son 30 güne göre hesaplandı (anlık durum).")
        else:
            try:
                m = int(month_req)
                m_lbl = MONTH_NAMES_TR.get(m, str(m))
                if season_label:
                    lines.append(f"Bu skor {m_lbl} dönemi simülasyonuna göre hesaplandı. Önerilen sezon: {season_label}.")
                else:
                    lines.append(f"Bu skor {m_lbl} dönemi simülasyonuna göre hesaplandı.")
            except Exception:
                if season_label:
                    lines.append(f"Bu skor seçilen döneme göre hesaplandı. Önerilen sezon: {season_label}.")
                else:
                    lines.append("Bu skor seçilen döneme göre hesaplandı.")
    except Exception:
        pass

    # 1) Quick headline
    if water.get("status") == "Aktif" and (w_score or 0) < 50 and not water_managed:
        lines.append("Bu alanın can sıkıcı tarafı: doğal su kaynağı zayıf. Su yoksa arı genelde verim değil hayatta kalma moduna geçer.")
    elif water.get("status") == "Aktif" and (w_score or 0) < 50 and water_managed:
        lines.append("Doğal su zayıf; ama 'yapay su desteği' seçildiği için bu durum elenme sebebi değil. Yine de maliyet ve risk artar.")
    else:
        lines.append("Genel tablo fena değil; temel katmanlar birbirini tamamen baltalamıyor.")

    # 2) Flora comment
    if flora.get("status") == "Aktif" and f_score is not None:
        if float(f_score) < 30:
            lines.append("Flora zayıf görünüyor; bal akımı kısa ve kırılgan olabilir.")
        elif float(f_score) < 60:
            lines.append("Flora orta seviyede; doğru sezonda verim alınabilir ama sürekli akım garanti değil.")
        else:
            lines.append("Flora iyi görünüyor; bu alan doğru sezonda güçlü nektar/polen potansiyeli taşıyabilir.")
    else:
        lines.append("Flora verisi net değil; bu sonuç daha temkinli okunmalı.")

    # 3) Microclimate (flight days)
    if flight.get("status") == "Aktif" and flight_lbl != "--":
        lines.append(f"Mikroiklim için kabaca ölçüt: uçuşa uygun gün sayısı {flight_lbl}. Bu sayı düşükse koloni gelişimi yavaşlar.")

    # 4) Human pressure
    lines.append(f"Yerleşim: {set_lbl} | Şehirleşme: {u_lbl} | Yol: {road_lbl}.")
    lines.append(f"Yağış: {p_lbl}.")

    return " ".join(lines)


def build_sys_msg_en(flora, water, precip, settlement, urban, transport, flight, water_managed=False, month_req="season", season_meta=None):
    """English version of the pre-assessment."""
    lines = []
    season_meta = season_meta or {}
    season_label = _season_label_en(season_meta) if season_meta else None

    f_score = flora.get("score")
    w_score = water.get("score")
    u_lbl = urban.get("label", "--")
    road_lbl = transport.get("label", "--")
    set_lbl = settlement.get("label", "--")
    p_lbl = precip.get("label", "--")
    flight_lbl = flight.get("label", "--")

    # Period context
    try:
        if month_req == "season":
            lines.append(f"This score is calculated for the recommended season: {season_label}." if season_label
                         else "This score is calculated for the recommended season (phenology).")
        elif month_req == "current":
            lines.append(f"This score is calculated for the last 30 days (current conditions). Recommended season: {season_label}." if season_label
                         else "This score is calculated for the last 30 days (current conditions).")
        else:
            try:
                m = int(month_req)
                m_lbl = MONTH_NAMES_EN.get(m, str(m))
                lines.append(f"This score is calculated for a {m_lbl} simulation. Recommended season: {season_label}." if season_label
                             else f"This score is calculated for a {m_lbl} simulation.")
            except Exception:
                lines.append(f"This score is calculated for the selected period. Recommended season: {season_label}." if season_label
                             else "This score is calculated for the selected period.")
    except Exception:
        pass

    # Water headline
    if water.get("status") == "Aktif" and (w_score or 0) < 50 and not water_managed:
        lines.append("Key limitation: natural water availability is weak. Without water, colonies often shift from production to survival mode.")
    elif water.get("status") == "Aktif" and (w_score or 0) < 50 and water_managed:
        lines.append("Natural water is weak, but 'managed water' is enabled—so this is not an automatic reject. Operational cost/risk increases.")
    else:
        lines.append("Overall, the main layers do not conflict strongly.")

    # Flora
    if flora.get("status") == "Aktif" and f_score is not None:
        if float(f_score) < 30:
            lines.append("Vegetation signal looks weak; nectar flow may be short and fragile.")
        elif float(f_score) < 60:
            lines.append("Vegetation is moderate; good yields can be possible in the right season, but continuous flow is not guaranteed.")
        else:
            lines.append("Vegetation looks strong; this area may support good nectar/pollen potential in the right season.")
    else:
        lines.append("Vegetation data is uncertain; interpret this result more cautiously.")

    # Microclimate
    if flight.get("status") == "Aktif" and flight_lbl != "--":
        lines.append(f"Microclimate proxy: flight-suitable days {flight_lbl}. Low values slow colony development.")

    # Human pressure + precip
    lines.append(f"Settlement: {set_lbl} | Urban pressure: {u_lbl} | Road: {road_lbl}.")
    lines.append(f"Precipitation: {p_lbl}.")

    return " ".join(lines)


def make_flight_suitability(flight_obj, lang="tr"):
    """Derive a compact suitability class from flight window (days/year).

    - flight_obj: dict from get_era5_flight_stats
    Returns a schema-ready dict with a label + score.
    """
    if not isinstance(flight_obj, dict) or flight_obj.get("status") != "Aktif":
        return {"val": 0, "score": None, "label": "--", "desc": "ERA5 verisi yok", "status": "Pasif"}

    days = flight_obj.get("value", flight_obj.get("val", None))
    try:
        days = float(days)
    except Exception:
        return {"val": 0, "score": None, "label": "--", "desc": "ERA5 verisi okunamadı", "status": "Pasif"}

    # Same bins used for flight score in get_era5_flight_stats
    is_en = str(lang).lower().startswith("en")
    if days < 120:
        cls, sc = ("Weak" if is_en else "Zayıf"), 20
    elif days < 200:
        cls, sc = ("Medium" if is_en else "Orta"), 50
    elif days < 260:
        cls, sc = ("Good" if is_en else "İyi"), 80
    else:
        cls, sc = ("Very Good" if is_en else "Çok İyi"), 95

    return {
        "val": int(round(days)),
        "value": days,
        "score": sc,
        "label": f"{cls} ({sc}/100)",
        "desc": (f"Flight window: {int(round(days))} days/year" if is_en else f"Uçuş penceresi: {int(round(days))} gün/yıl"),
        "status": "Aktif",
    }



# ----------------------------
# Report rendering (A4 HTML template)
# ----------------------------
def _season_label_en(season_meta: dict) -> str:
    """Compact season label in English, derived from phenology meta.
    Uses explicit start/end months when available; falls back to peak±1.
    Appends confidence when present.
    """
    if not isinstance(season_meta, dict):
        season_meta = {}

    def _to_int(x, default=None):
        try:
            return int(x)
        except Exception:
            return default

    peak = _to_int(season_meta.get("peak_month", 4), 4)
    start_m = _to_int(season_meta.get("start_month"), None)
    end_m = _to_int(season_meta.get("end_month"), None)
    if start_m is None or end_m is None:
        start_m = max(1, peak - 1)
        end_m = min(12, peak + 1)

    parts = [f"{MONTH_NAMES_EN[start_m]}–{MONTH_NAMES_EN[end_m]}", f"(Peak: {MONTH_NAMES_EN[peak]})"]

    sos = _to_int(season_meta.get("sos_month"), None)
    if sos:
        parts.append(f"Greening onset: {MONTH_NAMES_EN[sos]}")

    conf = str(season_meta.get("confidence", "") or "").strip()
    if conf:
        parts.append(f"Confidence: {conf}")

    return " ".join(parts)


def _default_contextual_anchor(score: int) -> str:
    # MVP anchors (calibrated later with real regional sampling)
    if score >= 85:
        return "Top ~15% locally"
    if score >= 70:
        return "Above local average"
    if score >= 55:
        return "Around local average"
    if score >= 40:
        return "Below local average"
    return "Low suitability in local context"


def build_report_context(payload: dict) -> dict:
    """Build a safe context dict for templates/report.html from an analyze payload."""
    payload = payload or {}
    details = payload.get("details") or {}
    # Variant compatibility: across v6.5 branches some keys were renamed.
    # Normalize them so the report doesn't render empty values.
    if isinstance(details, dict):
        if "vegetation" in details and "flora" not in details:
            details["flora"] = details.get("vegetation")
        if "road_distance" in details and "transport" not in details:
            details["transport"] = details.get("road_distance")
        if "nearest_settlement" in details and "settlement" not in details:
            details["settlement"] = details.get("nearest_settlement")
    season_meta = (details.get("season_meta") or {})

    # Core meta
    try:
        score = int(round(float(payload.get("score", 0))))
    except Exception:
        score = 0

    lat = payload.get("lat") or payload.get("latitude") or "--"
    lon = payload.get("lon") or payload.get("lng") or payload.get("longitude") or "--"
    try:
        lat = f"{float(lat):.5f}"
    except Exception:
        pass
    try:
        lon = f"{float(lon):.5f}"
    except Exception:
        pass

    # Analysis window label (English)
    analysis_window = payload.get("analysis_window")
    if not analysis_window:
        if season_meta and season_meta.get("peak_month"):
            analysis_window = f"Recommended season (phenology): {_season_label_en(season_meta)}"
        else:
            analysis_window = "Current conditions (last ~30 days) / or user-selected month"

    # Contextual anchor
    contextual_anchor = payload.get("contextual_anchor") or _default_contextual_anchor(score)
    contextual_reference = payload.get(
        "contextual_reference",
        "Compared to sampled areas within a locally relevant radius (10 km)."
    )

    regional_avg = payload.get("regional_avg")
    try:
        regional_avg = int(round(float(regional_avg)))
    except Exception:
        regional_avg = 55

    bar_pct = max(0, min(100, score))

    # Extract KPIs (compact)
    def _k(label, value):
        return {"k": label, "v": value}

    flora = details.get("flora") or {}
    water = details.get("water") or {}
    precip = details.get("precip") or {}
    flight = details.get("flight") or {}
    topo = details.get("topography") or {}
    # Topography subfields (keep names explicit to avoid NameError in PDF)
    aspect = topo.get("aspect") or {}
    slope = topo.get("slope") or {}
    elevation = topo.get("elevation") or {}

    # Human/operation layers (naming varies across payload versions)
    transport = details.get("transport") or details.get("road") or details.get("roads") or {}
    urban = details.get("urban") or details.get("urbanization") or details.get("human") or {}
    settlement = details.get("settlement") or details.get("settlements") or details.get("residential") or {}
    climate = details.get("climate") or {}
    temp = climate.get("temp") or {}
    wind = climate.get("wind") or {}
    humidity = climate.get("humidity") or {}

    def _disp(d, fallback="--"):
        """Prefer label if meaningful, otherwise val. Avoid default \"--\" label from ensure_schema()."""
        if not isinstance(d, dict):
            return fallback
        lab = (d.get("label") or "").strip()
        if lab and lab != "--":
            return lab
        val = d.get("val")
        if val is None:
            return fallback
        sval = str(val).strip()
        return sval if sval else fallback


    def _card(title, value, unit="", source="", why="", how_used="", extra=None):
        c = {
            "label": title,
            "value": value if value is not None else "--",
            "unit": unit or "",
            "source": source or "--",
            "why": why or "--",
            "how": how_used or "--",
            "extra": extra or [],
        }
        return c

    # Cards (ordered, 14 items) — designed to match UI + PDF needs
    # Note: Many raw fields are already included in `details`. We keep the PDF robust even if some fields are missing.
    cards = []

    # 1) Vegetation (NDVI + land cover)
    landcover = (flora.get("landcover") or flora.get("land_cover") or {})
    lc_items = []
    if isinstance(landcover, dict):
        # take top few classes
        for k, v in sorted(landcover.items(), key=lambda x: -float(x[1]) if str(x[1]).replace('.','',1).isdigit() else 0)[:5]:
            try:
                lc_items.append(f"{k}: {float(v):.0f}%")
            except Exception:
                lc_items.append(f"{k}: {v}")
    cards.append(_card(
        "Vegetation (NDVI)",
        flora.get("val", "--"),
        "",
        "Sentinel-2 / NDVI + land-cover fractions",
        "Vegetation is a fast proxy for potential forage (nectar/pollen).",
        "BeeLocate combines NDVI with land-cover fractions to reduce false "
        "positives (""green but not useful"" scenarios).",
        extra=lc_items + ([f"SOS/Peak: {season_meta.get('sos','--')} / {season_meta.get('peak','--')}"] if season_meta else [])
    ))

    # 2) Water
    cards.append(_card(
        "Water Source",
        water.get("label", water.get("val", "--")),
        "",
        "JRC Global Surface Water",
        "Water is critical for thermoregulation and colony health.",
        "If surface water is absent, score is penalized unless managed water support is assumed."
    ))

    # 3) Wind
    cards.append(_card(
        "Wind",
        _disp(wind),
        "",
        "ERA5-Land / operational weather",
        "Persistent strong winds reduce flight activity and increase stress.",
        "Wind conditions affect flight feasibility; higher risk suppresses the overall score."
    ))

    # 4) Flight window (days)
    cards.append(_card(
        "Flight Window",
        flight.get("val", flight.get("label", "--")),
        "days/year" if str(flight.get("val","")).isdigit() else "",
        "ERA5-Land (10–36°C & ≤8 m/s threshold)",
        "More flyable days generally means higher and more stable productivity.",
        "This is a backbone indicator: low flight days can negate good vegetation signals.",
        extra=[flight.get("desc")] if flight.get("desc") and flight.get("desc") != "--" else []
    ))

    # 5) Aspect
    cards.append(_card(
        "Aspect (Slope Orientation)",
        aspect.get("label", aspect.get("val", "--")),
        "",
        "SRTM-derived (DEM)",
        "Orientation affects morning sun exposure and moisture/cold stress.",
        "BeeLocate uses aspect as a micro-climate risk modifier."
    ))

    # 6) Humidity
    cards.append(_card(
        "Humidity",
        _disp(humidity),
        "",
        "Open-Meteo / operational weather",
        "Very high humidity may raise disease risk; very low humidity can signal drought stress.",
        "Interpreted alongside temperature, precipitation and vegetation (not in isolation)."
    ))

    # 7) Slope
    cards.append(_card(
        "Slope",
        topo.get("slope", {}).get("val", slope.get("val", "--")),
        "%",
        "NASA SRTM",
        "Steeper terrain reduces accessibility and increases operational cost.",
        "Higher slope lowers suitability; gentle terrain is operationally safer."
    ))

    # 8) Road distance
    cards.append(_card(
        "Road Distance",
        transport.get("label", transport.get("val", "--")),
        "",
        "OpenStreetMap / Overpass",
        "Too close can mean pollution/dust; too far increases logistics cost. Buffer matters.",
        "BeeLocate penalizes both extremes using an 'optimal distance' concept."
    ))

    # 9) Urbanization (night lights / human impact)
    cards.append(_card(
        "Urban Pressure",
        urban.get("label", urban.get("val", "--")),
        "",
        "Night lights / human-activity proxy",
        "Higher human pressure can imply pesticide, pollution, and conflict risk.",
        "Rural context is typically safer; more pressure reduces the score."
    ))

    # 10) Settlement distance
    cards.append(_card(
        "Settlement Distance",
        settlement.get("label", settlement.get("val", "--")),
        "",
        "ESA WorldCover (derived settlement layer)",
        "Being too close to settlements increases conflict, pesticide and disturbance risks.",
        "Closer settlement proximity reduces the score; a safe buffer is preferred."
    ))

    # 11) Flight suitability (score proxy)
    fs = flight.get("score")
    fs_label = f"{int(round(fs))}/100" if isinstance(fs,(int,float)) else "--"
    cards.append(_card(
        "Flight Suitability",
        fs_label,
        "",
        "Derived from ERA5-Land thresholds",
        "Summarizes how climate constraints affect flight performance.",
        "A backbone metric: if flight conditions are poor, other positives have limited impact."
    ))

    # 12) Precip
    cards.append(_card(
        "Precipitation (period)",
        precip.get("val", "--"),
        "mm",
        "CHIRPS (monthly)",
        "Rain supports forage growth, but excessive precipitation can disrupt flight and nectar flow.",
        "Interpreted together with humidity, temperature and vegetation (not a standalone verdict)."
    ))

    # 13) Elevation
    cards.append(_card(
        "Elevation",
        topo.get("elevation", {}).get("val", elevation.get("val", "--")),
        "m",
        "NASA SRTM",
        "Elevation shifts temperature and phenology; the same score can mean different things across altitudes.",
        "BeeLocate interprets elevation together with season recommendation and flight window."
    ))

    # 14) Temperature
    cards.append(_card(
        "Temperature",
        _disp(temp),
        "",
        "Open-Meteo (baseline: ERA5)",
        "Temperature controls key thresholds for flight and colony development.",
        "Temperature directly affects the flight window; colder tendencies suppress the score.",
        extra=[temp.get("desc")] if temp.get("desc") and temp.get("desc") != "--" else []
    ))

    # Backward-compat: keep `kpis` as the headline grid (now full 14 items)
    kpis = cards

    # Why points
    why_points = payload.get("why_points")
    if not why_points:
        why_points = []
        try:
            nd = float(flora.get("val"))
            if nd >= 0.5:
                why_points.append("Strong vegetation signal (high NDVI) suggests good forage potential in the selected window.")
            elif nd >= 0.3:
                why_points.append("Moderate vegetation signal (NDVI) suggests mixed forage potential; field validation is recommended.")
            else:
                why_points.append("Low vegetation signal (NDVI) in the selected window is a key limiter; season selection matters.")
        except Exception:
            why_points.append("Vegetation signal (NDVI) influences forage availability; interpret seasonally.")

        wlab = (water.get("label") or "").lower()
        if "yok" in wlab or "no" in wlab:
            why_points.append("Limited surface-water signal in the area; consider water support or a nearby alternative.")
        else:
            why_points.append("Water availability appears present/nearby; this supports colony activity during warm periods.")

        try:
            sl = float((topo.get("slope", {}) or {}).get("value") or topo.get("slope", {}).get("val"))
            if sl >= 25:
                why_points.append("Steep terrain may reduce practical accessibility and micro-site options.")
        except Exception:
            pass

        why_points = why_points[:5]

    # General comment
    general_comment = payload.get("general_comment")
    if not general_comment:
        general_comment = (
            "This location shows a mix of ecological potential and operational constraints. "
            "Use the score as a decision-support summary: the best outcomes usually happen when "
            "vegetation continuity aligns with a suitable season window, and practical factors "
            "(access, slope, and local pressure) are acceptable."
        )
        if season_meta and season_meta.get("peak_month"):
            general_comment += f" Recommended season for this area: {_season_label_en(season_meta)}."


    # If AI generated premium sections, prefer them for "Why this score" and the general interpretation.
    ai = payload.get("ai") or {}
    if isinstance(ai, dict):
        if ai.get("why_this_score"):
            why_points = ai.get("why_this_score") or why_points
        if ai.get("general_interpretation"):
            general_comment = ai.get("general_interpretation") or general_comment

    methodology_note = payload.get(
        "methodology_note",
        "NDVI is a vegetation density indicator (not plant species). Climate metrics are long-term reanalysis summaries. "
        "BeeLocate PRO is a decision-support tool and does not guarantee outcomes."
    )

    legal_disclaimer = payload.get(
        "legal_disclaimer",
        "This report is generated from satellite and spatial analysis data and is provided for decision support only. "
        "Results may vary with season, management practices, and on-site conditions; no yield, income, or colony-health guarantee is implied."
    )

    report_date = payload.get("report_date") or datetime.now().strftime("%Y-%m-%d")

    return {
        "report_title": "BeeLocate PRO – Location Suitability Report",
        "report_date": report_date,
        "lat": lat,
        "lon": lon,
        "analysis_window": analysis_window,
        "score": score,
        "contextual_anchor": contextual_anchor,
        "contextual_reference": contextual_reference,
        "why_points": why_points,
        "general_comment": general_comment,
        "kpis": kpis,
        "regional_avg": regional_avg,
        "bar_pct": bar_pct,
        "methodology_note": methodology_note,
        "legal_disclaimer": legal_disclaimer,
        "ai": (payload.get("ai") or {}),
    }


def _sample_report_context() -> dict:
    """A safe preview context (no GEE calls)."""
    sample_payload = {
        "score": 78,
        "lat": 40.0360,
        "lon": 30.6547,
        "analysis_window": "Recommended season (phenology): March–May (Peak: April)",
        "contextual_anchor": "Top ~15% locally",
        "contextual_reference": "Compared to sampled areas within a locally relevant radius (10 km).",
        "regional_avg": 56,
        "why_points": [
            "Vegetation signal suggests good forage potential in the recommended window.",
            "Water availability appears present/nearby, supporting colony activity.",
            "Slope is moderate; accessibility is generally feasible with careful micro-site selection.",
        ],
        "general_comment": (
            "In plain terms: this site looks promising during the seasonal window when vegetation peaks. "
            "If you confirm water access and choose micro-locations with manageable slope, it can support productive placement. "
            "Outside the recommended season, the same area may score lower due to natural vegetation dynamics."
        ),
    }
    return build_report_context(sample_payload)

@app.route('/health/gee')
def health_gee():
    # Quick, cheap check: can we talk to EE?
    ok = bool(GEE_OK)
    err = GEE_ERR
    # Try a tiny call if we think we're ok (catches transient failures)
    if ok:
        try:
            import ee
            ee.Number(1).getInfo()
            ok = True
            err = ''
        except Exception as e:
            ok = False
            err = str(e)
    return jsonify({'ok': ok, 'error': err}), (200 if ok else 503)

@app.route("/")
def index():
    return render_template("index.html", mapbox_token=os.environ.get("MAPBOX_TOKEN",""))


@app.route("/landing")
def landing():
    return render_template("landing.html")


@app.route("/my")
def my_reports_page():
    # Device-scoped (MVP). True accounts/login will come later.
    return render_template("my_reports.html")


@app.route("/api/my-reports")
def api_my_reports():
    uid = _uid_from_request(None)
    if not uid:
        return jsonify({"ok": True, "uid": "", "reports": [], "items": []})

    rows = []
    with _db() as con:
        rows = con.execute(
            """
            SELECT r.rid, r.created_at, r.expires_at, r.payload_json,
                   p.paid_at
            FROM reports r
            LEFT JOIN payments p ON p.rid = r.rid
            WHERE r.uid = ?
            ORDER BY r.created_at DESC
            LIMIT 50
            """,
            (uid,)
        ).fetchall()

    out = []
    for row in rows:
        try:
            payload = _json.loads(row["payload_json"] or "{}")
        except Exception:
            payload = {}
        out.append({
            "rid": row["rid"],
            "created_at": float(row["created_at"] or 0),
            "expires_at": float(row["expires_at"] or 0),
            "paid": True if row["paid_at"] else False,
            "score": payload.get("score"),
            "lat": payload.get("lat"),
            "lon": payload.get("lon") or payload.get("lng"),
            "label": payload.get("label") or payload.get("context_anchor") or "",
        })

    # Return both keys for compatibility across UI iterations.
    return jsonify({"ok": True, "uid": uid, "reports": out, "items": out})


def _sanitize_uid(uid: str) -> str:
    uid = (uid or "").strip()
    # Keep it small and URL-safe-ish (this is not a security token, just a browser/device key).
    if len(uid) > 80:
        uid = uid[:80]
    return uid


def _uid_from_request(d: dict | None = None) -> str:
    """Get the anonymous device UID.

    MVP: this enables "My Reports" without a login system.
    It is NOT used for authorization (paid checks still gate PDF/report).
    """
    uid = ""
    try:
        uid = (request.cookies.get('blp_uid') or '').strip()
    except Exception:
        uid = ""
    if not uid:
        try:
            uid = (request.headers.get('X-BLP-UID') or '').strip()
        except Exception:
            uid = ""
    if not uid and isinstance(d, dict):
        uid = str(d.get('uid') or '').strip()
    return _sanitize_uid(uid)


def _rate_limit_allow(uid: str, ip: str) -> tuple[bool, str]:
    """Very small SQLite-based rate limit (MVP).

    Goal: prevent obvious abuse (refresh spam / bot looping) without adding new dependencies.
    Not a security boundary; just a production safety valve.
    """
    # Configurable caps (conservative defaults)
    ip_per_min = int(os.environ.get("RL_IP_PER_MIN", "30") or 30)
    uid_per_min = int(os.environ.get("RL_UID_PER_MIN", "15") or 15)
    ip_per_day = int(os.environ.get("RL_IP_PER_DAY", "300") or 300)
    uid_per_day = int(os.environ.get("RL_UID_PER_DAY", "150") or 150)

    now = int(time.time())
    minute_bucket = now // 60
    day_bucket = now // 86400

    checks = []
    if ip:
        checks.append((f"ip:m:{ip}:{minute_bucket}", ip_per_min))
        checks.append((f"ip:d:{ip}:{day_bucket}", ip_per_day))
    if uid:
        checks.append((f"uid:m:{uid}:{minute_bucket}", uid_per_min))
        checks.append((f"uid:d:{uid}:{day_bucket}", uid_per_day))

    if not checks:
        return True, ""

    try:
        with _db() as con:
            for k, cap in checks:
                row = con.execute("SELECT count FROM rate_limits WHERE k=?", (k,)).fetchone()
                if row is None:
                    con.execute(
                        "INSERT OR REPLACE INTO rate_limits (k, window_start, count) VALUES (?, ?, ?)",
                        (k, now, 1)
                    )
                else:
                    c = int(row[0] or 0) + 1
                    if c > cap:
                        return False, "Rate limit exceeded. Please try again shortly."
                    con.execute("UPDATE rate_limits SET count=? WHERE k=?", (c, k))
            con.commit()
        return True, ""
    except Exception:
        # Fail-open: never break the product because of rate-limit storage issues.
        return True, ""


def _free_quota_allow(ip: str, uid: str) -> tuple[bool, int, int]:
    """Daily free analyses quota.

    Returns: (allowed, used, cap)

    MVP policy:
    - Default cap is 5/day.
    - Keyed by IP first (prevents the 'switch browser to reset' trick).
    - UID is used as an additional key when present (helps NAT/shared IP cases a bit).
    - If the IP has an active paid window, quota is bypassed.

    This is not a perfect anti-fraud system (VPNs exist). It's a pragmatic guardrail.
    """
    cap = int(os.environ.get("FREE_DAILY_CAP", "5") or 5)
    if cap <= 0:
        return True, 0, cap
    if _ip_has_active_paid(ip):
        return True, 0, cap

    day_bucket = int(time.time()) // 86400
    keys = []
    if ip:
        keys.append(f"ip:{ip}:{day_bucket}")
    if uid:
        keys.append(f"uid:{uid}:{day_bucket}")
    if not keys:
        # Fail-open if we can't identify the caller at all.
        return True, 0, cap

    used = 0
    try:
        with _db() as con:
            # We enforce the strictest (max used) among the keys.
            for k in keys:
                row = con.execute("SELECT count FROM free_quota WHERE k=?", (k,)).fetchone()
                if row is not None:
                    used = max(used, int(row[0] or 0))

            if used >= cap:
                return False, used, cap

            # Increment all keys for this request.
            for k in keys:
                row = con.execute("SELECT count FROM free_quota WHERE k=?", (k,)).fetchone()
                if row is None:
                    con.execute(
                        "INSERT OR REPLACE INTO free_quota (k, day_bucket, count) VALUES (?, ?, ?)",
                        (k, day_bucket, 1),
                    )
                else:
                    con.execute("UPDATE free_quota SET count=? WHERE k=?", (int(row[0] or 0) + 1, k))
            con.commit()
            return True, used + 1, cap
    except Exception:
        # Never break core product due to quota storage issues.
        return True, 0, cap



def get_demo_payload() -> dict:
    return {
        "score": 79,
        "context_anchor": "Above local average",
        "context_reference": "Compared to sampled areas within a locally relevant radius (10 km).",
        "analysis_window": "Recommended season (phenology)",
        "generated_at": time.strftime('%Y-%m-%d %H:%M:%S'),
        "lat": 39.60516,
        "lon": 29.67991,
        "details": {
            "season_meta": {"peak_month": 4, "sos_month": 3, "season_label_en": "April–June (Peak: April) | Green-up: March"},
            "flora": {"label": "Dense vegetation", "value": 0.58},
            "water": {"label": "Water present", "value": 1},
            "slope": {"label": "Slope", "value": 12},
            "precip": {"label": "Rainfall", "value": 56},
        },
        "why_points": [
            {"type": "good", "text": "Strong vegetation signal (high NDVI) suggests good forage potential in the selected window."},
            {"type": "good", "text": "Water availability appears present/nearby; this supports colony activity during warm periods."},
        ],
        "regional": {"avg": 55, "radius_km": 10},
        "methodology_note_en": "NDVI is a vegetation density indicator (not plant species). Climate metrics are long-term reanalysis summaries. BeeLocate PRO is a decision-support tool and does not guarantee outcomes.",
        "legal_disclaimer_en": "This report is generated from satellite and spatial analysis data and is provided for decision support only. Results may vary with season, management practices, and on-site conditions; no yield, income, or colony-health guarantee is implied.",
    }



def _last_report_set(ip: str, uid: str, rid: str) -> None:
    """Persist the last generated report id for this caller/day (MVP).

    Purpose: when a user hits the daily cap and client storage is cleared,
    we can still send them to the correct /buy/<rid> page.
    """
    try:
        day_bucket = int(time.time()) // 86400
        keys = []
        if ip:
            keys.append(f"ip:{ip}:{day_bucket}")
        if uid:
            keys.append(f"uid:{uid}:{day_bucket}")
        if not keys or not rid:
            return
        with _db() as con:
            for k in keys:
                con.execute(
                    "INSERT OR REPLACE INTO last_reports (k, day_bucket, rid) VALUES (?, ?, ?)",
                    (k, day_bucket, rid),
                )
            con.commit()
    except Exception:
        return


def _last_report_get(ip: str, uid: str) -> str:
    """Get the last report id for this caller/day (best-effort)."""
    try:
        day_bucket = int(time.time()) // 86400
        keys = []
        if ip:
            keys.append(f"ip:{ip}:{day_bucket}")
        if uid:
            keys.append(f"uid:{uid}:{day_bucket}")
        if not keys:
            return ""
        with _db() as con:
            for k in keys:
                row = con.execute("SELECT rid FROM last_reports WHERE k=?", (k,)).fetchone()
                if row and row[0]:
                    return str(row[0])
    except Exception:
        pass
    return ""


@app.route("/report-preview")
def report_preview():
    payload = get_demo_payload()
    rid = _report_store_put(payload)
    payload["report_id"] = rid
    html = _render_report_html(payload, report_id=rid, pdf_mode=False, is_paid=_is_paid(rid))
    return html




@app.route("/checkout")
def checkout():
    """Redirect helper so paywall can always send the user somewhere deterministic.

    It finds the last report for the caller for today (ip/uid) and redirects to /buy/<rid>.
    If none exists, it redirects to landing with a short message.
    """
    try:
        uid = (request.args.get("uid") or "").strip()
        fwd = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        ip = fwd or (request.headers.get("X-Real-IP") or "").strip() or (request.remote_addr or "").strip()
        rid = _last_report_get(ip, uid)
        if rid:
            return redirect(url_for("buy_report", rid=rid))
        # No stored report: user probably cleared storage before generating a report today.
        return redirect(url_for("index"))
    except Exception:
        return redirect(url_for("index"))



@app.route("/buy/<rid>")
def buy_report(rid: str):
    """Create a Lemon Squeezy checkout for this report and redirect user to hosted checkout."""
    payload = _report_store_get(rid)
    if not payload:
        return "Report not found or expired.", 404
    if _is_paid(rid):
        return redirect(url_for("report_by_id", rid=rid))

    # DEV / local testing: bypass Lemon and simulate a successful payment
    if os.environ.get("PAYMENTS_BYPASS", "").strip() == "1":
        # Optional safety: protect the test "mark paid" route with a simple token.
        # In production, set ADMIN_TOKEN and ALLOW_TEST_MARK_PAID=1 to enable the button.
        admin_token = os.environ.get("ADMIN_TOKEN", "").strip()
        token_qs = ("?t=" + admin_token) if admin_token else ""
        return f"""
        <html><head><title>Test Checkout (BYPASS)</title></head>
        <body style='font-family:system-ui; padding:24px;'>
          <h2>Test Checkout (BYPASS)</h2>
          <p>Report: <b>{rid}</b></p>
          <p>This simulates a successful payment.</p>
          <p><a href='/test/mark-paid/{rid}{token_qs}' style='display:inline-block;padding:10px 14px;background:#111;color:#fff;border-radius:10px;text-decoration:none;'>Mark as Paid (TEST)</a></p>
          <p><a href='/report/{rid}'>Back to report</a></p>
        </body></html>
        """

    api_key = os.environ.get("LS_API_KEY", "").strip()
    store_id = os.environ.get("LS_STORE_ID", "").strip()
    variant_id = os.environ.get("LS_VARIANT_ID", "").strip()
    if not (api_key and store_id and variant_id):
        return "Payment is not configured (missing LS_API_KEY / LS_STORE_ID / LS_VARIANT_ID).", 500

    base_url = (os.environ.get("APP_BASE_URL") or "").strip() or request.url_root.rstrip("/")
    # Never embed localhost URLs into hosted checkout return links.
    if "127.0.0.1" in base_url or "localhost" in base_url:
        base_url = request.url_root.rstrip("/")
    test_mode = os.environ.get("LS_TEST_MODE", "1").strip() in ("1","true","True","yes","YES")

    # Create a one-time checkout and attach report_id as custom data
    headers = {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
        "Authorization": f"Bearer {api_key}",
    }

    data = {
        "data": {
            "type": "checkouts",
            "attributes": {
                "product_options": {
                    "redirect_url": f"{base_url}/thank-you?rid={rid}",
                    "receipt_button_text": "View report",
                    "receipt_link_url": f"{base_url}/report/{rid}",
                },
                "checkout_options": {
                    "embed": False,
                    "discount": False,
                },
                "checkout_data": {
                    "custom": {
                        "report_id": rid
                    }
                },
                "test_mode": bool(test_mode),
                # Optional: expire after 2 hours
                "expires_at": (datetime.utcnow() + timedelta(hours=2)).isoformat() + "Z",
            },
            "relationships": {
                "store": { "data": { "type": "stores", "id": str(store_id) } },
                "variant": { "data": { "type": "variants", "id": str(variant_id) } },
            }
        }
    }

    try:
        resp = requests.post("https://api.lemonsqueezy.com/v1/checkouts", headers=headers, json=data, timeout=20)
        j = resp.json()
        if resp.status_code >= 300:
            return f"Could not create checkout: {j}", 502
        checkout_url = j["data"]["attributes"]["url"]
        return redirect(checkout_url)
    except Exception as e:
        return f"Checkout error: {e}", 502


@app.get("/test/mark-paid/<rid>")
def test_mark_paid(rid: str):
    """DEV-only helper. Marks a report as paid and redirects to PDF download."""
    # HARD GUARD:
    # - By default this route is disabled outside local dev.
    # - For temporary production testing, set:
    #     ALLOW_TEST_MARK_PAID=1
    #   and (strongly recommended) set ADMIN_TOKEN, then open:
    #     /test/mark-paid/<rid>?t=<ADMIN_TOKEN>
    is_dev = (app.debug or os.environ.get("FLASK_ENV", "").lower() == "development" or os.environ.get("DEBUG", "").strip() == "1")
    allow_prod_test = os.environ.get("ALLOW_TEST_MARK_PAID", "").strip() == "1"
    if not (is_dev or allow_prod_test):
        return "Not found", 404
    admin_token = os.environ.get("ADMIN_TOKEN", "").strip()
    if admin_token:
        if request.args.get("t", "") != admin_token:
            return "Forbidden", 403
    _paid_set(rid, provider="bypass", email="")
    return redirect(url_for("thank_you", rid=rid))



@app.get("/debug/routes")
def debug_routes():
    """DEV helper to list registered routes."""
    return "<pre>" + "\n".join(sorted([str(r) for r in app.url_map.iter_rules()])) + "</pre>"


@app.route("/thank-you")
def thank_you():
    rid = request.args.get("rid", "")
    # also allow /thank-you?rid=... and internal redirects that pass rid as a kwarg
    if not rid:
        rid = request.view_args.get('rid', '') if request.view_args else ''
    paid_until = _paid_until_ts(rid) if rid else None
    # Best-effort: bind buyer IP to the paid record so another browser on the same network
    # can benefit from the 24h unlimited window (no-login MVP).
    try:
        if rid and _is_paid(rid):
            fwd = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            ip = fwd or (request.headers.get("X-Real-IP") or "").strip() or (request.remote_addr or "").strip()
            _paid_bind_ip(rid, ip)
    except Exception:
        pass
    # Pre-warm premium AI insights after purchase so the report opens fast.
    try:
        if rid and _is_paid(rid):
            p = _report_store_get(rid)
            if p:
                _kickoff_ai_generation(rid, p)
    except Exception:
        pass
    return render_template("thank_you.html", report_id=rid, paid_until=paid_until)


def _verify_ls_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """Verify Lemon Squeezy webhook signature (HMAC SHA256 hex digest)."""
    if not (signature and secret):
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest().encode("utf-8")
    sig = signature.encode("utf-8")
    return hmac.compare_digest(digest, sig)


@app.route("/webhooks/lemonsqueezy", methods=["POST"])
def lemonsqueezy_webhook():
    secret = (os.environ.get("LS_WEBHOOK_SECRET") or os.environ.get("LEMON_WEBHOOK_SECRET") or "").strip()
    raw = request.get_data(cache=False)
    sig = request.headers.get("X-Signature", "") or request.headers.get("x-signature", "")
    if not _verify_ls_signature(raw, sig, secret):
        return "Invalid signature", 401

    event = request.headers.get("X-Event-Name", "") or request.headers.get("x-event-name", "")
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        return "Invalid JSON", 400

    # Extract report_id from multiple possible locations (Lemon payloads vary by event/version).
    rid = ""
    try:
        meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
        custom = meta.get("custom_data", {}) if isinstance(meta, dict) else {}
        rid = str(custom.get("report_id", "")).strip() or rid
    except Exception:
        pass
    try:
        data_obj = payload.get("data", {}) if isinstance(payload, dict) else {}
        attrs = data_obj.get("attributes", {}) if isinstance(data_obj, dict) else {}
        checkout_data = attrs.get("checkout_data", {}) if isinstance(attrs, dict) else {}
        # Some events embed custom checkout data under checkout_data.custom
        custom2 = checkout_data.get("custom", {}) if isinstance(checkout_data, dict) else {}
        rid = str(custom2.get("report_id", "")).strip() or rid
    except Exception:
        pass

    if event in ("order_created", "license_key_created") and rid:
        # Mark report as paid
        data_obj = payload.get("data", {}) if isinstance(payload, dict) else {}
        attrs = data_obj.get("attributes", {}) if isinstance(data_obj, dict) else {}
        email = attrs.get("user_email") or attrs.get("email") or ""
        _paid_set(rid, provider=event, email=email)
        return "OK", 200

    return "Ignored", 200




# Alias endpoint kept for backwards/alternate Lemon settings.

@app.route("/report/<rid>")
def report_by_id(rid: str):
    payload = _report_store_get(rid)
    if not payload:
        return "Report not found or expired.", 404
    payload["report_id"] = rid
    # New product rule: no preview report. Report access is paid-only.
    if not _is_paid(rid):
        return redirect(url_for("buy_report", rid=rid))

    # IMPORTANT: Do not block report rendering on an OpenAI call.
    # Kick off AI generation in the background (best-effort) and render immediately.
    payload = _kickoff_ai_generation(rid, payload)
    html = _render_report_html(payload, report_id=rid, pdf_mode=False, is_paid=True)
    return html


@app.route("/report/<rid>.pdf")
def report_pdf(rid: str):
    payload = _report_store_get(rid)
    if not payload:
        return "Report not found or expired.", 404
    payload["report_id"] = rid
    if not _is_paid(rid):
        # Not paid: send user to purchase flow (or BYPASS test checkout in dev).
        return redirect(url_for("buy_report", rid=rid))
    # Cache: avoid repeated headless Chrome runs for the same report.
    cached = _pdf_cache_get(rid)
    if cached:
        pdf_bytes = cached
    else:
        # Serialize PDF generation across processes (best effort) to prevent RAM/CPU stampede.
        with _FileLock(PDF_LOCK_FILE):
            # Double-check cache after acquiring lock
            cached2 = _pdf_cache_get(rid)
            if cached2:
                pdf_bytes = cached2
            else:
                # Generate premium AI commentary once and cache into report payload.
                payload = _ensure_ai_cached(rid, payload)
                html = _render_report_html(payload, report_id=rid, pdf_mode=True, is_paid=True)
                try:
                    pdf_bytes = _generate_pdf_from_html(html)
                except Exception as e:
                    return f"PDF export error: {e}", 500
                _pdf_cache_put(rid, pdf_bytes)

    filename = f"BeeLocatePRO_Report_{rid[:8]}.pdf"
    resp = make_response(pdf_bytes)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f"attachment; filename=\"{filename}\""
    # Reasonable caching for MVP (browser will not re-download unless changed).
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@app.route("/analyze", methods=["POST"])
def analyze():
    try:
        d = request.json or {}

        uid = _uid_from_request(d)

        # MVP abuse guard (does not replace proper auth).
        fwd = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        ip = fwd or (request.headers.get("X-Real-IP") or "").strip() or (request.remote_addr or "").strip()
        ok_rl, msg_rl = _rate_limit_allow(uid, ip)
        if not ok_rl:
            return jsonify({"ok": False, "error": msg_rl}), 429

        # Daily free quota (prevents 'switch browser to reset')
        ok_q, used_q, cap_q = _free_quota_allow(ip, uid)
        if not ok_q:
            last_rid = _last_report_get(ip, uid)
            buy_url = f"/checkout?uid={uid}"
            return jsonify({
                "ok": False,
                "error": f"Daily limit reached. You used {used_q}/{cap_q} free analyses today.",
                "quota": {"used": used_q, "cap": cap_q},
                "report_id": last_rid,
                "buy_url": buy_url
            }), 429

        # Language hint (presentation only; computation is language-agnostic)
        lang = (str(d.get('lang') or d.get('language') or 'en')).lower().strip()
        if lang not in ('en', 'tr'):
            lang = 'en'

        lat = d.get("lat")
        lon = d.get("lon") or d.get("lng")

        # Hard guard: if Earth Engine is not available, return a clear error payload (keep 200 to avoid breaking frontend)
        if not GEE_OK:
            return jsonify({
                'ok': False,
                'error': 'Earth Engine is not available right now (network/permissions).',
                'gee_ok': False
            })

        # month can be:
        # - "season"  : recommended season (phenology peak month)
        # - "current" : last 30 days
        # - "1".."12": simulated month
        month_req = d.get("month", "season")
        rad = d.get("rad", 2000)
        water_managed = bool(d.get("water_managed") or (d.get("water_strategy") == "managed"))

        if lat is None or lon is None:
            return jsonify({"error": "Missing coordinates"}), 400

        try:
            lat = float(lat)
            lon = float(lon)
        except Exception:
            return jsonify({"error": "Invalid coordinates"}), 400

        try:
            rad = float(rad)
        except Exception:
            rad = 2000.0

        rad = clamp(rad, 200, 20000)

        current_month = datetime.now().month

        # ROI must be created before any GEE-based meta calculations
        roi = ee.Geometry.Point([lon, lat]).buffer(rad)

        # Phenology meta is useful for trust: we compute it once and reuse it
        # (recommended season label appears even when user checks a specific month / current).
        season_meta = get_ndvi_phenology(roi) or {}
        # Attach human labels for UI (both languages)
        try:
            season_meta["season_label_tr"] = _season_label_tr(season_meta)
            season_meta["season_label_en"] = _season_label_en(season_meta)
        except Exception:
            pass

        if month_req == "season":
            peak_m = season_meta.get("peak_month", None)
            try:
                target_month_for_precip = int(peak_m) if peak_m else 4
            except Exception:
                target_month_for_precip = 4
            target_month = "season"
        elif month_req == "current":
            target_month_for_precip = current_month
            target_month = "current"
        else:
            try:
                target_month_for_precip = int(month_req)
            except Exception:
                target_month_for_precip = current_month
            target_month = target_month_for_precip

        print(f"--- ANALİZ: {lat}, {lon} | R: {rad}m | M: {target_month} ---")

        flora = get_flora(roi, target_month, season_meta=season_meta, lang=lang)
        water = get_water_hybrid(roi, lang=lang)
        topo = get_elevation_full(roi)
        clim = get_climate_smart(lat, lon, lang=lang)
        urban = get_urban(roi)
        transport = get_transport(lon, lat)
        precip = get_precipitation(roi, target_month_for_precip, lang=lang)
        settlement = get_settlement(lon, lat)
        flight = get_era5_flight_stats(roi)
        flight_suitability = make_flight_suitability(flight, lang=lang)

        # --- Presentation/i18n fixes for cards that still originate from core helpers ---
        # Core analysis stays language-agnostic; these transforms are UI-only.
        try:
            topo = pres.present_topo(topo, lang=lang)
        except Exception:
            pass
        try:
            urban = pres.present_urban(urban, lang=lang)
        except Exception:
            pass
        try:
            transport = pres.present_transport(transport, lang=lang)
        except Exception:
            pass
        try:
            settlement = pres.present_settlement(settlement, lang=lang)
        except Exception:
            pass
        try:
            flight = pres.present_flight(flight, lang=lang)
        except Exception:
            pass
        try:
            flight_suitability = pres.present_flight_suitability(flight_suitability, lang=lang)
        except Exception:
            pass

        # Enrich live climate text with ERA5 averages (if available)
        try:
            if flight.get("status") == "Aktif":
                tavg = flight.get("temp_avg_c", None)
                if tavg is not None:
                    tdesc = clim.get("temp", {}).get("desc", "")
                    clim.setdefault("temp", {})
                    tag = "Avg:" if str(lang).lower().startswith("en") else "Ort:"
                    clim["temp"]["desc"] = f"{tdesc} | {tag} {float(tavg):.1f}°C (ERA5)"
        except Exception:
            pass

        score_map = {
            "flora": metric_score(flora),
            "water": metric_score(water),
            "aspect": metric_score(topo.get("aspect", {})),
            "elevation": metric_score(topo.get("elevation", {})),
            "precip": metric_score(precip),
            "slope": metric_score(topo.get("slope", {})),
            "roads": metric_score(transport),
            "settlement": metric_score(settlement),
        }

        score = weighted_score(score_map)

        # MUST-HAVE gates
        w_s = score_map.get("water", None)
        f_s = score_map.get("flora", None)
        if w_s is not None and w_s < 50:
            # Eğer kullanıcı suyu yönetecekse (depo/kova), tamamen elemeyelim ama tavan koy.
            score = min(score, 60 if water_managed else 35)
        if f_s is not None and f_s < 25:
            score = min(score, 40)

        # Microclimate soft gate (ERA5): very low flight window should cap the score
        fl_sc = metric_score(flight)
        if fl_sc is not None and fl_sc < 50:
            score = min(score, 70)

        # Extra honesty penalties (operations): steep slope / high elevation
        try:
            slope_val = topo.get('slope', {}).get('value', None)
            if slope_val is not None and float(slope_val) > 25:
                score = max(0, score - 8)
        except Exception:
            pass
        try:
            elev_val = topo.get('elevation', {}).get('value', None)
            if elev_val is not None and float(elev_val) > 1400:
                score = max(0, score - 6)
        except Exception:
            pass

        sys_msg = build_sys_msg(flora, water, precip, settlement, urban, transport, flight, water_managed, month_req=month_req, season_meta=season_meta, lang=lang)

        resp = {
            "score": score,
            "lat": lat,
            "lon": lon,
            "lng": lon,
            "details": {
                "season_meta": season_meta or {},
                "flora": ensure_schema(flora),
                "water": ensure_schema(water),
                "urban": ensure_schema(urban, default_label=("Unknown" if lang=='en' else "Bilinmiyor")),
                "transport": ensure_schema(transport),
                "flight": ensure_schema(flight),
                "flight_suitability": ensure_schema(flight_suitability),
                "precip": ensure_schema(precip),
                "settlement": ensure_schema(settlement),

                "elevation": ensure_schema(topo.get("elevation", {})),
                "slope": ensure_schema(topo.get("slope", {})),
                "aspect": ensure_schema(topo.get("aspect", {})),

                "climate": {
                    "temp": ensure_schema(clim.get("temp", {})),
                    "wind": ensure_schema(clim.get("wind", {})),
                    "humidity": ensure_schema(clim.get("humidity", {})),
                },

                # legacy wrapper
                "topography": {
                    "elevation": ensure_schema(topo.get("elevation", {})),
                    "slope": ensure_schema(topo.get("slope", {})),
                    "aspect": ensure_schema(topo.get("aspect", {})),
                },
            },
            "sys_msg": sys_msg,
        }
        rid = _report_store_put(resp, uid=uid)
        resp["report_id"] = rid
        _last_report_set(ip, uid, rid)
        resp["generated_at"] = time.strftime('%Y-%m-%d %H:%M:%S')
        return jsonify(resp)

    except Exception as e:
        print(f"CRITICAL API ERROR: {e}")
        import traceback
        traceback.print_exc()

        return jsonify(
            {
                "score": 0,
                "sys_msg": "Sistem Hatası",
                "details": {
                    "flora": ensure_schema({}, default_label="Hata"),
                    "water": ensure_schema({}, default_label="Hata"),
                    "urban": ensure_schema({}, default_label="Hata"),
                    "transport": ensure_schema({}, default_label="Hata"),
                    "precip": ensure_schema({}, default_label="Hata"),
                    "settlement": ensure_schema({}, default_label="Hata"),
                    "elevation": ensure_schema({}, default_label="Hata"),
                    "slope": ensure_schema({}, default_label="Hata"),
                    "aspect": ensure_schema({}, default_label="Hata"),
                    "climate": {
                        "temp": ensure_schema({}, default_label="Hata"),
                        "wind": ensure_schema({}, default_label="Hata"),
                        "humidity": ensure_schema({}, default_label="Hata"),
                    },
                    "topography": {
                        "elevation": ensure_schema({}, default_label="Hata"),
                        "slope": ensure_schema({}, default_label="Hata"),
                        "aspect": ensure_schema({}, default_label="Hata"),
                    },
                },
            }
        )




# ----------------------------
# API Signature Lock (fail fast)
# ----------------------------
def _signature_lock():
    """Fail fast if someone breaks core function call contracts.
    This prevents the 'same function called 3 different ways' disaster.
    """
    import inspect as _inspect

    expected = {
        "get_urban": "(roi)",
        "get_settlement": "(lon, lat)",
        "get_transport": "(lon, lat)",
        "get_transport_overpass": "(lon, lat)",
        "get_water_hybrid": "(roi, lang='tr')",
        "get_flora": "(roi, month_arg, season_meta=None, lang='tr')",
    }

    for fn_name, sig_str in expected.items():
        fn = globals().get(fn_name)
        if not fn:
            raise RuntimeError(f"SignatureLock: missing function {fn_name}")
        got = str(_inspect.signature(fn))
        # normalize quotes
        got_norm = got.replace('"', "'")
        if got_norm != sig_str:
            raise RuntimeError(f"SignatureLock: {fn_name} expected {sig_str} but got {got}")

_signature_lock()



if __name__ == "__main__":
    app.run(debug=True, port=5000)


# =========================
# LEMON SQUEEZY WEBHOOK FIX
# =========================
# Fixes: "Invalid JSON" + ensures paid status is recorded in the SAME table used by _is_paid()
# Table used by app: payments(rid, paid_at, provider, email)
import json, time

@app.route("/lemon/webhook", methods=["POST"])
def lemon_webhook():
    # 1) Always parse from RAW body (Lemon may send application/vnd.api+json)
    raw = request.get_data(as_text=True)
    try:
        payload = json.loads(raw)
    except Exception as e:
        return jsonify({"error": "invalid json", "detail": str(e)}), 400

    event = payload.get("meta", {}).get("event_name")
    attrs = (payload.get("data", {}) or {}).get("attributes", {}) or {}

    # 2) Only act on successful paid orders
    if event == "order_created" and attrs.get("status") == "paid":
        rid = (payload.get("meta", {}) or {}).get("custom_data", {}).get("report_id")
        email = attrs.get("user_email")

        if rid:
            # 3) Persist payment in the SAME DB/table checked by _is_paid()
            _db_gc()
            with _db() as con:
                con.execute(
                    "INSERT OR REPLACE INTO payments (rid, paid_at, provider, email) VALUES (?, ?, ?, ?)",
                    (rid, time.time(), "lemon", email),
                )
                con.commit()

    return jsonify({"ok": True}), 200
