"""Regresiones GUI: acceso, errores de formularios y navegación de Sites."""
from html.parser import HTMLParser
from unittest.mock import patch

from django.contrib.auth.models import User, Group, Permission
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve

from .models import HubSite
from .ui_access import IMPORT_PERMISSIONS, navigation_access


class GuiAdministracionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create(username='gui-admin', is_superuser=True, is_staff=True)
        cls.reader = User.objects.create(username='gui-reader')
        cls.group = Group.objects.create(name='Rol existente')
        cls.permission = Permission.objects.get(content_type__app_label='mapas', codename='view_ruta')
        cls.group.permissions.add(cls.permission)

    def grant(self, *names):
        permissions = [Permission.objects.get(content_type__app_label=name.split('.')[0], codename=name.split('.')[1]) for name in names]
        self.reader.user_permissions.add(*permissions)
        self.reader = User.objects.get(pk=self.reader.pk)

    def request(self, path, user=None, data=None, ajax=False):
        factory = RequestFactory()
        request = factory.post(path, data) if data is not None else factory.get(path)
        request.user = user or self.admin
        request.session = {}
        request._messages = FallbackStorage(request)
        if ajax:
            request.META['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        match = resolve(request.path)
        request.resolver_match = match
        return match.func(request, *match.args, **match.kwargs)

    def selected_permissions(self, response):
        selected = set()
        class Parser(HTMLParser):
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'input' and attrs.get('name') == 'permissions' and 'checked' in attrs:
                    selected.add(attrs['value'])
        Parser().feed(response.content.decode())
        return selected

    def test_summary_menu_matches_existing_backend_authority(self):
        self.grant('mapas.can_view_reports')
        self.assertFalse(navigation_access(self.reader)['summary'])
        with self.assertRaises(PermissionDenied):
            self.request('/inventario/dashboard/', self.reader)
        self.grant('mapas.view_ruta')
        self.assertTrue(navigation_access(self.reader)['summary'])
        self.assertEqual(self.request('/inventario/dashboard/', self.reader).status_code, 200)

    def test_odf_importer_has_menu_without_route_permissions(self):
        self.grant(*IMPORT_PERMISSIONS['odf_inventario'])
        self.assertTrue(navigation_access(self.reader)['imports'])
        response = self.request('/Configuracion/', self.reader)
        self.assertContains(response, 'data-open-import="odf_inventario"')
        self.assertNotContains(response, 'data-open-import="sites_inventario"')

    def test_partial_import_permission_is_not_enough(self):
        self.grant('mapas.add_inventarioodf')
        self.assertFalse(navigation_access(self.reader)['imports'])
        with self.assertRaises(PermissionDenied):
            self.request('/Configuracion/', self.reader)

    def test_read_only_users_and_groups_hide_mutations(self):
        self.grant('auth.view_user', 'auth.view_group')
        self.assertNotContains(self.request('/usuarios/', self.reader), 'href="/usuarios/crear/"')
        self.assertNotContains(self.request('/grupos/', self.reader), 'href="/grupos/crear/"')
        with self.assertRaises(PermissionDenied):
            self.request('/grupos/crear/', self.reader)

    def test_invalid_new_role_preserves_selection_without_saving(self):
        before = Group.objects.count()
        response = self.request('/grupos/crear/', data={'name': '', 'permissions': [self.permission.pk]}, ajax=True)
        self.assertEqual(self.selected_permissions(response), {str(self.permission.pk)})
        self.assertEqual(Group.objects.count(), before)

    def test_invalid_edit_preserves_submitted_empty_selection_and_database(self):
        from django.urls import reverse
        response = self.request(reverse('editar_grupo', args=[self.group.pk]), data={'name': ''}, ajax=True)
        self.assertEqual(self.selected_permissions(response), set())
        self.assertEqual(list(self.group.permissions.all()), [self.permission])

    def test_role_can_be_resubmitted_after_validation_error(self):
        self.request('/grupos/crear/', data={'name': '', 'permissions': [self.permission.pk]}, ajax=True)
        response = self.request('/grupos/crear/', data={'name': 'Rol corregido', 'permissions': [self.permission.pk]}, ajax=True)
        self.assertJSONEqual(response.content, {'success': True})
        self.assertEqual(list(Group.objects.get(name='Rol corregido').permissions.all()), [self.permission])

    def test_ajax_user_success_is_explicit_not_a_redirect(self):
        with patch('mapas.views.usuarios.CustomUserCreationForm') as form:
            form.return_value.is_valid.return_value = True
            response = self.request('/usuarios/crear/', data={'username': 'example'}, ajax=True)
            self.assertJSONEqual(response.content, {'success': True})
            form.return_value.save.assert_called_once()

    def test_sites_paginate_and_keep_filter(self):
        HubSite.objects.bulk_create([HubSite(nombre=f'SITE-{i:02d}') for i in range(30)])
        response = self.request('/administracion/sites/?q=SITE-&page=2')
        self.assertContains(response, 'SITE-25')
        self.assertNotContains(response, 'SITE-00')
        self.assertContains(response, 'q=SITE-&amp;page=1')
        self.assertContains(response, 'Página 2 de 2')

    def test_sites_reader_and_central_import_link(self):
        self.grant('mapas.view_hubsite')
        response = self.request('/administracion/sites/', self.reader)
        self.assertNotContains(response, 'onclick="openModal')
        response = self.request('/administracion/sites/')
        self.assertContains(response, '?importar=sites_inventario')
        self.assertNotContains(response, 'id="importModal"')

    @override_settings(FIBERGENIUS_OPERATIONS_UI_ENABLED=False)
    def test_disabled_operations_never_execute_queries(self):
        for path in ['/dashboard/', '/ranking/', '/mapa-alarmas/', '/configuracion/umbrales/']:
            with self.subTest(path=path), self.assertNumQueries(0), self.assertRaises(Http404):
                self.request(path)

    @override_settings(FIBERGENIUS_OPERATIONS_UI_ENABLED=True)
    def test_enabled_operations_still_require_permission(self):
        with self.assertRaises(PermissionDenied):
            self.request('/dashboard/', self.reader)

    def test_users_translation_does_not_request_cdn(self):
        response = self.request('/usuarios/')
        self.assertNotContains(response, 'cdn.datatables.net')
        self.assertContains(response, 'js/admin-modal.js')

    def test_role_summary_uses_the_actual_screen_permission(self):
        from .views.usuarios import obtener_permisos_agrupados
        grouped = obtener_permisos_agrupados()
        self.assertEqual([p.codename for p in grouped['Inventario']['Dashboard Inventario']], ['view_ruta'])
