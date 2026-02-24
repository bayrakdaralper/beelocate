import os
import json
import re

en_adds = {
  "report": {
    "methodology_note_text": "NDVI is a vegetation density indicator (not plant species). Climate metrics are long-term reanalysis summaries. BeeLocate PRO is a decision-support tool and does not guarantee outcomes.",
    "legal_disclaimer_text": "This report is generated from satellite and spatial analysis data and is provided for decision support only. Results may vary with season, management practices, and on-site conditions; no yield, income, or colony-health guarantee is implied.",
    "general_comment_fallback": "This location shows a mix of ecological potential and operational constraints. Use the score as a decision-support summary: the best outcomes usually happen when vegetation continuity aligns with a suitable season window, and practical factors (access, slope, and local pressure) are acceptable.",
    "recommended_season_for_area": "Recommended season for this area: {label}."
  }
}

tr_adds = {
  "report": {
    "methodology_note_text": "NDVI bir bitki örtüsü yoğunluğu göstergesidir (bitki türü değil). İklim metrikleri uzun vadeli yeniden analiz özetleridir. BeeLocate PRO bir karar destek aracıdır ve sonuçları garanti etmez.",
    "legal_disclaimer_text": "Bu rapor uydu ve konumsal analiz verilerinden oluşturulmuştur ve yalnızca karar desteği için sağlanmıştır. Sonuçlar mevsime, yönetim uygulamalarına ve saha koşullarına göre değişebilir; herhangi bir verim, gelir veya koloni sağlığı garantisi ima edilmez.",
    "general_comment_fallback": "Bu konum, ekolojik potansiyel ve operasyonel kısıtlamaların bir karışımını göstermektedir. Puanı bir karar destek özeti olarak kullanın: en iyi sonuçlar genellikle bitki örtüsü sürekliliği uygun bir sezon penceresiyle uyumlu olduğunda ve pratik faktörler (erişim, eğim ve yerel baskı) kabul edilebilir olduğunda elde edilir.",
    "recommended_season_for_area": "Bu bölge için önerilen sezon: {label}."
  }
}

for lang, adds in [("en", en_adds), ("tr", tr_adds)]:
    path = f"/Users/alperbayrakdar/Desktop/beelocate-main-1/translations/{lang}.json"
    with open(path, "r") as f:
        data = json.load(f)
    if "report" in data:
        data["report"].update(adds["report"])
    else:
        data["report"] = adds["report"]
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# Now patch app.py
path = '/Users/alperbayrakdar/Desktop/beelocate-main-1/app.py'
with open(path, 'r') as f:
    c = f.read()

# Replace general_comment block
old_general_comment = '''        general_comment = (
            "This location shows a mix of ecological potential and operational constraints. "
            "Use the score as a decision-support summary: the best outcomes usually happen when "
            "vegetation continuity aligns with a suitable season window, and practical factors "
            "(access, slope, and local pressure) are acceptable."
        )
        if season_meta and season_meta.get("peak_month"):
            general_comment += f" Recommended season for this area: {_season_label_en(season_meta)}."'''

import sys
if old_general_comment not in c:
    print("Could not find old_general_comment string!")
    sys.exit(1)

new_general_comment = '''        general_comment = i18n.t("report.general_comment_fallback", lang=lang)
        if season_meta and season_meta.get("peak_month"):
            sl = _season_label_tr(season_meta) if lang == 'tr' else _season_label_en(season_meta)
            general_comment += " " + i18n.t("report.recommended_season_for_area", lang=lang, label=sl)'''

c = c.replace(old_general_comment, new_general_comment)

old_methodology = '''    methodology_note = payload.get(
        "methodology_note",
        "NDVI is a vegetation density indicator (not plant species). Climate metrics are long-term reanalysis summaries. "
        "BeeLocate PRO is a decision-support tool and does not guarantee outcomes."
    )'''

new_methodology = '''    methodology_note = payload.get("methodology_note") or i18n.t("report.methodology_note_text", lang=lang)'''

if old_methodology in c:
    c = c.replace(old_methodology, new_methodology)

old_legal = '''    legal_disclaimer = payload.get(
        "legal_disclaimer",
        "This report is generated from satellite and spatial analysis data and is provided for decision support only. "
        "Results may vary with season, management practices, and on-site conditions; no yield, income, or colony-health guarantee is implied."
    )'''

new_legal = '''    legal_disclaimer = payload.get("legal_disclaimer") or i18n.t("report.legal_disclaimer_text", lang=lang)'''

if old_legal in c:
    c = c.replace(old_legal, new_legal)

with open(path, 'w') as f:
    f.write(c)

print('Done')
