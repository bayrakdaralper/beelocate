import os
import json
import re

# 1. Update tr.json for proper Beekeeping / GIS terms
tr_path = "translations/tr.json"
with open(tr_path, 'r', encoding='utf-8') as f:
    tr = json.load(f)

# Fix mapbox / reporting terms specific to agriculture
tr["index"]["basemap_sat_labels"] = "Uydu + Etiketler"
tr["index"]["basemap_sat"] = "Uydu Görünümü"
tr["index"]["basemap_map"] = "Standart Harita"
tr["index"]["basemap_osm"] = "Açık Harita (OSM)"

tr["report"]["header_desc"] = "Arıcılık sahası değerlendirmesi için uydu destekli karar destek raporu."

# Technical API error messages
tr["api"] = {
    "sys_error": "Sistem Hatası",
    "err_flora": "Hata",
    "err_water": "Hata",
    "err_urban": "Bilinmiyor",
    "err_transport": "Hata",
    "err_precip": "Hata",
    "err_settlement": "Hata",
    "api_error": "API Hatası",
    "please_select_location": "Lütfen haritadan bir konum seçin.",
    "analyzing": "Analiz ediliyor...",
    "service_unavailable": "HİZMET DIŞI"
}

with open(tr_path, 'w', encoding='utf-8') as f:
    json.dump(tr, f, indent=2, ensure_ascii=False)

# 2. Update en.json technical errors
en_path = "translations/en.json"
with open(en_path, 'r', encoding='utf-8') as f:
    en = json.load(f)

en["api"] = {
    "sys_error": "System Error",
    "err_flora": "Error",
    "err_water": "Error",
    "err_urban": "Unknown",
    "err_transport": "Error",
    "err_precip": "Error",
    "err_settlement": "Error",
    "api_error": "API Error",
    "please_select_location": "Please select a location.",
    "analyzing": "Analyzing...",
    "service_unavailable": "SERVICE UNAVAILABLE"
}

with open(en_path, 'w', encoding='utf-8') as f:
    json.dump(en, f, indent=2, ensure_ascii=False)

# 3. Patch report.html to use format_number
report_path = "templates/report.html"
with open(report_path, 'r', encoding='utf-8') as f:
    html = f.read()

# E.g. replace {{ score }} with {{ score | format_number(0) }}
# Watch out for existing formatting or expressions
html = re.sub(r"\{\{\s*score\s*\}\}", r"{{ score | format_number(0) }}", html)
html = re.sub(r"\{\{\s*regional_avg\s*\}\}", r"{{ regional_avg | format_number(0) }}", html)

# Handle cases like {{ lat|round(4) }} -> {{ lat|round(4)|format_number(4) }}
# But since format_number takes decimals as arg: {{ lat | format_number(4) }}
html = re.sub(r"\{\{\s*lat\s*\|\s*round\(4\)\s*\}\}", r"{{ lat | format_number(4) }}", html)
html = re.sub(r"\{\{\s*lon\s*\|\s*round\(4\)\s*\}\}", r"{{ lon | format_number(4) }}", html)

with open(report_path, 'w', encoding='utf-8') as f:
    f.write(html)

print("Patch formatting complete")
