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
DEBUG_MODE = st.sidebar.checkbox("Tampilkan info detil (Debug)", value=True)  # Default True

# --- FUNGSI LOAD KAMUS ---
def load_kamus_from_gsheet():
    try:
        st.sidebar.info("📡 Connecting to Google Sheets...")
        
        if "type" not in st.secrets:
            st.error("❌ Secrets belum dikonfigurasi di Streamlit Cloud!")
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
        
        st.sidebar.info("🔑 Authorizing...")
        gc = gspread.authorize(credentials)
        
        st.sidebar.info("📖 Opening spreadsheet...")
        spreadsheet_id = "15c1uN2dVwMMT-bldZzRwVVExEau2ZgnI2_RgSIw7gG4"
        spreadsheet = gc.open_by_key(spreadsheet_id)
        
        kamus_data = {}
        sheet_mapping = [("Kurir-Shopee", "kurir"), ("Bundle Master", "bundle"), ("SKU Master", "sku")]
        
        for sheet_name, key in sheet_mapping:
            try:
                st.sidebar.info(f"📋 Loading sheet: {sheet_name}...")
                ws = spreadsheet.worksheet(sheet_name)
                data = ws.get_all_records()
                
                if data: 
                    kamus_data[key] = pd.DataFrame(data)
                    st.sidebar.success(f"✅ '{sheet_name}' loaded: {len(data)} rows")
                    
                    if DEBUG_MODE:
                        st.sidebar.write(f"**Columns in {sheet_name}:**")
                        st.sidebar.write(kamus_data[key].columns.tolist())
                        if len(data) > 0:
                            st.sidebar.write(f"**First row sample:**")
                            st.sidebar.write(dict(zip(kamus_data[key].columns, kamus_data[key].iloc[0].tolist())))
                else: 
                    st.sidebar.warning(f"⚠️ Sheet '{sheet_name}' kosong")
                    kamus_data[key] = pd.DataFrame()
                    
            except gspread.exceptions.WorksheetNotFound:
                st.error(f"❌ Sheet '{sheet_name}' tidak ditemukan!")
                return None
            except Exception as e:
                st.error(f"❌ Error loading sheet '{sheet_name}': {e}")
                return None

        if len(kamus_data) < 3: 
            st.error("❌ Tidak semua sheet ditemukan!")
            return None
            
        st.sidebar.success("🎉 Semua kamus berhasil di-load!")
        return kamus_data
        
    except Exception as e:
        st.error(f"❌ Gagal load kamus: {str(e)}")
        import traceback
        st.error(f"🔍 Traceback: {traceback.format_exc()}")
        return None

# --- FUNGSI CLEANING SKU ---
def clean_sku(sku):
    if pd.isna(sku): return ""
    sku = str(sku).strip()
    sku = ''.join(char for char in sku if ord(char) >= 32)
    sku_upper = sku.upper()
    if sku_upper.startswith('FG-') or sku_upper.startswith('CS-'): return sku
    if '-' in sku: return sku.split('-', 1)[-1].strip()
    return sku

# --- FUNGSI SMART LOADER ---
def load_data_smart(file_obj, mp_type):
    df = None
    filename = file_obj.name.lower()
    
    st.sidebar.info(f"📂 Loading file: {filename}")
    
    try:
        if filename.endswith('.xlsx') or filename.endswith('.xls'):
            try: 
                df = pd.read_excel(file_obj, dtype=str, header=None, engine='openpyxl')
                st.sidebar.success(f"✅ Excel file loaded: {df.shape}")
            except Exception as e: 
                st.sidebar.warning(f"⚠️ Excel read error: {e}")
                df = None
                
        if df is None or df.shape[1] <= 1:
            file_obj.seek(0)
            encodings = ['utf-8-sig', 'utf-8', 'latin-1']
            for enc in encodings:
                if df is not None and df.shape[1] > 1: break
                for sep in [',', ';', '\t']:
                    try:
                        file_obj.seek(0)
                        temp_df = pd.read_csv(file_obj, sep=sep, dtype=str, header=None, encoding=enc, on_bad_lines='skip', quotechar='"')
                        if temp_df.shape[1] > 1:
                            df = temp_df
                            st.sidebar.success(f"✅ CSV file loaded: {df.shape}")
                            break
                    except Exception as e: 
                        continue
                        
    except Exception as e: 
        return None, f"Gagal baca file: {str(e)}"

    if df is None or df.empty: 
        return None, "File kosong/format salah."

    # Debug: Show raw data
    if DEBUG_MODE:
        st.sidebar.write(f"**Raw data shape:** {df.shape}")
        st.sidebar.write(f"**First 3 rows raw:**")
        st.sidebar.dataframe(df.head(3))

    header_idx = 0
    keywords = ['status', 'sku', 'order', 'pesanan', 'quantity', 'jumlah', 'product', 'opsi pengiriman']
    
    for i in range(min(20, df.shape[0])):
        row_str = " ".join([str(v).lower() for v in df.iloc[i].dropna().values])
        matches = sum(1 for kw in keywords if kw in row_str)
        if DEBUG_MODE:
            st.sidebar.write(f"Row {i}: '{row_str[:50]}...' - Matches: {matches}")
        if matches >= 2:
            header_idx = i
            st.sidebar.success(f"✅ Header found at row {i}")
            break
    
    try:
        df_final = df.iloc[header_idx:].copy()
        df_final.columns = df_final.iloc[0]
        df_final = df_final.iloc[1:].reset_index(drop=True)
        df_final.columns = [str(c).strip().replace('\n', ' ') for c in df_final.columns]
        df_final = df_final.dropna(how='all')
        
        if DEBUG_MODE:
            st.sidebar.write(f"**Final dataframe shape:** {df_final.shape}")
            st.sidebar.write(f"**Columns detected:**")
            st.sidebar.write(df_final.columns.tolist())
            if not df_final.empty:
                st.sidebar.write(f"**First 3 rows sample:**")
                st.sidebar.dataframe(df_final.head(3))
        
        return df_final, None
    except Exception as e: 
        return None, f"Gagal set header: {e}"

# ==========================================
# MAIN PROCESSOR
# ==========================================
def process_universal_data(uploaded_files, kamus_data):
    all_rows = []
    raw_stats_list = []
    
    st.sidebar.info("🔧 Starting data processing...")
    
    try:
        # Load kamus data
        df_kurir = kamus_data['kurir']
        df_bundle = kamus_data['bundle']
        df_sku = kamus_data['sku']
        
        if DEBUG_MODE:
            st.sidebar.write("📊 **Kamus Statistics:**")
            st.sidebar.write(f"- Kurir: {df_kurir.shape[0]} rows, {df_kurir.shape[1]} cols")
            st.sidebar.write(f"- Bundle: {df_bundle.shape[0]} rows, {df_bundle.shape[1]} cols")
            st.sidebar.write(f"- SKU: {df_sku.shape[0]} rows, {df_sku.shape[1]} cols")
            
            # Show kurir columns
            st.sidebar.write("**Kurir Sheet Columns:**", df_kurir.columns.tolist())
            if not df_kurir.empty:
                st.sidebar.write("**First few rows of Kurir:**")
                st.sidebar.dataframe(df_kurir.head(3))
        
        # Process Bundle Mapping
        bundle_map = {}
        k_cols = {str(c).lower(): c for c in df_bundle.columns}
        
        if DEBUG_MODE:
            st.sidebar.write("**Bundle columns (lowercase):**", list(k_cols.keys()))
        
        kit_c = next((v for k,v in k_cols.items() if any(x in k for x in ['kit','bundle','parent'])), None)
        comp_c = next((v for k,v in k_cols.items() if any(x in k for x in ['component','child']) and v != kit_c), None)
        if not comp_c: 
            comp_c = next((v for k,v in k_cols.items() if 'sku' in k and v != kit_c), None)
        qty_c = next((v for k,v in k_cols.items() if any(x in k for x in ['qty','quantity'])), None)
        
        if DEBUG_MODE:
            st.sidebar.write(f"Bundle mapping - Kit: '{kit_c}', Component: '{comp_c}', Qty: '{qty_c}'")
        
        if kit_c and comp_c:
            for _, row in df_bundle.iterrows():
                k_val = clean_sku(row[kit_c])
                c_val = clean_sku(row[comp_c])
                try: 
                    q_val = float(str(row[qty_c]).replace(',', '.')) if qty_c else 1.0
                except: 
                    q_val = 1.0
                if k_val and c_val:
                    if k_val not in bundle_map: 
                        bundle_map[k_val] = []
                    bundle_map[k_val].append((c_val, q_val))
            
            if DEBUG_MODE:
                st.sidebar.success(f"✅ Bundle mapping created: {len(bundle_map)} bundles")
                if bundle_map:
                    sample_bundle = list(bundle_map.keys())[0]
                    st.sidebar.write(f"Sample bundle '{sample_bundle}': {bundle_map[sample_bundle]}")
        else:
            st.sidebar.warning("⚠️ Kolom untuk bundle mapping tidak lengkap")

        # Process SKU Mapping
        sku_name_map = {}
        for _, row in df_sku.iterrows():
            vals = [str(v).strip() for v in row if pd.notna(v) and str(v).strip()]
            if len(vals) >= 2: 
                sku_name_map[clean_sku(vals[0])] = vals[1]
        
        if DEBUG_MODE:
            st.sidebar.success(f"✅ SKU mapping created: {len(sku_name_map)} SKUs")
            if sku_name_map:
                sample_sku = list(sku_name_map.keys())[0]
                st.sidebar.write(f"Sample SKU '{sample_sku}': '{sku_name_map[sample_sku]}'")

        # Process Instant Kurir List
        instant_list = []
        if not df_kurir.empty:
            # Find instant column
            ins_col = None
            for col in df_kurir.columns:
                if 'instant' in str(col).lower():
                    ins_col = col
                    break
            
            kur_col = df_kurir.columns[0] if not df_kurir.empty else None
            
            if ins_col and kur_col:
                instant_list = df_kurir[
                    df_kurir[ins_col].astype(str).str.lower().isin(['yes','ya','true','1', 'instant'])
                ][kur_col].astype(str).str.strip().tolist()
                
                if DEBUG_MODE:
                    st.sidebar.success(f"✅ Instant kurir list: {len(instant_list)} kurir")
                    st.sidebar.write(f"Instant kurir: {instant_list}")
            else:
                st.sidebar.warning(f"⚠️ Kolom instant tidak ditemukan. Columns: {df_kurir.columns.tolist()}")
        else:
            st.sidebar.warning("⚠️ Kurir sheet kosong!")
            
    except Exception as e: 
        st.sidebar.error(f"❌ Error processing kamus: {e}")
        import traceback
        st.sidebar.error(f"🔍 Traceback: {traceback.format_exc()}")
        return None, f"Error Kamus: {e}"

    # Process each uploaded file
    for mp_type, file_obj in uploaded_files:
        st.sidebar.info(f"🔄 Processing {mp_type}...")
        
        df_raw, err = load_data_smart(file_obj, mp_type)
        if err:
            st.warning(f"⚠️ Skip {mp_type}: {err}")
            continue
            
        df_filtered = pd.DataFrame()
        df_raw.columns = [str(c).strip().lower() for c in df_raw.columns]

        if DEBUG_MODE:
            st.sidebar.write(f"**{mp_type} - All columns:**")
            st.sidebar.write(df_raw.columns.tolist())

        # RAW STATS - Find kurir column
        raw_kurir_col = None
        if 'shopee' in mp_type.lower():
            raw_kurir_col = next((c for c in df_raw.columns if any(x in c for x in ['opsi','kirim'])), None)
            if DEBUG_MODE:
                st.sidebar.write(f"Shopee kurir column search result: {raw_kurir_col}")
        elif 'tokopedia' in mp_type.lower():
            raw_kurir_col = next((c for c in df_raw.columns if 'shipping provider' in c), None)
            if not raw_kurir_col: 
                raw_kurir_col = next((c for c in df_raw.columns if 'delivery option' in c), None)
            if not raw_kurir_col: 
                raw_kurir_col = next((c for c in df_raw.columns if 'kurir' in c), None)
            if DEBUG_MODE:
                st.sidebar.write(f"Tokopedia kurir column search result: {raw_kurir_col}")
        
        if raw_kurir_col:
            stats = df_raw[raw_kurir_col].fillna('BLANK').value_counts().reset_index()
            stats.columns = ['Jenis Kurir', 'Jumlah Order (Raw)']
            stats['Sumber Data'] = mp_type
            stats['Status Sistem'] = stats['Jenis Kurir'].apply(
                lambda x: '✅ Whitelisted' if str(x).strip() in instant_list else 
                         ('⚠️ Kemungkinan Instant' if 'instant' in str(x).lower() or 'same' in str(x).lower() else '❌ Non-Instant')
            )
            raw_stats_list.append(stats)
            
            if DEBUG_MODE:
                st.sidebar.write(f"**{mp_type} - Kurir Statistics:**")
                st.sidebar.dataframe(stats)
        else:
            st.sidebar.warning(f"⚠️ {mp_type}: Kolom kurir tidak ditemukan")
            raw_stats_list.append(pd.DataFrame({
                'Sumber Data': [mp_type], 
                'Jenis Kurir': ['(Not Found)'], 
                'Jumlah Order (Raw)': [len(df_raw)], 
                'Status Sistem': ['-']
            }))

        # FILTERING LOGIC
        if mp_type == 'Shopee (Official)':
            status_c = next((c for c in df_raw.columns if 'status' in c), None)
            resi_c = next((c for c in df_raw.columns if 'resi' in c), None)
            kurir_c = next((c for c in df_raw.columns if any(x in c for x in ['opsi','kirim'])), None)
            managed_c = next((c for c in df_raw.columns if 'dikelola' in c), None)

            if DEBUG_MODE:
                st.sidebar.write(f"**Shopee Official columns:**")
                st.sidebar.write(f"- Status: {status_c}")
                st.sidebar.write(f"- Resi: {resi_c}")
                st.sidebar.write(f"- Kurir: {kurir_c}")
                st.sidebar.write(f"- Managed: {managed_c}")

            if all([status_c, resi_c, kurir_c]):
                # Count each condition
                c1 = df_raw[status_c].astype(str).str.strip().str.lower() == 'perlu dikirim'
                c2 = df_raw[resi_c].fillna('').astype(str).str.strip().isin(['','nan','none'])
                c4 = df_raw[kurir_c].astype(str).str.strip().isin(instant_list)
                c3 = df_raw[managed_c].astype(str).str.strip().str.lower() == 'no' if managed_c else True
                
                if DEBUG_MODE:
                    st.sidebar.write(f"**Shopee Official filter counts:**")
                    st.sidebar.write(f"- Status 'Perlu Dikirim': {c1.sum()}")
                    st.sidebar.write(f"- Resi Blank: {c2.sum()}")
                    st.sidebar.write(f"- Kurir Instant: {c4.sum()}")
                    if managed_c:
                        st.sidebar.write(f"- Managed 'No': {(df_raw[managed_c].astype(str).str.strip().str.lower() == 'no').sum()}")
                
                df_filtered = df_raw[c1 & c2 & c3 & c4].copy()
                
                if DEBUG_MODE:
                    st.sidebar.success(f"✅ Shopee Official filtered: {len(df_filtered)} rows")
            else: 
                st.error(f"❌ Shopee Official: Kolom tidak lengkap!")

        elif mp_type == 'Shopee (INHOUSE)':
            status_c = next((c for c in df_raw.columns if 'status' in c), None)
            resi_c = next((c for c in df_raw.columns if 'resi' in c), None)
            kurir_c = next((c for c in df_raw.columns if any(x in c for x in ['opsi','kirim'])), None)
            
            if DEBUG_MODE:
                st.sidebar.write(f"**Shopee INHOUSE columns:**")
                st.sidebar.write(f"- Status: {status_c}")
                st.sidebar.write(f"- Resi: {resi_c}")
                st.sidebar.write(f"- Kurir: {kurir_c}")

            if all([status_c, resi_c, kurir_c]):
                c1 = df_raw[status_c].astype(str).str.strip().str.lower() == 'perlu dikirim'
                c2 = df_raw[resi_c].fillna('').astype(str).str.strip().isin(['','nan','none'])
                c3 = df_raw[kurir_c].astype(str).str.strip().isin(instant_list)
                
                if DEBUG_MODE:
                    st.sidebar.write(f"**Shopee INHOUSE filter counts:**")
                    st.sidebar.write(f"- Status 'Perlu Dikirim': {c1.sum()}")
                    st.sidebar.write(f"- Resi Blank: {c2.sum()}")
                    st.sidebar.write(f"- Kurir Instant: {c3.sum()}")
                
                df_filtered = df_raw[c1 & c2 & c3].copy()
                
                if DEBUG_MODE:
                    st.sidebar.success(f"✅ Shopee INHOUSE filtered: {len(df_filtered)} rows")
            else: 
                st.error(f"❌ Shopee Inhouse: Kolom tidak lengkap!")

        elif mp_type == 'Tokopedia':
            status_c = next((c for c in df_raw.columns if 'status' in c), None)
            if status_c:
                c1 = df_raw[status_c].astype(str).str.strip().str.lower() == 'perlu dikirim'
                
                if DEBUG_MODE:
                    st.sidebar.write(f"**Tokopedia filter counts:**")
                    st.sidebar.write(f"- Status 'Perlu Dikirim': {c1.sum()}")
                
                df_filtered = df_raw[c1].copy()
                
                if DEBUG_MODE:
                    st.sidebar.success(f"✅ Tokopedia filtered: {len(df_filtered)} rows")
            else: 
                st.error("❌ Tokopedia: Kolom Status tidak ditemukan")

        # Check if any data passed filtering
        if df_filtered.empty: 
            st.sidebar.warning(f"⚠️ {mp_type}: Tidak ada data yang lolos filter")
            continue

        # MAPPING SKU
        col_sku = 'SKU'
        if 'shopee' in mp_type.lower():
            col_sku = next((c for c in df_raw.columns if 'nomor referensi sku' in c), None)
            if not col_sku: 
                col_sku = next((c for c in df_raw.columns if 'referensi sku' in c), None)
            if not col_sku: 
                col_sku = next((c for c in df_raw.columns if 'sku' in c), 'SKU')
        else:
            col_sku = next((c for c in df_raw.columns if any(x in c for x in ['seller sku', 'nomor sku'])), None)
            if not col_sku: 
                col_sku = next((c for c in df_raw.columns if 'sku' in c), 'SKU')
        
        col_qty = next((c for c in df_raw.columns if any(x in c for x in ['jumlah','quantity'])), 'Jumlah')
        col_ord = next((c for c in df_raw.columns if any(x in c for x in ['pesanan','order','invoice'])), 'Order ID')

        if DEBUG_MODE:
            st.sidebar.write(f"**{mp_type} - Mapping columns:**")
            st.sidebar.write(f"- SKU column: {col_sku}")
            st.sidebar.write(f"- Qty column: {col_qty}")
            st.sidebar.write(f"- Order column: {col_ord}")
            st.sidebar.write(f"- Filtered rows: {len(df_filtered)}")

        for idx, row in df_filtered.iterrows():
            raw_sku = str(row.get(col_sku, ''))
            sku_clean = clean_sku(raw_sku)
            order_id = str(row.get(col_ord, ''))
            
            try: 
                qty = float(str(row.get(col_qty, 0)).replace(',', '.'))
            except: 
                qty = 0
            
            if DEBUG_MODE and idx < 3:  # Show first 3 rows for debugging
                st.sidebar.write(f"Row {idx}: SKU='{raw_sku}' -> '{sku_clean}', Qty={qty}")
            
            if not sku_clean or qty <= 0: 
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
    df_summary = pd.DataFrame()
    
    if not df_detail.empty:
        df_summary = df_detail.groupby(['Marketplace', 'SKU Component', 'Nama Produk'], as_index=False)['Qty Total'].sum()
        df_summary = df_summary.sort_values('Qty Total', ascending=False)
    
    df_raw_stats = pd.concat(raw_stats_list, ignore_index=True) if raw_stats_list else pd.DataFrame()
    
    if DEBUG_MODE:
        st.sidebar.write("📈 **Final Statistics:**")
        st.sidebar.write(f"- Detail rows: {len(df_detail)}")
        st.sidebar.write(f"- Summary rows: {len(df_summary)}")
        st.sidebar.write(f"- Raw stats rows: {len(df_raw_stats)}")
    
    return {'detail': df_detail, 'summary': df_summary, 'raw_stats': df_raw_stats}, None

# --- UI STREAMLIT ---
st.sidebar.header("📁 Load Kamus dari Google Sheets")
st.sidebar.markdown("- Kurir-Shopee | Bundle Master | SKU Master")

if st.sidebar.button("🔄 Load Kamus Sekarang", type="primary"):
    with st.spinner("Loading kamus dari Google Sheets..."):
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
    # Clear previous results
    if 'results' in st.session_state:
        del st.session_state['results']
    
    if 'kamus_data' not in st.session_state:
        st.error("❌ Load Kamus dulu!")
        st.stop()
    
    files = []
    if shp_off_f: 
        files.append(('Shopee (Official)', shp_off_f))
        st.sidebar.success(f"✅ Shopee Official uploaded: {shp_off_f.name}")
    if shp_inh_f: 
        files.append(('Shopee (INHOUSE)', shp_inh_f))
        st.sidebar.success(f"✅ Shopee INHOUSE uploaded: {shp_inh_f.name}")
    if tok_f: 
        files.append(('Tokopedia', tok_f))
        st.sidebar.success(f"✅ Tokopedia uploaded: {tok_f.name}")
    
    if not files:
        st.error("❌ Upload minimal satu file!")
        st.stop()
    
    with st.spinner("Processing data..."):
        try:
            res, err = process_universal_data(files, st.session_state['kamus_data'])
            
            if err:
                st.warning(f"⚠️ Error: {err}")
            else:
                st.session_state['results'] = res
                
                # Show results
                if not res['detail'].empty:
                    # Create tabs
                    t1, t2, t3 = st.tabs(["📋 Order Detail", "📦 Picking List-PRINT", "🔍 Validasi Kurir"])
                    
                    with t1: 
                        st.dataframe(res['detail'], use_container_width=True)
                    
                    with t2:
                        if not res['summary'].empty:
                            st.metric("Total Qty", res['summary']['Qty Total'].sum())
                        st.dataframe(res['summary'], use_container_width=True)
                    
                    with t3:
                        if not res['raw_stats'].empty:
                            st.dataframe(res['raw_stats'], use_container_width=True)
                        else:
                            st.info("Tidak ada data statistik kurir")
                    
                    # Download button
                    buf = io.BytesIO()
                    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
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
                else:
                    st.error("❌ Tidak ada data yang memenuhi kriteria!")
                    st.info("""
                    **Kemungkinan penyebab:**
                    1. File order tidak memiliki data dengan status 'Perlu Dikirim'
                    2. Untuk Shopee: Kurir tidak termasuk dalam list instant
                    3. Untuk Shopee: Resi sudah terisi
                    4. Format file tidak sesuai
                    
                    **Cek di sidebar (Debug Mode aktif) untuk info detail!**
                    """)
                    
        except Exception as e:
            st.error(f"❌ System Error: {e}")
            import traceback
            st.error(f"🔍 Traceback: {traceback.format_exc()}")

# Show results if they exist
if 'results' in st.session_state:
    res = st.session_state['results']
    if not res['detail'].empty:
        st.success(f"✅ Data processed successfully! Found {len(res['detail'])} orders.")
    else:
        st.warning("⚠️ Processing completed but no data found.")

st.sidebar.caption("v3.14 - Enhanced Debugging")

# Status indicator
if 'kamus_data' in st.session_state:
    st.sidebar.success("✅ Kamus sudah di-load")
else:
    st.sidebar.warning("⚠️ Kamus belum di-load")
