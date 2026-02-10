# Presentation / i18n layer for BeeLocate Pro
# Rule: core analysis must NOT depend on language.
# This file converts core result objects -> UI-friendly schemas.

from __future__ import annotations
from typing import Dict, Any, List

# ---- Common label mappings (TR -> EN) for UI consistency ----
LANDCOVER_TR_TO_EN = {
    "Ağaçlık Alan": "Forest/Tree Cover",
    "Orman": "Forest/Tree Cover",
    "Çayır/Mera": "Grassland",
    "Mera": "Grassland",
    "Tarım Arazisi": "Agricultural Land",
    "Tarım": "Agricultural Land",
    "Sulak Alan": "Wetland",
    "Su Yüzeyi": "Water",
    "Su": "Water",
    "Su Kütlesi": "Water Body",
    "Otsu Sulak": "Herbaceous Wetland",
    "Çalılık": "Shrubland",
    "Kentsel/Yapılaşma": "Built-up",
    "Çıplak/Seyrek": "Bare / Sparse",
    "Kar/Buz": "Snow / Ice",
    "Mangrov": "Mangroves",
    "Yosun/Liken": "Moss / Lichen",
    "Yerleşim": "Built-up",
    "Yapılaşmış": "Built-up",
    "Çıplak Alan": "Bare",
}

URBAN_TR_TO_EN = {
    "Kırsal": "Rural",
    "Banliyö": "Suburban",
    "Şehirleşmiş": "Urbanized",
}

CARDINAL_TR_TO_EN = {
    "Kuzey": "North",
    "Kuzeydoğu": "Northeast",
    "Doğu": "East",
    "Güneydoğu": "Southeast",
    "Güney": "South",
    "Güneybatı": "Southwest",
    "Batı": "West",
    "Kuzeybatı": "Northwest",
}

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
        parts_lc = []
        for x in top:
            name = str(x.get("name") or "").strip()
            if is_en(lang):
                name = LANDCOVER_TR_TO_EN.get(name, name)
            parts_lc.append(f"{name}: %{x.get('pct')}")
        lc_str = " | ".join(parts_lc)
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
        # In English, prefer cardinal abbreviations for compact UI.
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


def present_urban(core: Dict[str, Any], lang: str = "tr") -> Dict[str, Any]:
    """Translate urbanization card without touching the analysis logic."""
    if not isinstance(core, dict):
        return core
    if core.get("status") != "Aktif":
        return core

    lbl = str(core.get("label") or core.get("val") or "--").strip()
    raw = core.get("raw_val", core.get("value", None))
    if is_en(lang):
        lbl_out = URBAN_TR_TO_EN.get(lbl, lbl)
        desc_out = f"Light Index: {round(float(raw), 1)}" if isinstance(raw, (int, float)) else "Light Index: --"
    else:
        lbl_out = lbl
        desc_out = f"Işık Endeksi: {round(float(raw), 1)}" if isinstance(raw, (int, float)) else "Işık Endeksi: --"

    out = dict(core)
    out["val"] = lbl_out
    out["label"] = lbl_out
    out["desc"] = desc_out
    return out


def present_transport(core: Dict[str, Any], lang: str = "tr") -> Dict[str, Any]:
    if not isinstance(core, dict):
        return core

    out = dict(core)
    status = out.get("status")
    desc = str(out.get("desc") or "").strip()
    if is_en(lang):
        desc = desc.replace("En yakın yol", "Nearest road")
        desc = desc.replace("Yol bulunamadı", "Road not found")
        desc = desc.replace("Yol analizi hatası", "Road analysis error")
        desc = desc.replace("(OSM)", "(OSM)")
        out["desc"] = desc
    else:
        out["desc"] = desc
    # Keep label as is (already km), but protect '--' message in EN for passive case
    if status != "Aktif" and is_en(lang):
        if "Yol bulunamadı" in str(core.get("desc") or ""):
            out["desc"] = "Road not found (OSM)"
        elif "Yol analizi" in str(core.get("desc") or ""):
            out["desc"] = "Road analysis error"
    return out


def present_settlement(core: Dict[str, Any], lang: str = "tr") -> Dict[str, Any]:
    if not isinstance(core, dict):
        return core
    out = dict(core)
    desc = str(out.get("desc") or "")
    if is_en(lang):
        desc = desc.replace("En yakın yerleşim", "Nearest settlement")
        desc = desc.replace("içinde", "within")
        desc = desc.replace("10 km içinde yerleşim tespit edilmedi", "No settlement detected within 10 km")
        desc = desc.replace("Analiz Hatası", "Analysis error")
        out["desc"] = desc
    return out


def present_topography(topo: Dict[str, Any], lang: str = "tr") -> Dict[str, Any]:
    """Translate slope/aspect/elevation descriptions produced by topo core."""
    if not isinstance(topo, dict):
        return topo

    out = dict(topo)

    # Elevation
    elev = dict(out.get("elevation") or {})
    if elev:
        if is_en(lang):
            elev["label"] = "Elevation"
            elev["desc"] = "NASA SRTM"
        else:
            elev["label"] = "Yükseklik"
            elev["desc"] = elev.get("desc") or "NASA SRTM"
        out["elevation"] = elev

    # Slope
    slope = dict(out.get("slope") or {})
    if slope:
        if is_en(lang):
            slope["label"] = "Slope"
            # Make sure we don't leak TR words like 'derece'
            slope["desc"] = "Land slope (SRTM, degrees→%)"
        else:
            slope["label"] = "Eğim"
            slope["desc"] = "Arazi Eğimi (SRTM, derece->%)"
        out["slope"] = slope

    # Aspect
    aspect = dict(out.get("aspect") or {})
    if aspect:
        # aspect label currently uses deg_to_cardinal (TR). Translate if needed.
        lbl = str(aspect.get("label") or "--").strip()
        if is_en(lang):
            lbl = CARDINAL_TR_TO_EN.get(lbl, lbl)
            aspect["label"] = lbl
            # (Bakı) -> (Aspect)
            desc = str(aspect.get("desc") or "")
            desc = desc.replace("(Bakı)", "(Aspect)")
            aspect["desc"] = desc
        else:
            # Keep as TR
            aspect["desc"] = str(aspect.get("desc") or "")
        out["aspect"] = aspect

    return out


def present_flight_window(core: Dict[str, Any], lang: str = "tr") -> Dict[str, Any]:
    """Translate ERA5 flight window card."""
    if not isinstance(core, dict):
        return core
    out = dict(core)
    if out.get("status") != "Aktif":
        # Localize common passive message
        desc = str(out.get("desc") or "")
        if is_en(lang):
            desc = desc.replace("ERA5 verisi alınamadı", "ERA5 data unavailable")
            desc = desc.replace("ERA5 analizi hatası", "ERA5 analysis error")
        out["desc"] = desc
        return out

    # Label: "217 gün/yıl" -> "217 days/year"
    try:
        days = int(round(float(out.get("value", out.get("val", 0)))))
    except Exception:
        days = None

    if days is not None:
        out["label"] = f"{days} days/year" if is_en(lang) else f"{days} gün/yıl"

    desc = str(out.get("desc") or "")
    if is_en(lang):
        desc = desc.replace("Uçuş uygun gün", "Suitable flight days")
        desc = desc.replace("Uçuş penceresi", "Flight window")
        desc = desc.replace("Ort:", "Avg:")
        desc = desc.replace("gün/yıl", "days/year")
    out["desc"] = desc
    return out


# Backwards-compatible aliases (some call sites may use these names)
present_topo = present_topography
present_flight = present_flight_window


def present_flight_suitability(core: Dict[str, Any], lang: str = "tr") -> Dict[str, Any]:
    """Translate Flight Suitability card (derived from flight window).

    Some branches still emit Turkish labels (e.g., "İyi") even when UI is English.
    This is a pure presentation-layer fix (no core logic changes).
    """
    if not isinstance(core, dict):
        core = {}

    is_en = str(lang).lower().startswith("en")
    if not is_en:
        return core

    out = dict(core)

    # Status
    st = str(out.get("status", "") or "")
    out["status"] = "Active" if st.lower().startswith("akt") else ("Inactive" if st else st)

    # Label translations (defensive: handle mixed strings)
    lbl = str(out.get("label", "") or "")
    lbl = lbl.replace("Çok İyi", "Very Good")
    lbl = lbl.replace("İyi", "Good")
    lbl = lbl.replace("Orta", "Medium")
    lbl = lbl.replace("Zayıf", "Weak")
    out["label"] = lbl

    # Desc translations
    desc = str(out.get("desc", "") or "")
    desc = desc.replace("Uçuş penceresi", "Flight window")
    desc = desc.replace("gün/yıl", "days/year")
    desc = desc.replace("Ort:", "Avg:")
    out["desc"] = desc

    return out
