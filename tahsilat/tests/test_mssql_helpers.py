from django.test import SimpleTestCase

from tahsilat.services.mssql.common import build_lookup_candidates, fix_turkish_encoding
from tahsilat.services.mssql.tahsilat import build_gunluk_tahsilat_filters


class MSSQLHelperTests(SimpleTestCase):
    def test_build_gunluk_tahsilat_filters_reuses_shared_order(self):
        conditions, params = build_gunluk_tahsilat_filters(
            cari_kod='120',
            tahsilat_turu='Nakit',
            kullanici='FIRAT',
            banka='garanti',
            plasiyer_filter='Ali',
            tarih_filtresi='today',
            banka_column='[BANKAADI]',
        )

        self.assertIn("UPPER([CariKod]) LIKE UPPER(?)", conditions)
        self.assertIn("CAST([Tarih] AS DATE) = CAST(GETDATE() AS DATE)", conditions)
        self.assertIn("UPPER([BANKAADI]) LIKE UPPER(?)", conditions)
        self.assertEqual(params, ['%120%', 'Nakit', '%garanti%', '%FIRAT%', '%Ali%'])

    def test_lookup_candidates_include_ascii_and_original_forms(self):
        candidates = build_lookup_candidates('Oğuz')
        self.assertIn('Oğuz', candidates)
        self.assertIn('OGUZ', {candidate.upper() for candidate in candidates})

    def test_fix_turkish_encoding_preserves_expected_characters(self):
        self.assertEqual(fix_turkish_encoding('EYÃœP'), 'EYÜP')
