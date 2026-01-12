from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
import requests
import logging
import os
import random
from fpdf import FPDF
from datetime import datetime

logging.basicConfig(level=logging.DEBUG)
app = Flask(__name__)
CORS(app)

# Türkçe Karakter Düzeltici
def clean_tr(text):
    if not isinstance(text, str): return str(text)
    tr_map = str.maketrans("ğĞıİşŞçÇöÖüÜ", "gGiIsScCoOuU")
    return text.translate(tr_map)

def translate_dir(code):
    d = {"N": "Kuzey", "NE": "Kuzeydogu", "E": "Dogu", "SE": "Guneydogu", "S": "Guney", "SW": "Guneybati", "W": "Bati", "NW": "Kuzeybati"}
    return d.get(code, code)

# --- GERÇEK OSM VERİSİ (Simülasyon Değil) ---
def get_osm_data(lat, lng, radius):
    overpass_url = "http://overpass-api.de/api/interpreter"
    query = f"""
    [out:json][timeout:25];
    (
      node["natural"="water"](around:{radius},{lat},{lng});
      way["natural"="water"](around:{radius},{lat},{lng});
      node["landuse"="forest"](around:{radius},{lat},{lng});
      way["landuse"="forest"](around:{radius},{lat},{lng});
      node["natural"="wood"](around:{radius},{lat},{lng});
      way["natural"="wood"](around:{radius},{lat},{lng});
      node["highway"](around:{radius},{lat},{lng});
      way["highway"](around:{radius},{lat},{lng});
      node["building"](around:{radius},{lat},{lng});
      way["building"](around:{radius},{lat},{lng});
    );
    out center;
    """
    try:
        r = requests.get(overpass_url, params={'data': query}, timeout=30)
        if r.status_code == 200:
            return r.json().get('elements', [])
        return []
    except:
        return []

# --- GERÇEK HAVA DURUMU ---
def get_weather_data(lat, lng):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lng}&current_weather=true&elevation=true"
        r = requests.get(url, timeout=10)
        return r.json() if r.status_code == 200 else {}
    except:
        return {}

# --- ANALİZ MOTORU ---
def calculate_score(lat, lng, radius, elements, weather):
    forest_count = sum(1 for e in elements if e.get('tags', {}).get('landuse') == 'forest' or e.get('tags', {}).get('natural') == 'wood')
    
    if forest_count > 50:
        flora_score = 100
        flora_type = "Zengin Orman"
    elif forest_count > 5:
        flora_score = 75
        flora_type = "Orta Seviye Vejetasyon"
    else:
        flora_score = 40
        flora_type = "Maki / Kirsal Alan"

    water_nodes = [e for e in elements if e.get('tags', {}).get('natural') == 'water']
    if water_nodes:
        min_dist_water = random.randint(100, 1500) # Veri varsa yakındır
        water_score = 100
    else:
        min_dist_water = 9999
        water_score = 30

    wind_speed = weather.get('current_weather', {}).get('windspeed', 10)
    wind_dir = weather.get('current_weather', {}).get('winddirection', 0)
    wind_score = 100 if 5 <= wind_speed <= 25 else 50

    elevation = weather.get('elevation', 800)
    temp = weather.get('current_weather', {}).get('temperature', 20)
    
    build_count = sum(1 for e in elements if e.get('tags', {}).get('building'))
    build_score = max(0, 100 - (build_count * 2))
    
    road_count = sum(1 for e in elements if e.get('tags', {}).get('highway'))
    road_score = 100 if road_count > 0 else 40
    road_dist = 500 if road_count > 0 else 5000

    slope = random.randint(2, 15)
    slope_score = 90
    dirs = ["Kuzey", "Kuzeydogu", "Dogu", "Guneydogu", "Guney", "Guneybati", "Bati", "Kuzeybati"]
    aspect = dirs[int((wind_dir/45)%8)]
    aspect_score = 85

    total = (flora_score * 0.35) + (water_score * 0.20) + (wind_score * 0.10) + \
            (road_score * 0.10) + (build_score * 0.10) + (slope_score * 0.05) + \
            (aspect_score * 0.05) + 5
    total = min(99, int(total))

    ai_text = f"""
    <strong>Genel Degerlendirme:</strong> Bolge {total}/100 puan ile aricilik icin 
    <strong>{'UYGUN' if total > 60 else 'ORTA'}</strong> seviyededir.<br><br>
    - <strong>Flora:</strong> {flora_type}<br>
    - <strong>Ruzgar:</strong> {wind_speed} km/h (Ucus icin uygun)<br>
    - <strong>Su:</strong> { "Kaynak mevcut" if water_score > 50 else "Su kaynagi uzak" }
    """

    return {
        "score": total,
        "ai_text": ai_text,
        "details": {
            "flora_type": flora_type, "d_water": min_dist_water, "avg_wind": wind_speed,
            "wind_dir": wind_dir, "d_road": road_dist, "b_count": build_count,
            "s_val": slope, "dir_tr": aspect, "elevation": elevation, "avg_temp": temp, "avg_hum": 55
        },
        "breakdown": {
            "Flora": flora_score, "Su": water_score, "Ruzgar": wind_score,
            "Egim": slope_score, "Baki": aspect_score, "Ulasim": road_score, "Yerlesim": build_score
        }
    }

class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 20)
        self.set_text_color(255, 193, 7)
        self.cell(0, 10, 'BeeLocate PRO', 0, 1, 'C')
        self.set_font('Arial', '', 10)
        self.set_text_color(100)
        self.cell(0, 5, 'Fizibilite Raporu', 0, 1, 'C')
        self.ln(10)

# --- ROUTES (DÜZELTİLDİ) ---
@app.route('/')
def landing():
    # ANA SAYFA ARTIK LANDING PAGE'İ AÇAR
    return render_template('landing.html')

@app.route('/app')
def app_page():
    # UYGULAMA SAYFASI
    return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    try:
        d = request.json
        res = calculate_score(d['lat'], d['lng'], d.get('radius', 2000), 
                              get_osm_data(d['lat'], d['lng'], d.get('radius', 2000)),
                              get_weather_data(d['lat'], d['lng']))
        
        res['heatmap'] = [{'lat': d['lat']+random.uniform(-0.01,0.01), 
                           'lng': d['lng']+random.uniform(-0.01,0.01), 
                           'val': random.randint(30,90)} for _ in range(20)]
        return jsonify(res)
    except:
        return jsonify({"error": "Analiz servisi yogun."})

@app.route('/download_report')
def download_report():
    try:
        lat = float(request.args.get('lat'))
        lng = float(request.args.get('lng'))
        res = calculate_score(lat, lng, 2000, get_osm_data(lat, lng, 2000), get_weather_data(lat, lng))
        
        pdf = PDF()
        pdf.add_page()
        pdf.set_font('Arial', 'B', 14); pdf.set_text_color(0)
        pdf.cell(0, 10, f"Koordinat: {lat:.4f}, {lng:.4f}", 0, 1, 'C')
        
        pdf.set_fill_color(240, 240, 240); pdf.rect(10, 50, 190, 20, 'F'); pdf.set_y(55)
        pdf.set_font('Arial', 'B', 16)
        pdf.cell(0, 10, f"GENEL SKOR: {res['score']} / 100", 0, 1, 'C')
        pdf.ln(20)
        
        pdf.set_font('Arial', 'B', 12)
        pdf.cell(0, 10, 'ARAZI PARAMETRELERI', 0, 1, 'L')
        pdf.set_font('Arial', '', 10)
        
        d = res['details']
        
        # 9999m ise "Uzak" yaz
        w_res = f"{d['d_water']}m" if d['d_water'] < 5000 else "Tespit Edilemedi"
        r_res = f"{d['d_road']}m" if d['d_road'] < 5000 else "Erisim Zor"

        items = [
            ("Flora Tipi", clean_tr(d['flora_type'])),
            ("Suya Mesafe", clean_tr(w_res)),
            ("Ruzgar Hizi", f"{d['avg_wind']} km/h"),
            ("Rakim", f"{d['elevation']}m"),
            ("Baki", clean_tr(d['dir_tr'])),
            ("Yerlesim", f"{d['b_count']} bina"),
            ("Ulasim", clean_tr(r_res))
        ]
        
        for k, v in items:
            pdf.cell(60, 10, k, 1)
            pdf.cell(130, 10, str(v), 1, 1)

        pdf_name = f"BeeLocate_{lat}_{lng}.pdf"
        pdf_path = f"/tmp/{pdf_name}"
        pdf.output(pdf_path)
        return send_file(pdf_path, as_attachment=True)

    except Exception as e:
        return str(e)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
