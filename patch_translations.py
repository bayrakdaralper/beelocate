import json

en_adds = {
  "index": {
    "title": "BeeLocate PRO v3.9",
    "tooltip_my_reports": "My reports",
    "tooltip_contact": "Contact",
    "metric": "Metric",
    "imperial": "Imperial",
    "basemap_sat_labels": "Satellite + Labels (Mapbox)",
    "basemap_sat": "Satellite (Mapbox)",
    "basemap_map": "Map (Mapbox)",
    "basemap_osm": "Map (OSM fallback)",
    "labels": "Labels",
    "search_placeholder": "Search location... (e.g., Sarıcakaya)",
    "scan_radius": "Scan radius (buffer)",
    "run_analysis": "RUN ANALYSIS",
    "initializing_system": "INITIALIZING SYSTEM...",
    "overall_score": "OVERALL SCORE",
    "analysis_period": "Analysis period:",
    "recommended_season": "🌿 RECOMMENDED SEASON",
    "live_current": "⚡ LIVE / CURRENT",
    "jan_sim": "JAN (Simulation)",
    "feb_sim": "FEB (Simulation)",
    "mar_sim": "MAR (Simulation)",
    "apr_sim": "APR (Simulation)",
    "may_sim": "MAY (Simulation)",
    "jun_sim": "JUN (Simulation)",
    "jul_sim": "JUL (Simulation)",
    "aug_sim": "AUG (Simulation)",
    "sep_sim": "SEP (Simulation)",
    "oct_sim": "OCT (Simulation)",
    "nov_sim": "NOV (Simulation)",
    "dec_sim": "DEC (Simulation)",
    "managed_water": "Managed Water",
    "land_suitability_score": "LAND SUITABILITY SCORE",
    "get_pdf_report": "GET PDF REPORT",
    "share": "SHARE",
    "report_hint": "Opens the report preview. If locked, you can unlock the full PDF + 24h unlimited analyses.",
    "summary_title": "SUMMARY",
    "unlimited_active": "🔓 Unlimited active"
  }
}

tr_adds = {
  "index": {
    "title": "BeeLocate PRO v3.9",
    "tooltip_my_reports": "Raporlarım",
    "tooltip_contact": "İletişim",
    "metric": "Metrik",
    "imperial": "İmperyal",
    "basemap_sat_labels": "Uydu + Etiketler (Mapbox)",
    "basemap_sat": "Uydu (Mapbox)",
    "basemap_map": "Harita (Mapbox)",
    "basemap_osm": "Harita (OSM yedeği)",
    "labels": "Etiketler",
    "search_placeholder": "Konum ara... (örn., Sarıcakaya)",
    "scan_radius": "Tarama yarıçapı (tampon)",
    "run_analysis": "ANALİZİ BAŞLAT",
    "initializing_system": "SİSTEM BAŞLATILIYOR...",
    "overall_score": "GENEL PUAN",
    "analysis_period": "Analiz dönemi:",
    "recommended_season": "🌿 TAVSİYE EDİLEN SEZON",
    "live_current": "⚡ CANLI / ŞU AN",
    "jan_sim": "OCA (Simülasyon)",
    "feb_sim": "ŞUB (Simülasyon)",
    "mar_sim": "MAR (Simülasyon)",
    "apr_sim": "NİS (Simülasyon)",
    "may_sim": "MAY (Simülasyon)",
    "jun_sim": "HAZ (Simülasyon)",
    "jul_sim": "TEM (Simülasyon)",
    "aug_sim": "AĞU (Simülasyon)",
    "sep_sim": "EYL (Simülasyon)",
    "oct_sim": "EKİ (Simülasyon)",
    "nov_sim": "KAS (Simülasyon)",
    "dec_sim": "ARA (Simülasyon)",
    "managed_water": "Yönetilen Su",
    "land_suitability_score": "ARAZİ UYGUNLUK PUANI",
    "get_pdf_report": "PDF RAPORU AL",
    "share": "PAYLAŞ",
    "report_hint": "Rapor önizlemesini açar. Kilitliyse, tam PDF'i ve 24 sa sınırsız analizi açabilirsiniz.",
    "summary_title": "ÖZET",
    "unlimited_active": "🔓 Sınırsız aktif"
  }
}

for lang, adds in [("en", en_adds), ("tr", tr_adds)]:
    path = f"/Users/alperbayrakdar/Desktop/beelocate-main-1/translations/{lang}.json"
    with open(path, "r") as f:
        data = json.load(f)
    data.update(adds)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
