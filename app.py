from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
import requests
import logging
import os
import random # Sadece heatmap görseli için, analiz için değil
from fpdf import FPDF

# Loglama
logging.basicConfig(level=logging.DEBUG)
app = Flask(__name__)
CORS(app)

# Türkçe Karakter Düzeltici
def clean_tr(text):
    if not isinstance(text, str): return str(text)
    tr_map = str.maketrans("ğĞıİşŞçÇöÖüÜ", "gGiIsScCoOuU")
    return text.translate(tr_map)

# --- 1. GERÇEK VERİ ÇEKME (11 Ocak Mantığı) ---
def get_osm_data(lat, lng, radius):
    overpass_url = "http://overpass-api.de/api/interpreter"
    # Sorgu basit ve nettir. Sadece gerekli olanları çağırır.
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

def get_weather_data(lat, lng):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lng}&current_weather=true&elevation=true"
        r = requests.get(url, timeout=5)
        return r.json() if r.status_code == 200 else {}
    except:
        return {}

# --- 2. ANALİZ MOTORU (Sade ve Gerçek) ---
def calculate_score(lat, lng, radius, elements, weather):
    
    # A. FLORA
    # Sadece orman ve ağaçlık alanları sayar
    forest_count = sum(1 for e in elements if e.get('tags', {}).get('landuse') == 'forest' or e.get('tags', {}).get('natural') == 'wood')
    
    if forest_count > 50:
        flora_score = 100
        flora_type = "Zengin Orman"
    elif forest_count > 10:
        flora_score = 70
        flora_type = "Orta Seviye Flora"
    elif forest_count > 0:
        flora_score = 40
        flora_type = "Seyrek Agaclik"
    else:
        flora_score = 10
        flora_type = "Ciliza / Bos Arazi"

    # B. SU
    # Su etiketi olan bir şey var mı bakar
    water_nodes = [e for e in elements if e.get('tags', {}).get('natural') == 'water']
    if len(water_nodes) > 0:
        water_score = 100
        water_txt = "Su Kaynagi Mevcut"
        dist_w = 500 # Temsili yakın mesafe
    else:
        water_score = 20
        water_txt = "Su Tespit Edilemedi"
        dist_w = 9999

    # C. RÜZGAR (Gerçek)
    wind_speed = weather.get('current_weather', {}).get('windspeed', 0)
    wind_dir = weather.get('current_weather', {}).get('winddirection', 0)
    
    if 0 < wind_speed <= 25:
        wind_score = 100
    else:
        wind_score = 50

    # D. DİĞERLERİ
    elevation = weather.get('elevation', 800)
    temp = weather.get('current_weather', {}).get('temperature', 20)
    
    # Bina (Gerçek)
    build_count = sum(1 for e in elements if e.get('tags', {}).get('building'))
    build_score = max(0, 100 - (build_count * 2))

    # Eğim & Bakı (Hata vermemesi için Standart Değer)
    # 11'indeki versiyonda burası API'ye bağlı değildi, standart kabul ediliyordu.
    slope_score = 80
    aspect_score = 80
    
    # Yön Text
    dirs = ["Kuzey", "KD", "Dogu", "GD", "Guney", "GB", "Bati", "KB"]
    dir_txt = dirs[int((wind_dir/45)%8)]

    # TOPLAM SKOR
    total = (flora_score * 0.40) + \
            (water_score * 0.20) + \
            (wind_score * 0.10) + \
            (build_score * 0.10) + \
            (slope_score * 0.10) + \
            (aspect_score * 0.10)

    total = int(total)

    ai_text = f"""
    <strong>Analiz Sonucu:</strong> {total}/100 Puan.<br>
    <strong>Flora:</strong> {flora_type} ({forest_count} veri).<br>
    <strong>Su:</strong> {water_txt}.<br>
    <strong>Ruzgar:</strong> {wind_speed} km/h.
    """

    return {
        "score": total,
        "ai_text": ai_text,
        "details": {
            "flora_type": flora_type, 
            "d_water": dist_w, 
            "avg_wind": wind_speed, 
            "wind_dir": wind_dir, 
            "d_road": 0, 
            "b_count": build_count, 
            "s_val": 5, 
            "dir_tr": dir_txt, 
            "elevation": elevation, 
            "avg_temp": temp, 
            "avg_hum": 50
        },
        "breakdown": {
            "Flora": flora_score, "Su": water_score, "Ruzgar": wind_score,
            "Egim": slope_score, "Baki": aspect_score, "Yerlesim": build_score
        }
    }

class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 20); self.set_text_color(255, 193, 7)
        self.cell(0, 10, 'BeeLocate PRO', 0, 1, 'C')
        self.set_font('Arial', '', 10); self.set_text_color(100)
        self.cell(0, 5, 'Fizibilite Raporu', 0, 1, 'C'); self.ln(10)

@app.route('/')
def landing(): return render_template('landing.html')

@app.route('/app')
def app_page(): return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    try:
        d = request.json
        # 11 Ocak usulü: Veriyi çek, hesapla.
        res = calculate_score(d['lat'], d['lng'], d.get('radius', 2000), 
                              get_osm_data(d['lat'], d['lng'], d.get('radius', 2000)),
                              get_weather_data(d['lat'], d['lng']))
        
        # Heatmap görsel amaçlıdır
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
        
        pdf.set_font('Arial', '', 12)
        d = res['details']
        items = [
            f"Flora: {clean_tr(d['flora_type'])}",
            f"Su Durumu: {clean_tr('Mevcut' if d['d_water'] < 2000 else 'Yok')}",
            f"Ruzgar: {d['avg_wind']} km/h",
            f"Rakim: {d['elevation']}m",
            f"Bina Sayisi: {d['b_count']}"
        ]
        
        for item in items:
            pdf.cell(0, 10, item, 1, 1)
            
        pdf_name = f"BeeLocate_{lat}_{lng}.pdf"
        pdf_path = f"/tmp/{pdf_name}"
        pdf.output(pdf_path)
        return send_file(pdf_path, as_attachment=True)
    except Exception as e:
        return str(e)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
