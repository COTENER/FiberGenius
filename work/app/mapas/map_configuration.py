"""Client-side raster providers. Never send the CARTO key to another host."""
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

from django.conf import settings


def is_carto(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    return parsed.scheme == 'https' and (
        host == 'basemaps.cartocdn.com' or host.endswith('.basemaps.cartocdn.com')
    )


def provider(url, attribution, key=''):
    carto = is_carto(url)
    parts = urlsplit(url)
    has_key = any(k == 'key' and v.strip() for k, v in parse_qsl(parts.query))
    # An explicit URL key takes precedence (supports installations with separate keys).
    if carto and key.strip() and not has_key:
        query = '&'.join(p for p in parts.query.split('&') if p and p.split('=')[0] != 'key')
        query += ('&' if query else '') + 'key=' + quote(key.strip(), safe='')
        url = urlunsplit(parts._replace(query=query))
        has_key = True
    host = (parts.hostname or '').lower()
    if carto:
        attribution = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
    elif host == 'server.arcgisonline.com' and '/World_Imagery/' in parts.path:
        attribution = 'Tiles &copy; Esri — Sources: Esri, Maxar, Earthstar Geographics, and the GIS User Community'
    elif host == 'server.arcgisonline.com' and '/World_Street_Map/' in parts.path:
        attribution = 'Tiles &copy; Esri — Sources: Esri, HERE, Garmin, USGS, Intermap, INCREMENT P, NRCan, Esri Japan, METI, Esri China (Hong Kong), Esri Korea, Esri (Thailand), NGCC, &copy; OpenStreetMap contributors, and the GIS User Community'
    return {'url': url, 'attribution': attribution, 'missingKey': carto and not has_key}


def map_configuration(light, dark, satellite, attribution):
    key = getattr(settings, 'FIBERGENIUS_CARTO_API_KEY', '')
    streets = getattr(settings, 'MAP_TILE_URL_STREETS',
                      getattr(settings, 'FIBERGENIUS_MAP_TILE_STREETS', ''))
    return {
        'light': provider(light, attribution, key),
        'dark': provider(dark, attribution, key),
        'streets': provider(streets, attribution, key),
        'satellite': provider(satellite, attribution, key),
        'fallbackEnabled': getattr(settings, 'FIBERGENIUS_MAP_FALLBACK_ENABLED', True),
    }
