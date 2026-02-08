"""Minimal i18n layer for BeeLocate Pro.

MVP rule: ship EN-first, but keep the codebase ready for additional languages
without scattering hardcoded strings.

Add a new language by:
  1) Adding a new dict under STRINGS (e.g., 'tr', 'de')
  2) Ensuring keys match EN
  3) Passing ?lang=TR (or setting APP_LANG)
"""

from __future__ import annotations

import os
from typing import Dict


STRINGS: Dict[str, Dict[str, str]] = {
    "en": {
        "app_title": "BeeLocate Pro",
        "search_placeholder": "Search location... (e.g., Canterbury)",
        "system_initializing": "SYSTEM INITIALIZING...",
        "analysis_period": "Analysis period:",
        "recommended_season": "RECOMMENDED SEASON (Phenology)",
        "live_now": "LIVE / NOW",
        "month_sim": "{month} (Simulation)",
        "land_suitability": "LAND SUITABILITY",
        "run_analysis": "Run analysis",
        "select_location": "Please select a location.",
        "analyzing": "Analyzing...",
        "analysis_error": "Analysis error. Check console.",
        "report_id_missing": "Report ID not found. Please run the analysis again.",
        "copied": "Copied.",
        "share": "Share",
        "download_pdf": "Download PDF",
        "back_to_map": "Back to map",
        "summary_placeholder": "Run an analysis to see a human-readable summary here.",
    }
}


def get_lang(lang: str | None) -> str:
    if not lang:
        lang = os.getenv("APP_LANG", "EN")
    lang = str(lang).strip().lower()
    if lang in ("en", "english"):
        return "en"
    if lang in ("tr", "turkish", "türkçe"):
        return "tr" if "tr" in STRINGS else "en"
    return "en"


def t(key: str, lang: str = "en", **kwargs) -> str:
    base = STRINGS.get(lang, STRINGS["en"])
    s = base.get(key) or STRINGS["en"].get(key) or key
    if kwargs:
        try:
            return s.format(**kwargs)
        except Exception:
            return s
    return s
