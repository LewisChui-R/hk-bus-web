import os
import sqlite3
import json
import pandas as pd
import streamlit as st
import geopy.distance

# ==========================================
# 網頁初始化配置
# ==========================================
st.set_page_config(page_title="香港巴士數據引擎", layout="wide")
st.title("🚌 香港巴士數據引擎中心")

DB_FILE = "bus_data_engine.db"

# 💡 基準點：固定設為竹園邨總站，背景地理空間計算基準
BASE_COORDS = (22.345415, 114.192640)

# ==========================================
# 1. 數據攝取與 SQL 儲存模組 (單一 GeoJSON 驅動)
# ==========================================
def init_sqlite_database():
    """直接解析單個 GeoJSON 檔案 (Bus_data.json.json)，生成純繁體 SQL 倉庫"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 絕對路徑定位：直接從 programing 根目錄出發尋找 data 資料夾
    project_root = os.path.dirname(current_dir) # 取得 python 的上一層 (programing)
    geojson_path = os.path.join(project_root, "data", "Bus_data.json.json")
    db_path = os.path.join(current_dir, DB_FILE)

    # 強制刪除舊的快取資料庫檔案，避免抓到舊的空表格
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except:
            pass

    st.info("🔄 正在讀取單一 GeoJSON 核心檔案並建構 SQL 數據倉庫...")
    
    try:
        # 使用 'utf-8-sig' 編碼，徹底跳過 Unexpected UTF-8 BOM 報錯字元
        with open(geojson_path, 'r', encoding='utf-8-sig') as f:
            geojson_data = json.load(f)
        
        # 🔄 打平 GeoJSON 結構：將 properties 與 geometry.coordinates 解構至同一層
        flattened_features = []
        for feature in geojson_data.get("features", []):
            props = feature.get("properties", {}).copy()
            coords = feature.get("geometry", {}).get("coordinates", [None, None])
            
            # 🛠️ 修正點：利用 [0] 和 [1] 正確拆解經緯度清單元素，防止 float() 報錯
            if isinstance(coords, list) and len(coords) >= 2:
                props["long"] = float(coords[0]) if coords[0] is not None else None
                props["lat"] = float(coords[1]) if coords[1] is not None else None
            else:
                props["long"] = None
                props["lat"] = None
                
            flattened_features.append(props)
            
        final_df = pd.DataFrame(flattened_features)
        
        if final_df.empty:
            st.error("❌ 讀取到的 JSON 數據為空，請確認 features 內是否有資料！")
            st.stop()
            
        # 🛠️ 模糊匹配路線名稱欄位
        route_col = 'routeNameC'
        if route_col not in final_df.columns:
            potential_cols = [c for c in final_df.columns if 'route' in c.lower() and not any(k in c.lower() for k in ['type', 'seq', 'id', 'mode'])]
            if potential_cols:
                route_col = potential_cols[0]
                st.warning(f"⚠️ 找不到 'routeNameC'，系統已自動綁定最接近的欄位：'{route_col}' 作為路線名稱。")
            else:
                st.error(f"❌ 在您的 JSON properties 中完全找不到任何與 'route' 相關的欄位！目前現有的欄位有：{list(final_df.columns)}")
                st.stop()
        
        # 🛠️ 模糊匹配站點名稱欄位
        stop_name_col = 'stopNameC' if 'stopNameC' in final_df.columns else ([c for c in final_df.columns if 'stopname' in c.lower() or 'name' in c.lower()] + ['stopNameC'])[0]

        # ⚙️ 欄位標準化重命名，完美對接您原有的前端邏輯
        final_df = final_df.rename(columns={
            route_col: 'route',
            stop_name_col: 'name_tc',
            'locStartNameC': 'orig_tc',
            'locEndNameC': 'dest_tc',
            'stopSeq': 'seq',
            'stopId': 'stop'
        })
        
        # 強制將路線名稱 (route) 全部轉為去空格字串
        final_df['route'] = final_df['route'].astype(str).str.strip()
            
        # ⚙️ 補齊或映射可能缺失的前端核心控制欄位 (如方向、班次類型)
        if 'bound' not in final_df.columns:
            final_df['bound'] = final_df['serviceMode'].map(lambda x: 'O' if x == 'R' else x).fillna('O') if 'serviceMode' in final_df.columns else 'O'
        if 'service_type' not in final_df.columns:
            final_df['service_type'] = final_df['routeType'].fillna(1).astype(str) if 'routeType' in final_df.columns else '1'

        # ⚙️ 數據類型校正與排序
        final_df['seq'] = pd.to_numeric(final_df['seq'], errors='coerce').fillna(1)
        final_df = final_df.sort_values(by=['route', 'bound', 'service_type', 'seq']).reset_index(drop=True)
        
        # 🧼 徹底剔除所有結尾為 S (簡體) 和 E (英文) 的欄位
        drop_cols = [c for c in final_df.columns if c.endswith('S') or c.endswith('E') or c.endswith('_en') or c.endswith('_sc')]
        final_df = final_df.drop(columns=drop_cols, errors='ignore')
        
        # 徹底移除所有帶有網頁超連結的相關欄位
        link_cols = [c for c in final_df.columns if 'hyperlink' in c.lower() or c == 'hyperlinkC']
        final_df = final_df.drop(columns=link_cols, errors='ignore')
        
        # 確保寫入 SQLite 前，沒有任何殘留的物件或陣列欄位
        for col in final_df.columns:
            if final_df[col].apply(lambda x: isinstance(x, (list, dict))).any():
                final_df[col] = final_df[col].astype(str)
        
        # 寫入 SQLite 數據庫
        conn = sqlite3.connect(db_path)
        final_df.to_sql("integrated_bus_data", conn, if_exists="replace", index=False)
        conn.close()
        st.success(f"💾 數據底層刷新完畢！已成功建立含 {len(final_df)} 筆紀錄的數據源。")
        
    except FileNotFoundError as e:
        st.error(f"❌ 找不到核心 GeoJSON 檔案，請確認路徑：{geojson_path}")
        st.stop()
    except Exception as e:
        import traceback
        st.error(f"❌ 初始化資料庫時發生未預期錯誤：{str(e)}")
        st.code(traceback.format_exc())
        st.stop()

# 執行初始化
init_sqlite_database()

# ==========================================
# 2. 數據查詢模組 (SQLite3 + Pandas)
# ==========================================
def query_all_data():
    """直接從 SQLite 中撈出數據"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(current_dir, DB_FILE)
    
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT * FROM integrated_bus_data", conn)
    if 'route' in df.columns:
        df['route'] = df['route'].astype(str).str.strip()
    conn.close()
    return df

df_bus = query_all_data()

# ==========================================
# 3. 前端互動介面模組 (Streamlit)
# ==========================================
tab1, tab2 = st.tabs(["🔍 路線站點查詢", "📊 數據倉庫總覽"])

# --- 路線連動顯示分頁 ---
with tab1:
    st.subheader("巴士路線查詢引擎")
    
    if not df_bus.empty and 'route' in df_bus.columns:
        available_routes = sorted(df_bus['route'].unique(), key=lambda x: (len(str(x)), str(x)))
    else:
        available_routes = []
        
    if available_routes:
        selected_route = st.selectbox("請選擇或輸入巴士路線名稱：", available_routes)
        
        df_filtered_route = df_bus[df_bus['route'] == selected_route]
        
        available_bounds = df_filtered_route['bound'].unique()
        bound_labels = {"O": "去程 (Outbound)", "I": "回程 (Inbound)"}
        selected_bound = st.radio("請選擇行車方向：", available_bounds, format_func=lambda x: bound_labels.get(x, x))
        
        df_result = df_filtered_route[df_filtered_route['bound'] == selected_bound].sort_values('seq').copy()
        
        if not df_result.empty:
            orig_station = df_result['orig_tc'].iloc[0] if 'orig_tc' in df_result.columns and len(df_result) > 0 else ""
            dest_station = df_result['dest_tc'].iloc[0] if 'dest_tc' in df_result.columns and len(df_result) > 0 else ""
            st.markdown(f"### 🗺️ 路線總覽：{orig_station} ➔ {dest_station}")
            st.caption("💡 距離欄位是以「竹園邨總站」為基準點進行背景地理空間計算後的結果。")
            
            # 在背景使用經緯度計算精確距離
            def calc_distance_background(row):
                try:
                    stop_coords = (float(row['lat']), float(row['long']))
                    return f"{geopy.distance.geodesic(BASE_COORDS, stop_coords).meters:.1f} 米"
                except:
                    return "未知"
            
            with st.spinner("正在即時計算該路線各站點距離..."):
                df_result['距離'] = df_result.apply(calc_distance_background, axis=1)
            
            # 📌 定義基礎展示欄位
            show_cols = ['seq', 'name_tc', '距離', 'stop']
            
            # 📌 自動抽取新檔案帶來的精美商務欄位
            custom_cols = [c for c in df_result.columns if c not in show_cols + ['bound', 'service_type', 'orig_tc', 'dest_tc', 'lat', 'long', 'route', 'serviceMode', 'routeType']]
            all_show_cols = show_cols + custom_cols
            
            st.dataframe(df_result[all_show_cols].rename(columns={'seq': '站序', 'name_tc': '站點名稱'}), use_container_width=True)
        else:
            st.warning("無對應路線資料。")
    else:
        st.error("⚠️ 資料庫內沒有撈到任何路線名稱，請確認您的 JSON 資料結構。")

with tab2:
    st.subheader("全面串聯數據庫資料庫（純繁體中文）")
    st.write(f"目前數據庫內共有 **{len(df_bus)}** 筆獨立紀錄。")
    
    df_warehouse_view = df_bus.drop(columns=['lat', 'long'], errors='ignore')
    st.dataframe(df_warehouse_view, use_container_width=True)
