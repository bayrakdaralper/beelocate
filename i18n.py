"""Minimal i18n layer for BeeLocate Pro.

MVP rule: ship EN-first, but keep the codebase ready for additional languages
without scattering hardcoded strings.

Add a new language by:
  1) Adding a new JSON file under translations/
  2) Ensuring keys match EN
"""

from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Dict


STRINGS: Dict[str, Dict[str, str]] = {}

def load_translations():
    global STRINGS
    base_dir = Path(__file__).parent / "translations"
    if not base_dir.exists():
        return
    for tfile in base_dir.glob("*.json"):
        lang_code = tfile.stem
        try:
            with open(tfile, "r", encoding="utf-8") as f:
                STRINGS[lang_code] = json.load(f)
        except Exception as e:
            print(f"Error loading translation for {lang_code}: {e}")

# Call it once at import
load_translations()


def get_lang(lang: str | None = None) -> str:
    # 1. Force lang
    forced = os.getenv("FORCE_LANG")
    if forced:
        if forced.strip().lower() in STRINGS:
            return forced.strip().lower()
            
    # 2. Check explicitly provided
    if lang:
        lang = str(lang).strip().lower()
        if lang in ("tr", "turkish", "türkçe") and "tr" in STRINGS:
            return "tr"
        if lang in ("en", "english") and "en" in STRINGS:
            return "en"
        if lang in STRINGS:
            return lang
            
    # 3. Request logic (Flask context)
    try:
        from flask import request, has_request_context
        if has_request_context():
            cookie_lang = request.cookies.get("blp_lang")
            if cookie_lang:
                cl = cookie_lang.strip().lower()
                if cl in STRINGS:
                    return cl
            
            # Fallback to browser Accept-Language header if no explicit user cookie
            accept_lang = request.headers.get("Accept-Language", "")
            if accept_lang:
                if "tr" in accept_lang.split(",")[0].lower():
                    return "tr"
                return "en"
    except ImportError:
        pass

    # 4. Default to EN (or env var)
    env_lang = os.getenv("APP_LANG", "EN").strip().lower()
    return env_lang if env_lang in STRINGS else "en"


def t(key: str, default_text: str | None = None, lang: str | None = None, **kwargs) -> str:
    lang = get_lang(lang)
    base = STRINGS.get(lang, STRINGS.get("en", {}))
    s = base.get(key)
    
    # If not found in the target language, fallback to English
    if s is None and lang != "en":
        s = STRINGS.get("en", {}).get(key)
        
    s = s or default_text or key
    if kwargs:
        try:
            return s.format(**kwargs)
        except Exception:
            return s
    return s
