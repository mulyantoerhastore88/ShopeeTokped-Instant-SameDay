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
st.title("🛒 Universal Marketplace Order Processor")
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

# --- FUNGSI LOAD KAMUS ---
def load_kamus_from_gsheet():
    try:
        # Cek apakah secrets tersedia
        if "type" not in st.secrets:
            st.error("❌ Secrets belum dikonfigurasi di Streamlit Cloud!")
            st.info("Buka Dashboard Streamlit > App Settings > Secrets, lalu paste isi JSON service account Anda.")
            return None

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
        credentials = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        gc = gspread.authorize(credentials)
        spreadsheet_id = "15c1uN2dVwMMT-bldZzRwVVExEau2ZgnI2_RgSIw7gG4"
        spreadsheet = gc.open_by_key(spreadsheet_id)
        
        kamus_data = {}
        for sheet_name, key in [("Kurir-Shopee", "kurir"), ("Bundle Master", "bundle"), ("SKU Master", "sku")]:
            try:
                ws = spreadsheet.worksheet(sheet_name)
                data = ws.get_all_records()
                if data:
                    kamus_data[key] = pd.DataFrame(data)
                else:
                    st.sidebar.warning(f"⚠️ Sheet {sheet_name} kosong")
            except gspread.exceptions.WorksheetNotFound:
                st.error(f"❌ Sheet '{sheet_name}' tidak ditemukan!")
                return None

        if len(kamus_data) < 3: return None
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
    
    # 1. PREPARE KAMUS & BUNDLE MAP
    try:
        df_kurir = kamus_data['kurir']
        df_bundle = kamus_data['bundle']
        df_sku = kamus_data['sku']
        
        bundle_map = {}
        # Kolom mapping (Lowercase)
        k_cols = {str(c).lower(): c for c in df_bundle.columns}
        
        # --- FIXED LOGIC: Strict Column Identification ---
        kit_c = next((v for k,v in k_cols.items() if any(x in k for x in ['kit','bundle','parent'])), None)
        comp_c = None
        comp_c = next((v for k,v in k_cols.items() if any(x in k for x in ['component','child']) and v != kit_c), None)
        if not comp_c:
             comp_c = next((v for k,v in k_cols.items() if 'sku' in k and v != kit_c), None)
             
        qty_c = next((v for k,v in k_cols.items() if any(x in k for x in ['qty','quantity'])), None)
        
        if kit_c and comp_c:
            for _, row in df_bundle.iterrows():
                k_val = clean_sku(row[kit_c])
                c_val = clean_sku(row[comp_c])
                
                try: q_val = float(str(row[qty_c]).replace(',', '.')) if qty_c else 1.0
                except: q_val = 1.0
                
                if k_val and c_val:
                    if k_val not in bundle_map: bundle_map[k_val] = []
                    bundle_map[k_val].append((c_val, q_val))

        sku_name_map = {}
        for _, row in df_sku.iterrows():
            vals = [str(v).strip() for v in row if pd.notna(v) and str(v).strip()]
            if len(vals) >= 2: sku_name_map[clean_sku(vals[0])] = vals[1]

        instant_list = []
        if not df_kurir.empty:
            ins_col = next((c for c in df_kurir.columns if 'instant' in str(c).lower()), None)
            kur_col = df_kurir.columns[0]
            if ins_col:
                instant_list = df_kurir[
                    df_kurir[ins_col].astype(str).str.lower().isin(['yes','ya','true','1'])
                ][kur_col].astype(str).str.strip().tolist()

    except Exception as e: return None, f"Error Kamus: {e}"

    # 2. PROCESS FILES
    for mp_type, file_obj in uploaded_files:
        df_raw, err = load_data_smart(file_obj)
        if err:
            st.warning(f"⚠️ Skip {mp_type}: {err}")
            continue
            
        df_filtered = pd.DataFrame()
        df_raw.columns = [str(c).strip().lower() for c in df_raw.columns]

        # --- A. RAW STATS ---
        raw_kurir_col = None
        if 'shopee' in mp_type.lower():
            raw_kurir_col = next((c for c in df_raw.columns if any(x in c for x in ['opsi','kirim'])), None)
        elif 'tokopedia' in mp_type.lower():
            raw_kurir_col = next((c for c in df_raw.columns if 'shipping provider' in c), None)
            if not raw_kurir_col: raw_kurir_col = next((c for c in df_raw.columns if 'delivery option' in c), None)
            if not raw_kurir_col: raw_kurir_col = next((c for c in df_raw.columns if 'kurir' in c), None)
        
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
            raw_stats_list.append(pd.DataFrame({'Sumber Data': [mp_type], 'Jenis Kurir': ['(Not Found)'], 'Jumlah Order (Raw)': [len(df_raw)], 'Status Sistem': ['-']}))

        # --- B. FILTERING LOGIC ---
        if mp_type == 'Shopee (Official)':
            status_c = next((c for c in df_raw.columns if 'status' in c), None)
            resi_c = next((c for c in df_raw.columns if 'resi' in c), None)
            kurir_c = next((c for c in df_raw.columns if any(x in c for x in ['opsi','kirim'])), None)
            managed_c = next((c for c in df_raw.columns if 'dikelola' in c), None)

            if all([status_c, resi_c, kurir_c]):
                c1 = df_raw[status_c].astype(str).str.strip().str.lower() == 'perlu dikirim'
                c2 = df_raw[resi_c].fillna('').astype(str).str.strip().isin(['','nan','none'])
                c4 = df_raw[kurir_c].astype(str).str.strip().isin(instant_list)
                c3 = df_raw[managed_c].astype(str).str.strip().str.lower() == 'no' if managed_c else True
                df_filtered = df_raw[c1 & c2 & c3 & c4].copy()
            else: st.error(f"Shopee Official: Kolom tidak lengkap!")

        elif mp_type == 'Shopee (INHOUSE)':
            status_c = next((c for c in df_raw.columns if 'status' in c), None)
            resi_c = next((c for c in df_raw.columns if 'resi' in c), None)
            kurir_c = next((c for c in df_raw.columns if any(x in c for x in ['opsi','kirim'])), None)
            
            if all([status_c, resi_c, kurir_c]):
                c1 = df_raw[status_c].astype(str).str.strip().str.lower() == 'perlu dikirim'
                c2 = df_raw[resi_c].fillna('').astype(str).str.strip().isin(['','nan','none'])
                c3 = df_raw[kurir_c].astype(str).str.strip().isin(instant_list)
                df_filtered = df_raw[c1 & c2 & c3].copy()
            else: st.error(f"Shopee Inhouse: Kolom tidak lengkap!")

        elif mp_type == 'Tokopedia':
            status_c = next((c for c in df_raw.columns if 'status' in c), None)
            if status_c:
                c1 = df_raw[status_c].astype(str).str.strip().str.lower() == 'perlu dikirim'
                df_filtered = df_raw[c1].copy()
            else: st.error("Tokopedia: Status tidak ditemukan")

        # --- C. MAPPING SKU ---
        if df_filtered.empty: continue

        col_sku = 'SKU'
        if 'shopee' in mp_type.lower():
            col_sku = next((c for c in df_raw.columns if 'nomor referensi sku' in c), None)
            if not col_sku: col_sku = next((c for c in df_raw.columns if 'referensi sku' in c), None)
            if not col_sku: col_sku = next((c for c in df_raw.columns if 'sku' in c), 'SKU')
        else:
            col_sku = next((c for c in df_raw.columns if any(x in c for x in ['seller sku', 'nomor sku'])), None)
            if not col_sku: col_sku = next((c for c in df_raw.columns if 'sku' in c), 'SKU')
        
        col_qty = next((c for c in df_raw.columns if any(x in c for x in ['jumlah','quantity'])), 'Jumlah')
        col_ord = next((c for c in df_raw.columns if any(x in c for x in ['pesanan','order','invoice'])), 'Order ID')

        for _, row in df_filtered.iterrows():
            raw_sku = str(row.get(col_sku, ''))
            sku_clean = clean_sku(raw_sku)
            order_id = str(row.get(col_ord, ''))
            try: qty = float(str(row.get(col_qty, 0)).replace(',', '.'))
            except: qty = 0
            
            if not sku_clean or qty <= 0: continue
            
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
    df_summary = pd.DataFrame()
    if not df_detail.empty:
        df_summary = df_detail.groupby(['Marketplace', 'SKU Component', 'Nama Produk'], as_index=False)['Qty Total'].sum()
        df_summary = df_summary.sort_values('Qty Total', ascending=False)
    
    df_raw_stats = pd.concat(raw_stats_list, ignore_index=True) if raw_stats_list else pd.DataFrame()
    
    return {'detail': df_detail, 'summary': df_summary, 'raw_stats': df_raw_stats}, None

# --- UI STREAMLIT ---
st.sidebar.header("📁 Load Kamus dari Google Sheets")
st.sidebar.markdown("- Kurir-Shopee | Bundle Master | SKU Master")

if st.sidebar.button("🔄 Load Kamus Sekarang", type="primary"):
    with st.spinner("Loading..."):
        kamus_data = load_kamus_from_gsheet()
        if kamus_data:
            st.session_state['kamus_data'] = kamus_data
            st.sidebar.success("✅ Kamus Loaded!")

st.sidebar.markdown("---")
st.sidebar.markdown("**Upload Order:**")
shp_off_f = st.sidebar.file_uploader("Shopee (Official)", key="so")
shp_inh_f = st.sidebar.file_uploader("Shopee (INHOUSE)", key="si")
tok_f = st.sidebar.file_uploader("Tokopedia", key="toped")

st.sidebar.markdown("---")
if st.sidebar.button("🚀 PROSES DATA", type="primary"):
    if 'kamus_data' not in st.session_state:
        st.error("❌ Load Kamus dulu!")
    else:
        files = []
        if shp_off_f: files.append(('Shopee (Official)', shp_off_f))
        if shp_inh_f: files.append(('Shopee (INHOUSE)', shp_inh_f))
        if tok_f: files.append(('Tokopedia', tok_f))
        
        if not files:
            st.error("❌ Upload minimal satu file!")
        else:
            with st.spinner("Processing..."):
                try:
                    res, err = process_universal_data(files, st.session_state['kamus_data'])
                    if err: st.warning(err)
                    
                    t1, t2, t3 = st.tabs(["📋 Order Detail", "📦 Picking List-PRINT", "🔍 Validasi Kurir"])
                    # FIX: Menghapus use_container_width=True untuk kompatibilitas
                    with t1: st.dataframe(res['detail']) 
                    with t2:
                        st.metric("Total Qty", res['summary']['Qty Total'].sum() if not res['summary'].empty else 0)
                        st.dataframe(res['summary'])
                    with t3:
                        if not res['raw_stats'].empty:
                            def color_coding(val):
                                if '✅' in val: return 'background-color: #d4edda; color: #155724'
                                if '⚠️' in val: return 'background-color: #fff3cd; color: #856404'
                                return ''
                            st.dataframe(res['raw_stats'].style.applymap(color_coding, subset=['Status Sistem']))
                    
                    if not res['detail'].empty:
                        buf = io.BytesIO()
                        with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                            res['detail'].to_excel(writer, sheet_name='Order Detail', index=False)
                            res['summary'].to_excel(writer, sheet_name='Picking List-PRINT', index=False)
                            if not res['raw_stats'].empty: res['raw_stats'].to_excel(writer, sheet_name='Validasi Kurir', index=False)
                            for sheet in writer.sheets.values(): sheet.set_column(0, 5, 20)
                        st.download_button("📥 Download Excel", data=buf.getvalue(), file_name=f"Report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")

                except Exception as e:
                    st.error(f"❌ System Error: {e}")

st.sidebar.caption("v3.11 - Fix Warning & Secrets Check")
