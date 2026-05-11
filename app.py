import io
import re
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

APP_TITLE = "Pantau Harga Telur & Daging Ayam Harian"
DATA_DIR = Path("data")
DATA_FILE = DATA_DIR / "harga_telur_ayam.csv"

COMMODITIES = {
    "telur": [
        "Telur Ayam Ras",
        "Telur Ayam Ras Segar",
        "TELUR AYAM RAS",
    ],
    "daging": [
        "Daging Ayam Ras",
        "Daging Ayam Ras Segar",
        "DAGING AYAM RAS",
        "Ayam Broiler",
        "Daging Ayam",
    ],
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
}


# =========================
# Utilitas data lokal
# =========================
def ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        empty = pd.DataFrame(
            columns=[
                "tanggal",
                "harga_telur_ayam_ras",
                "harga_daging_ayam_ras",
                "sumber",
                "url_sumber",
                "catatan",
                "waktu_input",
            ]
        )
        empty.to_csv(DATA_FILE, index=False)


def load_data() -> pd.DataFrame:
    ensure_storage()
    df = pd.read_csv(DATA_FILE)
    if not df.empty:
        df["tanggal"] = pd.to_datetime(df["tanggal"], errors="coerce").dt.date
        for col in ["harga_telur_ayam_ras", "harga_daging_ayam_ras"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def save_data(df: pd.DataFrame) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out["tanggal"] = pd.to_datetime(out["tanggal"], errors="coerce").dt.strftime("%Y-%m-%d")
    out.to_csv(DATA_FILE, index=False)


def upsert_record(record: Dict) -> Tuple[pd.DataFrame, str]:
    df = load_data()
    tanggal = pd.to_datetime(record["tanggal"]).date()
    sumber = record["sumber"]

    record = record.copy()
    record["tanggal"] = tanggal
    record["waktu_input"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if df.empty:
        df = pd.DataFrame([record])
        action = "menambahkan"
    else:
        mask = (df["tanggal"] == tanggal) & (df["sumber"].astype(str) == str(sumber))
        if mask.any():
            for key, value in record.items():
                df.loc[mask, key] = value
            action = "memperbarui"
        else:
            df = pd.concat([df, pd.DataFrame([record])], ignore_index=True)
            action = "menambahkan"

    df = df.sort_values(["tanggal", "sumber"], ascending=[False, True]).reset_index(drop=True)
    save_data(df)
    return df, action


# =========================
# Utilitas parsing harga
# =========================
def clean_text(value) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_rupiah(value) -> Optional[int]:
    """Mengubah teks harga seperti 'Rp 37.643', '37,643', '37500' menjadi integer."""
    if value is None:
        return None
    text = str(value)
    if not text.strip() or text.strip() in {"-", "nan", "None"}:
        return None

    # Ambil angka termasuk pemisah ribuan/desimal.
    candidates = re.findall(r"\d[\d\.,]*", text)
    if not candidates:
        return None

    # Biasanya harga adalah angka terbesar/paling panjang pada teks.
    raw = max(candidates, key=len)

    # Kasus Indonesia: 37.643 atau 37,643 berarti 37643.
    # Jika ada dua pemisah, buang semua pemisah.
    digits = re.sub(r"[^0-9]", "", raw)
    if not digits:
        return None
    return int(digits)


def row_contains_any(row_values, keywords) -> bool:
    joined = " | ".join(clean_text(x).lower() for x in row_values)
    return any(k.lower() in joined for k in keywords)


def extract_price_from_row(row_values) -> Optional[int]:
    """Ambil harga dari baris tabel. Prioritas ke sel yang mengandung Rp, lalu angka wajar."""
    texts = [clean_text(x) for x in row_values]

    rp_cells = [t for t in texts if "rp" in t.lower()]
    for cell in rp_cells:
        price = parse_rupiah(cell)
        if price and 1_000 <= price <= 1_000_000:
            return price

    # Fallback: cek semua sel numerik wajar, pilih yang terlihat seperti harga.
    prices = []
    for cell in texts:
        price = parse_rupiah(cell)
        if price and 1_000 <= price <= 1_000_000:
            prices.append(price)
    if not prices:
        return None

    # Ambil angka terakhir, karena tabel sering berbentuk: komoditas, satuan, harga.
    return prices[-1]


def extract_from_html_tables(html: str) -> Dict[str, Optional[int]]:
    result = {"telur": None, "daging": None}
    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError:
        tables = []

    for table in tables:
        table = table.fillna("")
        for _, row in table.iterrows():
            values = list(row.values)
            if result["telur"] is None and row_contains_any(values, COMMODITIES["telur"]):
                result["telur"] = extract_price_from_row(values)
            if result["daging"] is None and row_contains_any(values, COMMODITIES["daging"]):
                result["daging"] = extract_price_from_row(values)

        if result["telur"] is not None and result["daging"] is not None:
            break

    return result


def extract_from_plain_text(html: str) -> Dict[str, Optional[int]]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)

    result = {"telur": None, "daging": None}
    patterns = {
        "telur": [
            r"Telur\s+Ayam\s+Ras(?:\s+Segar)?[^0-9R]{0,80}(?:Rp\s*)?([0-9][0-9\.\,]{2,})",
        ],
        "daging": [
            r"Daging\s+Ayam\s+Ras(?:\s+Segar)?[^0-9R]{0,80}(?:Rp\s*)?([0-9][0-9\.\,]{2,})",
            r"Ayam\s+Broiler[^0-9R]{0,80}(?:Rp\s*)?([0-9][0-9\.\,]{2,})",
        ],
    }

    for key, pats in patterns.items():
        for pat in pats:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if m:
                price = parse_rupiah(m.group(1))
                if price and 1_000 <= price <= 1_000_000:
                    result[key] = price
                    break

    return result


def merge_price_results(*results: Dict[str, Optional[int]]) -> Dict[str, Optional[int]]:
    merged = {"telur": None, "daging": None}
    for res in results:
        for key in merged:
            if merged[key] is None and res.get(key) is not None:
                merged[key] = res.get(key)
    return merged


def request_html(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


# =========================
# Fetcher sumber publik
# =========================
@st.cache_data(ttl=60 * 30, show_spinner=False)
def fetch_sp2kp_kemendag() -> Dict:
    """Ambil harga nasional tertimbang dari SP2KP Kemendag.

    Catatan: sumber publik dapat berubah sewaktu-waktu. Fungsi dibuat berlapis:
    parse tabel HTML terlebih dahulu, lalu fallback regex teks halaman.
    """
    url = "https://sp2kp.kemendag.go.id/"
    html = request_html(url)
    prices = merge_price_results(
        extract_from_html_tables(html),
        extract_from_plain_text(html),
    )

    if prices["telur"] is None or prices["daging"] is None:
        raise RuntimeError(
            "SP2KP berhasil dibuka, tetapi harga Telur Ayam Ras dan/atau "
            "Daging Ayam Ras tidak ditemukan pada struktur halaman saat ini."
        )

    return {
        "tanggal": date.today(),
        "harga_telur_ayam_ras": prices["telur"],
        "harga_daging_ayam_ras": prices["daging"],
        "sumber": "SP2KP Kemendag - Nasional",
        "url_sumber": url,
        "catatan": "Harga otomatis dari halaman publik SP2KP Kemendag.",
    }


@st.cache_data(ttl=60 * 30, show_spinner=False)
def fetch_dpkp_diy() -> Dict:
    """Ambil harga pangan terbaru dari DPKP DIY."""
    url = "https://dpkp.jogjaprov.go.id/harga-pangan/list"
    html = request_html(url)
    prices = merge_price_results(
        extract_from_html_tables(html),
        extract_from_plain_text(html),
    )

    # Cari tanggal terbaru dari halaman.
    tanggal_match = re.search(r"20\d{2}-\d{2}-\d{2}", html)
    tanggal = pd.to_datetime(tanggal_match.group(0)).date() if tanggal_match else date.today()

    if prices["telur"] is None or prices["daging"] is None:
        raise RuntimeError(
            "Halaman DPKP DIY berhasil dibuka, tetapi harga Telur Ayam Ras "
            "dan/atau Daging Ayam Ras tidak ditemukan."
        )

    return {
        "tanggal": tanggal,
        "harga_telur_ayam_ras": prices["telur"],
        "harga_daging_ayam_ras": prices["daging"],
        "sumber": "DPKP DIY",
        "url_sumber": url,
        "catatan": "Harga otomatis dari tabel publik DPKP DIY.",
    }


@st.cache_data(ttl=60 * 30, show_spinner=False)
def fetch_dataku_salatiga() -> Dict:
    """Ambil harga dari integrasi Dataku Salatiga."""
    url = "https://dataku.salatiga.go.id/integration/harga"
    html = request_html(url)
    prices = merge_price_results(
        extract_from_html_tables(html),
        extract_from_plain_text(html),
    )

    if prices["telur"] is None or prices["daging"] is None:
        raise RuntimeError(
            "Halaman Dataku Salatiga berhasil dibuka, tetapi harga Telur Ayam Ras "
            "dan/atau Daging Ayam Ras tidak ditemukan."
        )

    return {
        "tanggal": date.today(),
        "harga_telur_ayam_ras": prices["telur"],
        "harga_daging_ayam_ras": prices["daging"],
        "sumber": "Dataku Salatiga",
        "url_sumber": url,
        "catatan": "Harga otomatis dari integrasi publik Dataku Salatiga.",
    }


def try_all_sources() -> Tuple[Optional[Dict], list]:
    errors = []
    fetchers = [
        fetch_sp2kp_kemendag,
        fetch_dpkp_diy,
        fetch_dataku_salatiga,
    ]
    for fetcher in fetchers:
        try:
            record = fetcher()
            return record, errors
        except Exception as exc:
            errors.append(f"{fetcher.__name__}: {exc}")
    return None, errors


# =========================
# Tampilan Streamlit
# =========================
st.set_page_config(page_title=APP_TITLE, page_icon="🥚", layout="wide")
st.title("🥚 Pantau Harga Telur & Daging Ayam Harian")
st.caption(
    "Aplikasi ini menyimpan harga harian ke CSV lokal. "
    "Sumber otomatis dapat berubah sewaktu-waktu, jadi form manual tetap disediakan sebagai fallback."
)

ensure_storage()

with st.sidebar:
    st.header("Pengaturan")
    mode = st.radio(
        "Metode input data",
        [
            "Otomatis - coba semua sumber",
            "Otomatis - SP2KP Kemendag Nasional",
            "Otomatis - DPKP DIY",
            "Otomatis - Dataku Salatiga",
            "Manual",
        ],
    )
    st.info(
        "Simponi Ternak tidak digunakan lagi karena sering mengembalikan 403 Forbidden "
        "untuk request otomatis."
    )

st.subheader("1. Ambil / Input Harga Harian")

if mode.startswith("Otomatis"):
    col_a, col_b = st.columns([1, 2])
    with col_a:
        ambil = st.button("Ambil harga otomatis", type="primary", use_container_width=True)
    with col_b:
        st.write("Harga yang berhasil dibaca akan langsung masuk ke tabel penyimpanan.")

    if ambil:
        try:
            with st.spinner("Mengambil data dari sumber publik..."):
                if mode == "Otomatis - coba semua sumber":
                    record, errors = try_all_sources()
                    if record is None:
                        raise RuntimeError("Semua sumber otomatis gagal:\n" + "\n".join(errors))
                    if errors:
                        st.warning("Beberapa sumber gagal, tetapi sumber lain berhasil digunakan.")
                        with st.expander("Detail sumber yang gagal"):
                            st.write("\n".join(errors))
                elif mode == "Otomatis - SP2KP Kemendag Nasional":
                    record = fetch_sp2kp_kemendag()
                elif mode == "Otomatis - DPKP DIY":
                    record = fetch_dpkp_diy()
                else:
                    record = fetch_dataku_salatiga()

            df, action = upsert_record(record)
            st.success(
                f"Berhasil {action} data {record['sumber']} tanggal {record['tanggal']}. "
                f"Telur: Rp{record['harga_telur_ayam_ras']:,}/kg, "
                f"Daging ayam: Rp{record['harga_daging_ayam_ras']:,}/kg."
                .replace(",", ".")
            )
        except Exception as exc:
            st.error("Harga otomatis gagal dibaca.")
            st.code(str(exc))
            st.info(
                "Gunakan mode Manual, atau sesuaikan fungsi fetch_* bila situs sumber mengubah struktur/aksesnya."
            )

with st.expander("Input manual harga harian", expanded=(mode == "Manual")):
    with st.form("manual_form"):
        c1, c2, c3 = st.columns(3)
        with c1:
            tanggal_manual = st.date_input("Tanggal", value=date.today())
        with c2:
            harga_telur = st.number_input(
                "Harga Telur Ayam Ras (Rp/kg)", min_value=0, step=100, value=0
            )
        with c3:
            harga_daging = st.number_input(
                "Harga Daging Ayam Ras (Rp/kg)", min_value=0, step=100, value=0
            )

        sumber_manual = st.text_input("Sumber data", value="Input Manual")
        catatan_manual = st.text_area("Catatan", value="Diinput manual oleh pengguna.")
        submit_manual = st.form_submit_button("Simpan data manual", type="primary")

    if submit_manual:
        if harga_telur <= 0 or harga_daging <= 0:
            st.error("Harga telur dan daging ayam harus lebih dari 0.")
        else:
            record = {
                "tanggal": tanggal_manual,
                "harga_telur_ayam_ras": int(harga_telur),
                "harga_daging_ayam_ras": int(harga_daging),
                "sumber": sumber_manual.strip() or "Input Manual",
                "url_sumber": "-",
                "catatan": catatan_manual.strip(),
            }
            df, action = upsert_record(record)
            st.success(f"Berhasil {action} data manual tanggal {tanggal_manual}.")

st.divider()
st.subheader("2. Tabel Data Harga")

df = load_data()

if df.empty:
    st.warning("Belum ada data. Ambil harga otomatis atau isi data manual terlebih dahulu.")
else:
    min_date = min(df["tanggal"])
    max_date = max(df["tanggal"])
    f1, f2, f3 = st.columns([1, 1, 2])
    with f1:
        start_date = st.date_input("Dari tanggal", value=min_date)
    with f2:
        end_date = st.date_input("Sampai tanggal", value=max_date)
    with f3:
        sumber_pilihan = st.multiselect(
            "Filter sumber",
            options=sorted(df["sumber"].dropna().astype(str).unique().tolist()),
            default=sorted(df["sumber"].dropna().astype(str).unique().tolist()),
        )

    filtered = df[
        (df["tanggal"] >= start_date)
        & (df["tanggal"] <= end_date)
        & (df["sumber"].astype(str).isin(sumber_pilihan))
    ].copy()

    display_df = filtered.rename(
        columns={
            "tanggal": "Tanggal",
            "harga_telur_ayam_ras": "Harga Telur Ayam Ras (Rp/kg)",
            "harga_daging_ayam_ras": "Harga Daging Ayam Ras (Rp/kg)",
            "sumber": "Sumber",
            "url_sumber": "URL Sumber",
            "catatan": "Catatan",
            "waktu_input": "Waktu Input",
        }
    )

    st.dataframe(display_df, use_container_width=True, hide_index=True)

    st.subheader("3. Grafik Tren Harga")
    chart_df = filtered.copy()
    if not chart_df.empty:
        chart_df["tanggal"] = pd.to_datetime(chart_df["tanggal"])
        # Jika ada beberapa sumber pada tanggal sama, ambil rata-rata agar grafik rapi.
        chart_df = (
            chart_df.groupby("tanggal", as_index=False)[
                ["harga_telur_ayam_ras", "harga_daging_ayam_ras"]
            ]
            .mean()
            .sort_values("tanggal")
        )
        chart_df = chart_df.set_index("tanggal").rename(
            columns={
                "harga_telur_ayam_ras": "Telur Ayam Ras",
                "harga_daging_ayam_ras": "Daging Ayam Ras",
            }
        )
        st.line_chart(chart_df)
    else:
        st.info("Tidak ada data pada filter yang dipilih.")

    st.subheader("4. Download Data")
    csv_bytes = filtered.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download CSV",
        data=csv_bytes,
        file_name="harga_telur_daging_ayam.csv",
        mime="text/csv",
    )

    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        filtered.to_excel(writer, index=False, sheet_name="Harga Harian")
    st.download_button(
        "Download Excel",
        data=excel_buffer.getvalue(),
        file_name="harga_telur_daging_ayam.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

st.divider()
st.caption(
    "Catatan teknis: scraping halaman publik rentan gagal jika server memblokir request otomatis "
    "atau struktur HTML berubah. Kode ini memakai beberapa sumber dan menyediakan input manual sebagai cadangan."
)
