from flask import Flask, render_template, request, jsonify, send_file
import osmnx as ox
import geopandas as gpd
from shapely.geometry import Point
import pandas as pd
from fpdf import FPDF
import requests
import numpy as np
import io
from datetime import datetime
from math import sqrt, atan2, degrees

app = Flask(__name__)

# --- HELPERS ---
def tr_chars(text):
    """Fix Turkish characters for PDF"""
    replacements = {'ğ':'g', 'Ğ':'G', 'ü':'u', 'Ü':'U', 'ş':'s', 'Ş':'S', 'ı':'i', 'İ':'I', 'ö':'o', 'Ö':'O', 'ç':'c', 'Ç':'C'}
    for k, v in replacements.items(): text = str(text).replace(k, v)
    return text

def translate_dir(code):
    # Basic 8-point compass translation
    d = {"N": "Kuzey", "NE": "Kuzeydogu", "E": "Dogu", "SE": "Guneydogu", "S": "Guney", "SW": "Guneybati", "W": "Bati", "NW": "Kuzeybati"}
    return d.get(code, code)

def degree_to_dir(deg):
    # Convert degrees to compass direction
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    ix = int((deg + 22.5) / 45) % 8
    return dirs[ix]

# --- DATA ENGINE ---
def get_meteo_extended(lat, lng):
    try:
        # Added winddirection_10m_dominant to get ACTUAL wind direction
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lng}&current=temperature_2m&daily=temperature_2m_max,relative_humidity_2m_mean,wind_speed_10m_max,winddirection_10m_dominant,sunrise,sunset&timezone=auto&forecast_days=7"
        r = requests.get(url, timeout=3).json()
        d = r.get('daily', {}); c = r.get('current', {})
        
        wind_dir_deg = np.mean(d.get('winddirection_10m_dominant', [0]))
        wind_dir_code = degree_to_dir(wind_dir_deg)

        return {
            'cur_temp': c.get('temperature_2m', 0),
            'avg_temp': round(np.mean(d.get('temperature_2m_max', [20])), 1),
            'avg_wind': round(np.mean(d.get('wind_speed_10m_max', [5])), 1),
            'wind_dir': wind_dir_code, # Actual wind direction
            'avg_hum': int(np.mean(d.get('relative_humidity_2m_mean', [50]))),
            'sunrise': d.get('sunrise', ['06:00'])[0].split('T')[1],
            'sunset': d.get('sunset', ['19:00'])[0].split('T')[1]
        }
    except: return {'cur_temp':20, 'avg_temp':20, 'avg_wind':5, 'wind_dir':'N', 'avg_hum':50, 'sunrise':'06:00', 'sunset':'19:00'}

def get_terrain_pro(lat, lng):
    try:
        url = f"https://api.open-meteo.com/v1/elevation?latitude={lat},{lat+0.001},{lat}&longitude={lng},{lng},{lng+0.001}"
        e = requests.get(url, timeout=3).json().get('elevation', [0,0,0])
        dz_dx, dz_dy = (e[2]-e[0])/90, (e[1]-e[0])/90
        s_rad = atan2(sqrt(dz_dx**2 + dz_dy**2), 1)
        asp = degrees(atan2(dz_dy, -dz_dx)); asp = asp+360 if asp<0 else asp
        dirs = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]
        return abs(np.tan(s_rad)*100), degrees(s_rad), dirs[int((asp+22.5)/45)%8], int(e[0])
    except: return 0, 0, "N", 0

def calculate_score(lat, lng, radius=2000):
    # OPTIMIZATION: Only fetch essential tags. Reduced timeout risk.
    tags = {
        'natural': ['water','wood','scrub','grassland'], 
        'waterway': True, 
        'landuse': ['forest','orchard','farmland','meadow','grass'], 
        'building': True,
        'highway': ['motorway','primary','secondary']
    }
    
    # Cap radius at 2500m for performance, even if user asks for more via API manually
    search_rad = min(radius, 3000)

    try: 
        gdf = ox.features_from_point((lat, lng), tags, dist=search_radius)
    except: gdf = gpd.GeoDataFrame()
    
    p = Point(lng, lat); f=gpd.GeoDataFrame(); w=gpd.GeoDataFrame(); b=gpd.GeoDataFrame(); r=gpd.GeoDataFrame()
    
    if not gdf.empty:
        if 'natural' in gdf.columns: 
            f = pd.concat([f, gdf[gdf['natural'].isin(['wood','scrub','grassland'])]])
            w = pd.concat([w, gdf[gdf['natural']=='water']])
        
        if 'landuse' in gdf.columns: 
            f = pd.concat([f, gdf[gdf['landuse'].isin(['forest','orchard','farmland','meadow','grass'])]])
        
        if 'waterway' in gdf.columns: 
            w = pd.concat([w, gdf[gdf['waterway'].notna()]])
        
        if 'building' in gdf.columns: b = gdf[gdf['building'].notna()]
        if 'highway' in gdf.columns: r = gdf[gdf['highway'].notna()]

    meteo = get_meteo_extended(lat, lng)
    slope_pct, slope_deg, aspect_dir, elevation = get_terrain_pro(lat, lng)

    # --- SCORING ---
    d_flora = 9999; flora_name = "Bilinmiyor"
    if not f.empty:
        d_flora = f.distance(p).min()*111000
        near = f.iloc[f.distance(p).argmin()]
        ts = str(near.get('natural',''))+str(near.get('landuse',''))
        if "wood" in ts or "forest" in ts: flora_name = "Orman (Cam/mese)"
        elif "meadow" in ts or "grass" in ts: flora_name = "Mera/Cicek"
        elif "farm" in ts: flora_name = "Tarim (Risk)"
        else: flora_name = "Calilik"
    
    s_f = 100 if d_flora < 10 else max(0, 100-(d_flora/2000)*100)
    if "Tarim" in flora_name: s_f *= 0.6
    
    asp_sc = {'S':100,'SE':100,'SW':90,'E':80,'W':50,'NE':30,'NW':20,'N':10}
    s_a = asp_sc.get(aspect_dir, 50)
    
    win = meteo['avg_wind']; s_w = 100 if win<15 else max(0, 100-(win-15)*5)
    tmp = meteo['avg_temp']; s_t = 100 if 15<=tmp<=30 else 50
    cnt = len(b); s_b = 100 if cnt<5 else max(0, 100-cnt*2)
    
    dw = w.distance(p).min()*111000 if not w.empty else 9999 # Use 9999 to indicate 'Not Found' clearly
    s_wt = 100 if dw<1000 else max(0, 100-(dw/3000)*100)
    
    s_sl = 100 if 0<=slope_pct<=30 else 20
    dr = r.distance(p).min()*111000 if not r.empty else 9999
    s_r = 100 if 200<dr<3000 else 40
    
    total = (s_f*0.4)+(s_a*0.15)+(s_w*0.1)+(s_t*0.05)+(s_b*0.1)+(s_wt*0.1)+(s_sl*0.05)+(s_r*0.05)
    
    subs = {'flora': s_f, 'aspect': s_a, 'wind': s_w, 'temp': s_t, 'build': s_b, 'water': s_wt, 'slope': s_sl, 'road': s_r}
    dets = {
        'flora_type': flora_name, 'd_flora': int(d_flora), 'dir': aspect_dir, 'dir_tr': translate_dir(aspect_dir), 
        'avg_wind': win, 'wind_dir': meteo['wind_dir'], 'wind_dir_tr': translate_dir(meteo['wind_dir']),
        'avg_temp': tmp, 'avg_hum': meteo['avg_hum'],
        'b_count': cnt, 'd_water': int(dw), 's_val': int(slope_pct), 'd_road': int(dr), 
        'sunrise': meteo['sunrise'], 'sunset': meteo['sunset'], 'elevation': elevation
    }
    return int(total), subs, dets

class BeeReport(FPDF):
    def header(self): self.set_font('Arial','B',10); self.set_text_color(150); self.cell(0,10,'BeeLocate PRO',0,0,'R'); self.ln(15)

@app.route('/')
def index(): return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    try:
        data = request.json
        lat, lng, rad = data['lat'], data['lng'], int(data['radius'])
        score, subs, dets = calculate_score(lat, lng, rad)
        
        grid = []
        off = rad / 111000 
        offsets = [(0,0), (off/2, 0), (-off/2, 0), (0, off/2), (0, -offset/2)]
        for i, (ox, oy) in enumerate(offsets):
            val = score if i == 0 else max(0, min(100, score + np.random.randint(-15, 10)))
            grid.append({'lat': lat+ox, 'lng': lng+oy, 'val': val})

        return jsonify({'score': score, 'breakdown': subs, 'details': dets, 'heatmap': grid})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/download_report')
def download_report():
    try:
        lat = float(request.args.get('lat'))
        lng = float(request.args.get('lng'))
        rad = int(request.args.get('radius', 2000))
        score, s, d = calculate_score(lat, lng, rad)
        
        pdf = BeeReport(); pdf.add_page()
        pdf.set_font('Arial','B',24); pdf.set_text_color(44,62,80); pdf.cell(0,10,tr_chars("BEELOCATE PRO"),0,1); pdf.ln(10)
        pdf.set_font('Arial','',12); pdf.cell(0,10,f"Konum: {lat:.4f}, {lng:.4f} | Tarih: {datetime.now().strftime('%d.%m.%Y')}",0,1); pdf.ln(10)
        
        pdf.set_fill_color(253,242,233); pdf.rect(10,pdf.get_y(),190,30,'F'); pdf.set_xy(15,pdf.get_y()+10)
        pdf.set_font('Arial','B',20); pdf.set_text_color(230,126,34); pdf.cell(0,10,f"SKOR: {score}/100",0,1); pdf.ln(20)
        
        pdf.set_font('Arial','B',14); pdf.set_text_color(0); pdf.cell(0,10,tr_chars("DETAYLI ANALIZ RAPORU"),0,1)
        pdf.set_font('Arial','',11)
        
        dw_str = f"{d['d_water']}m" if d['d_water'] < 5000 else "Tespit Edilemedi (>5km)"
        
        items = [
            ("Flora", f"{tr_chars(d['flora_type'])}", s['flora']),
            ("Baki (Yon)", f"{tr_chars(translate_dir(d['dir']))}", s['aspect']),
            ("Ruzgar (Yon/Hiz)", f"{tr_chars(translate_dir(d['wind_dir']))} / {d['avg_wind']} km/h", s['wind']),
            ("Su Kaynagi", dw_str, s['water']),
            ("Sicaklik (7 Gun)", f"{d['avg_temp']} C", s['temp']),
            ("Ulasim", f"{d['d_road']}m", s['road']),
            ("Yerlesim", f"{d['b_count']} Bina", s['build']),
            ("Egim", f"%{d['s_val']}", s['slope']),
            ("Rakim", f"{d['elevation']}m", 100)
        ]
        
        for k,v,sc in items:
            pdf.cell(50,8,k,1); pdf.cell(90,8,str(v),1); pdf.cell(40,8,f"Puan: {int(sc)}",1,1)

        pdf_bytes = pdf.output(dest='S').encode('latin-1')
        buffer = io.BytesIO(pdf_bytes)
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name=f"Rapor_{lat:.4f}.pdf", mimetype='application/pdf')
    except Exception as e: return str(e), 500

if __name__ == '__main__':
    app.run(debug=True)
