#!/usr/bin/env python3
import os
import sys
from pathlib import Path

# Project root
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mrktahsilat.settings')

import django
django.setup()

from tahsilat import views as tahsilat_views
from tahsilat.mssql_service import mssql_service

OUTPUT = '/tmp/genel_ekstre_generated.pdf'

def main():
    print('Fetching data from MSSQL via mssql_service...')
    try:
        data = mssql_service.get_cari_bakiye_all_plasiyers(bolge=None, cari_tipi=None) or []
        print(f'Fetched {len(data)} records')
    except Exception as e:
        print('Error fetching data:', e)
        data = []

    if not data:
        print('No data to generate PDF — exiting.')
        return 1

    print('Calling generate_genel_ekstre_pdf...')
    resp = tahsilat_views.generate_genel_ekstre_pdf(data, plasiyer='', bolge_filter='all', cari_tipi='')
    try:
        content = resp.content
    except Exception as e:
        print('Failed to get content from response:', e)
        return 2

    with open(OUTPUT, 'wb') as f:
        f.write(content)

    print('Wrote PDF to', OUTPUT)
    return 0

if __name__ == '__main__':
    sys.exit(main())
