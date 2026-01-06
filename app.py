import streamlit as st
import pandas as pd
import numpy as np
import io
import time
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# --- CONFIG ---
st.set_page_config(page_title="Universal Order Processor", layout="wide")
st.title("🛒 Universal Marketplace Order Processor")
st.markdown("""
**Logic Applied:**
1. **Shopee**: Status='Perlu Dikirim' | Resi=Blank | Managed='No' | Kurir=Instant(Kamus).
2. **Tokopedia**: Status='Perlu Dikirim'.
3. **SKU Logic**: Prefix **FG-** & **CS-** dipertahankan, sisanya ambil suffix.
""")

# --- DEBUG MODE ---
st.sidebar.header("🔧 Debug Mode")
DEBUG_MODE = st.sidebar.checkbox("Tampilkan info detil", value=False)

# --- FUNGSI CLEANING SKU (UPDATED) ---
def clean_sku(sku):
    """
    Logic:
    1. Jika awalan 'FG-' atau 'CS-', biarkan apa adanya (hanya trim spasi).
    2. Jika tidak, ambil bagian kanan setelah hyphen (-).
    """
    if pd.isna(sku): return ""
    sku = str(sku).strip()
    # Hapus karakter aneh (non-printable)
    sku = ''.join(char for char in sku if ord(char) >= 32)
    
    sku_upper = sku.upper()
    
    # KECUALIAN: FG- dan CS- jangan dipotong
    if sku_upper.startswith('FG-') or sku_upper.startswith('CS-'):
        return sku
        
    # Logic Default: Ambil kanan
    if '-' in sku:
        return sku.split('-', 1)[-1].strip()
        
    return sku

# --- FUNGSI DETEKSI ENCODING SEDERHANA ---
def detect_simple_encoding(file_obj):
    """Deteksi encoding file secara sederhana"""
    file_obj.seek(0)
    sample = file_obj.read(10000)
    file_obj.seek(0)
    
    # Cek BOM untuk UTF-8
    if sample.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'
    
    # Coba decode dengan UTF-8 dulu
    try:
        sample.decode('utf-8')
        return 'utf-8'
    except:
        pass
    
    # Default ke latin-1 (selalu berhasil)
    return 'latin-1'

# --- FUNGSI SMART LOADER ---
def load_data_smart(file_obj):
    """
    Mencoba membaca file dengan prioritas Excel -> CSV.
    """
    df = None
    filename = file_obj.name.lower()
    file_display_name = file_obj.name
    
    if DEBUG_MODE:
        st.sidebar.subheader(f"📂 Processing: {file_display_name}")
    
    try:
        # A. COBA BACA EXCEL
        if filename.endswith('.xlsx') or filename.endswith('.xls'):
            try:
                df = pd.read_excel(file_obj, dtype=str, header=None, engine='openpyxl')
                if DEBUG_MODE:
                    st.sidebar.success(f"✓ Excel loaded: {df.shape[0]} rows, {df.shape[1]} cols")
            except Exception as e:
                if DEBUG_MODE:
                    st.sidebar.warning(f"Excel failed: {str(e)[:100]}")
                df = None

        # B. COBA BACA CSV
        if df is None or df.shape[1] <= 1:
            file_obj.seek(0)
            
            # Encoding yang akan dicoba (prioritas untuk Indonesia)
            encodings_to_try = ['utf-8-sig', 'utf-8', 'latin-1', 'cp1252', 'iso-8859-1']
            separators = [',', ';', '\t', '|']
            
            for enc in encodings_to_try:
                if df is not None and df.shape[1] > 1:
                    break
                    
                for sep in separators:
                    try:
                        file_obj.seek(0)
                        temp_df = pd.read_csv(
                            file_obj, 
                            sep=sep, 
                            dtype=str, 
                            header=None, 
                            encoding=enc,
                            on_bad_lines='skip',
                            quotechar='"',
                            skipinitialspace=True
                        )
                        
                        if temp_df.shape[1] > 1:
                            df = temp_df
                            if DEBUG_MODE:
                                st.sidebar.success(f"✓ CSV loaded: encoding={enc}, separator='{sep}'")
                                st.sidebar.text(f"Shape: {df.shape}")
                            break
                    except:
                        continue
                
                if df is not None and df.shape[1] > 1:
                    break

    except Exception as e:
        return None, f"Gagal membaca file: {str(e)[:200]}"

    if df is None or df.empty:
        return None, "File kosong atau format tidak dikenali."

    if DEBUG_MODE:
        st.sidebar.text(f"Raw data shape: {df.shape}")

    # 2. CARI BARIS HEADER SEBENARNYA
    header_idx = -1
    keywords = [
        'order status', 'status pesanan',
        'seller sku', 'nomor sku',
        'order id', 'no. pesanan',
        'quantity', 'jumlah',
        'sku id', 'product name'
    ]
    
    # Tampilkan beberapa baris pertama untuk debug
    if DEBUG_MODE and df.shape[0] > 0:
        st.sidebar.text("First 2 rows preview:")
        for i in range(min(2, df.shape[0])):
            row_preview = " | ".join([str(x)[:20] for x in df.iloc[i].fillna('').tolist()[:3]])
            st.sidebar.text(f"Row {i}: {row_preview}...")
    
    # Scan 30 baris pertama
    max_scan_rows = min(30, df.shape[0])
    for i in range(max_scan_rows):
        row = df.iloc[i]
        row_str = " ".join([str(val).lower().strip() if pd.notna(val) else '' for val in row.values])
        
        match_count = sum(1 for kw in keywords if kw in row_str)
        
        if match_count >= 2:  # Minimal 2 keyword match
            header_idx = i
            if DEBUG_MODE:
                st.sidebar.success(f"✅ Header ditemukan di baris {i+1}")
            break
    
    if header_idx == -1:
        if DEBUG_MODE:
            st.sidebar.warning("⚠️ Header tidak terdeteksi, menggunakan baris 0")
        header_idx = 0

    # 3. SET HEADER & BERSIHKAN
    try:
        df_final = df.iloc[header_idx:].copy()
        df_final.columns = df_final.iloc[0]
        df_final = df_final.iloc[1:].reset_index(drop=True)
        
        # Bersihkan nama kolom
        df_final.columns = [
            str(col).replace('\n', ' ').replace('\r', ' ').strip() 
            if pd.notna(col) else f"Unnamed_{i}" 
            for i, col in enumerate(df_final.columns)
        ]
        
        # Hapus baris kosong
        df_final = df_final.dropna(how='all').reset_index(drop=True)
        
        if DEBUG_MODE:
            st.sidebar.success(f"✅ Final shape: {df_final.shape}")
            st.sidebar.text(f"Columns ({len(df_final.columns)}): {list(df_final.columns)}")
        
        return df_final, None
        
    except Exception as e:
        error_msg = f"Error saat set header: {str(e)}"
        if DEBUG_MODE:
            st.sidebar.error(error_msg)
        return None, error_msg

# ==========================================
# MAIN PROCESSOR
# ==========================================
def process_universal_data(uploaded_files, kamus_data):
    start_time = time.time()
    
    if DEBUG_MODE:
        st.sidebar.subheader("🔧 Processing Kamus")
    
    # 1. LOAD & MAP KAMUS
    try:
        df_kurir = kamus_data['kurir']
        df_bundle = kamus_data['bundle']
        df_sku = kamus_data['sku']

        # A. Mapping Bundle
        bundle_map = {}
        for _, row in df_bundle.iterrows():
            # Cari kolom yang cocok
            col_dict = {str(col).lower(): col for col in df_bundle.columns}
            
            # Cari kolom kit/bundle
            kit_col = None
            for key in ['kit_sku', 'sku bundle', 'bundle', 'parent']:
                if key in col_dict:
                    kit_col = col_dict[key]
                    break
            
            # Cari kolom component
            comp_col = None
            for key in ['component_sku', 'sku component', 'component', 'child']:
                if key in col_dict:
                    comp_col = col_dict[key]
                    break
            
            # Cari kolom quantity
            qty_col = None
            for key in ['component_qty', 'component quantity', 'qty', 'quantity']:
                if key in col_dict:
                    qty_col = col_dict[key]
                    break
            
            if kit_col and comp_col and kit_col in df_bundle.columns and comp_col in df_bundle.columns:
                kit_val = clean_sku(row[kit_col])
                comp_val = clean_sku(row[comp_col])
                
                try:
                    qty_val = float(str(row[qty_col]).replace(',', '.')) if qty_col and pd.notna(row[qty_col]) else 1.0
                except:
                    qty_val = 1.0
                
                if kit_val and comp_val:
                    if kit_val not in bundle_map: 
                        bundle_map[kit_val] = []
                    bundle_map[kit_val].append((comp_val, qty_val))

        if DEBUG_MODE:
            st.sidebar.info(f"Bundle mapping: {len(bundle_map)} entries")

        # B. Mapping SKU Name
        sku_name_map = {}
        if not df_sku.empty and len(df_sku.columns) >= 2:
            # Asumsi kolom 0 atau 1 adalah SKU, kolom terakhir atau ke-2 adalah nama
            for _, row in df_sku.iterrows():
                try:
                    # Coba ambil dari kolom pertama yang tidak kosong
                    code = None
                    name = None
                    
                    for idx, val in enumerate(row):
                        if pd.notna(val) and str(val).strip():
                            if code is None:
                                code = clean_sku(val)
                            elif name is None:
                                name = str(val).strip()
                                break
                    
                    if code and name:
                        sku_name_map[code] = name
                except:
                    continue

        if DEBUG_MODE:
            st.sidebar.info(f"SKU name mapping: {len(sku_name_map)} entries")

        # C. List Kurir Instant Shopee
        instant_list = []
        if not df_kurir.empty:
            # Cari kolom Instant/Same Day
            instant_col = None
            for col in df_kurir.columns:
                if 'instant' in str(col).lower() or 'same' in str(col).lower():
                    instant_col = col
                    break
            
            # Cari kolom nama kurir (biasanya kolom pertama)
            kurir_col = df_kurir.columns[0] if len(df_kurir.columns) > 0 else None
            
            if instant_col and kurir_col:
                instant_list = df_kurir[
                    df_kurir[instant_col].astype(str).str.strip().str.lower().isin(['yes', 'ya', 'true', '1', 'y'])
                ][kurir_col].astype(str).str.strip().tolist()
            
        if DEBUG_MODE:
            st.sidebar.info(f"Instant couriers: {len(instant_list)} entries")
            
    except Exception as e:
        return None, f"Error memproses data Kamus: {e}"

    all_rows = []

    # 2. LOOP SETIAP FILE ORDER
    for mp_type, file_obj in uploaded_files:
        if DEBUG_MODE:
            st.sidebar.subheader(f"📦 Processing {mp_type}")
        
        df_raw, err = load_data_smart(file_obj)
        if err:
            st.error(f"❌ File {mp_type} Gagal: {err}")
            continue
            
        if df_raw.empty:
            st.warning(f"⚠️ File {mp_type} kosong setelah cleaning")
            continue
            
        df_filtered = pd.DataFrame()
        
        # --- LOGIC SHOPEE ---
        if mp_type == 'Shopee':
            df_raw.columns = [str(col).strip().lower() for col in df_raw.columns]
            
            status_c = next((c for c in df_raw.columns if 'status' in c), None)
            managed_c = next((c for c in df_raw.columns if 'dikelola' in c), None)
            resi_c = next((c for c in df_raw.columns if 'resi' in c), None)
            kurir_c = next((c for c in df_raw.columns if any(x in c for x in ['opsi', 'kirim', 'kurir'])), None)
            
            if DEBUG_MODE:
                st.sidebar.text(f"Shopee columns: {list(df_raw.columns)[:10]}")
            
            if not all([status_c, managed_c, resi_c, kurir_c]):
                st.error(f"Shopee: Kolom tidak lengkap")
                continue

            try:
                c1 = df_raw[status_c].astype(str).str.strip() == 'Perlu Dikirim'
                c2 = df_raw[managed_c].astype(str).str.strip().str.lower() == 'no'
                c3 = df_raw[resi_c].fillna('').astype(str).str.strip().isin(['', 'nan', 'none'])
                c4 = df_raw[kurir_c].astype(str).str.strip().isin(instant_list)
                
                df_filtered = df_raw[c1 & c2 & c3 & c4].copy()
                
                if DEBUG_MODE:
                    st.sidebar.text(f"Shopee filter: {len(df_raw)} → {len(df_filtered)} rows")
            except Exception as e:
                st.error(f"Shopee filter error: {e}")
                continue
            
            # Tentukan kolom SKU
            col_sku = next((c for c in df_raw.columns if 'sku' in c), None)
            if not col_sku:
                col_sku = df_raw.columns[0] if len(df_raw.columns) > 0 else 'SKU'
            
            col_qty = next((c for c in df_raw.columns if 'jumlah' in c), None)
            if not col_qty:
                col_qty = df_raw.columns[1] if len(df_raw.columns) > 1 else 'Qty'
            
            col_ord = next((c for c in df_raw.columns if 'pesanan' in c), None)
            if not col_ord:
                col_ord = df_raw.columns[2] if len(df_raw.columns) > 2 else 'OrderID'

        # --- LOGIC TOKOPEDIA ---
        elif mp_type == 'Tokopedia':
            if DEBUG_MODE:
                st.sidebar.text(f"Tokopedia columns: {list(df_raw.columns)}")
            
            # Cari kolom status
            status_col = None
            for col in df_raw.columns:
                col_lower = str(col).lower()
                if 'status' in col_lower:
                    status_col = col
                    break
            
            if not status_col:
                st.error(f"Tokopedia: Kolom Status tidak ditemukan")
                st.error(f"Kolom yang ada: {list(df_raw.columns)}")
                continue
            
            if DEBUG_MODE:
                st.sidebar.success(f"Status column: {status_col}")
                unique_vals = df_raw[status_col].astype(str).str.strip().unique()[:5]
                st.sidebar.text(f"Sample status: {unique_vals}")
            
            # Filter status
            df_filtered = df_raw[
                df_raw[status_col].astype(str).str.strip().str.lower() == 'perlu dikirim'
            ].copy()
            
            if DEBUG_MODE:
                st.sidebar.text(f"Tokopedia filter: {len(df_raw)} → {len(df_filtered)} rows")
            
            # Tentukan kolom lainnya
            col_sku = None
            for col in df_raw.columns:
                col_lower = str(col).lower()
                if 'seller' in col_lower and 'sku' in col_lower:
                    col_sku = col
                    break
            if not col_sku:
                col_sku = next((c for c in df_raw.columns if 'sku' in str(c).lower()), 'Seller SKU')
            
            col_qty = next((c for c in df_raw.columns if any(x in str(c).lower() for x in ['quantity', 'jumlah'])), 'Quantity')
            col_ord = next((c for c in df_raw.columns if any(x in str(c).lower() for x in ['order', 'invoice', 'pesanan'])), 'Order ID')
            
            if DEBUG_MODE:
                st.sidebar.info(f"Mapping: SKU={col_sku}, Qty={col_qty}, Order={col_ord}")

        # 3. PROSES DATA FILTERED
        if df_filtered.empty:
            if DEBUG_MODE:
                st.sidebar.warning(f"⚠️ {mp_type}: No data after filtering")
            continue
        
        rows_processed = 0
        for idx, row in df_filtered.iterrows():
            # Ambil SKU
            raw_sku = ''
            if col_sku in row and pd.notna(row[col_sku]):
                raw_sku = str(row[col_sku])
            
            sku_clean = clean_sku(raw_sku)
            
            # Ambil Qty
            try:
                if col_qty in row and pd.notna(row[col_qty]):
                    q_val = str(row[col_qty]).replace(',', '.')
                    qty_order = float(q_val)
                else:
                    qty_order = 0
            except:
                qty_order = 0
            
            # Ambil Order ID
            order_id = ''
            if col_ord in row and pd.notna(row[col_ord]):
                order_id = str(row[col_ord])
            
            # Skip jika SKU kosong
            if not sku_clean:
                continue
            
            # Bundle logic
            if sku_clean in bundle_map:
                for comp_sku, comp_qty_unit in bundle_map[sku_clean]:
                    if comp_sku:
                        all_rows.append({
                            'Marketplace': mp_type,
                            'Order ID': order_id,
                            'SKU Original': raw_sku,
                            'Is Bundle?': 'Yes',
                            'SKU Component': comp_sku,
                            'Nama Produk': sku_name_map.get(comp_sku, comp_sku),
                            'Qty Total': qty_order * comp_qty_unit
                        })
                        rows_processed += 1
            else:
                all_rows.append({
                    'Marketplace': mp_type,
                    'Order ID': order_id,
                    'SKU Original': raw_sku,
                    'Is Bundle?': 'No',
                    'SKU Component': sku_clean,
                    'Nama Produk': sku_name_map.get(sku_clean, sku_clean),
                    'Qty Total': qty_order
                })
                rows_processed += 1
        
        if DEBUG_MODE:
            st.sidebar.success(f"✅ {mp_type}: {rows_processed} rows processed")

    if not all_rows:
        return None, "Data terbaca tapi 0 lolos filter. Cek Status/Kurir/Resi di file order."

    # 4. FINAL AGGREGATION
    try:
        df_detail = pd.DataFrame(all_rows)
        
        # Ensure all columns exist
        required_cols = ['Marketplace', 'Order ID', 'SKU Original', 'Is Bundle?', 'SKU Component', 'Nama Produk', 'Qty Total']
        for col in required_cols:
            if col not in df_detail.columns:
                df_detail[col] = ''
        
        # Reorder columns
        existing_cols = [c for c in required_cols if c in df_detail.columns]
        other_cols = [c for c in df_detail.columns if c not in required_cols]
        df_detail = df_detail[existing_cols + other_cols]
        
        # Buat summary
        df_summary = df_detail.groupby(['Marketplace', 'SKU Component', 'Nama Produk'], as_index=False).agg({
            'Qty Total': 'sum'
        }).sort_values('Qty Total', ascending=False)
        
        if DEBUG_MODE:
            st.sidebar.success(f"✅ Final: {len(df_detail)} detail rows, {len(df_summary)} summary rows")
        
        return {'detail': df_detail, 'summary': df_summary}, None
        
    except Exception as e:
        return None, f"Error saat aggregasi final: {e}"

# --- SIMPLE TEST FUNCTION ---
def test_tokopedia_file(file_obj):
    """Test sederhana untuk file Tokopedia"""
    if not file_obj:
        return
    
    with st.expander("🧪 Test Results", expanded=True):
        df_test, err = load_data_smart(file_obj)
        if err:
            st.error(f"Error: {err}")
            return
        
        st.success(f"File terbaca! Shape: {df_test.shape}")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.write("**Columns:**")
            st.write(list(df_test.columns))
            
            # Cari kolom status
            status_cols = [c for c in df_test.columns if 'status' in str(c).lower()]
            st.write(f"**Status columns:** {status_cols}")
            
            if status_cols:
                for col in status_cols[:2]:
                    perlu_count = len(df_test[df_test[col].astype(str).str.strip().str.lower() == 'perlu dikirim'])
                    st.write(f"'{col}': 'Perlu dikirim' = {perlu_count} rows")
        
        with col2:
            st.write("**First 3 rows:**")
            st.dataframe(df_test.head(3))

# --- UI STREAMLIT ---
st.sidebar.header("📁 1. Upload Kamus (Wajib)")
kamus_f = st.sidebar.file_uploader("Kamus Dashboard.xlsx", type=['xlsx'], key="kamus")

st.sidebar.header("📁 2. Upload Order")
shp_f = st.sidebar.file_uploader("Order Shopee", type=['xlsx', 'csv', 'xls'], key="shopee")
tok_f = st.sidebar.file_uploader("Order Tokopedia", type=['xlsx', 'csv', 'xls'], key="tokopedia")

# Test button
if DEBUG_MODE and tok_f:
    if st.sidebar.button("🧪 Test Tokopedia File"):
        test_tokopedia_file(tok_f)

# Reset state jika file dihapus
if not shp_f and not tok_f:
    if 'result' in st.session_state:
        del st.session_state['result']

# Main process button
if st.sidebar.button("🚀 PROSES DATA", type="primary"):
    if not kamus_f:
        st.error("❌ Upload Kamus dulu!")
    elif not shp_f and not tok_f:
        st.error("❌ Upload minimal satu file order!")
    else:
        with st.spinner("Processing..."):
            try:
                # Load Kamus
                k_excel = pd.ExcelFile(kamus_f, engine='openpyxl')
                sheet_names = k_excel.sheet_names
                
                if DEBUG_MODE:
                    st.sidebar.info(f"Kamus sheets: {sheet_names}")
                
                k_data = {}
                # Load dengan nama sheet yang fleksibel
                for name in ['Kurir-Shopee', 'Kurir', 'Courier']:
                    if any(name.lower() in s.lower() for s in sheet_names):
                        matching = [s for s in sheet_names if name.lower() in s.lower()]
                        k_data['kurir'] = pd.read_excel(k_excel, sheet_name=matching[0])
                        break
                
                for name in ['Bundle Master', 'Bundle', 'Kit']:
                    if any(name.lower() in s.lower() for s in sheet_names):
                        matching = [s for s in sheet_names if name.lower() in s.lower()]
                        k_data['bundle'] = pd.read_excel(k_excel, sheet_name=matching[0])
                        break
                
                for name in ['SKU Master', 'SKU', 'Product']:
                    if any(name.lower() in s.lower() for s in sheet_names):
                        matching = [s for s in sheet_names if name.lower() in s.lower()]
                        k_data['sku'] = pd.read_excel(k_excel, sheet_name=matching[0])
                        break
                
                # Validasi
                if 'kurir' not in k_data:
                    st.error("Sheet Kurir tidak ditemukan di Kamus!")
                    st.stop()
                if 'bundle' not in k_data:
                    st.error("Sheet Bundle tidak ditemukan di Kamus!")
                    st.stop()
                if 'sku' not in k_data:
                    st.error("Sheet SKU tidak ditemukan di Kamus!")
                    st.stop()
                
                # Prepare files
                files = []
                if shp_f: files.append(('Shopee', shp_f))
                if tok_f: files.append(('Tokopedia', tok_f))
                
                res, err_msg = process_universal_data(files, k_data)
                
                if err_msg:
                    st.warning(f"⚠️ {err_msg}")
                else:
                    total_qty = res['summary']['Qty Total'].sum()
                    st.success(f"✅ Sukses! Total Item: {total_qty}")
                    st.session_state.result = res
                    
            except Exception as e:
                st.error(f"❌ System Error: {str(e)}")

# --- OUTPUT AREA ---
if 'result' in st.session_state:
    res = st.session_state.result
    
    t1, t2 = st.tabs(["📋 Picking List (Detail)", "📦 Stock Check (Summary)"])
    
    with t1: 
        st.dataframe(res['detail'], use_container_width=True, height=400)
        st.write(f"**Total Rows:** {len(res['detail'])}")
    
    with t2: 
        st.dataframe(res['summary'], use_container_width=True, height=400)
        st.write(f"**Total SKU:** {len(res['summary'])}")
        st.write(f"**Total Quantity:** {res['summary']['Qty Total'].sum()}")
    
    # Download Button
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        res['detail'].to_excel(writer, sheet_name='Picking List', index=False)
        res['summary'].to_excel(writer, sheet_name='Stock Check', index=False)
        
        # Auto-adjust column widths
        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            df_to_use = res['detail'] if sheet_name == 'Picking List' else res['summary']
            for i, col in enumerate(df_to_use.columns):
                column_len = max(df_to_use[col].astype(str).str.len().max(), len(str(col)))
                worksheet.set_column(i, i, min(column_len + 2, 50))
    
    st.download_button(
        "📥 Download Excel Final",
        data=buf.getvalue(),
        file_name=f"Picking_List_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary"
    )

# --- FOOTER ---
st.sidebar.markdown("---")
st.sidebar.caption("v2.2 - Simple & Stable")
