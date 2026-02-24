import i18n
import json

# Force loading if not already
i18n.load_translations()

# Case 1: Key exists in TR
val1 = i18n.t("index.title", lang="tr")
print("TR index.title:", val1)

# Case 2: Key missing in TR, exists in EN
# Let's temporarily inject a test string in EN
i18n.STRINGS["en"]["test_fallback"] = "English Fallback Text"
if "test_fallback" in i18n.STRINGS.get("tr", {}):
    del i18n.STRINGS["tr"]["test_fallback"]
val2 = i18n.t("test_fallback", lang="tr")
print("TR test_fallback (should be EN):", val2)

# Case 3: Key missing everywhere, default_text provided
val3 = i18n.t("missing.key", default_text="Safe Default", lang="tr")
print("missing.key with default:", val3)

# Case 4: Key missing everywhere, no default
val4 = i18n.t("completely.missing.key", lang="tr")
print("completely.missing.key (should return key):", val4)

# Case 5: Entire language disabled/missing (e.g. 'fr')
val5 = i18n.t("index.title", lang="fr")
print("FR missing lang (should fallback to EN index.title):", val5)
