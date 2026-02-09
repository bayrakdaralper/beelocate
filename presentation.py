# Presentation / i18n layer for BeeLocate Pro
# Rule: core analysis must NOT depend on language.
# This file converts core result objects -> UI-friendly schemas.

from __future__ import annotations
from typing import Dict, Any, List

MONTH_NAMES_TR = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık"
}

MONTH_NAMES_EN = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December"
}

def is_en(lang: str | None) -> bool:
    return str(lang or "").lower().startswith("en")

def _t(lang: str, tr: str, en: str) -> str:
    return en if is_en(lang) else tr

def _date_info(lang: str, date_info: str) -> str:
    # Minimal translation for common tokens coming from core helpers.
    if not date_info:
        return "--"
    if is_en(lang):
        if date_info.strip() == "Son 30 Gün":
            return "Last 30 days"
    return date_info

def present_water(core: Dict[str, Any], lang: str = "tr") -> Dict[str, Any]:
    has_water = bool(core.get("has_water"))
    src = core.get("source", "none")

    if has_water:
        if src == "jrc":
            desc = _t(lang, "Kalıcı Su (JRC Global Water)", "Permanent water (JRC Global Surface Water)")
        elif src == "ndwi":
            desc = _t(lang, "Canlı Tespit (Uydu NDWI)", "Live detection (Satellite NDWI)")
        else:
            desc = _t(lang, "Su tespit edildi", "Water detected")

        return {
            "val": 100,
            "score": 100,
            "label": _t(lang, "Su Kaynağı Var", "Water Available"),
            "desc": desc,
            "status": "Aktif",
        }

    return {
        "val": 0,
        "score": 0,
        "label": _t(lang, "Su Yok", "No Water Detected"),
        "desc": _t(lang, "Yakınlarda su tespit edilemedi", "No surface water detected nearby"),
        "status": "Aktif",
    }

def present_flora(core: Dict[str, Any], lang: str = "tr") -> Dict[str, Any]:
    status = core.get("status", "Pasif")
    if status != "Aktif":
        reason = core.get("reason")
        if reason == "no_sentinel":
            return {
                "val": 0,
                "score": None,
                "label": _t(lang, "Veri Yok", "No Data"),
                "desc": _t(lang, "Sentinel-2 görüntüsü bulunamadı", "No Sentinel-2 image found"),
                "status": "Pasif",
            }
        return {"val": 0, "score": None, "label": "--", "desc": _t(lang, "Sistem Hatası", "System error"), "status": "Pasif"}

    ndvi = core.get("ndvi")
    score = core.get("score")
    class_code = core.get("class_code")

    label_map_tr = {
        "very_dense": "Çok Yoğun Bitki Örtüsü",
        "dense": "Yoğun Bitki",
        "moderate": "Orta-Seyrek Bitki",
        "sparse": "Seyrek Bitki / Karışık",
        "bare": "Çıplak Zemin / Yapılaşma",
    }
    label_map_en = {
        "very_dense": "Very Dense Vegetation",
        "dense": "Dense Vegetation",
        "moderate": "Moderate / Sparse Vegetation",
        "sparse": "Sparse / Mixed",
        "bare": "Bare / Built-up",
    }

    label = (label_map_en.get(class_code, "--") if is_en(lang) else label_map_tr.get(class_code, "--"))

    # Landcover top-3 string
    top: List[Dict[str, Any]] = core.get("landcover_top") or []
    if top:
        lc_str = " | ".join([f"{x.get('name')}: %{x.get('pct')}" for x in top])
        land_desc = _t(lang, f"Arazi Örtüsü: {lc_str}", f"Land cover: {lc_str}")
    else:
        land_desc = _t(lang, "Arazi Örtüsü: Tespit Edilemedi", "Land cover: Not detected")

    date_info = _date_info(lang, str(core.get("date_info") or "--"))

    # Season-friendly hint (optional)
    if core.get("window_type") == "season":
        peak_m = core.get("peak_month")
        sos_m = core.get("sos_month")
        months = MONTH_NAMES_EN if is_en(lang) else MONTH_NAMES_TR
        peak_name = months.get(int(peak_m), str(peak_m)) if peak_m else "--"
        sos_name = months.get(int(sos_m), str(sos_m)) if sos_m else "--"
        season_hint = _t(lang, f"Önerilen Sezon | SOS: {sos_name} | Peak: {peak_name}",
                        f"Recommended window | SOS: {sos_name} | Peak: {peak_name}")
    else:
        season_hint = None

    parts = []
    if ndvi is not None:
        parts.append(f"NDVI: {round(float(ndvi), 2)} (Sentinel-2)")
    parts.append(land_desc)
    if season_hint:
        parts.append(season_hint)
    else:
        parts.append(_t(lang, f"Dönem: {date_info}", f"Window: {date_info}"))

    desc = " | ".join(parts)

    return {
        "val": score if score is not None else 0,
        "score": score,
        "label": label,
        "desc": desc,
        "status": "Aktif",
    }
def present_precip(core: Dict[str, Any], lang: str = "tr") -> Dict[str, Any]:
    status = core.get("status", "Pasif")
    src = core.get("source", "CHIRPS")
    date_info = _date_info(lang, str(core.get("date_info") or "--"))

    if status != "Aktif":
        reason = core.get("reason")
        if reason == "no_data":
            return {
                "val": 0,
                "score": None,
                "label": _t(lang, "Veri Yok", "No Data"),
                "desc": _t(lang, f"{src} yağış verisi bulunamadı", f"{src} precipitation not available"),
                "status": "Pasif",
            }
        if reason == "no_value":
            return {
                "val": 0,
                "score": None,
                "label": _t(lang, "Veri Yok", "No Data"),
                "desc": _t(lang, "Yağış değeri alınamadı", "Could not retrieve precipitation value"),
                "status": "Pasif",
            }
        return {
            "val": 0,
            "score": None,
            "label": "--",
            "desc": _t(lang, "Analiz Hatası", "Analysis error"),
            "status": "Pasif",
        }

    mm = core.get("mm")
    score = core.get("score")
    label = "--"
    try:
        if mm is not None:
            label = f"{float(mm):.0f} mm"
    except Exception:
        label = "--"

    desc = _t(lang, f"Toplam Yağış ({date_info}) | Kaynak: {src}",
              f"Total precipitation ({date_info}) | Source: {src}")

    return {"val": mm if mm is not None else 0, "score": score, "label": label, "desc": desc, "status": "Aktif"}


def present_climate(core: Dict[str, Any], lang: str = "tr") -> Dict[str, Any]:
    status = core.get("status", "Pasif")
    src = core.get("source", "Open-Meteo")

    if status != "Aktif":
        return {"temp": {}, "wind": {}, "humidity": {}, "status": "Pasif"}

    t = core.get("temperature_c")
    h = core.get("humidity_pct")
    w = core.get("wind_kmh")
    d = core.get("wind_dir_deg")

    # Direction text (reuse app's cardinal mapping? Keep simple here.)
    def _cardinal(deg):
        try:
            deg = float(deg)
        except Exception:
            return None
        dirs_tr = ["Kuzey", "Kuzeydoğu", "Doğu", "Güneydoğu", "Güney", "Güneybatı", "Batı", "Kuzeybatı"]
        dirs_en = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        ix = int((deg + 22.5) // 45) % 8
        return (dirs_en[ix] if is_en(lang) else dirs_tr[ix])

    wind_dir = _cardinal(d)

    temp_val = f"{t:.1f}°C" if isinstance(t, (int, float)) else "--"
    hum_val = f"%{int(round(h))}" if isinstance(h, (int, float)) else "--"
    wind_val = f"{w:.1f} km/h" if isinstance(w, (int, float)) else "--"

    wind_desc = _t(lang, f"Yön: {wind_dir}" if wind_dir else "Yön: --",
                  f"Dir: {wind_dir}" if wind_dir else "Dir: --")

    return {
        "temp": {"val": temp_val, "desc": _t(lang, f"(Kaynak: {src})", f"(Source: {src})"), "status": "Aktif"},
        "wind": {"val": wind_val, "desc": wind_desc, "status": "Aktif"},
        "humidity": {"val": hum_val, "desc": _t(lang, "Anlık Nem Oranı", "Current humidity"), "status": "Aktif"},
        "status": "Aktif",
    }
