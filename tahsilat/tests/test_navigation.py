from django.contrib.auth.models import AnonymousUser, User
from django.test import TestCase

from tahsilat.models import KullaniciYetki
from tahsilat.services.navigation import (
    get_post_login_redirect_url,
    has_menu_permission,
)


class NavigationServiceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='OWNER')
        self.user = User.objects.create_user(username='TESTUSER')

    def grant(self, menu_name):
        return KullaniciYetki.objects.create(
            kullanici=self.user,
            menu_adi=menu_name,
            erisim_izni=True,
            olusturan=self.owner,
        )

    def test_has_menu_permission_infers_parent_from_child_permission(self):
        self.grant('muhasebe_tahsilat_listesi')
        self.assertTrue(has_menu_permission(self.user, 'muhasebe'))

    def test_muhasebe_yeni_tahsilat_does_not_grant_kdv_raporu(self):
        self.grant('muhasebe_yeni_tahsilat')
        self.assertTrue(has_menu_permission(self.user, 'muhasebe'))
        self.assertFalse(has_menu_permission(self.user, 'kdv_raporu'))

    def test_kdv_raporu_opens_muhasebe_parent_menu(self):
        self.grant('kdv_raporu')
        self.assertTrue(has_menu_permission(self.user, 'muhasebe'))

    def test_get_post_login_redirect_url_prefers_priority_menu(self):
        self.grant('chat')
        self.grant('perakende')
        self.assertEqual(get_post_login_redirect_url(self.user), '/perakende/')

    def test_has_menu_permission_rejects_anonymous_users(self):
        self.assertFalse(has_menu_permission(AnonymousUser(), 'dashboard'))

    def test_yapay_zeka_permission_is_independent(self):
        self.grant('yapay_zeka')
        self.assertTrue(has_menu_permission(self.user, 'yapay_zeka'))
        self.assertFalse(has_menu_permission(self.user, 'chat'))

    def test_plasiyer_prim_opens_genel_gorunum_parent_menu(self):
        self.grant('plasiyer_prim')
        self.assertTrue(has_menu_permission(self.user, 'genel_gorunum'))
        self.assertFalse(has_menu_permission(self.user, 'genel_dashboard'))
