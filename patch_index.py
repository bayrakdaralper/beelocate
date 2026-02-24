import os

path = '/Users/alperbayrakdar/Desktop/beelocate-main-1/templates/index.html'
with open(path, 'r') as f:
    c = f.read()

repls = [
    ('<title>BeeLocate PRO v3.9</title>', '<title>{{ t(\'index.title\') }}</title>'),
    ('data-tippy-content="My reports"', 'data-tippy-content="{{ t(\'index.tooltip_my_reports\') }}"'),
    ('data-tippy-content="Contact"', 'data-tippy-content="{{ t(\'index.tooltip_contact\') }}"'),
    ('>Metric<', '>{{ t(\'index.metric\') }}<'),
    ('>Imperial<', '>{{ t(\'index.imperial\') }}<'),
    ('Satellite + Labels (Mapbox)', '{{ t(\'index.basemap_sat_labels\') }}'),
    ('>Satellite (Mapbox)<', '>{{ t(\'index.basemap_sat\') }}<'),
    ('>Map (Mapbox)<', '>{{ t(\'index.basemap_map\') }}<'),
    ('Map (OSM fallback)', '{{ t(\'index.basemap_osm\') }}'),
    ('<span class="hidden sm:inline">Labels</span>', '<span class="hidden sm:inline">{{ t(\'index.labels\') }}</span>'),
    ('placeholder="Search location... (e.g., Sarıcakaya)"', 'placeholder="{{ t(\'index.search_placeholder\') }}"'),
    ('>Scan radius (buffer)<', '>{{ t(\'index.scan_radius\') }}<'),
    ('>RUN ANALYSIS<', '>{{ t(\'index.run_analysis\') }}<'),
    ('\n                INITIALIZING SYSTEM...\n            </div>', '\n                {{ t(\'index.initializing_system\') }}\n            </div>'),
    ('>OVERALL SCORE<', '>{{ t(\'index.overall_score\') }}<'),
    ('>Analysis period:<', '>{{ t(\'index.analysis_period\') }}<'),
    ('🌿 RECOMMENDED SEASON', '{{ t(\'index.recommended_season\') }}'),
    ('⚡ LIVE / CURRENT', '{{ t(\'index.live_current\') }}'),
    ('>JAN (Simulation)<', '>{{ t(\'index.jan_sim\') }}<'),
    ('>FEB (Simulation)<', '>{{ t(\'index.feb_sim\') }}<'),
    ('>MAR (Simulation)<', '>{{ t(\'index.mar_sim\') }}<'),
    ('>APR (Simulation)<', '>{{ t(\'index.apr_sim\') }}<'),
    ('>MAY (Simulation)<', '>{{ t(\'index.may_sim\') }}<'),
    ('>JUN (Simulation)<', '>{{ t(\'index.jun_sim\') }}<'),
    ('>JUL (Simulation)<', '>{{ t(\'index.jul_sim\') }}<'),
    ('>AUG (Simulation)<', '>{{ t(\'index.aug_sim\') }}<'),
    ('>SEP (Simulation)<', '>{{ t(\'index.sep_sim\') }}<'),
    ('>OCT (Simulation)<', '>{{ t(\'index.oct_sim\') }}<'),
    ('>NOV (Simulation)<', '>{{ t(\'index.nov_sim\') }}<'),
    ('>DEC (Simulation)<', '>{{ t(\'index.dec_sim\') }}<'),
    ('>Managed Water<', '>{{ t(\'index.managed_water\') }}<'),
    ('LAND\n                                SUITABILITY SCORE', '{{ t(\'index.land_suitability_score\') }}'),
    ('LAND\r\n                                SUITABILITY SCORE', '{{ t(\'index.land_suitability_score\') }}'),
    ('>GET PDF REPORT<', '>{{ t(\'index.get_pdf_report\') }}<'),
    ('>SHARE<', '>{{ t(\'index.share\') }}<'),
    ('Opens the report preview. If locked, you can unlock the full PDF + 24h unlimited analyses.', '{{ t(\'index.report_hint\') }}'),
    ('>SUMMARY<', '>{{ t(\'index.summary_title\') }}<'),
    ('🔓 Unlimited active', '{{ t(\'index.unlimited_active\') }}')
]

for o, n in repls:
    c = c.replace(o, n)

with open(path, 'w') as f:
    f.write(c)

print('Done')
