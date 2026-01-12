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

# --- 1. MATEMATİKSEL FONKSİYONLAR ---

def calculate_real_slope_aspect(lat, lng):
    """
    3 NOKTA YÖNTEMİ İLE GERÇEK TOPOGRAFYA ANALİZİ
    (A) Merkez, (B) Kuzey, (C) Doğu noktalarının yükseklikleri çekilir.
    Aradaki farktan matematiksel eğim ve yön (bakı) bulunur.
    """
    try:
        # Open-Meteo Elevation API (Ücretsiz ve Gerçek DEM Verisi)
        # Lat, Lat+0.001 (~111m Kuzey), Lat (Doğu için aynı)
        # Lng, Lng, Lng+0.001 (~85m Doğu)
        
        base_url = "https://api.open-meteo.com/v1/elevation"
        lats = f"{lat},{lat+0.001},{lat}"
        lngs = f"{lng},{lng},{lng+0.001}"
        
        r = requests.get(f"{base_url}?latitude={lats}&longitude={lngs}", timeout=5)
        data = r.json()
        
        if 'elevation' not in data:
            return 0, 0, 0 # Veri çekilemezse 0 dön (Uydurma yok)

        elevs = data['elevation']
        h_center = elevs[0]
        h_north = elevs[1]
        h_east = elevs[2]
        
        # Matematiksel Hesaplama
        # dz_dy: Kuzey-Güney yönündeki eğim (Rise/Run)
        # dz_dx: Doğu-Batı yönündeki eğim
        dz_dy = (h_north - h_center) / 111.0 
        dz_dx = (h_east - h_center) / 85.0
        
        # Eğim (Slope) - Radyan cinsinden dereceye ve yüzdeye çevrilir
        slope_rad = math.atan(math.sqrt(dz_dx**2 + dz_dy**2))
        slope_pct = math.tan(slope_rad) * 100 # Yüzde Eğim
        
        # Bakı (Aspect) - Eğim yüzeyinin baktığı yön (0-360 derece)
        aspect_rad = math.atan2(dz_dy, -dz_dx)
        aspect_deg = math.degrees(aspect_rad)
        
        # Coğrafi yön düzeltmesi (Kuzey 0 olacak şekilde)
        if aspect_deg < 0: aspect_deg += 90
        elif aspect_deg > 90: aspect_deg = 450 - aspect_deg
        else: aspect_deg = 90 - aspect_deg
        
        return int(slope_pct), int(aspect_deg), int(h_center)
        
    except Exception as e:
        print(f"Topografya hatasi: {e}")
        return 0, 0, 0 # Hata durumunda 0. Asla uydurma değer yok.

def get_compass_direction(deg):
    # Dereceyi (0-360) Metne Çevirir
    val = int((deg/22.5) + .5)
    arr = ["Kuzey", "Kuzey-K.Dogu", "Kuzey Dogu", "Dogu-K.Dogu", "Dogu", "Dogu-G.Dogu", "Guney Dogu", "Guney-G.Dogu", "Guney", "Guney-G.Bati", "Guney Bati", "Bati-G.Bati", "Bati", "Bati-K.Bati", "Kuzey Bati", "Kuzey-K.Bati"]
    return arr[(val % 16)]

# --- 2. HARİTA VERİSİ (OSM) ---
def get_osm_data(lat, lng, radius):
    overpass_url = "http://overpass-api.de/api/interpreter"
    # Sadece ve sadece var olan etiketleri çeker.
    query = f"""
    [out:json][timeout:45];
    (
      node["natural"="water"](around:{radius},{lat},{lng});
      way["natural"="water"](around:{radius},{lat},{lng});
      
      node["landuse"~"forest|orchard|farm|meadow|farmland"](around:{radius},{lat},{lng});
      way["landuse"~"forest|orchard|farm|meadow|farmland"](around:{radius},{lat},{lng});
      
      node["natural"~"wood|scrub|heath|grassland"](around:{radius},{lat},{lng});
      way["natural"~"wood|scrub|heath|grassland"](around:{radius},{lat},{lng});
      
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

# --- 3. HAVA DURUMU (GERÇEK) ---
def get_weather(lat, lng):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lng}&current_weather=true"
        r = requests.get(url, timeout=5)
        return r.json().get('current_weather', {}) if r.status_code == 200 else {}
    except:
        return {}

# --- 4. BİLİMSEL PUANLAMA MOTORU ---
def calculate_score(lat, lng, radius, elements, weather, slope, aspect, elevation):
    
    # --- KRİTER 1: FLORA (%35) ---
    # Literatür: Arılar için Orman > Meyvelik > Çayır > Tarla
    flora_score = 0
    flora_list = []
    
    for e in elements:
        t = e.get('tags', {})
        lu = t.get('landuse', '')
        nat = t.get('natural', '')
        
        if lu == 'forest' or nat == 'wood':
            flora_score += 5
            if "Orman" not in flora_list: flora_list.append("Orman")
        elif lu == 'orchard':
            flora_score += 4
            if "Meyvelik" not in flora_list: flora_list.append("Meyvelik")
        elif nat in ['scrub', 'heath', 'grassland'] or lu == 'meadow':
            flora_score += 2
            if "Maki/Cayir" not in flora_list: flora_list.append("Maki/Cayir")
    
    # 50 puana ulaşırsa tam verim kabul ediyoruz (Normalize)
    final_flora_score = min(100, flora_score * 2)
    flora_text = ", ".join(flora_list) if flora_list else "Yetersiz Flora"

    # --- KRİTER 2: SU KAYNAKLARI (%20) ---
    # Gerçek veri yoksa 0 puan.
    water_count = sum(1 for e in elements if e.get('tags', {}).get('natural') == 'water')
    if water_count > 0:
        water_score = 100
        water_text = "Su Kaynagi Mevcut"
    else:
        water_score = 0
        water_text = "Su Kaynagi Yok"

    # --- KRİTER 3: EĞİM (%10) ---
    # Literatür: %2 - %10 arası mükemmel. %30 üzeri arı kovanı konmaz.
    if 2 <= slope <= 10: slope_score = 100
    elif slope < 2: slope_score = 80 # Düz
    elif slope <= 20: slope_score = 60 # İdare eder
    elif slope <= 30: slope_score = 20 # Zorlu
    else: slope_score = 0 # Çok dik, uygunsuz

    # --- KRİTER 4: BAKI (%10) ---
    # Literatür: Güney, Güney Doğu en iyisidir (Sabah güneşi). Kuzey soğuktur.
    # Aspect (0-360 derece). 90=Doğu, 180=Güney, 270=Batı
    if 135 <= aspect <= 225: aspect_score = 100 # Güney Hattı (Mükemmel)
    elif 90 <= aspect < 135 or 225 < aspect <= 270: aspect_score = 70 # Doğu/Batı
    else: aspect_score = 30 # Kuzey Hattı (Soğuk)

    # --- KRİTER 5: RÜZGAR (%15) ---
    # Literatür: 25 km/h üzeri uçuş durur.
    wind_spd = weather.get('windspeed', 0)
    if 0 < wind_spd <= 15: wind_score = 100
    elif wind_spd <= 25: wind_score = 60
    elif wind_spd > 25: wind_score = 0 # Çok rüzgarlı
    else: wind_score = 50 # Veri yoksa veya 0 ise nötr

    # --- KRİTER 6: İNSAN BASKISI & ULAŞIM (%10) ---
    # Bina sayısı arttıkça puan düşer. Yol yoksa puan düşer.
    buildings = sum(1 for e in elements if e.get('tags', {}).get('building'))
    roads = sum(1 for e in elements if e.get('tags', {}).get('highway'))
    
    pressure_score = max(0, 100 - (buildings * 5)) # Bina başı -5 puan
    access_score = 100 if roads > 0 else 0
    
    human_score = (pressure_score * 0.7) + (access_score * 0.3)

    # --- TOPLAM HESAP ---
    total = (final_flora_score * 0.35) + \
            (water_score * 0.20) + \
            (wind_score * 0.15) + \
            (slope_score * 0.10) + \
            (aspect_score * 0.10) + \
            (human_score * 0.10)

    ai_text = f"""
    <strong>Bilimsel Analiz:</strong> {int(total)}/100<br>
    <strong>Flora:</strong> {flora_text}<br>
    <strong>Su:</strong> {water_text}<br>
    <strong>Arazi:</strong> %{slope} Eğim, {get_compass_direction(aspect)} Bakı
    """

    return {
        "score": int(total),
        "ai_text": ai_text,
        "details": {
            "flora_type": flora_text,
            "d_water": 0 if water_score == 100 else 9999,
            "avg_wind": wind_spd, "wind_dir": 0,
            "d_road": 0 if roads > 0 else 9999,
            "b_count": buildings,
            "s_val": slope,
            "dir_tr": get_compass_direction(aspect),
            "elevation": int(elevation),
            "avg_temp": weather.get('temperature', 0),
            "avg_hum": 50
        },
        "breakdown": {
            "Flora": final_flora_score, "Su": water_score, "Ruzgar": wind_score,
            "Egim": slope_score, "Baki": aspect_score, "Konum": int(human_score)
        }
    }

class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 20)
        self.set_text_color(255, 193, 7)
        self.cell(0, 10, 'BeeLocate PRO', 0, 1, 'C')
        self.set_font('Arial', '', 10)
        self.set_text_color(100)
        self.cell(0, 5, 'Bilimsel Fizibilite Raporu', 0, 1, 'C')
        self.ln(10)

@app.route('/')
def landing(): return render_template('landing.html')

@app.route('/app')
def app_page(): return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    try:
        d = request.json
        # 1. Adım: Verileri Çek
        elements = get_osm_data(d['lat'], d['lng'], d.get('radius', 2000))
        weather = get_weather(d['lat'], d['lng'])
        slope, aspect, elevation = calculate_real_slope_aspect(d['lat'], d['lng'])
        
        # 2. Adım: Hesapla
        res = calculate_score(d['lat'], d['lng'], 2000, elements, weather, slope, aspect, elevation)
        
        # Heatmap sadece görsel efekt
        res['heatmap'] = [{'lat': d['lat']+random.uniform(-0.01,0.01), 
                           'lng': d['lng']+random.uniform(-0.01,0.01), 
                           'val': random.randint(30,90)} for _ in range(20)]
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/download_report')
def download_report():
    try:
        lat = float(request.args.get('lat'))
        lng = float(request.args.get('lng'))
        
        elements = get_osm_data(lat, lng, 2000)
        weather = get_weather(lat, lng)
        slope, aspect, elevation = calculate_real_slope_aspect(lat, lng)
        
        res = calculate_score(lat, lng, 2000, elements, weather, slope, aspect, elevation)
        
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
            f"Su Kaynagi: {clean_tr('Mevcut' if d['d_water'] == 0 else 'Yok')}",
            f"Ruzgar: {d['avg_wind']} km/h",
            f"Rakim: {d['elevation']}m",
            f"Egim: %{d['s_val']}",
            f"Baki: {clean_tr(d['dir_tr'])}",
            f"Bina: {d['b_count']} adet"
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
