"""Reusable filter builders for tahsilat-oriented MSSQL queries."""


def build_gunluk_tahsilat_filters(
    cari_kod=None,
    cari_unvan=None,
    tarih_filtresi='all',
    tahsilat_turu=None,
    teslim_durumu=None,
    kullanici=None,
    banka=None,
    baslangic_tarihi=None,
    bitis_tarihi=None,
    plasiyer_filter=None,
    banka_column='[Banka]',
):
    where_conditions = []
    params = []

    if cari_kod:
        where_conditions.append("UPPER([CariKod]) LIKE UPPER(?)")
        params.append(f"%{cari_kod}%")

    if cari_unvan:
        where_conditions.append("UPPER([CariUnvan]) LIKE UPPER(?)")
        params.append(f"%{cari_unvan}%")

    if baslangic_tarihi and bitis_tarihi:
        where_conditions.append(
            "CAST([Tarih] AS DATE) >= ? AND CAST([Tarih] AS DATE) <= ?")
        params.extend([baslangic_tarihi, bitis_tarihi])
    elif baslangic_tarihi:
        where_conditions.append("CAST([Tarih] AS DATE) >= ?")
        params.append(baslangic_tarihi)
    elif bitis_tarihi:
        where_conditions.append("CAST([Tarih] AS DATE) <= ?")
        params.append(bitis_tarihi)
    elif tarih_filtresi == 'today':
        where_conditions.append("CAST([Tarih] AS DATE) = CAST(GETDATE() AS DATE)")
    elif tarih_filtresi == 'week':
        where_conditions.append(
            "CAST([Tarih] AS DATE) >= CAST(DATEADD(day, -7, GETDATE()) AS DATE)")
    elif tarih_filtresi == 'month':
        where_conditions.append(
            "CAST([Tarih] AS DATE) >= CAST(DATEADD(month, -1, GETDATE()) AS DATE)")

    if tahsilat_turu:
        where_conditions.append("[TahsilatTuru] = ?")
        params.append(tahsilat_turu)

    if banka:
        where_conditions.append(f"UPPER({banka_column}) LIKE UPPER(?)")
        params.append(f"%{banka}%")

    if teslim_durumu:
        where_conditions.append("[TeslimDurumu] = ?")
        params.append(teslim_durumu)

    if kullanici:
        where_conditions.append("UPPER([Kullanici]) LIKE UPPER(?)")
        params.append(f"%{kullanici}%")

    if plasiyer_filter:
        where_conditions.append("UPPER([Plasiyer]) LIKE UPPER(?)")
        params.append(f"%{plasiyer_filter}%")

    return where_conditions, params
