"""Map configuration is pure; no external requests and no inventory writes."""
import json
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.template import Context, Template

from .context_processors import fibergenius_ui
from .map_configuration import map_configuration, provider


CARTO = 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png'
ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
STREETS = ESRI.replace('World_Imagery', 'World_Street_Map')


class MapConfigurationTests(SimpleTestCase):
    def test_key_encoded_without_breaking_tile_placeholders(self):
        result = provider(CARTO + '?language=es#part', 'custom', ' a&b/?= ')
        self.assertIn('/{z}/{x}/{y}{r}.png', result['url'])
        self.assertEqual(parse_qs(urlsplit(result['url']).query), {'language': ['es'], 'key': ['a&b/?=']})
        self.assertFalse(result['missingKey'])
        self.assertEqual(urlsplit(result['url']).fragment, 'part')

    def test_existing_url_key_has_precedence(self):
        self.assertEqual(provider(CARTO + '?key=existing', '', 'new')['url'], CARTO + '?key=existing')

    def test_empty_url_key_is_replaced_not_duplicated(self):
        self.assertEqual(provider(CARTO + '?key=&x=1', '', 'new')['url'], CARTO + '?x=1&key=new')

    def test_only_exact_carto_https_hosts_receive_key(self):
        for url in (ESRI, STREETS, 'https://maps.internal/{z}', 'https://basemaps.cartocdn.com.evil.test/{z}',
                    'https://evilbasemaps.cartocdn.com/{z}', 'http://basemaps.cartocdn.com/{z}'):
            with self.subTest(url=url):
                self.assertEqual(provider(url, '', 'only-carto')['url'], url)

    def test_missing_key_only_for_carto(self):
        self.assertTrue(provider(CARTO, '')['missingKey'])
        self.assertFalse(provider(ESRI, '')['missingKey'])
        self.assertFalse(provider('', '')['missingKey'])

    def test_provider_specific_attributions(self):
        self.assertIn('OpenStreetMap', provider(CARTO, '')['attribution'])
        self.assertIn('Esri', provider(ESRI, '')['attribution'])
        self.assertNotIn('CARTO', provider(ESRI, '')['attribution'])
        self.assertIn('Esri', provider(STREETS, '')['attribution'])
        self.assertNotIn('CARTO', provider(STREETS, '')['attribution'])
        self.assertFalse(provider(STREETS, '')['missingKey'])
        self.assertEqual(provider('https://internal/{z}', 'Internal maps')['attribution'], 'Internal maps')

    @override_settings(FIBERGENIUS_CARTO_API_KEY='test-not-real', FIBERGENIUS_MAP_FALLBACK_ENABLED=False,
                       FIBERGENIUS_MAP_TILE_STREETS='')
    def test_disable_auto_fallback_and_support_internal_urls(self):
        result = map_configuration('https://internal/{z}', '', '', 'Internal')
        self.assertFalse(result['fallbackEnabled'])
        self.assertEqual(result['light']['url'], 'https://internal/{z}')
        self.assertEqual(result['satellite']['url'], '')
        self.assertEqual(result['streets']['url'], '')

    @override_settings(FIBERGENIUS_MAP_TILE_STREETS=STREETS, FIBERGENIUS_CARTO_API_KEY='test-not-real')
    def test_street_backup_is_independent_of_satellite(self):
        result = map_configuration(CARTO, CARTO, ESRI, '')
        self.assertEqual(result['streets']['url'], STREETS)
        self.assertEqual(result['satellite']['url'], ESRI)
        self.assertNotIn('key=', result['streets']['url'])

    @override_settings(MAP_TILE_URL_STREETS='https://internal.test/streets/{z}/{x}/{y}',
                       FIBERGENIUS_MAP_TILE_STREETS=STREETS)
    def test_production_street_override_has_precedence(self):
        result = map_configuration(CARTO, CARTO, ESRI, 'Internal')
        self.assertEqual(result['streets']['url'], 'https://internal.test/streets/{z}/{x}/{y}')
        self.assertEqual(result['streets']['attribution'], 'Internal')

    @override_settings(MAP_TILE_URL_LIGHT=CARTO, MAP_TILE_URL_DARK=CARTO,
                       MAP_TILE_URL_SATELLITE=ESRI, MAP_TILE_ATTRIBUTION='custom',
                       FIBERGENIUS_CARTO_API_KEY='not-a-real-key')
    def test_production_and_inventory_context_use_same_resolved_urls(self):
        from fibergenius.context_processors import deployment
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        result = fibergenius_ui(request)
        self.assertEqual(result['MAP_TILE_URL_LIGHT'], deployment(request)['MAP_TILE_URL_LIGHT'])
        self.assertEqual(result['fg_map_config']['light']['url'], result['MAP_TILE_URL_LIGHT'])
        self.assertIn('key=not-a-real-key', result['fg_map_tile_light'])
        self.assertNotIn('key=', result['fg_map_tile_satellite'])

    def test_json_script_escapes_configuration_not_executable_markup(self):
        config = map_configuration(CARTO, '', ESRI, '</script><script>bad</script>')
        rendered = Template('{{ config|json_script:"fg-map-config" }}').render(Context({'config': config}))
        self.assertNotIn('</script><script>', rendered)
        payload = rendered.split('>', 1)[1].rsplit('</script>', 1)[0]
        self.assertEqual(json.loads(payload), config)
