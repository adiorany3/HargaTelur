# Aplikasi Streamlit Harga Harian Telur dan Daging Ayam

Aplikasi ini digunakan untuk mencari/mengambil harga harian **Telur Ayam Ras** dan **Daging Ayam Ras**, lalu menyimpannya ke dalam tabel lokal berbentuk CSV.

## Fitur

- Ambil harga otomatis dari sumber publik.
- Simpan data harian ke `data/harga_harian.csv`.
- Input manual jika pengambilan otomatis gagal.
- Tabel data dengan filter tanggal dan komoditas.
- Grafik perkembangan harga.
- Download data dalam format CSV dan Excel.

## Cara Menjalankan

1. Ekstrak file ZIP.
2. Buka terminal pada folder proyek.
3. Install dependensi:

```bash
pip install -r requirements.txt
```

4. Jalankan aplikasi:

```bash
streamlit run app.py
```

5. Buka alamat lokal yang muncul, biasanya:

```text
http://localhost:8501
```

## Sumber Data

Sumber otomatis utama diarahkan ke halaman publik SP2KP Kemendag. Opsi PIHPS BI juga disediakan sebagai cadangan/eksperimental.

Penting: struktur situs publik dapat berubah. Jika tombol pengambilan otomatis gagal, data tetap bisa dimasukkan lewat form **Input Manual / Koreksi Data**.

## Struktur Folder

```text
harga_telur_ayam_streamlit/
├── app.py
├── requirements.txt
├── README.md
├── .streamlit/
│   └── config.toml
└── data/
    └── harga_harian.csv
```

## Catatan Penggunaan Harian

Untuk pencatatan harian, buka aplikasi setiap hari lalu klik **Ambil harga otomatis**. Data dengan kombinasi tanggal, komoditas, dan sumber yang sama akan diperbarui, bukan digandakan.
