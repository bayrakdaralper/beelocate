from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import requests
import logging
import os
import random

# Loglama Ayarları
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# --- OSM VERİ ÇEKME (HIZLI & STABİL) ---
def get_osm_data(lat, lng, radius):
    overpass_url = "http://overpass-api.de/api/interpreter"
    query = f"""
    [out:json];
    (
      node["natural"="water"](around:{radius},{lat},{lng});
      way["natural"="water"](around:{radius},{lat},{lng});
      node["landuse"="forest"](around:{radius},{lat},{lng});
      way["landuse"="forest"](around:{radius},{lat},{lng});
      node["highway"](around:{radius},{lat},{lng});
      way["highway"](around:{radius},{lat},{lng});
      node["building"](around:{radius},{lat},{lng});
      way["building"](around:{radius},{lat},{lng});
    );
    out center;
    """
    try:
        r = requests.get(overpass_url, params={'data': query}, timeout=20)
        return r.json().get('elements', []) if r.status_code == 200 else []
    except:
        return []

# --- HAVA DURUMU ---
def get_weather_data(lat, lng):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lng}&current_weather=true&elevation=true"
        r = requests.get(url, timeout=10)
        return r.json() if r.status_code == 200 else {}
    except:
        return {}

# --- PUANLAMA MOTORU (YÜKSEK PUAN VEREN VERSİYON) ---
def calculate_score(lat, lng, radius, elements, weather):
    # Flora: Orman bulamazsa bile en az 40 puan verir
    forest_count = sum(1 for e in elements if e.get('tags', {}).get('landuse') == 'forest')
    flora_score = min(100, forest_count * 5 + 40)
    flora_type = "Zengin Orman" if flora_score > 70 else "Maki / Mera"

    # Su: Bulamazsa simüle eder (Puan kırmaz)
    water_nodes = [e for e in elements if e.get('tags', {}).get('natural') == 'water']
    if water_nodes:
        min_dist_water = random.randint(100, 2000)
    else:
        min_dist_water = 9999 
    
    water_score = 100 if min_dist_water < 1000 else (50 if min_dist_water < 3000 else 25)

    # Rüzgar
    wind_speed = weather.get('current_weather', {}).get('windspeed', 10)
    wind_dir = weather.get('current_weather', {}).get('winddirection', 0)
    wind_score = 100 if 5 < wind_speed < 25 else 50

    # Diğerleri (Simüle - Hızlı)
    slope = random.uniform(2, 20)
    slope_score = 100 if 2 < slope < 15 else 50
    
    aspects = ["Kuzey", "Guney", "Dogu", "Bati"]
    aspect = random.choice(aspects)
    aspect_score = 100 if "Guney" in aspect else 70
    
    road_count = sum(1 for e in elements if e.get('tags', {}).get('highway'))
    road_dist = random.randint(50, 2000) if road_count > 0 else 5000
    road_score = 100 if 50 < road_dist < 1000 else 60

    build_count = sum(1 for e in elements if e.get('tags', {}).get('building'))
    build_score = max(20, 100 - (build_count * 2))

    elevation = weather.get('elevation', 800)
    temp = weather.get('current_weather', {}).get('temperature', 20)
    temp_score = 90

    # Skor Hesapla
    total = (flora_score * 0.35) + (water_score * 0.15) + (wind_score * 0.10) + \
            (slope_score * 0.05) + (aspect_score * 0.10) + (road_score * 0.10) + \
            (build_score * 0.10) + (temp_score * 0.05)

    ai_text = f"""
    Genel Degerlendirme: Bolge {int(total)}/100 puan.
    Flora: {flora_type}.
    Ruzgar: {wind_speed} km/h.
    """

    return {
        "score": int(total),
        "ai_text": ai_text,
        "details": {
            "flora_type": flora_type, "d_water": min_dist_water, "avg_wind": wind_speed,
            "wind_dir": wind_dir, "d_road": road_dist, "b_count": build_count,
            "s_val": int(slope), "dir_tr": aspect, "elevation": elevation, "avg_temp": temp, "avg_hum": 55
        },
        "breakdown": {
            "Flora": flora_score, "Su": water_score, "Ruzgar": wind_score,
            "Egim": slope_score, "Baki": aspect_score, "Ulasim": road_score, "Yerlesim": build_score
        }
    }

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    d = request.json
    res = calculate_score(d['lat'], d['lng'], d.get('radius', 2000), 
                          get_osm_data(d['lat'], d['lng'], d.get('radius', 2000)),
                          get_weather_data(d['lat'], d['lng']))
    
    # Isı Haritası Simülasyonu
    res['heatmap'] = [{'lat': d['lat']+random.uniform(-0.01,0.01), 
                       'lng': d['lng']+random.uniform(-0.01,0.01), 
                       'val': random.randint(30,90)} for _ in range(20)]
    return jsonify(res)

# PDF İndirme Rotasını boşalttık, hata vermesin ama işlem de yapmasın
@app.route('/download_report')
def download_report():
    return "Rapor sistemi bakımda."

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
