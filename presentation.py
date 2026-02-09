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
