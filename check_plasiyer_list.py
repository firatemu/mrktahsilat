from tahsilat.mssql_service import MSSQLService

service = MSSQLService()
query = "SELECT DISTINCT [Plasiyer] FROM [GO3].[dbo].[GunlukTahsilat_V] WHERE [Plasiyer] IS NOT NULL AND [Plasiyer] != '' ORDER BY [Plasiyer]"
results = service.execute_query(query)
distinct_plasiyers = [row['Plasiyer'] for row in results]

print("Found Plasiyers:", distinct_plasiyers)

# Check which ones from the hardcoded list are missing
hardcoded = ['ALİ', 'ATAKAN', 'AZİZ', 'EYÜP', 'HASAN', 'MERT', 'SÜLEYMAN', 'YİĞİT']
missing_in_db = [p for p in hardcoded if p not in distinct_plasiyers]
new_in_db = [p for p in distinct_plasiyers if p not in hardcoded]

print("Hardcoded but not in DB (Non-existent?):", missing_in_db)
print("In DB but not hardcoded (Missing?):", new_in_db)
