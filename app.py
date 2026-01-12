# app.py
from __future__ import annotations

import os
import math
import time
import json
import random
import logging
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, render_template, request, jsonify, send_file, after_this_request
from flask_cors import CORS
from fpdf import FPDF
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ------------------------------------------------------------
# UYGULAMA / LOG
# ------------------------------------------------------------
app = Flask(__name__)
CORS(app)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("beelocate")

# requests session + retry
def build_http() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update({"User-Agent": "BeeLocatePRO/1.0 (contact: you@example.com)"})
    return s

HTTP = build_http()


# ------------------------------------------------------------
# KÜÇÜK YARDIMCILAR
# ------------------------------------------------------------
def clean_tr(text: Any) -> str:
    """PDF gibi ASCII bekleyen yerler için Türkçe karakterleri sadeleştir."""
    if not isinstance(text, str):
        text = str(text)
    tr_map = str.maketrans("ğĞıİşŞçÇöÖüÜ", "gGiIsScCoOuU")
    return text.translate(tr_map)

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def get_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine (metre)."""
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def meters_per_deg_lon(lat: float) -> float:
    # ~111.32km * cos(lat)
    return 111320.0 * math.cos(math.radians(lat))

def meters_per_deg_lat() -> float:
    # ~111.32km
    return 111320.0

def get_compass_direction_tr(deg: float) -> str:
    arr = ["Kuzey", "KD", "Dogu", "GD", "Guney", "GB", "Bati", "KB"]
    return arr[int((deg / 45) % 8)]


# ------------------------------------------------------------
# BASİT TTL CACHE (RAM)
# ------------------------------------------------------------
@dataclass
class CacheItem:
    value: Any
    expires_at: float

class TTLCache:
    def __init__(self, ttl_seconds: int = 300):
        self.ttl = ttl_seconds
        self._store: Dict[str, CacheItem] = {}

    def get(self, key: str) -> Any:
        item = self._store.get(key)
        if not item:
            return None
        if time.time() > item.expires_at:
            self._store.pop(key, None)
            return None
        return item.value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = CacheItem(value=value, expires_at=time.time() + self.ttl)

CACHE_OSM = TTLCache(ttl_seconds=600)
CACHE_WEATHER = TTLCache(ttl_seconds=300)
CACHE_ELEV = TTLCache(ttl_seconds=3600)


# ------------------------------------------------------------
# INPUT DOĞRULAMA
# ------------------------------------------------------------
def parse_lat_lng_radius(payload: Dict[str, Any]) -> Tuple[float, float, int]:
    if not isinstance(payload, dict):
        raise ValueError("Geçersiz JSON gövdesi.")

    if "lat" not in payload or "lng" not in payload:
        raise ValueError("lat ve lng zorunlu.")

    lat = float(payload["lat"])
    lng = float(payload["lng"])
    radius = int(payload.get("radius", 2000))

    if not (-90.0 <= lat <= 90.0):
        raise ValueError("lat -90 ile 90 arasında olmalı.")
    if not (-180.0 <= lng <= 180.0):
        raise ValueError("lng -180 ile 180 arasında olmalı.")
    radius = int(clamp(radius, 250, 10000))  # 250m - 10km arası
    return lat, lng, radius


# ------------------------------------------------------------
# VERİ TOPLAMA
# ------------------------------------------------------------
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

def get_osm_data(lat: float, lng: float, radius: int) -> List[Dict[str, Any]]:
    cache_key = f"osm:{lat:.5f}:{lng:.5f}:{radius}"
    cached = CACHE_OSM.get(cache_key)
    if cached is not None:
        return cached

    query = f"""
    [out:json][timeout:45];
    (
      node["natural"="water"](around:{radius},{lat},{lng});
      way["natural"="water"](around:{radius},{lat},{lng});

      node["landuse"~"forest|orchard|farm|meadow"](around:{radius},{lat},{lng});
      way["landuse"~"forest|orchard|farm|meadow"](around:{radius},{lat},{lng});

      node["natural"~"wood|scrub|heath"](around:{radius},{lat},{lng});
      way["natural"~"wood|scrub|heath"](around:{radius},{lat},{lng});

      node["highway"](around:{radius},{lat},{lng});
      way["highway"](around:{radius},{lat},{lng});

      node["building"](around:{radius},{lat},{lng});
      way["building"](around:{radius},{lat},{lng});
    );
    out center;
    """

    last_err = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            r = HTTP.get(endpoint, params={"data": query}, timeout=45)
            if r.status_code == 200:
                data = r.json()
                elements = data.get("elements", []) or []
                CACHE_OSM.set(cache_key, elements)
                return elements
            last_err = f"Overpass {endpoint} status {r.status_code}"
        except Exception as e:
            last_err = str(e)

    log.warning("OSM verisi alınamadı: %s", last_err)
    CACHE_OSM.set(cache_key, [])
    return []

def get_full_weather(lat: float, lng: float) -> Tuple[Dict[str, Any], int]:
    """Sıcaklık/rüzgar (current_weather) + nem (hourly'den current time ile eşleştirilmiş)."""
    cache_key = f"wx:{lat:.5f}:{lng:.5f}"
    cached = CACHE_WEATHER.get(cache_key)
    if cached is not None:
        return cached

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lng}"
        "&current_weather=true"
        "&hourly=relativehumidity_2m,time"
        "&timezone=auto"
    )
    try:
        r = HTTP.get(url, timeout=8)
        if r.status_code != 200:
            raise RuntimeError(f"Weather status {r.status_code}")

        data = r.json() or {}
        current = data.get("current_weather", {}) or {}
        humidity = 50

        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []
        hums = hourly.get("relativehumidity_2m") or []

        cur_time = current.get("time")
        if cur_time and times and hums and len(times) == len(hums):
            # Tam eşleşme varsa al
            if cur_time in times:
                idx = times.index(cur_time)
                humidity = int(hums[idx])
            else:
                # Yoksa: en yakın saat (string ISO format; basit yaklaşım)
                # times sıralı gelir; ilk 24 saatte genelde yakın bulunur
                humidity = int(hums[0])

        out = (current, int(clamp(humidity, 0, 100)))
        CACHE_WEATHER.set(cache_key, out)
        return out

    except Exception as e:
        log.warning("Weather alınamadı: %s", e)
        out = ({}, 50)
        CACHE_WEATHER.set(cache_key, out)
        return out

def calculate_real_topography(lat: float, lng: float) -> Tuple[int, int, int]:
    """3-nokta (merkez, kuzey, doğu) ile yaklaşık eğim (%) + bakı (0=N,90=E) + rakım."""
    cache_key = f"elev:{lat:.5f}:{lng:.5f}"
    cached = CACHE_ELEV.get(cache_key)
    if cached is not None:
        return cached

    base_url = "https://api.open-meteo.com/v1/elevation"
    ddeg = 0.001  # ~111m
    lats = f"{lat},{lat + ddeg},{lat}"
    lngs = f"{lng},{lng},{lng + ddeg}"

    try:
        r = HTTP.get(f"{base_url}?latitude={lats}&longitude={lngs}", timeout=8)
        if r.status_code != 200:
            raise RuntimeError(f"Elevation status {r.status_code}")

        elevs = (r.json() or {}).get("elevation", []) or []
        if len(elevs) < 3:
            out = (0, 0, 0)
            CACHE_ELEV.set(cache_key, out)
            return out

        h0, h_north, h_east = float(elevs[0]), float(elevs[1]), float(elevs[2])

        dy_m = meters_per_deg_lat() * ddeg
        dx_m = meters_per_deg_lon(lat) * ddeg
        if dx_m <= 1e-6 or dy_m <= 1e-6:
            out = (0, 0, int(h0))
            CACHE_ELEV.set(cache_key, out)
            return out

        dz_dy = (h_north - h0) / dy_m
        dz_dx = (h_east - h0) / dx_m

        # slope
        slope_rad = math.atan(math.sqrt(dz_dx**2 + dz_dy**2))
        slope_pct = math.tan(slope_rad) * 100.0

        # aspect: 0=N,90=E (yaklaşık; en dik aşağı yön)
        # gradient yukarı yönü verir, aşağı için işaret değiştiriyoruz
        aspect_rad = math.atan2(-dz_dx, -dz_dy)
        aspect_deg = (math.degrees(aspect_rad) + 360.0) % 360.0

        out = (int(round(clamp(slope_pct, 0, 200))), int(round(aspect_deg)), int(round(h0)))
        CACHE_ELEV.set(cache_key, out)
        return out

    except Exception as e:
        log.warning("Topography hesaplanamadı: %s", e)
        out = (0, 0, 0)
        CACHE_ELEV.set(cache_key, out)
        return out


# ------------------------------------------------------------
# ANALİZ / SKOR
# ------------------------------------------------------------
def calculate_score(
    lat: float,
    lng: float,
    radius: int,
    elements: List[Dict[str, Any]],
    weather: Dict[str, Any],
    humidity: int,
    slope: int,
    aspect: int,
    elevation: int,
) -> Dict[str, Any]:

    # 1) FLORA (OSM tag’lerinden kaba proxy)
    flora_points = 0
    flora_types = set()

    for e in elements:
        t = e.get("tags", {}) or {}
        landuse = t.get("landuse")
        natural = t.get("natural")

        if landuse == "forest" or natural == "wood":
            flora_points += 5
            flora_types.add("Orman")
        elif landuse == "orchard":
            flora_points += 4
            flora_types.add("Meyvelik")
        elif (natural in ["scrub", "heath"]) or (landuse == "meadow"):
            flora_points += 2
            flora_types.add("Maki/Cayir")
        elif landuse == "farm":
            flora_points += 1
            flora_types.add("Tarim")

    # OSM yoğunluğu yanlılığını azaltmak için yumuşat:
    # çok eleman = sonsuz iyi değil -> log ölçek + cap
    final_flora = int(clamp(math.log1p(flora_points) * 25.0, 0, 100))
    flora_txt = ", ".join(sorted(list(flora_types))) if flora_types else "Veri zayif"

    # 2) SU (en yakın su öğesi mesafesi)
    water_elems = [e for e in elements if (e.get("tags", {}) or {}).get("natural") == "water"]
    min_dist = None
    for w in water_elems:
        w_lat = w.get("lat") or (w.get("center", {}) or {}).get("lat")
        w_lon = w.get("lon") or (w.get("center", {}) or {}).get("lon")
        if w_lat is None or w_lon is None:
            continue
        d = get_distance_m(lat, lng, float(w_lat), float(w_lon))
        min_dist = d if (min_dist is None or d < min_dist) else min_dist

    if min_dist is None:
        water_score = 10  # veri yoksa “0” yerine düşük ama tamamen öldürmeyen
        d_water_m = 0
    else:
        d_water_m = int(round(min_dist))
        # basamaklı değil, yumuşak azalan fonksiyon (0m->100, 2000m->~40, 5000m->~10)
        water_score = int(round(clamp(110.0 / (1.0 + (d_water_m / 900.0)), 0, 100)))

    # 3) İKLİM
    wind = float(weather.get("windspeed", 0) or 0)
    temp = float(weather.get("temperature", 0) or 0)

    # rüzgar: 0-15 çok iyi, 15-25 orta, >25 kötü
    wind_score = int(round(clamp(100 - (wind - 12) * 6, 0, 100)))

    # nem: ideal 40-70, dışına çıkınca yumuşak düş
    if 40 <= humidity <= 70:
        hum_score = 100
    else:
        hum_score = int(round(clamp(100 - abs(humidity - 55) * 2.2, 0, 100)))

    # 4) ARAZİ
    # eğim: 2-10 iyi; 0-2 ve 10-20 orta; >20 düş
    if 2 <= slope <= 10:
        slope_score = 100
    elif slope < 2:
        slope_score = 70
    elif slope <= 20:
        slope_score = 60
    else:
        slope_score = int(round(clamp(60 - (slope - 20) * 2.0, 5, 60)))

    # bakı: TR’de kabaca güney bandı daha avantajlı proxy (135-225)
    aspect_score = 100 if 135 <= aspect <= 225 else 65

    # 5) BASKI (building yoğunluğu)
    buildings = sum(1 for e in elements if (e.get("tags", {}) or {}).get("building") is not None)
    area_km2 = math.pi * (radius / 1000.0) ** 2
    b_density = buildings / area_km2 if area_km2 > 0 else buildings
    pressure_score = int(round(clamp(100 - b_density * 3.5, 0, 100)))

    # AĞIRLIKLI TOPLAM
    total = (
        final_flora * 0.30
        + water_score * 0.20
        + wind_score * 0.10
        + hum_score * 0.10
        + slope_score * 0.10
        + aspect_score * 0.10
        + pressure_score * 0.10
    )
    total_int = int(round(clamp(total, 0, 100)))

    aspect_tr = get_compass_direction_tr(aspect)

    # Frontend uyumluluğu: breakdown anahtarlarını küçük harfli döndür
    breakdown = {
        "flora": final_flora,
        "water": water_score,
        "wind": wind_score,
        "humidity": hum_score,
        "slope": slope_score,
        "aspect": aspect_score,
        "pressure": pressure_score,
    }

    details = {
        "flora_type": flora_txt,
        "d_water": d_water_m,
        "avg_wind": round(wind, 1),
        "avg_temp": round(temp, 1),
        "avg_hum": int(humidity),
        "s_val": int(slope),
        "aspect_deg": int(aspect),
        "dir_tr": aspect_tr,
        "elevation": int(elevation),
        "b_count": int(buildings),
        "radius_m": int(radius),
        # şeffaflık: bu skorlar OSM+OpenMeteo proxy’lerinden türetilmiştir
        "data_notes": "OSM + Open-Meteo proxy verileriyle uretilen yaklasik skorlama.",
    }

    ai_text = (
        f"<strong>Skor:</strong> {total_int}/100<br>"
        f"<strong>Flora:</strong> {flora_txt} (proxy)<br>"
        f"<strong>Su:</strong> {d_water_m if d_water_m else 'Bilinmiyor'} m<br>"
        f"<strong>Nem:</strong> %{humidity}<br>"
        f"<strong>Arazi:</strong> %{slope} egim, {aspect_tr} baki<br>"
        f"<strong>Ruzgar:</strong> {round(wind,1)} km/h<br>"
        f"<small>Not: Bu rapor veri kaynaklarina dayali yaklasik bir analizdir.</small>"
    )

    return {
        "score": total_int,
        "ai_text": ai_text,
        "details": details,
        "breakdown": breakdown,
    }


def build_heatmap(lat: float, lng: float, base_score: int) -> Dict[str, Any]:
    """
    Gerçek raster/NDVI gibi grid verisi yoksa heatmap dürüstçe simülasyon olmalı.
    Burada değerleri base_score etrafında küçük gürültüyle üretiyoruz.
    """
    pts = []
    for _ in range(24):
        dlat = random.uniform(-0.01, 0.01)
        dlng = random.uniform(-0.01, 0.01)
        val = int(clamp(random.gauss(base_score, 8), 5, 100))
        pts.append({"lat": lat + dlat, "lng": lng + dlng, "val": val})

    return {"simulated": True, "points": pts}


# ------------------------------------------------------------
# PDF
# ------------------------------------------------------------
class PDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 18)
        self.set_text_color(30, 30, 30)
        self.cell(0, 10, "BeeLocate PRO", 0, 1, "C")
        self.set_font("Helvetica", "", 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 6, "Saha Analiz Raporu (Proxy verilerle)", 0, 1, "C")
        self.ln(6)


# ------------------------------------------------------------
# ROTALAR
# ------------------------------------------------------------
@app.route("/health")
def health():
    return jsonify({"ok": True, "ts": int(time.time())})

@app.route("/")
def landing():
    return render_template("landing.html")

@app.route("/app")
def app_page():
    return render_template("index.html")

@app.route("/analyze", methods=["POST"])
def analyze():
    try:
        payload = request.get_json(force=True, silent=False)  # JSON değilse patlasın, doğru davranış
        lat, lng, radius = parse_lat_lng_radius(payload)

        elements = get_osm_data(lat, lng, radius)
        weather, humidity = get_full_weather(lat, lng)
        slope, aspect, elevation = calculate_real_topography(lat, lng)

        res = calculate_score(lat, lng, radius, elements, weather, humidity, slope, aspect, elevation)
        res["heatmap"] = build_heatmap(lat, lng, res["score"])

        return jsonify(res), 200

    except Exception as e:
        log.exception("Analyze hata: %s", e)
        return jsonify({"error": str(e)}), 400

@app.route("/download_report")
def download_report():
    try:
        lat = float(request.args.get("lat", "nan"))
        lng = float(request.args.get("lng", "nan"))
        radius = int(request.args.get("radius", "2000"))

        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
            return "Koordinat hatasi", 400

        radius = int(clamp(radius, 250, 10000))

        elements = get_osm_data(lat, lng, radius)
        weather, humidity = get_full_weather(lat, lng)
        slope, aspect, elevation = calculate_real_topography(lat, lng)

        res = calculate_score(lat, lng, radius, elements, weather, humidity, slope, aspect, elevation)

        pdf = PDF()
        pdf.add_page()
        pdf.set_font("Helvetica", "", 12)
        pdf.set_text_color(0, 0, 0)

        pdf.cell(0, 10, clean_tr(f"Koordinat: {lat:.5f}, {lng:.5f}  |  Yaricap: {radius} m"), 0, 1, "C")
        pdf.ln(4)

        d = res["details"]
        lines = [
            f"Genel Puan: {res['score']}/100",
            f"Bitki Ortusu (proxy): {clean_tr(d['flora_type'])}",
            f"Suya Mesafe: {d['d_water']} m" if d["d_water"] else "Suya Mesafe: Bilinmiyor",
            f"Nem Orani: %{d['avg_hum']}",
            f"Ruzgar: {d['avg_wind']} km/h",
            f"Sicaklik: {d['avg_temp']} C",
            f"Rakim: {d['elevation']} m",
            f"Egim: %{d['s_val']}",
            f"Baki: {clean_tr(d['dir_tr'])} ({d['aspect_deg']}°)",
            f"Yapi Sayisi (OSM): {d['b_count']}",
        ]

        for line in lines:
            pdf.cell(0, 9, clean_tr(line), 1, 1)

        pdf.ln(4)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(90, 90, 90)
        pdf.multi_cell(
            0,
            5,
            clean_tr(
                "Not: Bu rapor OSM etiketleri ve Open-Meteo (hava/rakim) verileriyle uretilen yaklasik bir analizdir. "
                "Kesin saha karari icin yerel gozlem/uzman degerlendirmesi gerekir."
            ),
        )

        # Çakışmasız temp dosya
        safe_name = f"BeeLocate_{lat:.5f}_{lng:.5f}_r{radius}.pdf".replace("-", "m")
        tmp = tempfile.NamedTemporaryFile(prefix="beelocate_", suffix=".pdf", delete=False)
        tmp_path = tmp.name
        tmp.close()

        pdf.output(tmp_path)

        @after_this_request
        def cleanup(response):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            return response

        return send_file(tmp_path, as_attachment=True, download_name=safe_name)

    except Exception as e:
        log.exception("PDF hata: %s", e)
        return "Rapor hatasi", 500


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
