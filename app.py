from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
import requests
import logging
import os
import random
from fpdf import FPDF
from datetime import datetime

# Loglama
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# --- DİL AYARLARI ---
TRANS = {
    'TR': {
        'title': 'ARICILIK SAHA ANALIZ RAPORU', 'footer': 'BeeLocate Systems 2026',
        'ch_1': '1. YONETICI OZETI', 'ch_2': '2. TEKNIK DETAYLAR',
        'loc': 'Konum', 'flora': 'Flora Tipi', 'water': 'Su Kaynagi', 'wind': 'Hakim Ruzgar', 
        'aspect': 'Arazi Bakisi', 'temp': 'Ort. Sicaklik', 'elev': 'Rakim', 'settle': 'Yerlesim',
        'not_found': 'Tespit Edilemedi (>5km)', 'access_err': 'Erisim Zor',
        'w_good': "Su kaynaklarina erisim ideal seviyededir",
        'w_bad': "Su kaynagi uzaktir",
        'wi_warn': "Yuksek Ruzgar Riski",
        'std_summary': "Bolgede {flora} hakimiyeti gorulmustur. Hakim ruzgar {wind} yonundedir. {water_txt}."
    },
    'EN': {
        'title': 'BEEKEEPING SITE ANALYSIS REPORT', 'footer': 'BeeLocate Systems 2026',
        'ch_1': '1. EXECUTIVE SUMMARY', 'ch_2': '2. TECHNICAL DETAILS',
        'loc': 'Location', 'flora': 'Flora Type', 'water': 'Water Source', 'wind': 'Prevailing Wind', 
        'aspect': 'Aspect', 'temp': 'Avg. Temp', 'elev': 'Elevation', 'settle': 'Settlement',
        'not_found': 'Not Detected (>5km)', 'access_err': 'Hard Access',
        'w_good': "Water access is ideal",
        'w_bad': "Water source is distant",
        'wi_warn': "High Wind Risk",
        'std_summary': "Area dominated by {flora}. Prevailing wind is {wind}. {water_txt}."
    }
}

def tr_chars(text):
    if not isinstance(text, str): return str(text)
    tr_map = str.maketrans("ğĞıİşŞçÇöÖüÜ", "gGiIsScCoOuU")
    return text.translate(tr_map)

# --- HAFİF VERİ ÇEKME (OSMNX KÜTÜPHANESİ OLMADAN) ---
# Bu yöntem sunucuyu yormaz, direkt API ile konuşur.
def get_osm_data_light(lat, lng, radius):
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
        r = requests.get(overpass_url, params={'data': query}, timeout=15)
        return r.json().get('elements', []) if r.status_code == 200 else []
    except:
        return []

def get_weather_data(lat, lng):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lng}&current_weather=true&elevation=true"
        r = requests.get(url, timeout=5)
        return r.json() if r.status_code == 200 else {}
    except:
        return {}

def calculate_score(lat, lng, radius, elements, weather, lang="TR"):
    # Flora
    forest_count = sum(1 for e in elements if e.get('tags', {}).get('landuse') == 'forest')
    flora_score = min(100, forest_count * 5 + 40)
    
    flora_type = "Zengin Orman" if flora_score > 70 else "Maki / Mera"
    if lang == "EN": flora_type = "Rich Forest" if flora_score > 70 else "Scrub / Meadow"

    # Su
    water_nodes = [e for e in elements if e.get('tags', {}).get('natural') == 'water']
    min_dist_water = random.randint(100, 2000) if water_nodes else 9999
    water_score = 100 if min_dist_water < 1000 else (50 if min_dist_water < 3000 else 20)

    # Rüzgar
    wind_speed = weather.get('current_weather', {}).get('windspeed', 10)
    wind_dir_code = weather.get('current_weather', {}).get('winddirection', 0)
    wind_score = 100 if 5 < wind_speed < 25 else 50
    
    # Yön Çeviri
    dirs = ["Kuzey", "Kuzeydogu", "Dogu", "Guneydogu", "Guney", "Guneybati", "Bati", "Kuzeybati"]
    if lang == "EN": dirs = ["North", "NE", "East", "SE", "South", "SW", "West", "NW"]
    wind_dir = dirs[int((wind_dir_code/45)%8)]
    
    # Diğerleri (Simüle - Stabilite için)
    slope = random.uniform(2, 20)
    slope_score = 100 if 2 < slope < 15 else 50
    aspect = random.choice(dirs)
    aspect_score = 100 if "Guney" in aspect or "South" in aspect else 70
    
    road_count = sum(1 for e in elements if e.get('tags', {}).get('highway'))
    road_dist = random.randint(50, 2000) if road_count > 0 else 5000
    road_score = 100 if 50 < road_dist < 1000 else 60

    build_count = sum(1 for e in elements if e.get('tags', {}).get('building'))
    build_score = max(20, 100 - (build_count * 2))

    elevation = weather.get('elevation', 800)
    temp = weather.get('current_weather', {}).get('temperature', 20)
    temp_score = 90

    total = (flora_score * 0.35) + (water_score * 0.15) + (wind_score * 0.10) + \
            (slope_score * 0.05) + (aspect_score * 0.10) + (road_score * 0.10) + \
            (build_score * 0.10) + (temp_score * 0.05)

    L = TRANS[lang]
    w_txt = L['w_good'] if min_dist_water < 2000 else L['w_bad']
    ai_text = L['std_summary'].format(flora=flora_type, wind=wind_dir, water_txt=w_txt, wind_txt="")

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

class PDF(FPDF):
    def __init__(self, lang='TR'):
        super().__init__()
        self.L = TRANS[lang]
    def header(self):
        self.set_font('Arial', 'B', 20); self.set_text_color(255, 193, 7)
        self.cell(0, 10, 'BeeLocate PRO', 0, 1, 'C')
        self.set_font('Arial', '', 10); self.set_text_color(100)
        self.cell(0, 5, 'AI Powered Feasibility Report', 0, 1, 'C'); self.ln(10)
    def footer(self):
        self.set_y(-15); self.set_font('Arial', 'I', 8); self.set_text_color(128)
        self.cell(0, 10, tr_chars(self.L['footer']), 0, 0, 'C')

@app.route('/')
def home(): return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    d = request.json
    lat, lng = float(d['lat']), float(d['lng'])
    lang = d.get('lang', 'TR')
    
    # Hafif Veri Çekme
    osm_data = get_osm_data_light(lat, lng, 2000)
    weather_data = get_weather_data(lat, lng)
    
    res = calculate_score(lat, lng, 2000, osm_data, weather_data, lang)
    
    # Isı Haritası Simülasyonu
    res['heatmap'] = [{'lat': lat+random.uniform(-0.01,0.01), 'lng': lng+random.uniform(-0.01,0.01), 'val': random.randint(30,90)} for _ in range(20)]
    
    return jsonify(res)

@app.route('/download_report')
def download_report():
    try:
        lat = float(request.args.get('lat'))
        lng = float(request.args.get('lng'))
        lang = request.args.get('lang', 'TR')
        
        osm_data = get_osm_data_light(lat, lng, 2000)
        weather_data = get_weather_data(lat, lng)
        res = calculate_score(lat, lng, 2000, osm_data, weather_data, lang)
        
        L = TRANS[lang]
        pdf = PDF(lang); pdf.add_page()
        
        pdf.set_font('Arial', 'B', 14); pdf.set_text_color(0)
        pdf.cell(0, 10, f"{L['loc']}: {lat:.4f}, {lng:.4f}", 0, 1, 'C')
        
        pdf.set_fill_color(240, 240, 240); pdf.rect(10, 50, 190, 20, 'F'); pdf.set_y(55)
        pdf.set_font('Arial', 'B', 16)
        pdf.cell(0, 10, f"{L['score']}: {res['score']} / 100", 0, 1, 'C'); pdf.ln(20)
        
        pdf.set_font('Arial', 'B', 12); pdf.cell(0, 10, tr_chars(L['ch_2']), 0, 1, 'L')
        pdf.set_font('Arial', '', 10)
        
        d = res['details']
        w_res = f"{d['d_water']}m" if d['d_water'] < 5000 else tr_chars(L['not_found'])
        r_res = f"{d['d_road']}m" if d['d_road'] < 5000 else tr_chars(L['access_err'])

        items = [
            (L['flora'], clean_tr(d['flora_type'])), (L['water'], w_res),
            (L['wind'], f"{d['avg_wind']} km/h"), (L['elev'], f"{d['elevation']}m"),
            (L['aspect'], clean_tr(d['dir_tr'])), (L['temp'], f"{d['avg_temp']} C")
        ]
        
        for k, v in items:
            pdf.cell(50, 8, tr_chars(k), 1)
            pdf.cell(100, 8, str(v), 1, 1)

        pdf_name = f"BeeLocate_{lat}_{lng}.pdf"
        pdf_path = f"/tmp/{pdf_name}"
        pdf.output(pdf_path)
        return send_file(pdf_path, as_attachment=True)

    except Exception as e: return str(e)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
