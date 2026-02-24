import ai_premium

dummy_payload_en = {
    "lang": "en",
    "score": 85,
    "lat": 39.0,
    "lon": 32.0,
    "season_label_en": "spring",
    "details": {
        "flora": {"label": "Lush vegetation", "score": 90, "desc": "High density"},
        "water": {"label": "River nearby", "score": 100, "desc": "Permanent source"},
        "aspect": {"label": "South-facing", "score": 90},
        "slope": {"label": "Gentle", "val": "5%", "score": 80},
        "elevation": {"val": "800m", "score": 90},
        "climate": {"wind": {"val": "Low wind", "score": 90}},
        "flight": {"val": "220 days", "score": 85},
        "precip": {"val": "Moderate rainfall", "score": 80},
        "settlement": {"val": "3 km away", "score": 80},
        "transport": {"val": "Dirt road 500m", "score": 75}
    }
}

dummy_payload_tr = {
    "lang": "tr",
    "score": 85,
    "lat": 39.0,
    "lon": 32.0,
    "season_label_en": "spring",
    "details": {
        "flora": {"label": "Yoğun bitki örtüsü", "score": 90, "desc": "Yüksek yoğunluk"},
        "water": {"label": "Yakın nehir", "score": 100, "desc": "Kalıcı kaynak"},
        "aspect": {"label": "Güneye bakan", "score": 90},
        "slope": {"label": "Hafif", "val": "5%", "score": 80},
        "elevation": {"val": "800m", "score": 90},
        "climate": {"wind": {"val": "Düşük rüzgar", "score": 90}},
        "flight": {"val": "220 gün", "score": 85},
        "precip": {"val": "Orta yağış", "score": 80},
        "settlement": {"val": "3 km uzakta", "score": 80},
        "transport": {"val": "Toprak yol 500m", "score": 75}
    }
}

# Test the English fallback
try:
    print("----- ENGLISH FALLBACK TEST -----")
    en_result = ai_premium._fallback_template(ai_premium._extract_inputs(dummy_payload_en))
    print(en_result['verdict'])
    print(en_result['sections']['forage'])
    print(en_result['why_this_score'][0])
except Exception as e:
    print(f"Error testing EN fallback: {e}")

# Test the Turkish fallback
try:
    print("\n----- TURKISH FALLBACK TEST -----")
    tr_result = ai_premium._fallback_template(ai_premium._extract_inputs(dummy_payload_tr))
    print(tr_result['verdict'])
    print(tr_result['sections']['forage'])
    print(tr_result['why_this_score'][0])
except Exception as e:
    print(f"Error testing TR fallback: {e}")
