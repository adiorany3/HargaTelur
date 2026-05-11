from __future__ import annotations

import io
import re
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

APP_TIMEZONE = "Asia/Jakarta"
DATA_DIR = Path("data")
DATA_FILE = DATA_DIR / "harga_harian.csv"

SP2KP_URL = "https://sp2kp.kemendag.go.id/"
PIHPS_URL = "https://www.bi.go.id/hargapangan"

COMMODITY_ALIASES = {
    "Telur Ayam Ras": [
        "Telur Ayam Ras",
        "Telur Ayam Ras Segar",
        "Telur",
    ],
    "Daging Ayam Ras": [
        "Daging Ayam Ras",
        "Daging Ayam Ras Segar",
        "Ayam Ras",
    ],
}

BASE_COLUMNS = ["tanggal", "komoditas", "harga_rp_per_kg", "satuan", "sumber", "catatan", "waktu_input"]


# -----------------------------
# Utilitas data
# -----------------------------
def today_jakarta() -> date:
    return datetime.now(ZoneInfo(APP_TIMEZONE)).date()


def ensure_data_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        pd.DataFrame(columns=BASE_COLUMNS).to_csv(DATA_FILE, index=False)


def load_data() -> pd.DataFrame:
    ensure_data_file()
    try:
        df = pd.read_csv(DATA_FILE)
    except pd.errors.EmptyDataError:
        df = pd.DataFrame(columns=BASE_COLUMNS)

    for col in BASE_COLUMNS:
        if col not in df.columns:
            df[col] = None

    if not df.empty:
        df["tanggal"] = pd.to_datetime(df["tanggal"], errors="coerce").dt.date
        df["harga_rp_per_kg"] = pd.to_numeric(df["harga_rp_per_kg"], errors="coerce").astype("Int64")

    return df[BASE_COLUMNS]


def save_data(df: pd.DataFrame) -> None:
    ensure_data_file()
    out = df.copy()
    out["tanggal"] = pd.to_datetime(out["tanggal"], errors="coerce").dt.strftime("%Y-%m-%d")
    out.to_csv(DATA_FILE, index=False)


def upsert_rows(new_rows: pd.DataFrame) -> pd.DataFrame:
    """Tambah/update data berdasarkan tanggal + komoditas + sumber."""
    old = load_data()
    combined = pd.concat([old, new_rows], ignore_index=True)
    combined["tanggal"] = pd.to_datetime(combined["tanggal"], errors="coerce").dt.date
    combined["harga_rp_per_kg"] = pd.to_numeric(combined["harga_rp_per_kg"], errors="coerce").astype("Int64")
    combined = combined.dropna(subset=["tanggal", "komoditas", "harga_rp_per_kg"])
    combined = combined.sort_values("waktu_input").drop_duplicates(
        subset=["tanggal", "komoditas", "sumber"],
        keep="last",
    )
    combined = combined.sort_values(["tanggal", "komoditas"], ascending=[False, True])
    save_data(combined)
    return combined


def build_rows(
    prices: dict[str, int],
    selected_date: date,
    source: str,
    note: str = "",
) -> pd.DataFrame:
    now = datetime.now(ZoneInfo(APP_TIMEZONE)).isoformat(timespec="seconds")
    rows = []
    for commodity, price in prices.items():
        if price is None:
            continue
        rows.append(
            {
                "tanggal": selected_date,
                "komoditas": commodity,
                "harga_rp_per_kg": int(price),
                "satuan": "kg",
                "sumber": source,
                "catatan": note,
                "waktu_input": now,
            }
        )
    return pd.DataFrame(rows, columns=BASE_COLUMNS)


# -----------------------------
# Utilitas parsing harga
# -----------------------------
def normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def parse_rupiah(value: object) -> Optional[int]:
    """Konversi teks harga seperti 'Rp 37.643' atau '28.536,00' menjadi integer."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None

    text = str(value)
    matches = re.findall(r"\d[\d\.,]*", text)
    if not matches:
        return None

    candidates: list[int] = []
    for raw in matches:
        s = raw.strip()
        if not s:
            continue

        # Heuristik pemisah ribuan/desimal Indonesia dan internasional.
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                # 28.536,00 -> 28536
                s = s.replace(".", "")
                s = s.split(",")[0]
            else:
                # 28,536.00 -> 28536
                s = s.replace(",", "")
                s = s.split(".")[0]
        elif "," in s:
            parts = s.split(",")
            if len(parts[-1]) <= 2:
                s = "".join(parts[:-1]) or parts[0]
            else:
                s = "".join(parts)
        elif "." in s:
            parts = s.split(".")
            if len(parts[-1]) == 3:
                s = "".join(parts)
            elif len(parts[-1]) <= 2:
                s = "".join(parts[:-1]) or parts[0]
            else:
                s = "".join(parts)

        digits = re.sub(r"\D", "", s)
        if digits:
            number = int(digits)
            # Batas wajar harga pangan per kg agar tanggal/tahun tidak ikut terambil.
            if 5_000 <= number <= 250_000:
                candidates.append(number)

    if not candidates:
        return None

    # Biasanya harga adalah kandidat terbesar pada baris komoditas.
    return max(candidates)


def row_contains_alias(row_text: str, aliases: Iterable[str]) -> bool:
    haystack = normalize_text(row_text)
    return any(normalize_text(alias) in haystack for alias in aliases)


def extract_from_html_tables(html: str) -> dict[str, int]:
    results: dict[str, int] = {}
    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError:
        tables = []

    for table in tables:
        # Ubah semua cell menjadi teks agar pencarian fleksibel.
        table = table.fillna("")
        for _, row in table.iterrows():
            row_text = " | ".join(str(x) for x in row.tolist())
            for commodity, aliases in COMMODITY_ALIASES.items():
                if commodity in results:
                    continue
                if row_contains_alias(row_text, aliases):
                    # Cari angka harga dari seluruh cell dalam baris.
                    price_candidates = []
                    for cell in row.tolist():
                        parsed = parse_rupiah(cell)
                        if parsed is not None:
                            price_candidates.append(parsed)
                    if price_candidates:
                        results[commodity] = max(price_candidates)

    return results


def extract_from_plain_text(html: str) -> dict[str, int]:
    """Fallback bila data tidak terbaca sebagai tabel HTML."""
    soup = BeautifulSoup(html, "html.parser")
    text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
    results: dict[str, int] = {}

    for commodity, aliases in COMMODITY_ALIASES.items():
        for alias in aliases:
            # Ambil harga yang posisinya dekat dengan nama komoditas.
            pattern = re.compile(
                rf"{re.escape(alias)}.{{0,120}}?(?:Rp\s*)?(\d[\d\.,]*)",
                flags=re.IGNORECASE,
            )
            match = pattern.search(text)
            if match:
                price = parse_rupiah(match.group(1))
                if price is not None:
                    results[commodity] = price
                    break

    return results


def fetch_public_prices(url: str, source_name: str, selected_date: date) -> pd.DataFrame:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }
    response = requests.get(url, headers=headers, timeout=25)
    response.raise_for_status()
    html = response.text

    prices = extract_from_html_tables(html)
    if len(prices) < len(COMMODITY_ALIASES):
        prices.update({k: v for k, v in extract_from_plain_text(html).items() if k not in prices})

    if not prices:
        raise RuntimeError(
            "Harga tidak berhasil dibaca otomatis. Struktur halaman kemungkinan berubah atau data dimuat lewat JavaScript. "
            "Gunakan form input manual, atau sesuaikan fungsi fetch_public_prices() dengan endpoint resmi terbaru."
        )

    missing = [commodity for commodity in COMMODITY_ALIASES if commodity not in prices]
    note = "Diambil otomatis dari halaman publik."
    if missing:
        note += " Komoditas belum terbaca: " + ", ".join(missing)

    return build_rows(prices, selected_date, source_name, note)


# -----------------------------
# UI Streamlit
# -----------------------------
def render_header() -> None:
    st.set_page_config(
        page_title="Harga Harian Telur & Daging Ayam",
        page_icon="🥚",
        layout="wide",
    )
    st.title("Pemantauan Harga Harian Telur & Daging Ayam")
    st.caption(
        "Aplikasi Streamlit untuk mengambil, menyimpan, menampilkan, dan mengunduh data harga harian "
        "Telur Ayam Ras dan Daging Ayam Ras dalam tabel."
    )


def render_fetch_section() -> None:
    st.subheader("1. Ambil Harga Harian")
    st.write(
        "Pilih tanggal dan sumber. Jika pengambilan otomatis gagal karena struktur situs berubah, "
        "gunakan bagian input manual di bawah."
    )

    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        selected_date = st.date_input("Tanggal", value=today_jakarta(), format="YYYY-MM-DD")
    with col2:
        source = st.selectbox(
            "Sumber otomatis",
            ["SP2KP Kemendag", "PIHPS BI"],
            help="SP2KP dipakai sebagai sumber utama. PIHPS disediakan sebagai opsi cadangan/eksperimental.",
        )
    with col3:
        st.write("")
        st.write("")
        fetch_clicked = st.button("Ambil harga otomatis", use_container_width=True)

    auto_fetch_today = st.toggle(
        "Ambil otomatis saat aplikasi dibuka jika data hari ini belum ada",
        value=False,
        help="Aktifkan bila aplikasi dipakai sebagai pencatatan harian. Data tidak akan digandakan untuk tanggal, komoditas, dan sumber yang sama.",
    )

    data = load_data()
    has_today_for_source = False
    if not data.empty:
        has_today_for_source = (
            (data["tanggal"] == today_jakarta())
            & (data["sumber"] == source)
        ).any()

    should_fetch = fetch_clicked or (auto_fetch_today and not has_today_for_source)
    if should_fetch:
        url = SP2KP_URL if source == "SP2KP Kemendag" else PIHPS_URL
        try:
            with st.spinner("Mengambil dan membaca harga dari sumber publik..."):
                rows = fetch_public_prices(url, source, selected_date)
                combined = upsert_rows(rows)
            st.success(f"Berhasil menyimpan {len(rows)} baris data dari {source}.")
            st.dataframe(rows, use_container_width=True, hide_index=True)
        except Exception as exc:  # noqa: BLE001 - ditampilkan agar pengguna tahu penyebabnya
            st.error(str(exc))


def render_manual_input() -> None:
    st.subheader("2. Input Manual / Koreksi Data")
    with st.form("manual_input_form", clear_on_submit=False):
        col1, col2, col3 = st.columns(3)
        with col1:
            manual_date = st.date_input("Tanggal data", value=today_jakarta(), key="manual_date", format="YYYY-MM-DD")
        with col2:
            egg_price = st.number_input("Harga Telur Ayam Ras / kg", min_value=0, step=500, value=0)
        with col3:
            chicken_price = st.number_input("Harga Daging Ayam Ras / kg", min_value=0, step=500, value=0)

        source_note = st.text_input("Sumber/Catatan", value="Input Manual")
        submitted = st.form_submit_button("Simpan ke tabel")

    if submitted:
        prices: dict[str, int] = {}
        if egg_price > 0:
            prices["Telur Ayam Ras"] = int(egg_price)
        if chicken_price > 0:
            prices["Daging Ayam Ras"] = int(chicken_price)

        if not prices:
            st.warning("Masukkan minimal satu harga yang lebih besar dari 0.")
        else:
            rows = build_rows(prices, manual_date, "Input Manual", source_note)
            upsert_rows(rows)
            st.success(f"Berhasil menyimpan {len(rows)} baris data manual.")


def render_table_and_chart() -> None:
    st.subheader("3. Tabel Data Harga")
    df = load_data()

    if df.empty:
        st.info("Belum ada data. Ambil harga otomatis atau isi data manual terlebih dahulu.")
        return

    min_date = min(df["tanggal"])
    max_date = max(df["tanggal"])

    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        start_date = st.date_input("Dari tanggal", value=min_date, format="YYYY-MM-DD")
    with col2:
        end_date = st.date_input("Sampai tanggal", value=max_date, format="YYYY-MM-DD")
    with col3:
        commodities = st.multiselect(
            "Komoditas",
            sorted(df["komoditas"].dropna().unique().tolist()),
            default=sorted(df["komoditas"].dropna().unique().tolist()),
        )

    filtered = df[
        (df["tanggal"] >= start_date)
        & (df["tanggal"] <= end_date)
        & (df["komoditas"].isin(commodities))
    ].copy()
    filtered = filtered.sort_values(["tanggal", "komoditas"], ascending=[False, True])

    st.dataframe(
        filtered,
        use_container_width=True,
        hide_index=True,
        column_config={
            "tanggal": st.column_config.DateColumn("Tanggal", format="YYYY-MM-DD"),
            "komoditas": "Komoditas",
            "harga_rp_per_kg": st.column_config.NumberColumn("Harga Rp/kg", format="Rp %d"),
            "satuan": "Satuan",
            "sumber": "Sumber",
            "catatan": "Catatan",
            "waktu_input": "Waktu Input",
        },
    )

    st.subheader("4. Grafik Perkembangan Harga")
    chart_df = filtered.copy()
    chart_df["tanggal"] = pd.to_datetime(chart_df["tanggal"])
    if not chart_df.empty:
        pivot = chart_df.pivot_table(
            index="tanggal",
            columns="komoditas",
            values="harga_rp_per_kg",
            aggfunc="mean",
        ).sort_index()
        st.line_chart(pivot)
    else:
        st.info("Tidak ada data pada filter yang dipilih.")

    st.subheader("5. Unduh Data")
    csv_bytes = filtered.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "Download CSV",
        data=csv_bytes,
        file_name="harga_telur_daging_ayam.csv",
        mime="text/csv",
        use_container_width=True,
    )

    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        filtered.to_excel(writer, index=False, sheet_name="Harga Harian")
    st.download_button(
        "Download Excel",
        data=excel_buffer.getvalue(),
        file_name="harga_telur_daging_ayam.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )


def render_sidebar() -> None:
    with st.sidebar:
        st.header("Tentang Aplikasi")
        st.markdown(
            """
            **Fitur:**
            - Ambil harga otomatis dari sumber publik.
            - Simpan data ke CSV lokal.
            - Input manual bila scraping gagal.
            - Tabel, filter tanggal, grafik, dan ekspor data.

            **File data:** `data/harga_harian.csv`
            """
        )
        st.warning(
            "Catatan: situs publik dapat mengubah struktur halaman sewaktu-waktu. "
            "Jika otomatis gagal, tetap gunakan input manual atau sesuaikan parser."
        )


def main() -> None:
    render_header()
    render_sidebar()
    render_fetch_section()
    st.divider()
    render_manual_input()
    st.divider()
    render_table_and_chart()


if __name__ == "__main__":
    main()
