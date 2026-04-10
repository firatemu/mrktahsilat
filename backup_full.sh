#!/bin/bash

# MrkTahsilat Projesi - Tam Yedekleme Scripti
# Tarih: $(date +%Y-%m-%d %H:%M:%S)

echo "=== MrkTahsilat Projesi Tam Yedekleme ==="
echo "Başlangıç: $(date)"

# Değişkenler
PROJECT_DIR="/var/www/mrktahsilat"
BACKUP_DIR="/var/backups/mrktahsilat"
DATE_STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="mrktahsilat_full_backup_${DATE_STAMP}"

# Yedek dizini oluştur
sudo mkdir -p $BACKUP_DIR
cd $BACKUP_DIR

echo "1. Proje dosyalarının yedeği alınıyor..."
sudo tar --exclude='__pycache__' \
         --exclude='*.pyc' \
         --exclude='*.log' \
         --exclude='venv' \
         --exclude='staticfiles' \
         --exclude='*.sock' \
         -czf "${BACKUP_NAME}_code.tar.gz" \
         -C /var/www mrktahsilat/

echo "2. Veritabanı yedeği alınıyor..."
cd $PROJECT_DIR
python3 manage.py dumpdata --output="${BACKUP_DIR}/${BACKUP_NAME}_database.json"

echo "3. Sistem ayarları yedeği alınıyor..."
sudo tar -czf "${BACKUP_DIR}/${BACKUP_NAME}_system.tar.gz" \
    /etc/systemd/system/gunicorn_mrktahsilat.service \
    /etc/nginx/sites-available/mrktahsilat \
    /etc/nginx/sites-enabled/mrktahsilat 2>/dev/null || true

echo "4. Yedek dosyaları kontrol ediliyor..."
cd $BACKUP_DIR
ls -la ${BACKUP_NAME}*

echo "5. Yedek dosyalarının boyutları:"
du -sh ${BACKUP_NAME}*

echo "6. Yedek dosyalarının bütünlüğü kontrol ediliyor..."
for file in ${BACKUP_NAME}*.tar.gz; do
    if tar -tzf "$file" >/dev/null 2>&1; then
        echo "✅ $file - Bütünlük OK"
    else
        echo "❌ $file - Bütünlük HATALI"
    fi
done

# JSON dosyası kontrol
if [ -f "${BACKUP_NAME}_database.json" ]; then
    if python3 -m json.tool "${BACKUP_NAME}_database.json" >/dev/null 2>&1; then
        echo "✅ ${BACKUP_NAME}_database.json - Geçerli JSON"
    else
        echo "❌ ${BACKUP_NAME}_database.json - Geçersiz JSON"
    fi
fi

echo "7. Tek dosya halinde birleştiriliyor..."
sudo tar -czf "${BACKUP_NAME}_COMPLETE.tar.gz" ${BACKUP_NAME}_*

echo "8. Son kontrol..."
COMPLETE_SIZE=$(du -sh "${BACKUP_NAME}_COMPLETE.tar.gz" | cut -f1)
echo "📦 Tam yedek dosyası: ${BACKUP_NAME}_COMPLETE.tar.gz ($COMPLETE_SIZE)"

echo ""
echo "=== YEDEKLEME TAMAMLANDI ==="
echo "Bitiş: $(date)"
echo "📍 Yedek Konumu: $BACKUP_DIR"
echo "📁 Ana Dosya: ${BACKUP_NAME}_COMPLETE.tar.gz"
echo ""
echo "Yedek İçeriği:"
echo "- Proje kaynak kodları"
echo "- Veritabanı (JSON export)"
echo "- Sistem ayar dosyaları"
echo "- Nginx konfigürasyonu"
echo "- Gunicorn servis dosyası"
echo ""
echo "Geri Yükleme için:"
echo "1. ${BACKUP_NAME}_COMPLETE.tar.gz dosyasını hedef sunucuya kopyala"
echo "2. tar -xzf ${BACKUP_NAME}_COMPLETE.tar.gz"
echo "3. Proje dosyalarını /var/www/mrktahsilat'a kopyala"
echo "4. python3 manage.py loaddata database.json"
echo "5. Sistem dosyalarını ilgili dizinlere kopyala"
echo "6. Servisleri yeniden başlat"