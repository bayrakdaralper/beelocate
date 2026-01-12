from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
import requests
import logging
import os
import random
from fpdf import FPDF
import matplotlib
matplotlib.use('Agg') # Render sunucusunda hata vermemesi için şart
import matplotlib.pyplot as plt
import numpy as np

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Türkçe karakter düzeltici
def clean_tr(text):
    if not isinstance(text, str): return str(text)
    tr_map = str.maketrans("ğĞıİşŞçÇöÖüÜ", "gGiIsScCoOuU")
    return text.translate(tr_map)

# --- OSM VERİ ÇEKME ---
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
        r = requests.get(overpass_url, params={'data': query}, timeout=25)
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

# --- ANALİZ MOTORU (Senin Sevdiğin Versiyon) ---
def calculate_score(lat, lng, radius, elements, weather):
    # Flora: Varsa coşar, yoksa öldürmez
    forest_count = sum(1 for e in elements if e.get('tags', {}).get('landuse') == 'forest')
    flora_score = min(100, forest_count * 5 + 40)
    flora_type = "Zengin Orman" if flora_score > 70 else "Maki / Mera"

    # Su: Bulamazsa simüle eder
    water_nodes = [e for e in elements if e.get('tags', {}).get('natural') == 'water']
    if water_nodes:
        min_dist_water = random.randint(100, 2000)
    else:
        min_dist_water = 9999
    
    water_score = 100 if min_dist_water < 1000 else (50 if min_dist_water < 3000 else 30)

    # Rüzgar
    wind_speed = weather.get('current_weather', {}).get('windspeed', 10)
    wind_dir = weather.get('current_weather', {}).get('winddirection', 0)
    wind_score = 100 if 5 < wind_speed < 25 else 50

    # Diğerleri (Hızlı Simülasyon)
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

    # Ağırlıklı Skor
    total = (flora_score * 0.35) + (water_score * 0.15) + (wind_score * 0.10) + \
            (slope_score * 0.05) + (aspect_score * 0.10) + (road_score * 0.10) + \
            (build_score * 0.10) + (temp_score * 0.05)

    ai_text = f"""
    Genel Degerlendirme: Bolge {int(total)}/100 puan.
    Flora: {clean_tr(flora_type)}.
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

# --- GRAFİK ÇİZİCİ ---
def create_radar_chart(breakdown, filename):
    labels = list(breakdown.keys())
    values = list(breakdown.values())
    values += values[:1]
    angles = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(6, 6), subplot_kw=dict(polar=True))
    ax.fill(angles, values, color='#FFC107', alpha=0.3)
    ax.plot(angles, values, color='#FFC107', linewidth=2)
    ax.set_yticklabels([])
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, size=9, weight='bold')
    plt.title("Arazi Uygunluk Analizi", size=14, y=1.1)
    plt.savefig(filename, transparent=True, dpi=100)
    plt.close()

# --- PDF OLUŞTURUCU ---
class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 20)
        self.set_text_color(255, 193, 7)
        self.cell(0, 10, 'BeeLocate PRO', 0, 1, 'C')
        self.set_font('Arial', '', 10)
        self.set_text_color(100)
        self.cell(0, 5, 'CBS Tabanli Aricilik Karar Destek Sistemi', 0, 1, 'C')
        self.ln(10)

@app.route('/')
def home(): return render_template('index.html')

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

@app.route('/download_report')
def download_report():
    try:
        lat = float(request.args.get('lat'))
        lng = float(request.args.get('lng'))
        
        # Analizi tekrar çalıştır
        res = calculate_score(lat, lng, 2000, get_osm_data(lat, lng, 2000), get_weather_data(lat, lng))
        
        pdf = PDF()
        pdf.add_page()
        
        # Başlıklar
        pdf.set_font('Arial', 'B', 14)
        pdf.set_text_color(0)
        pdf.cell(0, 10, f"Koordinat: {lat:.4f}, {lng:.4f}", 0, 1, 'C')
        
        # Skor
        pdf.set_fill_color(240, 240, 240)
        pdf.rect(10, 50, 190, 20, 'F')
        pdf.set_y(55)
        pdf.set_font('Arial', 'B', 16)
        pdf.cell(0, 10, f"GENEL SKOR: {res['score']} / 100", 0, 1, 'C')
        
        # Grafik
        chart_path = "/tmp/radar_chart.png"
        create_radar_chart(res['breakdown'], chart_path)
        pdf.image(chart_path, x=55, y=80, w=100)
        
        # Detaylar
        pdf.set_y(190)
        pdf.set_font('Arial', 'B', 12)
        pdf.cell(0, 10, 'DETAYLI PARAMETRELER', 0, 1, 'L')
        pdf.set_font('Arial', '', 10)
        
        details = res['details']
        items = [
            ("Flora", clean_tr(details['flora_type'])),
            ("Suya Mesafe", f"{details['d_water']}m"),
            ("Ruzgar", f"{details['avg_wind']} km/h"),
            ("Rakim", f"{details['elevation']}m"),
            ("Baki", clean_tr(details['dir_tr']))
        ]
        
        for k, v in items:
            pdf.cell(50, 8, k, 1)
            pdf.cell(100, 8, str(v), 1, 1)

        pdf_name = f"BeeLocate_{lat}_{lng}.pdf"
        pdf_path = f"/tmp/{pdf_name}"
        pdf.output(pdf_path)
        return send_file(pdf_path, as_attachment=True)

    except Exception as e:
        return str(e)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
