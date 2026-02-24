"""AI premium commentary generator for BeeLocate Pro.

Goals
-----
Create a *paid-worthy* commentary block that references the actual analysis
cards (vegetation, water, aspect, slope, elevation, wind, flight window,
settlement/road, precipitation). The output must be stable and easy to
render.

Design constraints
------------------
* Backward compatible: the app template historically expects:
    - executive_summary: str
    - key_drivers: list[str]
    - risks: list[str]
    - field_checks: list[str]

  New premium layout (optional):
    - verdict: str (2–3 sentences)
    - sections: dict with keys forage, terrain, climate, access
    - why_this_score: list[str] (2–4 bullets; short)
    - general_interpretation: str (2–3 sentences; short)
    - best_use_case: list[str]
    - next_checks: list[str]
* We use the OpenAI "responses" API via the `openai` Python package.
* The Responses API may not support structured parameters like
  `response_format` in all SDK versions. So we request strict JSON in text
  and parse it ourselves.
* If parsing fails, fall back to a deterministic (non-AI) generator.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Tuple

try:
    # openai>=1.x
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


def _dig(payload: Dict[str, Any], path: Tuple[str, ...], default=None):
    cur: Any = payload
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def _extract_inputs(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize data from different payload schemas into a compact dict."""

    score = payload.get("score")
    lat = payload.get("lat")
    lon = payload.get("lon") or payload.get("lng")

    # New schema: payload["details"][...]
    details = payload.get("details") if isinstance(payload.get("details"), dict) else None

    if details:
        flora = details.get("flora") or {}
        water = details.get("water") or {}
        aspect = details.get("aspect") or {}
        slope = details.get("slope") or {}
        elev = details.get("elevation") or {}
        precip = details.get("precip") or {}
        settlement = details.get("settlement") or {}
        road = details.get("transport") or {}
        urban = details.get("urban") or {}
        wind = _dig(details, ("climate", "wind"), {}) or {}
        flight = details.get("flight") or {}
        season = details.get("season_meta") or {}

        return {
            "schema": "details",
            "lang": payload.get("lang"),
            "score": score,
            "lat": lat,
            "lon": lon,
            "season_label_en": season.get("season_label_en") or "",
            "vegetation": {
                "label": flora.get("label") or flora.get("_main") or "",
                "score": flora.get("score"),
                "desc": flora.get("desc") or flora.get("_sub") or "",
            },
            "water": {
                "label": water.get("label") or water.get("_main") or "",
                "score": water.get("score"),
                "desc": water.get("desc") or water.get("_sub") or "",
            },
            "aspect": {
                "label": aspect.get("label") or aspect.get("_main") or "",
                "deg": aspect.get("val"),
                "score": aspect.get("score"),
                "desc": aspect.get("desc") or aspect.get("_sub") or "",
            },
            "slope": {
                "label": slope.get("label") or "",
                "val": slope.get("val") or slope.get("_main"),
                "score": slope.get("score"),
                "desc": slope.get("desc") or slope.get("_sub") or "",
            },
            "elevation": {
                "val": elev.get("val") or elev.get("_main"),
                "score": elev.get("score"),
                "desc": elev.get("desc") or elev.get("_sub") or "",
            },
            "wind": {
                "val": wind.get("val") or wind.get("_main"),
                "desc": wind.get("desc") or wind.get("_sub") or "",
            },
            "flight": {
                "days": flight.get("val") or flight.get("_main"),
                "score": flight.get("score"),
                "desc": flight.get("desc") or flight.get("_sub") or "",
            },
            "precip": {
                "val": precip.get("_main") or precip.get("val"),
                "score": precip.get("score"),
                "desc": precip.get("desc") or precip.get("_sub") or "",
            },
            "settlement": {
                "val": settlement.get("_main") or settlement.get("val"),
                "score": settlement.get("score"),
                "desc": settlement.get("desc") or settlement.get("_sub") or "",
            },
            "road": {
                "val": road.get("_main") or road.get("val"),
                "score": road.get("score"),
                "desc": road.get("desc") or road.get("_sub") or "",
            },
            "urban": {
                "label": urban.get("_main") or urban.get("val"),
                "score": urban.get("score"),
                "desc": urban.get("desc") or urban.get("_sub") or "",
            },
        }

    # Older schema: payload["cards"]
    cards = payload.get("cards") if isinstance(payload.get("cards"), dict) else None
    if cards:
        def c(k: str):
            v = cards.get(k) or {}
            return {
                "label": v.get("label") or v.get("name") or k,
                "score": v.get("score"),
                "desc": v.get("desc") or v.get("sub") or "",
                "val": v.get("main") or v.get("value"),
            }

        return {
            "schema": "cards",
            "lang": payload.get("lang"),
            "score": score,
            "lat": lat,
            "lon": lon,
            "season_label_en": payload.get("season_label_en") or "",
            "vegetation": c("flora"),
            "water": c("water"),
            "aspect": c("aspect"),
            "slope": c("slope"),
            "elevation": c("elevation"),
            "wind": c("wind"),
            "flight": c("flight"),
            "precip": c("precip"),
            "settlement": c("settlement"),
            "road": c("road"),
            "urban": c("urban"),
        }

    # Worst case: minimal
    return {
        "schema": "minimal",
        "lang": payload.get("lang"),
        "score": score,
        "lat": lat,
        "lon": lon,
        "season_label_en": payload.get("season_label_en") or "",
    }


def _strict_json_from_text(text: str) -> Dict[str, Any] | None:
    if not text:
        return None
    s = text.strip()
    # Prefer a raw JSON object.
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # Try to extract the first {...} block.
    m = re.search(r"\{[\s\S]*\}", s)
    if not m:
        return None
    candidate = m.group(0)
    try:
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None


def _normalize_output(obj: Dict[str, Any]) -> Dict[str, Any]:
    def as_list(v: Any) -> List[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        # split lines/bullets
        if isinstance(v, str):
            parts = re.split(r"\n+|\s*•\s*|\s*-\s*", v)
            parts = [p.strip() for p in parts if p.strip()]
            return parts
        return [str(v).strip()]

    # Optional new layout
    sections = obj.get("sections") if isinstance(obj.get("sections"), dict) else {}
    sections_norm = {}
    for k in ("forage", "terrain", "climate", "access"):
        v = sections.get(k) if isinstance(sections, dict) else None
        if isinstance(v, str) and v.strip():
            sections_norm[k] = v.strip()

    out = {
        # Backward-compatible keys
        "executive_summary": str(obj.get("executive_summary") or "").strip(),
        "key_drivers": as_list(obj.get("key_drivers")),
        "risks": as_list(obj.get("risks")),
        "field_checks": as_list(obj.get("field_checks")),
        # New premium layout keys (optional)
        "verdict": str(obj.get("verdict") or "").strip(),
        "sections": sections_norm,
        "why_this_score": as_list(obj.get("why_this_score")),
        "general_interpretation": str(obj.get("general_interpretation") or "").strip(),
        "best_use_case": as_list(obj.get("best_use_case")),
        "next_checks": as_list(obj.get("next_checks")),
    }

    # If only the new layout was provided, synthesize legacy fields so the
    # rest of the app can still render a meaningful premium block.
    if (not out["executive_summary"]) and out["verdict"]:
        parts = [out["verdict"]]
        for k in ("forage", "terrain", "climate", "access"):
            if out["sections"].get(k):
                parts.append(out["sections"][k])
        out["executive_summary"] = " ".join(parts).strip()

    if not out["field_checks"] and out["next_checks"]:
        out["field_checks"] = out["next_checks"][:]
    if not out["key_drivers"] and out["best_use_case"]:
        out["key_drivers"] = out["best_use_case"][:]

    # Keep the short blocks sane.
    if len(out["why_this_score"]) > 6:
        out["why_this_score"] = out["why_this_score"][:6]

    return out


def _fallback_template(inp: Dict[str, Any]) -> Dict[str, Any]:
    score = inp.get("score")
    veg = inp.get("vegetation", {})
    water = inp.get("water", {})
    slope = inp.get("slope", {})
    aspect = inp.get("aspect", {})
    elev = inp.get("elevation", {})
    wind = inp.get("wind", {})
    flight = inp.get("flight", {})
    road = inp.get("road", {})
    settlement = inp.get("settlement", {})
    precip = inp.get("precip", {})

    season = inp.get("season_label_en") or "recommended season"
    veg_line = (veg.get("desc") or veg.get("label") or "vegetation")
    water_line = (water.get("label") or "water")
    slope_line = (slope.get("val") or slope.get("desc") or "")
    aspect_line = (aspect.get("label") or "")
    elev_line = (elev.get("val") or "")
    wind_line = (wind.get("val") or "")
    flight_line = (flight.get("days") or "")
    road_line = (road.get("val") or "")
    settlement_line = (settlement.get("val") or "")
    precip_line = (precip.get("val") or "")

    lang = str(inp.get("lang") or "en").lower()

    if lang == "tr":
        season_str = "tavsiye edilen sezon" if season == "recommended season" else season
        verdict = (
            f"Bu lokasyon {season_str} için 100 üzerinden {score} puan alıyor. "
            f"Vejetasyon sinyali belirgin ({veg_line}). "
            f"Su durumu: {water_line}. "
            f"Topografya eğim {slope_line} ve bakı {aspect_line} ile karakterize, rakım ~{elev_line}. "
            f"Rüzgar {wind_line} ve tahmini uçuş penceresi {flight_line}. "
            f"Lojistik: yol ~{road_line}, en yakın yerleşim ~{settlement_line}. "
            f"Yağış durumu: {precip_line}."
        ).strip()

        sections = {
            "forage": (
                f"Nektar potansiyeli temel olarak {season_str} dönemindeki vejetasyon ile yönlendiriliyor. "
                f"Bunu konuma özel bir bitki türü kesinliği olarak değil, dönemsel bir sinyal olarak değerlendirin. "
                f"Parçalı arazi örtüsünde kaynaklar dağınık olabilir, arılık düzeninizi buna göre planlayın."
            ),
            "terrain": (
                f"Arazi kısıtlamaları eğim ({slope_line}) ve bakıdan ({aspect_line}) kaynaklanıyor. "
                f"Devrilmeyi önlemek ve işçiliği azaltmak için kovanları güvenli, düz zeminlere yerleştirmeye öncelik verin. "
                f"Yüksek rakımlarda (~{elev_line}) fenolojinin gecikmesini ve çalışma süresinin kısalmasını bekleyin."
            ),
            "climate": (
                f"Uçuş aktivitesi rüzgar ({wind_line}) ve simüle edilmiş uçuş günleriyle ({flight_line} gün/yıl) sınırlıdır. "
                f"Açık alanlarda rüzgarkıran kullanın ve kovan uçuş deliklerini hakim rüzgardan koruyacak şekilde konumlandırın. "
                f"Soğuk eğilimli hava koşulları gelişimi yavaşlatır; koloni gücünü buna göre planlayın."
            ),
            "access": (
                f"Operasyonel uygunluk yol erişimi (~{road_line}) ve yerleşim baskısına (~{settlement_line}) bağlıdır. "
                f"Uzak konumlar rahatsızlığı azaltır ancak lojistik maliyetini artırır; yağışlı havada araç erişimini teyit edin. "
                f"Eğer su güvenilir saptanmadıysa ({water_line}), taşıma su planlayın veya alternatif konum arayın."
            ),
        }
    else:
        verdict = (
            f"This location scores {score}/100 for the {season}. "
            f"Vegetation looks promising ({veg_line}). "
            f"Water signal: {water_line}. "
            f"Topography is defined by slope {slope_line} and aspect {aspect_line}, at ~{elev_line} elevation. "
            f"Wind is {wind_line} and the flight window is about {flight_line}. "
            f"Logistics: road ~{road_line}, nearest settlement ~{settlement_line}. "
            f"Recent precipitation: {precip_line}."
        ).strip()

        sections = {
            "forage": (
                f"Forage potential is primarily driven by the vegetation proxy in the {season}. "
                f"Treat this as a seasonal signal (not species-level certainty) and align placement with the peak month if available. "
                f"If land cover is fragmented, expect uneven forage distribution and plan apiary layout accordingly."
            ),
            "terrain": (
                f"Terrain constraints come from slope ({slope_line}) and aspect ({aspect_line}). "
                f"Prioritize safe, level hive pads to reduce labor and prevent tipping; keep handling/harvest paths in mind. "
                f"If elevation is high (~{elev_line}), expect later phenology and shorter working windows."
            ),
            "climate": (
                f"Flight activity is bounded by wind ({wind_line}) and the flight-window proxy ({flight_line} days/year). "
                f"Use sheltered placement and windbreaks where exposure is high; reduce entrance exposure to prevailing winds. "
                f"Cold-leaning conditions will suppress buildup; plan colony strength and timing conservatively."
            ),
            "access": (
                f"Operational feasibility is shaped by access (road ~{road_line}) and proximity/pressure (settlement ~{settlement_line}). "
                f"Remote sites can reduce disturbance but increase logistics cost; verify vehicle access under wet conditions. "
                f"If water is not reliably detected ({water_line}), plan managed water or choose a nearby alternative."
            ),
        }

    if lang == "tr":
        return {
            "verdict": verdict,
            "why_this_score": [
                "Vejetasyon sinyali önerilen dönem için nektar potansiyelini destekliyor (kesin tür tespiti değil, bölgesel yoğunluk).",
                "Su durumu operasyonel bir kısıttır; su sinyali zayıfsa taşıma su/sulama planlayın.",
                "Ekolojik durum iyi görünse de rüzgar, eğim ve erişim gibi faktörler sahadaki gerçek fizibiliteyi belirler.",
            ],
            "general_interpretation": (
                "Bu puan, ekolojik potansiyeli (çiçeklenme/su) ve operasyonel kısıtları (arazi/erişim) özetler. "
                "Karar destek rehberi olarak kullanın ve kesin bir yatırım yapmadan önce riskleri sahada doğrulayın." 
            ),
            "sections": sections,
            "best_use_case": [
                "En yoğun çiçeklenme zamanına göre ayarlanmış dönemsel (sezonluk) yerleşim.",
                "Eğer su güvenilirse bal üretimi odaklı, aksi takdirde koloni güvencesine yönelik risk yönetimi.",
            ],
            "next_checks": [
                "1-3 km çapındaki baskın çiçekli bitkileri ve gerçek yerel çiçeklenme zamanını sahada onaylayın.",
                "Sıcak haftalarda su kaynaklarının devamlılığını kontrol edin (geçici su birikintisi olmadığından emin olun).",
                "Kovan kuruluşu ve bal hasadı için güvenli, düz alanları ve araç iletişimini onaylayın.",
            ],
            "executive_summary": verdict,
            "key_drivers": [
                "Önerilen dönem içerisindeki vejetasyon (NDVI) sinyali.",
                "Su durumu ve yaz aylarındaki sıcaklık baskısı (termal yönetim).",
                "Kovan yerleşimi ve genel erişimi etkileyen topografya (eğim/bakı).",
            ],
            "risks": [
                "Eğer su kesikli/geçiciyse, koloniler üretimden çok hayatta kalmaya odaklanabilir.",
                "Dik veya parçalı araziler uygun mikro alanları kısıtlayıp işçiliği artırabilir.",
                "Aşırı rüzgar baskısı uçuş verimliliğini düşürür ve koloni stresini artırır.",
            ],
            "field_checks": [
                "1-3 km içindeki baskın çiçekli florayı ve lokal fenolojiyi yerel bilgi/saha yürüyüşü ile tespit edin.",
                "Yaz mevsiminde su kalıcılığını teyit edin (bazı akarsu veya göletler tamamen kuruyabilir).",
                "Kovan hizasındaki rüzgarı hissedin; aşırı rüzgar durumunda doğal/suni tel örgü-rüzgarkıran kullanın.",
                "Ağır araçların yağış durumunda da alana kolay erişebileceğini ve hasat operasyonlarını yönetebileceğinizi test edin.",
            ],
        }
    else:
        return {
            "verdict": verdict,
            "why_this_score": [
                "Vegetation proxy supports forage during the recommended window (seasonal signal, not species-level certainty).",
                "Water availability is a key operational constraint; plan managed water if surface-water signals are weak.",
                "Terrain and access determine real-world feasibility (pads, handling, vehicle access) even when ecology looks promising.",
            ],
            "general_interpretation": (
                "This score summarizes ecological potential (forage/water) and operational constraints (terrain/access). "
                "Treat it as a decision-support baseline and validate the top uncertainties on-site before committing." 
            ),
            "sections": sections,
            "best_use_case": [
                "Seasonal placement during the recommended window, timed to peak bloom.",
                "Production focus if water is reliable; otherwise prioritize colony strength and risk management.",
            ],
            "next_checks": [
                "Confirm dominant flowering plants within ~1–3 km and the real peak week on-site.",
                "Verify water persistence through the hottest weeks (not just a one-off signal).",
                "Confirm safe, level hive pads and vehicle access for setup and harvest.",
            ],
            "executive_summary": verdict,
            "key_drivers": [
                "Forage signal (NDVI/land cover) within the recommended window.",
                "Water availability and summer heat management.",
                "Topography (slope/aspect) affecting hive placement and access.",
            ],
            "risks": [
                "If water is intermittent, colonies may shift from production to survival.",
                "Steep or fragmented terrain can limit safe micro-sites and increase labor.",
                "Wind exposure can reduce flight efficiency and increase stress.",
            ],
            "field_checks": [
                "Confirm dominant flowering species within 1–3 km (local knowledge / field walk).",
                "Verify seasonal water persistence (streams/ponds may be intermittent).",
                "Inspect wind exposure at hive height; use windbreaks or sheltered placement.",
                "Confirm vehicle access and safe, level pads for hives and harvesting.",
            ],
        }


def generate_ai_insights(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Public entry: returns a dict for templates.

    Raises on missing API key or hard failures so the caller can set ai_error.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY missing")

    if OpenAI is None:
        # SDK mismatch (e.g., openai<1.x). Let caller fall back deterministically.
        raise RuntimeError("OpenAI SDK incompatible: install openai>=1.x")

    model = os.getenv("OPENAI_MODEL", "gpt-5-mini")
    inp = _extract_inputs(payload)

    # Compact JSON the model can reliably digest.
    data_blob = json.dumps(inp, ensure_ascii=False)

    lang = str(inp.get("lang") or "en").lower()

    if lang == "tr":
        system = (
            "Sen müşteriye dönük uzman raporları hazırlayan kıdemli bir arıcılık saha uygunluk analistisin. "
            "SADECE verilen metrikleri kullan. Çiçek türleri, mahsuller, rekolteler veya yerel gerçekler UYDURMA. "
            "Eğer bir metrik eksikse tahmin etmek yerine 'Bilinmiyor' de. "
            "Deneyimli bir arıcı gibi yaz: pratik, somut ve sakin—abartı yok. "
            "Bir tavsiyede bulunduğunda, bunu bir metriğe bağla (NDVI, eğim %, rüzgar, uçuş günleri, mesafeler, rakım, su sinyali)."
        )
        user_lang_instruction = "ÖNEMLİ: Tüm yanıtını tamamen TÜRKÇE (Turkish) dilinde yaz. JSON anahtarlarını (keys) İngilizce bırak, ancak tüm metin içeriklerini Türkçe oluştur."
    else:
        system = (
            "You are a senior beekeeping site-suitability analyst writing customer-facing expert commentary. "
            "Use ONLY the provided metrics. Do NOT invent flowering species, crops, yields, or local facts. "
            "If a metric is missing, say 'Unknown' rather than guessing. "
            "Write like an experienced beekeeper: practical, concrete, and calm—no hype. "
            "When you make a recommendation, tie it to a metric (NDVI, slope %, wind, flight days, distances, elevation, water signal)."
        )
        user_lang_instruction = "IMPORTANT: Write your entire response in English."

    user = (
        "Return ONLY a valid JSON object. No markdown. No backticks.\n\n"
        "Required keys (premium layout):\n"
        "- why_this_score: array of 2–4 short bullet strings (these will replace the 'Why this score?' panel)\n"
        "- general_interpretation: string (2–3 sentences; neutral decision-support framing)\n"
        "- verdict: string (2–3 sentences; MUST include overall score, main strength, main risk)\n"
        "- sections: object with exactly these string keys: forage, terrain, climate, access\n"
        "  Each section MUST be 3–5 sentences, include relevant numbers, and explain operational implications.\n"
        "- best_use_case: array of 1–2 bullet strings\n"
        "- next_checks: array of exactly 3 bullet strings (on-the-ground validation)\n\n"
        "Also include backward-compatible keys:\n"
        "- executive_summary: string (250–450 words; plain English; reads like an expert reviewer, not a template)\n"
        "- key_drivers: array of 3–5 bullet strings\n"
        "- risks: array of 3–5 bullet strings\n"
        "- field_checks: array of 4–7 bullet strings\n\n"
        "Content rules:\n"
        "- Use ONLY INPUT_METRICS. No speculation about species, crops, yields, or local realities.\n"
        "- Mention timing: recommended season and peak month/onset if present.\n"
        "- If a value is missing, say 'Unknown' (do not guess).\n"
        "- Do not ask questions.\n"
        "- Avoid generic filler like 'confirm on-site' unless tied to a specific uncertainty (water, access, slope micro-sites, exposure).\n\n"
        f"{user_lang_instruction}\n\n"
        f"INPUT_METRICS={data_blob}"
    )

    if OpenAI is None:
        # SDK mismatch (e.g., openai<1.0). Caller will fall back.
        raise RuntimeError("openai SDK incompatible: OpenAI client not available")

    client = OpenAI(api_key=api_key)

    def call(prompt_user: str) -> Dict[str, Any] | None:
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt_user},
            ],
            # Keep cost bounded but allow enough room for premium text.
            max_output_tokens=900,
            store=False,
        )
        text = (getattr(resp, "output_text", None) or "").strip()
        return _strict_json_from_text(text)

    obj = call(user)
    if obj is None:
        # Retry with even stricter instructions.
        user2 = (
            "Return ONLY JSON. Start with '{' and end with '}'. "
            "Do not include markdown or commentary outside JSON. "
            f"INPUT_METRICS={data_blob}"
        )
        obj = call(user2)

    if obj is None:
        # Last-resort fallback to deterministic text.
        return _fallback_template(inp)

    out = _normalize_output(obj)

    # If the model returned empty arrays/summary, don't ship a dud.
    if not out["executive_summary"] or len(out["key_drivers"]) < 2:
        return _fallback_template(inp)

    return out
