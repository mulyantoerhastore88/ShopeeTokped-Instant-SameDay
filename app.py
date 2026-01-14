import streamlit as st
import pandas as pd
import numpy as np
import io
import time
from datetime import datetime
import warnings
import gspread
from google.oauth2 import service_account
warnings.filterwarnings('ignore')

# --- CONFIG ---
st.set_page_config(page_title="Universal Order Processor", layout="wide")
st.title("🛒 Universal Marketplace Order Processor - Created By Mulyanto")
st.markdown("""
**Logic Applied:**
1. **Shopee (Official)**: Status='Perlu Dikirim' | Resi=Blank | Managed='No' (Optional) | **Kurir=Instant (Kamus)**.
2. **Shopee (INHOUSE)**: Status='Perlu Dikirim' | Resi=Blank | **Kurir=Instant (Kamus)**.
3. **Tokopedia**: Status='Perlu Dikirim'.
4. **SKU Logic**: Shopee Priority -> **'Nomor Referensi SKU'**.
""")

# --- DEBUG MODE ---
st.sidebar.header("🔧 Debug Mode")
DEBUG_MODE = st.sidebar.checkbox("Tampilkan info detil (Debug)", value=False)

# --- FUNGSI UNTUK LOAD KAMUS DARI GOOGLE SHEETS ---
def load_kamus_from_gsheet():
    """Membaca kamus dari Google Sheets dengan nama sheet yang spesifik"""
    try:
        # Load credentials from secrets
        credentials_dict = {
            "type": st.secrets["type"],
            "project_id": st.secrets["project_id"],
            "private_key_id": st.secrets["private_key_id"],
            "private_key": st.secrets["private_key"].replace('\\n', '\n'),
            "client_email": st.secrets["client_email"],
            "client_id": st.secrets["client_id"],
            "auth_uri": st.secrets["auth_uri"],
            "token_uri": st.secrets["token_uri"],
            "auth_provider_x509_cert_url": st.secrets["auth_provider_x509_cert_url"],
            "client_x509_cert_url": st.secrets["client_x509_cert_url"],
            "universe_domain": st.secrets["universe_domain"]
        }
        
        # Create credentials
        credentials = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        
        # Connect to Google Sheets
        gc = gspread.authorize(credentials)
        
        # Open the spreadsheet (gunakan ID dari URL)
        spreadsheet_id = "15c1uN2dVwMMT-bldZzRwVVExEau2ZgnI2_RgSIw7gG4"
        spreadsheet = gc.open_by_key(spreadsheet_id)
        
        # Tampilkan semua sheet yang tersedia (untuk debugging)
        if DEBUG_MODE:
            sheet_names = [ws.title for ws in spreadsheet.worksheets()]
            st.sidebar.info(f"Sheet yang tersedia: {', '.join(sheet_names)}")
        
        # Baca semua sheet yang diperlukan dengan nama spesifik
        kamus_data = {}
        
        # Sheet Kurir-Shopee
        try:
            worksheet = spreadsheet.worksheet("Kurir-Shopee")
            data = worksheet.get_all_records()
            if data:
                kamus_data['kurir'] = pd.DataFrame(data)
                if DEBUG_MODE:
                    st.sidebar.success(f"✓ Sheet 'Kurir-Shopee' ditemukan: {len(data)} baris")
                    st.sidebar.info(f"Kolom: {list(kamus_data['kurir'].columns)}")
            else:
                st.sidebar.warning("⚠️ Sheet 'Kurir-Shopee' ditemukan tapi kosong")
        except gspread.exceptions.WorksheetNotFound:
            st.sidebar.error("❌ Sheet 'Kurir-Shopee' tidak ditemukan")
            raise Exception("Sheet 'Kurir-Shopee' tidak ditemukan")
        
        # Sheet Bundle Master
        try:
            worksheet = spreadsheet.worksheet("Bundle Master")
            data = worksheet.get_all_records()
            if data:
                kamus_data['bundle'] = pd.DataFrame(data)
                if DEBUG_MODE:
                    st.sidebar.success(f"✓ Sheet 'Bundle Master' ditemukan: {len(data)} baris")
                    st.sidebar.info(f"Kolom: {list(kamus_data['bundle'].columns)}")
            else:
                st.sidebar.warning("⚠️ Sheet 'Bundle Master' ditemukan tapi kosong")
        except gspread.exceptions.WorksheetNotFound:
            st.sidebar.error("❌ Sheet 'Bundle Master' tidak ditemukan")
            raise Exception("Sheet 'Bundle Master' tidak ditemukan")
        
        # Sheet SKU Master
        try:
            worksheet = spreadsheet.worksheet("SKU Master")
            data = worksheet.get_all_records()
            if data:
                kamus_data['sku'] = pd.DataFrame(data)
                if DEBUG_MODE:
                    st.sidebar.success(f"✓ Sheet 'SKU Master' ditemukan: {len(data)} baris")
                    st.sidebar.info(f"Kolom: {list(kamus_data['sku'].columns)}")
            else:
                st.sidebar.warning("⚠️ Sheet 'SKU Master' ditemukan tapi kosong")
        except gspread.exceptions.WorksheetNotFound:
            st.sidebar.error("❌ Sheet 'SKU Master' tidak ditemukan")
            raise Exception("Sheet 'SKU Master' tidak ditemukan")
        
        # Validasi semua sheet ditemukan
        if len(kamus_data) < 3:
            missing = []
            if 'kurir' not in kamus_data: missing.append('Kurir-Shopee')
            if 'bundle' not in kamus_data: missing.append('Bundle Master')
            if 'sku' not in kamus_data: missing.append('SKU Master')
            raise Exception(f"Sheet tidak ditemukan: {', '.join(missing)}")
        
        # Cek struktur data minimal
        for sheet_name, df in kamus_data.items():
            if df.empty:
                st.sidebar.warning(f"⚠️ Sheet {sheet_name} kosong")
            if DEBUG_MODE:
                st.sidebar.info(f"Preview {sheet_name} (3 baris pertama):")
                st.sidebar.dataframe(df.head(3))
        
        return kamus_data
        
    except Exception as e:
        st.error(f"❌ Gagal load kamus: {str(e)}")
        return None

# --- FUNGSI CLEANING SKU ---
def clean_sku(sku):
    if pd.isna(sku): return ""
    sku = str(sku).strip()
    sku = ''.join(char for char in sku if ord(char) >= 32)
    sku_upper = sku.upper()
    if sku_upper.startswith('FG-') or sku_upper.startswith('CS-'):
        return sku
    if '-' in sku:
        return sku.split('-', 1)[-1].strip()
    return sku

# --- FUNGSI SMART LOADER ---
def load_data_smart(file_obj):
    df = None
    filename = file_obj.name.lower()
    
    try:
        if filename.endswith('.xlsx') or filename.endswith('.xls'):
            try: df = pd.read_excel(file_obj, dtype=str, header=None, engine='openpyxl')
            except: df = None

        if df is None or df.shape[1] <= 1:
            file_obj.seek(0)
            encodings = ['utf-8-sig', 'utf-8', 'latin-1']
            separators = [',', ';', '\t']
            for enc in encodings:
                if df is not None and df.shape[1] > 1: break
                for sep in separators:
                    try:
                        file_obj.seek(0)
                        temp_df = pd.read_csv(
                            file_obj, sep=sep, dtype=str, header=None, 
                            encoding=enc, on_bad_lines='skip', quotechar='"'
                        )
                        if temp_df.shape[1] > 1:
                            df = temp_df
                            break
                    except: continue

    except Exception as e: return None, f"Gagal membaca file: {str(e)[:100]}"

    if df is None or df.empty: return None, "File kosong atau format tidak dikenali."

    header_idx = 0
    keywords = ['status', 'sku', 'order', 'pesanan', 'quantity', 'jumlah', 'product', 'opsi pengiriman']
    for i in range(min(20, df.shape[0])):
        row_str = " ".join([str(v).lower() for v in df.iloc[i].dropna().values])
        if sum(1 for kw in keywords if kw in row_str) >= 2:
            header_idx = i
            break
    
    try:
        df_final = df.iloc[header_idx:].copy()
        df_final.columns = df_final.iloc[0]
        df_final = df_final.iloc[1:].reset_index(drop=True)
        df_final.columns = [str(c).strip().replace('\n', ' ') for c in df_final.columns]
        df_final = df_final.dropna(how='all')
        return df_final, None
    except Exception as e: return None, f"Gagal set header: {e}"

# ==========================================
# MAIN PROCESSOR
# ==========================================
def process_universal_data(uploaded_files, kamus_data):
    all_rows = []
    raw_stats_list = []
    
    # 1. PREPARE KAMUS
    try:
        df_kurir = kamus_data['kurir']
        df_bundle = kamus_data['bundle']
        df_sku = kamus_data['sku']
        
        if DEBUG_MODE:
            st.sidebar.info("Memproses kamus...")
            st.sidebar.info(f"Kurir: {df_kurir.shape} baris, {df_kurir.columns.tolist()}")
            st.sidebar.info(f"Bundle: {df_bundle.shape} baris, {df_bundle.columns.tolist()}")
            st.sidebar.info(f"SKU: {df_sku.shape} baris, {df_sku.columns.tolist()}")
        
        bundle_map = {}
        k_cols = {str(c).lower(): c for c in df_bundle.columns}
        if DEBUG_MODE:
            st.sidebar.info(f"Kolom Bundle (lowercase): {list(k_cols.keys())}")
        
        kit_c = next((v for k,v in k_cols.items() if any(x in k for x in ['kit','bundle','parent'])), None)
        comp_c = next((v for k,v in k_cols.items() if any(x in k for x in ['component','child','sku'])), None)
        qty_c = next((v for k,v in k_cols.items() if any(x in k for x in ['qty','quantity'])), None)
        
        if DEBUG_MODE:
            st.sidebar.info(f"Kit column: {kit_c}, Component column: {comp_c}, Qty column: {qty_c}")
        
        if kit_c and comp_c:
            for idx, row in df_bundle.iterrows():
                k_val = clean_sku(row[kit_c])
                c_val = clean_sku(row[comp_c])
                try: 
                    q_val = float(str(row[qty_c]).replace(',', '.')) if qty_c else 1.0
                except: 
                    q_val = 1.0
                if k_val and c_val:
                    if k_val not in bundle_map: bundle_map[k_val] = []
                    bundle_map[k_val].append((c_val, q_val))
            if DEBUG_MODE:
                st.sidebar.success(f"Bundle mapping: {len(bundle_map)} bundles ditemukan")
        else:
            st.sidebar.warning("⚠️ Kolom untuk bundle mapping tidak lengkap")

        sku_name_map = {}
        for _, row in df_sku.iterrows():
            vals = [str(v).strip() for v in row if pd.notna(v) and str(v).strip()]
            if len(vals) >= 2: 
                sku_name_map[clean_sku(vals[0])] = vals[1]
        if DEBUG_MODE:
            st.sidebar.success(f"SKU mapping: {len(sku_name_map)} SKU ditemukan")

        instant_list = []
        if not df_kurir.empty:
            ins_col = next((c for c in df_kurir.columns if 'instant' in str(c).lower()), None)
            kur_col = df_kurir.columns[0] if not df_kurir.empty else None
            
            if ins_col and kur_col:
                if DEBUG_MODE:
                    st.sidebar.info(f"Kolom instant: {ins_col}, Kolom kurir: {kur_col}")
                
                instant_list = df_kurir[
                    df_kurir[ins_col].astype(str).str.lower().isin(['yes','ya','true','1', 'instant'])
                ][kur_col].astype(str).str.strip().tolist()
                
                if DEBUG_MODE:
                    st.sidebar.success(f"Instant kurir: {len(instant_list)} kurir ditemukan")
                    st.sidebar.info(f"Daftar kurir instant: {instant_list}")
            else:
                st.sidebar.warning("⚠️ Kolom untuk instant kurir tidak lengkap")

    except Exception as e: 
        st.error(f"Error Kamus: {e}")
        if DEBUG_MODE:
            import traceback
            st.sidebar.error(f"Traceback: {traceback.format_exc()}")
        return None, f"Error Kamus: {e}"

    # 2. PROCESS FILES
    for mp_type, file_obj in uploaded_files:
        df_raw, err = load_data_smart(file_obj)
        if err:
            st.warning(f"⚠️ Skip {mp_type}: {err}")
            continue
            
        df_filtered = pd.DataFrame()
        df_raw.columns = [str(c).strip().lower() for c in df_raw.columns]

        if DEBUG_MODE:
            st.sidebar.markdown(f"**Processing {mp_type}...**")
            st.sidebar.info(f"Kolom yang ada: {df_raw.columns.tolist()}")

        # --- A. RAW STATS (VALIDATION) ---
        raw_kurir_col = None
        if 'shopee' in mp_type.lower():
            raw_kurir_col = next((c for c in df_raw.columns if any(x in c for x in ['opsi','kirim'])), None)
        elif 'tokopedia' in mp_type.lower():
            raw_kurir_col = next((c for c in df_raw.columns if 'shipping provider' in c), None)
            if not raw_kurir_col:
                raw_kurir_col = next((c for c in df_raw.columns if 'delivery option' in c), None)
            if not raw_kurir_col:
                raw_kurir_col = next((c for c in df_raw.columns if 'kurir' in c), None)
        
        if raw_kurir_col:
            stats = df_raw[raw_kurir_col].fillna('BLANK').value_counts().reset_index()
            stats.columns = ['Jenis Kurir', 'Jumlah Order (Raw)']
            stats['Sumber Data'] = mp_type
            
            def check_status(k_name):
                k_name = str(k_name).strip()
                if k_name in instant_list: return '✅ Whitelisted'
                k_lower = k_name.lower()
                if 'instant' in k_lower or 'same' in k_lower: return '⚠️ Kemungkinan Instant'
                return '❌ Non-Instant'
                
            stats['Status Sistem'] = stats['Jenis Kurir'].apply(check_status)
            raw_stats_list.append(stats)
        else:
            raw_stats_list.append(pd.DataFrame({
                'Sumber Data': [mp_type],
                'Jenis Kurir': ['(Kolom Kurir Tidak Ditemukan)'],
                'Jumlah Order (Raw)': [len(df_raw)],
                'Status Sistem': ['-']
            }))

        # --- B. FILTERING LOGIC ---
        
        # 1. SHOPEE OFFICIAL
        if mp_type == 'Shopee (Official)':
            status_c = next((c for c in df_raw.columns if 'status' in c), None)
            resi_c = next((c for c in df_raw.columns if 'resi' in c), None)
            kurir_c = next((c for c in df_raw.columns if any(x in c for x in ['opsi','kirim'])), None)
            managed_c = next((c for c in df_raw.columns if 'dikelola' in c), None)

            if DEBUG_MODE:
                st.sidebar.info(f"Shopee Official - Status: {status_c}, Resi: {resi_c}, Kurir: {kurir_c}, Managed: {managed_c}")

            if all([status_c, resi_c, kurir_c]):
                # Fix: Case Insensitive 'perlu dikirim'
                c1 = df_raw[status_c].astype(str).str.strip().str.lower() == 'perlu dikirim'
                c2 = df_raw[resi_c].fillna('').astype(str).str.strip().isin(['','nan','none'])
                c4 = df_raw[kurir_c].astype(str).str.strip().isin(instant_list)
                
                if managed_c:
                     c3 = df_raw[managed_c].astype(str).str.strip().str.lower() == 'no'
                else:
                     c3 = True

                df_filtered = df_raw[c1 & c2 & c3 & c4].copy()
                if DEBUG_MODE:
                    st.sidebar.text(f"  > Status OK: {c1.sum()}")
                    st.sidebar.text(f"  > Resi Blank: {c2.sum()}")
                    st.sidebar.text(f"  > Kurir Instant: {c4.sum()}")
            else:
                st.error(f"Shopee Official: Kolom Status/Resi/Opsi Kirim tidak lengkap!")

        # 2. SHOPEE INHOUSE
        elif mp_type == 'Shopee (INHOUSE)':
            status_c = next((c for c in df_raw.columns if 'status' in c), None)
            resi_c = next((c for c in df_raw.columns if 'resi' in c), None)
            kurir_c = next((c for c in df_raw.columns if any(x in c for x in ['opsi','kirim'])), None)
            
            if DEBUG_MODE:
                st.sidebar.info(f"Shopee INHOUSE - Status: {status_c}, Resi: {resi_c}, Kurir: {kurir_c}")

            if all([status_c, resi_c, kurir_c]):
                # Fix: Case Insensitive 'perlu dikirim'
                c1 = df_raw[status_c].astype(str).str.strip().str.lower() == 'perlu dikirim'
                c2 = df_raw[resi_c].fillna('').astype(str).str.strip().isin(['','nan','none'])
                c3 = df_raw[kurir_c].astype(str).str.strip().isin(instant_list)
                
                df_filtered = df_raw[c1 & c2 & c3].copy()
                if DEBUG_MODE:
                    st.sidebar.text(f"  > Status OK: {c1.sum()}")
                    st.sidebar.text(f"  > Resi Blank: {c2.sum()}")
                    st.sidebar.text(f"  > Kurir Instant: {c3.sum()}")
            else:
                st.error(f"Shopee Inhouse: Kolom Status/Resi/Opsi Kirim tidak lengkap!")

        # 3. TOKOPEDIA
        elif mp_type == 'Tokopedia':
            status_c = next((c for c in df_raw.columns if 'status' in c), None)
            if status_c:
                c1 = df_raw[status_c].astype(str).str.strip().str.lower() == 'perlu dikirim'
                df_filtered = df_raw[c1].copy()
                if DEBUG_MODE:
                    st.sidebar.text(f"  > Status OK: {c1.sum()}")
            else:
                st.error("Tokopedia: Kolom Status tidak ditemukan")

        # --- C. MAPPING SKU ---
        if df_filtered.empty:
            if DEBUG_MODE:
                st.sidebar.warning(f"  > {mp_type}: Tidak ada data yang lolos filter")
            continue

        if DEBUG_MODE: 
            st.sidebar.success(f"  > {len(df_filtered)} data lolos filter.")
            st.sidebar.info(f"Kolom yang tersedia: {df_filtered.columns.tolist()}")

        col_sku = 'SKU' # default
        if 'shopee' in mp_type.lower():
            # Cari spesifik 'nomor referensi sku' dulu
            col_sku = next((c for c in df_raw.columns if 'nomor referensi sku' in c), None)
            if not col_sku:
                col_sku = next((c for c in df_raw.columns if 'referensi sku' in c), None)
            if not col_sku:
                col_sku = next((c for c in df_raw.columns if 'sku' in c), 'SKU')
        else:
            col_sku = next((c for c in df_raw.columns if any(x in c for x in ['seller sku', 'nomor sku'])), None)
            if not col_sku:
                 col_sku = next((c for c in df_raw.columns if 'sku' in c), 'SKU')
        
        if DEBUG_MODE: 
            st.sidebar.info(f"  > Menggunakan kolom SKU: '{col_sku}'")

        col_qty = next((c for c in df_raw.columns if any(x in c for x in ['jumlah','quantity'])), 'Jumlah')
        col_ord = next((c for c in df_raw.columns if any(x in c for x in ['pesanan','order','invoice'])), 'Order ID')

        for _, row in df_filtered.iterrows():
            raw_sku = str(row.get(col_sku, ''))
            sku_clean = clean_sku(raw_sku)
            order_id = str(row.get(col_ord, ''))
            try: 
                qty = float(str(row.get(col_qty, 0)).replace(',', '.'))
            except: 
                qty = 0
            
            if not sku_clean or qty <= 0: 
                if DEBUG_MODE and sku_clean:
                    st.sidebar.warning(f"SKU {sku_clean} memiliki qty {qty}")
                continue
            
            if sku_clean in bundle_map:
                for comp_sku, comp_qty in bundle_map[sku_clean]:
                    all_rows.append({
                        'Marketplace': mp_type,
                        'Order ID': order_id,
                        'SKU Original': raw_sku,
                        'Is Bundle': 'Yes',
                        'SKU Component': comp_sku,
                        'Nama Produk': sku_name_map.get(comp_sku, comp_sku),
                        'Qty Total': qty * comp_qty
                    })
            else:
                all_rows.append({
                    'Marketplace': mp_type,
                    'Order ID': order_id,
                    'SKU Original': raw_sku,
                    'Is Bundle': 'No',
                    'SKU Component': sku_clean,
                    'Nama Produk': sku_name_map.get(sku_clean, sku_clean),
                    'Qty Total': qty
                })

    # FINAL RESULTS
    df_detail = pd.DataFrame(all_rows)
    if not df_detail.empty:
        df_summary = df_detail.groupby(['Marketplace', 'SKU Component', 'Nama Produk'], as_index=False)['Qty Total'].sum()
        df_summary = df_summary.sort_values('Qty Total', ascending=False)
    else:
        df_summary = pd.DataFrame()

    df_raw_stats = pd.concat(raw_stats_list, ignore_index=True) if raw_stats_list else pd.DataFrame()
    
    return {'detail': df_detail, 'summary': df_summary, 'raw_stats': df_raw_stats}, None

# --- UI STREAMLIT ---

# --- Bagian Atas Sidebar (FIXED) ---
st.sidebar.header("📁 Load Kamus dari Google Sheets")
st.sidebar.markdown("**Nama Sheet yang dibutuhkan:**")
st.sidebar.markdown("- Kurir-Shopee")
st.sidebar.markdown("- Bundle Master")
st.sidebar.markdown("- SKU Master")

if st.sidebar.button("🔄 Load Kamus Sekarang", type="primary"):
    with st.spinner("Loading kamus dari Google Sheets..."):
        kamus_data = load_kamus_from_gsheet()
        if kamus_data:
            st.session_state['kamus_data'] = kamus_data
            
            # Tampilkan status success di bagian atas
            st.sidebar.success("✅ Kamus siap digunakan!")
            
            # Tampilkan preview data di expander agar tidak memakan space banyak
            with st.sidebar.expander("📊 Preview Kamus (Opsional)", expanded=False):
                tab1, tab2, tab3 = st.tabs(["Kurir", "Bundle", "SKU"])
                with tab1:
                    st.dataframe(kamus_data['kurir'].head(3))
                with tab2:
                    st.dataframe(kamus_data['bundle'].head(3))
                with tab3:
                    st.dataframe(kamus_data['sku'].head(3))

# --- Separator yang jelas ---
st.sidebar.markdown("---")
st.sidebar.markdown("## 📁 Upload Order Marketplace")

# --- Bagian Upload File (PENTING - selalu visible) ---
st.sidebar.markdown("**Upload file order dari marketplace:**")
shp_off_f = st.sidebar.file_uploader("Shopee (Official)", key="so")
shp_inh_f = st.sidebar.file_uploader("Shopee (INHOUSE)", key="si")
tok_f = st.sidebar.file_uploader("Tokopedia", key="toped")

# --- Tombol Proses Data (PENTING - selalu visible) ---
st.sidebar.markdown("---")
if st.sidebar.button("🚀 PROSES DATA", type="primary"):
    if 'kamus_data' not in st.session_state:
        st.error("❌ Kamus belum di-load! Klik tombol 'Load Kamus Sekarang' terlebih dahulu.")
        st.info("Pastikan Google Spreadsheet sudah dibagikan (shared) ke service account.")
    else:
        files = []
        if shp_off_f: files.append(('Shopee (Official)', shp_off_f))
        if shp_inh_f: files.append(('Shopee (INHOUSE)', shp_inh_f))
        if tok_f: files.append(('Tokopedia', tok_f))
        
        if not files:
            st.error("❌ Upload minimal satu file order!")
        else:
            with st.spinner("Processing data..."):
                try:
                    res, err = process_universal_data(files, st.session_state['kamus_data'])
                    
                    if err: 
                        st.warning(err)
                    else:
                        # --- UPDATED TAB NAMES ---
                        t1, t2, t3 = st.tabs(["📋 Order Detail", "📦 Picking List-PRINT", "🔍 Validasi Kurir"])
                        
                        with t1:
                            if not res['detail'].empty:
                                st.dataframe(res['detail'], use_container_width=True)
                            else: 
                                st.info("Tidak ada data yang memenuhi kriteria.")
                        
                        with t2:
                            if not res['summary'].empty:
                                st.metric("Total Qty", res['summary']['Qty Total'].sum())
                                st.dataframe(res['summary'], use_container_width=True)
                            else: 
                                st.info("Tidak ada summary.")

                        with t3:
                            st.markdown("### 🔍 Cek Total Order per Kurir")
                            if not res['raw_stats'].empty:
                                def color_coding(val):
                                    if '✅' in val: return 'background-color: #d4edda; color: #155724'
                                    if '⚠️' in val: return 'background-color: #fff3cd; color: #856404'
                                    return ''
                                styled_df = res['raw_stats'].style.applymap(color_coding, subset=['Status Sistem'])
                                st.dataframe(styled_df, use_container_width=True)
                            else: 
                                st.info("Tidak ada data statistik kurir.")
                        
                        if not res['detail'].empty:
                            buf = io.BytesIO()
                            with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                                # --- UPDATED SHEET NAMES ---
                                res['detail'].to_excel(writer, sheet_name='Order Detail', index=False)
                                res['summary'].to_excel(writer, sheet_name='Picking List-PRINT', index=False)
                                
                                if not res['raw_stats'].empty:
                                    res['raw_stats'].to_excel(writer, sheet_name='Validasi Kurir', index=False)
                                
                                for sheet in writer.sheets.values():
                                    sheet.set_column(0, 5, 20)

                            st.download_button(
                                "📥 Download Excel Report",
                                data=buf.getvalue(),
                                file_name=f"Report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                type="primary"
                            )

                except Exception as e:
                    st.error(f"❌ System Error: {e}")
                    if DEBUG_MODE:
                        import traceback
                        st.error(f"Detail error: {traceback.format_exc()}")

# --- Bagian Bawah Sidebar (optional info) ---
st.sidebar.markdown("---")

# Informasi troubleshooting dalam expander agar tidak memakan space
with st.sidebar.expander("❓ Troubleshooting"):
    st.markdown("""
    **Jika gagal load kamus:**
    1. Pastikan Google Spreadsheet sudah di-share ke email service account:
       `gsheet-forcast-to-dashboard@inventoryforecast-479502.iam.gserviceaccount.com`
    2. Berikan permission **Editor** atau **Viewer**
    3. Nama sheet harus tepat:
       - `Kurir-Shopee`
       - `Bundle Master` 
       - `SKU Master`
    4. Aktifkan Debug Mode untuk info detil
    """)

st.sidebar.caption("v3.9 - Layout Optimized")

# Menampilkan status kamus jika sudah di-load (opsional, bisa di-expand)
if 'kamus_data' in st.session_state:
    with st.sidebar.expander("📋 Status Kamus (Loaded)", expanded=False):
        st.success("✅ Kamus sudah di-load")
        st.info(f"Kurir: {len(st.session_state['kamus_data']['kurir'])} baris")
        st.info(f"Bundle: {len(st.session_state['kamus_data']['bundle'])} baris")
        st.info(f"SKU: {len(st.session_state['kamus_data']['sku'])} baris")
