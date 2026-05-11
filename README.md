# Pantau Harga Telur & Daging Ayam Harian

Aplikasi Streamlit untuk mengambil dan menyimpan harga harian **Telur Ayam Ras** dan **Daging Ayam Ras** ke tabel lokal.

## Fitur

- Ambil harga otomatis dari beberapa sumber publik:
  - SP2KP Kemendag Nasional
  - DPKP DIY
  - Dataku Salatiga
- Input manual bila sumber otomatis gagal.
- Simpan data ke `data/harga_telur_ayam.csv`.
- Tampilkan tabel, grafik tren, dan tombol download CSV/Excel.
- Mekanisme `upsert`: data dengan tanggal dan sumber yang sama akan diperbarui, bukan diduplikasi.

## Cara Menjalankan

1. Ekstrak file ZIP.
2. Buka terminal di folder proyek.
3. Jalankan perintah berikut:

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Catatan Penting

Situs sumber harga publik dapat berubah struktur atau memblokir request otomatis. Jika muncul error seperti `403 Forbidden`, gunakan mode **Manual** atau sesuaikan fungsi `fetch_*` di `app.py`.

Versi ini **tidak lagi menggunakan Simponi Ternak Kementan** karena situs tersebut sering menolak request otomatis dengan error `403 Forbidden`.
