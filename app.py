from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
import requests
import logging
import math
import random
from fpdf import FPDF
from datetime import datetime

app = Flask(__name__)
CORS(app)

# Türkçe karakter düzeltici
def clean_tr(text):
    if not isinstance(text, str): return str(text)
    tr_map = str.maketrans("ğĞıİşŞçÇöÖüÜ", "gGiIsScCoOuU")
    return text.translate(tr_map)

# --- 1. MATEMATİKSEL FONKSİYONLAR ---

def get_distance(lat1, lon1, lat2, lon2):
    # Haversine Formülü: Kuş uçuşu gerçek mesafe
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def calculate_real_topography(lat, lng):
    # 3-Nokta Yöntemi: Merkez, Kuzey ve Doğu'nun rakımlarından Eğim/Bakı hesapla
    try:
        base_url = "https://api.open-meteo.com/v1/elevation"
        lats = f"{lat},{lat+0.001},{lat}"
        lngs = f"{lng},{lng},{lng+0.001}"
        
        r = requests.get(f"{base_url}?latitude={lats}&longitude={lngs}", timeout=5)
        elevs = r.json().get('elevation', [])
        
        if len(elevs) < 3: return 0, 0, 800
        
        h0, h_north, h_east = elevs[0], elevs[1], elevs[2]
        
        dz_dy = (h_north - h0) / 111.0 
        dz_dx = (h_east - h0) / 85.0
        
        slope_rad = math.atan(math.sqrt(dz_dx**2 + dz_dy**2))
        slope_pct = math.tan(slope_rad) * 100
        
        aspect_rad = math.atan2(dz_dy, -dz_dx)
        aspect_deg = math.degrees(aspect_rad)
        if aspect_deg < 0: aspect_deg += 360
        
        return int(slope_pct), int(aspect_deg), int(h0)
    except:
        return 0, 0, 0

def get_compass_direction(deg):
    arr = ["Kuzey", "KD", "Dogu", "GD", "Guney", "GB", "Bati", "KB"]
    return arr[int((deg/45)%8)]

# --- 2. VERİ TOPLAMA (NEM DAHİL) ---

def get_osm_data(lat, lng, radius):
    overpass_url = "http://overpass-api.de/api/interpreter"
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
    try:
        r = requests.get(overpass_url, params={'data': query}, timeout=45)
        return r.json().get('elements', []) if r.status_code == 200 else []
    except:
        return []

def get_full_weather(lat, lng):
    # Sıcaklık, Rüzgar VE NEM verisini çeker
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lng}&current_weather=true&hourly=relativehumidity_2m"
        r = requests.get(url, timeout=5)
        data = r.json()
        
        current = data.get('current_weather', {})
        # Nem verisi saatlik dizide gelir, o anki saati alırız
        humidity = 50 # Varsayılan
        if 'hourly' in data:
            # Basitçe ilk saatin nemini al (Yaklaşık değer)
            humidity = data['hourly']['relativehumidity_2m'][0]
            
        return current, humidity
    except:
        return {}, 50

# --- 3. ANALİZ MOTORU ---

def calculate_score(lat, lng, radius, elements, weather, humidity, slope, aspect, elevation):
    
    # 1. FLORA
    flora_score = 0
    flora_types = set()
    for e in elements:
        t = e.get('tags', {})
        if t.get('landuse') == 'forest' or t.get('natural') == 'wood':
            flora_score += 5; flora_types.add("Orman")
        elif t.get('landuse') == 'orchard':
            flora_score += 4; flora_types.add("Meyvelik")
        elif t.get('natural') in ['scrub', 'heath'] or t.get('landuse') == 'meadow':
            flora_score += 2; flora_types.add("Maki/Cayir")
            
    final_flora = min(100, flora_score * 2)
    flora_txt = ", ".join(list(flora_types)) if flora_types else "Verimsiz"

    # 2. SU (Gerçek Mesafe)
    water_nodes = [e for e in elements if e.get('tags', {}).get('natural') == 'water']
    min_dist = 9999
    if water_nodes:
        for w in water_nodes:
            w_lat = w.get('lat') or w.get('center', {}).get('lat')
            w_lon = w.get('lon') or w.get('center', {}).get('lon')
            if w_lat:
                d = get_distance(lat, lng, w_lat, w_lon)
                if d < min_dist: min_dist = d
    
    if min_dist < 500: water_score = 100
    elif min_dist < 2000: water_score = 60
    else: water_score = 0

    # 3. İKLİM (Rüzgar & Nem & Sıcaklık)
    wind = weather.get('windspeed', 0)
    temp = weather.get('temperature', 0)
    
    # Rüzgar: 25 km/h üstü kötü
    wind_score = 100 if wind < 20 else (50 if wind < 30 else 0)
    
    # Nem: %40-%70 arası ideal
    hum_score = 100 if 40 <= humidity <= 70 else 50

    # 4. ARAZİ (Eğim & Bakı)
    # Eğim %2-%10 arası süper
    slope_score = 100 if 2 <= slope <= 10 else (50 if slope < 20 else 10)
    
    # Bakı Güney ise süper
    aspect_score = 100 if 135 <= aspect <= 225 else 60

    # 5. BASKI
    buildings = sum(1 for e in elements if e.get('tags', {}).get('building'))
    pressure_score = max(0, 100 - (buildings * 5))

    # HESAP
    total = (final_flora * 0.30) + (water_score * 0.20) + (wind_score * 0.10) + \
            (slope_score * 0.10) + (aspect_score * 0.10) + (pressure_score * 0.10) + (hum_score * 0.10)

    ai_text = f"""
    <strong>Bilimsel Rapor:</strong> {int(total)}/100<br>
    <strong>Flora:</strong> {flora_txt}<br>
    <strong>Su:</strong> {int(min_dist) if min_dist < 9999 else 'Yok'}m<br>
    <strong>Nem:</strong> %{humidity} (Anlik)<br>
    <strong>Arazi:</strong> %{slope} Egim, {get_compass_direction(aspect)} Baki
    """

    return {
        "score": int(total),
        "ai_text": ai_text,
        "details": {
            "flora_type": flora_txt, "d_water": int(min_dist) if min_dist < 9999 else 0,
            "avg_wind": wind, "wind_dir": 0, "d_road": 0, "b_count": buildings,
            "s_val": slope, "dir_tr": get_compass_direction(aspect),
            "elevation": elevation, "avg_temp": temp, "avg_hum": humidity
        },
        "breakdown": {
            "Flora": final_flora, "Su": water_score, "Ruzgar": wind_score,
            "Egim": slope_score, "Baki": aspect_score, "Nem": hum_score, "Baski": pressure_score
        }
    }

class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 20); self.set_text_color(255, 193, 7)
        self.cell(0, 10, 'BeeLocate PRO', 0, 1, 'C')
        self.set_font('Arial', '', 10); self.set_text_color(100)
        self.cell(0, 5, 'Bilimsel Saha Analiz Raporu', 0, 1, 'C'); self.ln(10)

# ROTALAR
@app.route('/')
def landing(): return render_template('landing.html')

@app.route('/app')
def app_page(): return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    try:
        d = request.json
        elements = get_osm_data(d['lat'], d['lng'], 2000)
        weather, humidity = get_full_weather(d['lat'], d['lng'])
        slope, aspect, elevation = calculate_real_topography(d['lat'], d['lng'])
        
        res = calculate_score(d['lat'], d['lng'], 2000, elements, weather, humidity, slope, aspect, elevation)
        
        # Heatmap görseldir
        res['heatmap'] = [{'lat': d['lat']+random.uniform(-0.01,0.01), 'lng': d['lng']+random.uniform(-0.01,0.01), 'val': random.randint(30,90)} for _ in range(20)]
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/download_report')
def download_report():
    try:
        lat = float(request.args.get('lat'))
        lng = float(request.args.get('lng'))
        
        elements = get_osm_data(lat, lng, 2000)
        weather, humidity = get_full_weather(lat, lng)
        slope, aspect, elevation = calculate_real_topography(lat, lng)
        
        res = calculate_score(lat, lng, 2000, elements, weather, humidity, slope, aspect, elevation)
        
        pdf = PDF()
        pdf.add_page()
        pdf.set_font('Arial', 'B', 14); pdf.set_text_color(0)
        pdf.cell(0, 10, f"Koordinat: {lat:.4f}, {lng:.4f}", 0, 1, 'C'); pdf.ln(10)
        
        pdf.set_font('Arial', '', 12)
        d = res['details']
        lines = [
            f"Genel Puan: {res['score']}/100",
            f"Bitki Ortusu: {clean_tr(d['flora_type'])}",
            f"Suya Mesafe: {d['d_water']}m",
            f"Nem Orani: %{d['avg_hum']}",
            f"Ruzgar: {d['avg_wind']} km/h",
            f"Rakim: {d['elevation']}m",
            f"Egim: %{d['s_val']}",
            f"Baki: {clean_tr(d['dir_tr'])}"
        ]
        for line in lines: pdf.cell(0, 10, line, 1, 1)
            
        pdf_name = f"BeeLocate_{lat}_{lng}.pdf"
        pdf_path = f"/tmp/{pdf_name}"
        pdf.output(pdf_path)
        return send_file(pdf_path, as_attachment=True)
    except: return "Rapor hatasi"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
