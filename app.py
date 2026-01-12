from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
import requests
import logging
import math
import random
from fpdf import FPDF

app = Flask(__name__)
CORS(app)

# Türkçe karakter düzeltici
def clean_tr(text):
    if not isinstance(text, str): return str(text)
    tr_map = str.maketrans("ğĞıİşŞçÇöÖüÜ", "gGiIsScCoOuU")
    return text.translate(tr_map)

# --- 1. GERÇEK OSM VERİSİ ---
def get_osm_data(lat, lng, radius):
    overpass_url = "http://overpass-api.de/api/interpreter"
    # Timeout'u 45 saniyeye çıkardık ki veri gelmeden pes etmesin
    query = f"""
    [out:json][timeout:45];
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
        r = requests.get(overpass_url, params={'data': query}, timeout=45)
        if r.status_code == 200:
            return r.json().get('elements', [])
        return []
    except:
        return []

# --- 2. GERÇEK HAVA DURUMU ---
def get_weather_data(lat, lng):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lng}&current_weather=true&elevation=true"
        r = requests.get(url, timeout=10)
        return r.json() if r.status_code == 200 else {}
    except:
        return {}

# --- 3. ANALİZ MOTORU (Rastgelelik YOK) ---
def calculate_score(lat, lng, radius, elements, weather):
    # A. FLORA (Gerçek Sayım)
    # Listede kaç tane orman/ağaçlık objesi varsa sayar.
    forest_objects = [e for e in elements if e.get('tags', {}).get('landuse') == 'forest' or e.get('tags', {}).get('natural') == 'wood']
    forest_count = len(forest_objects)
    
    if forest_count > 50:
        flora_score = 100
        flora_type = "Zengin Orman (Yogun)"
    elif forest_count > 10:
        flora_score = 70
        flora_type = "Orta Seviye Flora"
    elif forest_count > 0:
        flora_score = 40
        flora_type = "Seyrek Agaclik"
    else:
        flora_score = 10
        flora_type = "Yetersiz / Ciplak Arazi"

    # B. SU (Gerçek Varlık)
    water_objects = [e for e in elements if e.get('tags', {}).get('natural') == 'water']
    if len(water_objects) > 0:
        # Su objesi bulunduysa tam puan
        water_score = 100
        water_dist_text = "< 2000m (Mevcut)"
    else:
        # Bulunamadıysa düşük puan
        water_score = 20
        water_dist_text = "Tespit Edilemedi"

    # C. RÜZGAR (Gerçek Veri)
    wind_speed = weather.get('current_weather', {}).get('windspeed', 0)
    wind_dir = weather.get('current_weather', {}).get('winddirection', 0)
    
    if 0 < wind_speed <= 25:
        wind_score = 100
    elif wind_speed == 0:
        wind_score = 50 # Veri yoksa veya rüzgar yoksa nötr
    else:
        wind_score = 40 # Çok rüzgarlı

    # D. DİĞER (Rakım, Bina, Yol)
    elevation = weather.get('elevation', 0)
    temp = weather.get('current_weather', {}).get('temperature', 0)
    
    # Bina Sayısı
    buildings = [e for e in elements if e.get('tags', {}).get('building')]
    b_count = len(buildings)
    build_score = max(0, 100 - (b_count * 2)) # Çok bina = Düşük puan
    
    # Yol Varlığı
    roads = [e for e in elements if e.get('tags', {}).get('highway')]
    road_score = 100 if len(roads) > 0 else 30 # Yol yoksa ulaşım zor

    # Eğim (Basit Yaklaşım - API Hatasına karşı sabit nötr değer)
    # Burada random kullanmıyoruz, herkese eşit 80 veriyoruz ki analiz sapmasın.
    slope_score = 80 
    slope_val = "Makul"

    # SKORLAMA (Ağırlıklı)
    total = (flora_score * 0.40) + (water_score * 0.20) + (wind_score * 0.10) + \
            (road_score * 0.10) + (build_score * 0.10) + (slope_score * 0.10)

    total = int(total)

    # Yön Çeviri
    dirs = ["Kuzey", "KD", "Dogu", "GD", "Guney", "GB", "Bati", "KB"]
    aspect_txt = dirs[int((wind_dir/45)%8)]

    ai_text = f"""
    <strong>Analiz Sonucu:</strong> {total}/100 Puan.<br>
    <strong>Flora:</strong> {flora_type} ({forest_count} nokta).<br>
    <strong>Su Durumu:</strong> {water_dist_text}.<br>
    <strong>Ruzgar:</strong> {wind_speed} km/h.
    """

    return {
        "score": total,
        "ai_text": ai_text,
        "details": {
            "flora_type": flora_type, "d_water": 0 if water_score==100 else 9999, 
            "avg_wind": wind_speed, "wind_dir": wind_dir, "d_road": 0 if road_score==100 else 9999, 
            "b_count": b_count, "s_val": 5, "dir_tr": aspect_txt, 
            "elevation": elevation, "avg_temp": temp, "avg_hum": 50
        },
        "breakdown": {
            "Flora": flora_score, "Su": water_score, "Ruzgar": wind_score,
            "Ulasim": road_score, "Baski": build_score
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

# ROTALAR (Landing Page Düzeltmesi)
@app.route('/')
def landing():
    return render_template('landing.html')

@app.route('/app')
def app_page():
    return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    try:
        d = request.json
        res = calculate_score(d['lat'], d['lng'], d.get('radius', 2000), 
                              get_osm_data(d['lat'], d['lng'], d.get('radius', 2000)),
                              get_weather_data(d['lat'], d['lng']))
        
        # Sadece görsellik için heatmap (analize etkisi yok)
        res['heatmap'] = [{'lat': d['lat']+random.uniform(-0.01,0.01), 
                           'lng': d['lng']+random.uniform(-0.01,0.01), 
                           'val': random.randint(30,90)} for _ in range(20)]
        return jsonify(res)
    except:
        return jsonify({"error": "Analiz servisi yanıt vermiyor."})

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
        items = [
            ("Flora", clean_tr(d['flora_type'])),
            ("Ruzgar", f"{d['avg_wind']} km/h"),
            ("Rakim", f"{d['elevation']}m"),
            ("Bina Sayisi", f"{d['b_count']}"),
            ("Sicaklik", f"{d['avg_temp']} C")
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
