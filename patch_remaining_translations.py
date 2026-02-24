import json

def update_json(file_path, new_keys):
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    for k, v in new_keys.items():
        parts = k.split('.')
        current = data
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = v
        
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

en_keys = {
    "my_reports.refresh": "Refresh",
    "report.forage": "Forage",
    "report.slope": "Slope",
    "report.access": "Access",
    "report.veg_signal": "Vegetation signal (NDVI)",
    "report.water_proximity": "Water proximity"
}

tr_keys = {
    "my_reports.refresh": "Yenile",
    "report.forage": "Nektar (Flora)",
    "report.slope": "Eğim",
    "report.access": "Erişim",
    "report.veg_signal": "Vejetasyon Sinyali (NDVI)",
    "report.water_proximity": "Su Yakınlığı"
}

update_json("translations/en.json", en_keys)
update_json("translations/tr.json", tr_keys)
print("Updated JSON files")
