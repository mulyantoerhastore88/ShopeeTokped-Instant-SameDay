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
st.set_page_config(
    page_title="Universal Order Processor", 
    layout="wide",
    initial_sidebar_state="expanded"
)

st.title("🛒 Universal Marketplace Order Processor")
st.markdown("""
**Logic Applied:**
1. **Shopee (Official)**: Status='Perlu Dikirim' | Resi=Blank | Managed='No' (Optional) | **Kurir=Instant (Kamus)**.
2. **Shopee (INHOUSE)**: Status='Perlu Dikirim' | Resi=Blank | **Kurir=Instant (Kamus)**.
3. **Tokopedia**: Status='Perlu Dikirim'.
4. **SKU Logic**: 
   - **Shopee (Official & INHOUSE)**: Hanya ambil dari **"Nomor Referensi SKU"**
   - **Tokopedia**: Tetap seperti sebelumnya (ambil dari kolom SKU)
""")

# --- SESSION STATE INIT ---
if 'kamus_data' not in st.session_state:
    st.session_state.kamus_data = None
if 'results' not in st.session_state:
    st.session_state.results = None
if 'processed' not in st.session_state:
    st.session_state.processed = False

# --- DEBUG MODE ---
st.sidebar.header("🔧 Debug Mode")
DEBUG_MODE = st.sidebar.checkbox("Tampilkan info detil (Debug)", value=False)

# --- FUNGSI LOAD KAMUS ---
@st.cache_data(ttl=300)
def load_kamus_from_gsheet():
    try:
        st.sidebar.info("📡 Connecting to Google Sheets...")
        
        if "type" not in st.secrets:
            st.error("❌ Secrets belum dikonfigurasi!")
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
        sheet_mapping = [("Kurir-Shopee", "kurir"), ("Bundle Master", "bundle"), ("SKU Master", "sku")]
        
        for sheet_name, key in sheet_mapping:
            try:
                ws = spreadsheet.worksheet(sheet_name)
                data = ws.get_all_records()
                
                if data: 
                    kamus_data[key] = pd.DataFrame(data)
                    st.sidebar.success(f"✅ '{sheet_name}' loaded: {len(data)} rows")
                    if DEBUG_MODE:
                        st.sidebar.write(f"Kolom {sheet_name}: {kamus_data[key].columns.tolist()}")
                else: 
                    st.sidebar.warning(f"⚠️ '{sheet_name}' kosong")
                    kamus_data[key] = pd.DataFrame()
                    
            except Exception as e:
                st.error(f"❌ Error loading '{sheet_name}': {e}")
                return None

        if len(kamus_data) < 3: 
            st.error("❌ Tidak semua sheet ditemukan!")
            return None
            
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
    if sku_upper.startswith('FG-') or sku_upper.startswith('CS-'): return sku
    if '-' in sku: return sku.split('-', 1)[-1].strip()
    return sku

# --- FUNGSI SMART LOADER ---
def load_data_smart(file_obj):
    try:
        filename = file_obj.name.lower()
        
        # Try Excel first
        if filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(file_obj, dtype=str, engine='openpyxl')
        else:
            # Try CSV with different encodings
            encodings = ['utf-8-sig', 'utf-8', 'latin-1']
            for enc in encodings:
                try:
                    file_obj.seek(0)
                    df = pd.read_csv(file_obj, dtype=str, encoding=enc, on_bad_lines='skip')
                    break
                except:
                    continue
            else:
                return None, "Format file tidak didukung"
        
        return df, None
        
    except Exception as e:
        return None, f"Error membaca file: {str(e)}"

# ==========================================
# MAIN PROCESSOR dengan LOGIC SKU TERTENTU
# ==========================================
def process_universal_data(uploaded_files, kamus_data):
    try:
        all_rows = []
        raw_stats_list = []
        
        # Load kamus
        df_kurir = kamus_data['kurir']
        df_bundle = kamus_data['bundle']
        df_sku = kamus_data['sku']
        
        # Debug info
        if DEBUG_MODE:
            st.sidebar.write("📊 **Kamus Info:**")
            st.sidebar.write(f"- Kurir: {df_kurir.shape}")
            st.sidebar.write(f"- Bundle: {df_bundle.shape}")
            st.sidebar.write(f"- SKU: {df_sku.shape}")
        
        # Process Bundle Map
        bundle_map = {}
        if not df_bundle.empty:
            k_cols = {str(c).lower(): c for c in df_bundle.columns}
            kit_c = next((v for k,v in k_cols.items() if any(x in k for x in ['kit','bundle','parent'])), df_bundle.columns[0])
            comp_c = next((v for k,v in k_cols.items() if any(x in k for x in ['component','child','sku']) and v != kit_c), 
                         df_bundle.columns[1] if len(df_bundle.columns) > 1 else kit_c)
            qty_c = next((v for k,v in k_cols.items() if any(x in k for x in ['qty','quantity'])), None)
            
            if DEBUG_MODE:
                st.sidebar.write(f"Bundle columns - Kit: {kit_c}, Component: {comp_c}, Qty: {qty_c}")
            
            for _, row in df_bundle.iterrows():
                k_val = clean_sku(row[kit_c])
                c_val = clean_sku(row[comp_c])
                q_val = 1.0
                if qty_c:
                    try: q_val = float(str(row[qty_c]).replace(',', '.'))
                    except: q_val = 1.0
                
                if k_val and c_val:
                    if k_val not in bundle_map: bundle_map[k_val] = []
                    bundle_map[k_val].append((c_val, q_val))
            
            if DEBUG_MODE:
                st.sidebar.write(f"Bundle mapping created: {len(bundle_map)} items")
        
        # SKU Map
        sku_name_map = {}
        if not df_sku.empty:
            for _, row in df_sku.iterrows():
                vals = [str(v).strip() for v in row if pd.notna(v) and str(v).strip()]
                if len(vals) >= 2: 
                    sku_name_map[clean_sku(vals[0])] = vals[1]
        
        # Instant Kurir List
        instant_list = []
        if not df_kurir.empty:
            ins_col = next((c for c in df_kurir.columns if 'instant' in str(c).lower()), None)
            kur_col = df_kurir.columns[0]
            if ins_col:
                instant_list = df_kurir[
                    df_kurir[ins_col].astype(str).str.lower().isin(['yes','ya','true','1'])
                ][kur_col].astype(str).str.strip().tolist()
                
                if DEBUG_MODE:
                    st.sidebar.write(f"Instant kurir list: {instant_list}")
        
        # Process each file
        for mp_type, file_obj in uploaded_files:
            df_raw, err = load_data_smart(file_obj)
            if err:
                st.sidebar.warning(f"⚠️ {mp_type}: {err}")
                continue
            
            if DEBUG_MODE:
                st.sidebar.write(f"📁 Processing {mp_type} - Original columns:")
                st.sidebar.write(df_raw.columns.tolist())
            
            # Convert column names to lowercase for matching
            original_columns = {str(c): c for c in df_raw.columns}  # Simpan mapping original
            df_raw.columns = [str(c).strip().lower() for c in df_raw.columns]
            
            # Find kurir column for stats
            kurir_col = None
            if 'shopee' in mp_type.lower():
                kurir_col = next((c for c in df_raw.columns if any(x in c for x in ['opsi','kirim'])), None)
            elif 'tokopedia' in mp_type.lower():
                kurir_col = next((c for c in df_raw.columns if any(x in c for x in ['kurir','shipping','delivery'])), None)
            
            # Create stats
            if kurir_col and kurir_col in df_raw.columns:
                stats = df_raw[kurir_col].fillna('BLANK').value_counts().reset_index()
                stats.columns = ['Jenis Kurir', 'Jumlah Order (Raw)']
                stats['Sumber Data'] = mp_type
                stats['Status Sistem'] = stats['Jenis Kurir'].apply(
                    lambda x: '✅ Whitelisted' if str(x).strip() in instant_list 
                    else ('⚠️ Kemungkinan Instant' if any(kw in str(x).lower() for kw in ['instant', 'same']) 
                    else '❌ Non-Instant')
                )
                raw_stats_list.append(stats)
            
            # Filter based on marketplace
            df_filtered = pd.DataFrame()
            
            if mp_type == 'Shopee (Official)':
                status_col = next((c for c in df_raw.columns if 'status' in c), None)
                resi_col = next((c for c in df_raw.columns if 'resi' in c), None)
                kurir_col = next((c for c in df_raw.columns if any(x in c for x in ['opsi','kirim'])), None)
                managed_col = next((c for c in df_raw.columns if 'dikelola' in c), None)
                
                if DEBUG_MODE:
                    st.sidebar.write(f"Shopee Official columns found:")
                    st.sidebar.write(f"- Status: {status_col}")
                    st.sidebar.write(f"- Resi: {resi_col}")
                    st.sidebar.write(f"- Kurir: {kurir_col}")
                    st.sidebar.write(f"- Managed: {managed_col}")
                
                if all([status_col, resi_col, kurir_col]):
                    # Filter conditions
                    c1 = df_raw[status_col].astype(str).str.strip().str.lower() == 'perlu dikirim'
                    c2 = df_raw[resi_col].fillna('').astype(str).str.strip().isin(['', 'nan', 'none'])
                    c3 = df_raw[kurir_col].astype(str).str.strip().isin(instant_list)
                    
                    if managed_col:
                        c4 = df_raw[managed_col].astype(str).str.strip().str.lower() == 'no'
                        df_filtered = df_raw[c1 & c2 & c3 & c4].copy()
                    else:
                        df_filtered = df_raw[c1 & c2 & c3].copy()
                    
                    if DEBUG_MODE:
                        st.sidebar.write(f"Shopee Official filtered: {len(df_filtered)} rows")
                        if len(df_filtered) > 0:
                            st.sidebar.write("Sample filtered rows:")
                            st.sidebar.dataframe(df_filtered.head(3))
                    
            elif mp_type == 'Shopee (INHOUSE)':
                status_col = next((c for c in df_raw.columns if 'status' in c), None)
                resi_col = next((c for c in df_raw.columns if 'resi' in c), None)
                kurir_col = next((c for c in df_raw.columns if any(x in c for x in ['opsi','kirim'])), None)
                
                if DEBUG_MODE:
                    st.sidebar.write(f"Shopee INHOUSE columns found:")
                    st.sidebar.write(f"- Status: {status_col}")
                    st.sidebar.write(f"- Resi: {resi_col}")
                    st.sidebar.write(f"- Kurir: {kurir_col}")
                
                if all([status_col, resi_col, kurir_col]):
                    mask = (
                        (df_raw[status_col].astype(str).str.strip().str.lower() == 'perlu dikirim') &
                        (df_raw[resi_col].fillna('').astype(str).str.strip().isin(['', 'nan', 'none'])) &
                        (df_raw[kurir_col].astype(str).str.strip().isin(instant_list))
                    )
                    df_filtered = df_raw[mask].copy()
                    
                    if DEBUG_MODE:
                        st.sidebar.write(f"Shopee INHOUSE filtered: {len(df_filtered)} rows")
                    
            elif mp_type == 'Tokopedia':
                status_col = next((c for c in df_raw.columns if 'status' in c), None)
                if status_col:
                    mask = df_raw[status_col].astype(str).str.strip().str.lower() == 'perlu dikirim'
                    df_filtered = df_raw[mask].copy()
                    
                    if DEBUG_MODE:
                        st.sidebar.write(f"Tokopedia filtered: {len(df_filtered)} rows")
            
            # Skip if no data
            if df_filtered.empty:
                if DEBUG_MODE:
                    st.sidebar.warning(f"⚠️ {mp_type}: Tidak ada data yang lolos filter")
                continue
            
            # === FIND SKU COLUMN - LOGIC BERBEDA UNTUK SETIAP MARKETPLACE ===
            sku_col = None
            qty_col = None
            order_col = None
            
            if 'shopee' in mp_type.lower():
                # === SHOPEE: HANYA AMBIL DARI "NOMOR REFERENSI SKU" ===
                # Priority 1: "nomor referensi sku" (case insensitive)
                sku_col = next((c for c in df_filtered.columns if 'nomor referensi sku' in c), None)
                
                # Priority 2: "referensi sku" (fallback)
                if not sku_col:
                    sku_col = next((c for c in df_filtered.columns if 'referensi sku' in c), None)
                
                if DEBUG_MODE:
                    if sku_col:
                        st.sidebar.success(f"✅ Shopee SKU column found: '{sku_col}'")
                        # Show sample SKU values
                        unique_skus = df_filtered[sku_col].dropna().unique()[:5]
                        st.sidebar.write(f"Sample SKUs: {list(unique_skus)}")
                    else:
                        st.sidebar.error(f"❌ Shopee: Kolom 'Nomor Referensi SKU' tidak ditemukan!")
                        st.sidebar.write(f"Available columns: {df_filtered.columns.tolist()}")
                        
            else:
                # === TOKOPEDIA: LOGIC TETAP SAMA SEPERTI SEBELUMNYA ===
                # Priority 1: 'seller sku' atau 'nomor sku'
                sku_col = next((c for c in df_filtered.columns if any(x in c for x in ['seller sku', 'nomor sku'])), None)
                
                # Priority 2: 'sku' (fallback)
                if not sku_col:
                    sku_col = next((c for c in df_filtered.columns if 'sku' in c), 'SKU')
                
                if DEBUG_MODE and sku_col:
                    st.sidebar.success(f"✅ Tokopedia SKU column found: '{sku_col}'")
            
            # Find quantity and order columns (SAMA UNTUK SEMUA MARKETPLACE)
            qty_col = next((c for c in df_filtered.columns if any(x in c for x in ['jumlah', 'quantity'])), None)
            order_col = next((c for c in df_filtered.columns if any(x in c for x in ['order', 'pesanan', 'invoice'])), None)
            
            if DEBUG_MODE:
                st.sidebar.write(f"Columns identified - SKU: {sku_col}, Qty: {qty_col}, Order: {order_col}")
            
            if not all([sku_col, qty_col, order_col]):
                st.sidebar.warning(f"⚠️ {mp_type}: Kolom tidak lengkap. SKU: {sku_col}, Qty: {qty_col}, Order: {order_col}")
                continue
            
            # Process rows
            for idx, row in df_filtered.iterrows():
                raw_sku = str(row[sku_col]) if pd.notna(row[sku_col]) else ''
                sku_clean = clean_sku(raw_sku)
                order_id = str(row[order_col]) if pd.notna(row[order_col]) else ''
                
                try:
                    qty = float(str(row[qty_col]).replace(',', '.')) if pd.notna(row[qty_col]) else 0
                except:
                    qty = 0
                
                if DEBUG_MODE and idx < 3:  # Show first 3 rows
                    st.sidebar.write(f"Row {idx}: SKU='{raw_sku}' -> Clean='{sku_clean}', Qty={qty}")
                
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
        
        # Create results
        df_detail = pd.DataFrame(all_rows)
        df_summary = pd.DataFrame()
        
        if not df_detail.empty:
            df_summary = df_detail.groupby(['Marketplace', 'SKU Component', 'Nama Produk'], as_index=False)['Qty Total'].sum()
            df_summary = df_summary.sort_values('Qty Total', ascending=False)
        
        df_raw_stats = pd.concat(raw_stats_list, ignore_index=True) if raw_stats_list else pd.DataFrame()
        
        return {
            'detail': df_detail,
            'summary': df_summary,
            'raw_stats': df_raw_stats,
            'success': True
        }, None
        
    except Exception as e:
        import traceback
        if DEBUG_MODE:
            st.sidebar.error(f"❌ Processing error: {traceback.format_exc()}")
        return None, str(e)

# --- UI STREAMLIT ---

# Sidebar Section 1: Load Kamus
st.sidebar.header("📁 1. Load Kamus dari Google Sheets")
st.sidebar.markdown("**Required Sheets:**")
st.sidebar.markdown("- Kurir-Shopee")
st.sidebar.markdown("- Bundle Master")
st.sidebar.markdown("- SKU Master")

if st.sidebar.button("🔄 Load Kamus Sekarang", type="primary", key="load_kamus"):
    with st.spinner("Loading kamus..."):
        kamus_data = load_kamus_from_gsheet()
        if kamus_data:
            st.session_state.kamus_data = kamus_data
            st.sidebar.success("✅ Kamus berhasil di-load!")
            st.rerun()

# Show kamus status
if st.session_state.kamus_data:
    st.sidebar.success("✅ Kamus sudah di-load")
else:
    st.sidebar.warning("⚠️ Kamus belum di-load")

st.sidebar.markdown("---")

# Sidebar Section 2: Upload Files
st.sidebar.header("📁 2. Upload Order Files")
st.sidebar.markdown("""
**Format SKU:**
- **Shopee**: **Hanya ambil dari "Nomor Referensi SKU"**
- **Tokopedia**: Tetap seperti sebelumnya
""")

shp_off_f = st.sidebar.file_uploader("Shopee (Official)", type=['xlsx', 'xls', 'csv'], key="shopee_off")
shp_inh_f = st.sidebar.file_uploader("Shopee (INHOUSE)", type=['xlsx', 'xls', 'csv'], key="shopee_in")
tok_f = st.sidebar.file_uploader("Tokopedia", type=['xlsx', 'xls', 'csv'], key="tokopedia")

st.sidebar.markdown("---")

# Sidebar Section 3: Process Button
st.sidebar.header("⚡ 3. Process Data")

if st.sidebar.button("🚀 PROSES DATA", type="primary", use_container_width=True):
    # Reset state
    st.session_state.processed = False
    st.session_state.results = None
    
    # Validation
    if not st.session_state.kamus_data:
        st.error("❌ Silakan load kamus terlebih dahulu!")
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
        st.error("❌ Upload minimal satu file order!")
        st.stop()
    
    # Process data
    with st.spinner("🔄 Memproses data..."):
        try:
            results, error = process_universal_data(files, st.session_state.kamus_data)
            
            if error:
                st.error(f"❌ Error: {error}")
            else:
                st.session_state.results = results
                st.session_state.processed = True
                st.success("✅ Data berhasil diproses!")
                
        except Exception as e:
            st.error(f"❌ System Error: {str(e)}")

# Main Area - Display Results
st.markdown("---")
st.header("📊 Hasil Proses")

if st.session_state.processed and st.session_state.results:
    results = st.session_state.results
    
    if not results['detail'].empty:
        # Create tabs
        tab1, tab2, tab3 = st.tabs([
            f"📋 Order Detail ({len(results['detail'])} rows)",
            f"📦 Picking List-PRINT ({len(results['summary'])} items)",
            f"🔍 Validasi Kurir ({len(results['raw_stats'])} kurir)"
        ])
        
        with tab1:
            st.dataframe(results['detail'], use_container_width=True)
            
            # Summary stats
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Total Orders", len(results['detail']['Order ID'].unique()))
            with col2:
                st.metric("Total SKUs", len(results['detail']['SKU Component'].unique()))
            with col3:
                st.metric("Total Qty", int(results['detail']['Qty Total'].sum()))
            with col4:
                bundle_count = results['detail']['Is Bundle'].value_counts().get('Yes', 0)
                st.metric("Bundle Items", bundle_count)
        
        with tab2:
            if not results['summary'].empty:
                st.dataframe(results['summary'], use_container_width=True)
                
                # Download button for picking list
                csv = results['summary'].to_csv(index=False)
                st.download_button(
                    label="📥 Download Picking List (CSV)",
                    data=csv,
                    file_name=f"picking_list_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv"
                )
        
        with tab3:
            if not results['raw_stats'].empty:
                # Apply coloring
                def color_status(val):
                    if '✅' in str(val):
                        return 'background-color: #d4edda; color: #155724'
                    elif '⚠️' in str(val):
                        return 'background-color: #fff3cd; color: #856404'
                    elif '❌' in str(val):
                        return 'background-color: #f8d7da; color: #721c24'
                    return ''
                
                styled_df = results['raw_stats'].style.applymap(color_status, subset=['Status Sistem'])
                st.dataframe(styled_df, use_container_width=True)
        
        # Excel Download
        st.markdown("---")
        st.subheader("📥 Download Full Report")
        
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
            results['detail'].to_excel(writer, sheet_name='Order Detail', index=False)
            if not results['summary'].empty:
                results['summary'].to_excel(writer, sheet_name='Picking List-PRINT', index=False)
            if not results['raw_stats'].empty:
                results['raw_stats'].to_excel(writer, sheet_name='Validasi Kurir', index=False)
            
            # Auto-adjust column widths
            for sheet in writer.sheets.values():
                sheet.set_column('A:G', 20)
        
        st.download_button(
            label="📥 Download Excel Report",
            data=buf.getvalue(),
            file_name=f"order_report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True
        )
        
    else:
        st.warning("⚠️ Tidak ada data yang memenuhi kriteria filter.")
        st.info("""
        **Kemungkinan penyebab:**
        1. Status order bukan 'Perlu Dikirim'
        2. Untuk Shopee: Kurir tidak termasuk dalam daftar instant
        3. Untuk Shopee: Resi sudah terisi
        4. Kolom 'Nomor Referensi SKU' tidak ditemukan di file Shopee
        5. File mungkin kosong atau format salah
        
        **Aktifkan Debug Mode di sidebar untuk info detail!**
        """)

elif 'results' in st.session_state and st.session_state.results is None:
    st.info("ℹ️ Upload file dan klik 'PROSES DATA' untuk memulai.")

st.sidebar.markdown("---")
st.sidebar.caption(f"v4.2 • Shopee: SKU Logic Fixed • Tokped: Unchanged • {datetime.now().strftime('%d/%m/%Y %H:%M')}")

# Info panel
with st.sidebar.expander("ℹ️ Panduan SKU"):
    st.markdown("""
    **Aturan Pengambilan SKU:**
    
    **SHOPEE (Official & INHOUSE):**
    - ✅ **Hanya ambil dari:** "Nomor Referensi SKU"
    - ❌ **Tidak ambil dari:** "Nama Produk", "Varian", "Seller SKU", dll
    
    **TOKOPEDIA:**
    - ✅ **Tetap logic sebelumnya:**
      1. Cari "seller sku" atau "nomor sku" dulu
      2. Jika tidak ada, ambil dari kolom "sku"
    
    **Pastikan file Shopee memiliki kolom 'Nomor Referensi SKU'!**
    """)

# Clear cache button (for debugging)
if DEBUG_MODE and st.sidebar.button("🧹 Clear Cache"):
    st.cache_data.clear()
    st.session_state.clear()
    st.rerun()
