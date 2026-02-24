import i18n
import json

i18n.load_translations()

# Mocking Flask Request
class MockHeaders:
    def __init__(self, headers):
        self.headers = headers
    def get(self, key, default=""):
        return self.headers.get(key, default)

class MockRequest:
    def __init__(self, cookies, headers):
        self.cookies = cookies
        self.headers = MockHeaders(headers)

import flask
flask.has_request_context = lambda: True

# Test 1: Active Cookie
flask.request = MockRequest(cookies={"blp_lang": "tr"}, headers={})
print("Test 1 (Cookie=tr):", i18n.get_lang())

# Test 2: Active Cookie overrides Header
flask.request = MockRequest(cookies={"blp_lang": "en"}, headers={"Accept-Language": "tr-TR,tr;q=0.9"})
print("Test 2 (Cookie=en, Header=tr):", i18n.get_lang())

# Test 3: No Cookie, TR Header Header
flask.request = MockRequest(cookies={}, headers={"Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"})
print("Test 3 (No Cookie, Header=tr):", i18n.get_lang())

# Test 4: No Cookie, FR Header (fallback to EN expected per rule)
flask.request = MockRequest(cookies={}, headers={"Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"})
print("Test 4 (No Cookie, Header=fr):", i18n.get_lang())

# Test 5: Missing Context (e.g. CLI)
flask.has_request_context = lambda: False
print("Test 5 (CLI, no context):", i18n.get_lang())
