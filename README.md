# Aplikasi Streamlit Harga Harian Telur & Daging Ayam

Aplikasi ini digunakan untuk mencatat harga harian:

- Telur Ayam Ras
- Daging Ayam / Ayam Broiler

Fitur utama:

- Ambil harga otomatis dari sumber publik.
- Sumber otomatis utama: Simponi Ternak Kementan.
- Sumber cadangan: SP2KP Kemendag dan PIHPS BI.
- Input manual jika scraping otomatis gagal.
- Simpan data ke `data/harga_harian.csv`.
- Tampilkan tabel, grafik, dan download CSV/Excel.

## Cara menjalankan

1. Ekstrak ZIP.
2. Buka terminal di folder hasil ekstrak.
3. Jalankan:

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Catatan penting

Situs publik dapat berubah struktur HTML atau memuat data lewat JavaScript. Karena itu, aplikasi ini menyediakan input manual agar tabel tetap bisa digunakan meskipun sumber otomatis sedang berubah atau maintenance.

Untuk sumber Simponi Ternak, komoditas `Ayam Broiler` digunakan sebagai padanan harga daging ayam.
