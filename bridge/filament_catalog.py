"""Read-only public SpoolmanDB lookup, independent of the Spoolman service.

Source: https://github.com/Donkie/SpoolmanDB (MIT).
Search queries stay on this server; only the fixed public JSON is downloaded.
"""
import json
import threading
import time
import urllib.request

CATALOG_URL = 'https://donkie.github.io/SpoolmanDB/filaments.json'


class CatalogUnavailable(ValueError):
    pass


class FilamentCatalog:
    def __init__(self, loader=None, ttl=21600):
        self.loader = loader or self._download
        self.ttl = ttl
        self._lock = threading.Lock()
        self._rows = None
        self._index = {}
        self._loaded_at = 0
        self._retry_at = 0

    @staticmethod
    def _download():
        with urllib.request.urlopen(CATALOG_URL, timeout=12) as response:
            payload = response.read(16 * 1024 * 1024 + 1)
        if len(payload) > 16 * 1024 * 1024:
            raise CatalogUnavailable('Der Katalog ist größer als erwartet.')
        return json.loads(payload)

    def _load(self):
        with self._lock:
            now = time.monotonic()
            if self._rows is not None and (now-self._loaded_at < self.ttl or now < self._retry_at):
                return now-self._loaded_at >= self.ttl
            if self._rows is None and now < self._retry_at:
                raise CatalogUnavailable('Der öffentliche Katalog ist gerade nicht erreichbar. Bitte später erneut suchen oder das Produkt manuell anlegen.')
            try:
                rows = self.loader()
                if not isinstance(rows, list) or len(rows) > 50000:
                    raise ValueError('Invalid catalog')
                valid = [r for r in rows if isinstance(r, dict) and isinstance(r.get('id'), str) and isinstance(r.get('name'), str) and isinstance(r.get('manufacturer'), str) and r['id'] and r['name'].strip() and r['manufacturer'].strip()]
                index = {r['id']: r for r in valid}
                if not valid or len(index) != len(valid):
                    raise ValueError('Ambiguous catalog')
                self._rows, self._index, self._loaded_at = valid, index, now
                self._retry_at = 0
                return False
            except Exception as exc:
                self._retry_at = now+60
                if self._rows is not None:
                    return True
                raise CatalogUnavailable('Der öffentliche Katalog ist gerade nicht erreichbar. Du kannst das Produkt weiterhin manuell anlegen.') from exc

    def search(self, query, limit=30):
        query = query.strip().casefold()
        if not 2 <= len(query) <= 100:
            raise ValueError('Bitte mindestens zwei und höchstens 100 Zeichen eingeben.')
        stale = self._load()
        terms = query.split()
        matches = []
        total = 0
        for row in self._rows:
            haystack = ' '.join(str(row.get(key) or '') for key in ('manufacturer', 'name', 'material', 'color_name', 'color_hex')).casefold()
            if all(term in haystack for term in terms):
                total += 1
                if len(matches) < limit:
                    matches.append({key: row.get(key) for key in ('id','manufacturer','name','material','density','weight','diameter','color_hex','color_name')})
        return dict(results=matches, total=total, stale=stale, source=CATALOG_URL)

    def get(self, identifier):
        self._load()
        row = self._index.get(identifier)
        if row is None:
            raise ValueError('Das ausgewählte Katalogprodukt ist nicht mehr verfügbar. Bitte erneut suchen oder manuell anlegen.')
        return dict(row)
