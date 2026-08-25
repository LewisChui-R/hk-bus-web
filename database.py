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
    """全自動路徑搜尋，確保在任何雲端或本地環境下都能順利抓到檔案"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(current_dir, DB_FILE)

    # 🛠️ 關鍵修正：目標資料庫檔案已精確標準化為單一副檔名 Bus_data.json
    target_filename = "Bus_data.json"
    geojson_path = None
    
    # 優先搜尋常見的雲端與本地路徑結構
    possible_dirs = [
        current_dir,
        os.path.dirname(current_dir), 
        os.path.join(os.path.dirname(current_dir), "data"),
        os.path.join(current_dir, "data"),
        "/mount/src/hk-bus-web",
        "/mount/src/hk-bus-web/data"
    ]
    
    for d in possible_dirs:
        p = os.path.join(d, target_filename)
        if os.path.exists(p):
            geojson_path = p
            break
            
    # 如果找不到，進行全盤遞迴暴力搜尋保底
    if not geojson_path:
        search_root = os.path.dirname(current_dir) if "hk-bus-web" in os.path.dirname(current_dir) else current_dir
        if not os.path.exists(search_root):
            search_root = "/mount/src"
            
        for root, dirs, files in os.walk(search_root):
            for f in files:
                if f.lower() == target_filename.lower():
                    geojson_path = os.path.join(root, f)
                    break
            if geojson_path:
                break

    # 強制刪除舊的快取資料庫檔案
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except:
            pass

    st.info("🔄 正在讀取單一 GeoJSON 核心檔案並建構 SQL 數據倉庫...")
    
    if not geojson_path or not os.path.exists(geojson_path):
        st.error(f"❌ 系統發動全盤搜尋仍找不到核心 JSON 檔案！目前執行路徑為: {current_dir}。請確認您已將資料檔案上傳至 GitHub。")
        st.stop()
        
    try:
        # 使用 'utf-8-sig' 編碼，徹底跳過 Unexpected UTF-8 BOM 報錯字元
        with open(geojson_path, 'r', encoding='utf-8-sig') as f:
            geojson_data = json.load(f)
        
        # 🔄 打平 GeoJSON 結構
        flattened_features = []
        for feature in geojson_data.get("features", []):
            props = feature.get("properties", {}).copy()
            coords = feature.get("geometry", {}).get("coordinates", [None, None])
            
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
            
        # 模糊匹配路線名稱欄位
        route_col = 'routeNameC'
        if route_col not in final_df.columns:
            potential_cols = [c for c in final_df.columns if 'route' in c.lower() and not any(k in c.lower() for k in ['type', 'seq', 'id', 'mode'])]
            if potential_cols:
                route_col = potential_cols[0]
        
        stop_name_col = 'stopNameC' if 'stopNameC' in final_df.columns else 'stopNameC'

        final_df = final_df.rename(columns={
            route_col: 'route',
            stop_name_col: 'name_tc',
            'locStartNameC': 'orig_tc',
            'locEndNameC': 'dest_tc',
            'stopSeq': 'seq',
            'stopId': 'stop'
        })
        
        final_df['route'] = final_df['route'].astype(str).str.strip()
            
        if 'bound' not in final_df.columns:
            final_df['bound'] = final_df['serviceMode'].map(lambda x: 'O' if x == 'R' else x).fillna('O') if 'serviceMode' in final_df.columns else 'O'
        if 'service_type' not in final_df.columns:
            final_df['service_type'] = final_df['routeType'].fillna(1).astype(str) if 'routeType' in final_df.columns else '1'

        final_df['seq'] = pd.to_numeric(final_df['seq'], errors='coerce').fillna(1)
        final_df = final_df.sort_values(by=['route', 'bound', 'service_type', 'seq']).reset_index(drop=True)
        
        drop_cols = [c for c in final_df.columns if c.endswith('S') or c.endswith('E') or c.endswith('_en') or c.endswith('_sc')]
        final_df = final_df.drop(columns=drop_cols, errors='ignore')
        
        link_cols = [c for c in final_df.columns if 'hyperlink' in c.lower() or c == 'hyperlinkC']
        final_df = final_df.drop(columns=link_cols, errors='ignore')
        
        for col in final_df.columns:
            if final_df[col].apply(lambda x: isinstance(x, (list, dict))).any():
                final_df[col] = final_df[col].astype(str)
        
        conn = sqlite3.connect(db_path)
        final_df.to_sql("integrated_bus_data", conn, if_exists="replace", index=False)
        conn.close()
        st.success(f"💾 數據底層刷新完畢！已成功建立含 {len(final_df)} 筆紀錄的數據源。")
        
    except Exception as e:
        import traceback
        st.error(f"❌ 初始化資料庫時發生未預期錯誤：{str(e)}")
        st.code(traceback.format_exc())
        st.stop()

init_sqlite_database()

# ==========================================
# 2. 數據查詢模組 (SQLite3 + Pandas)
# ==========================================
def query_all_data():
    conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), DB_FILE))
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
            
            def calc_distance_background(row):
                try:
                    stop_coords = (float(row['lat']), float(row['long']))
                    return f"{geopy.distance.geodesic(BASE_COORDS, stop_coords).meters:.1f} 米"
                except:
                    return "未知"
            
            with st.spinner("正在即時計算該路線各站點距離..."):
                df_result['距離'] = df_result.apply(calc_distance_background, axis=1)
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.markdown("**📌 站點明細列表**")
                show_cols = ['seq', 'name_tc', '距離', 'stop']
                custom_cols = [c for c in df_result.columns if c not in show_cols + ['bound', 'service_type', 'orig_tc', 'dest_tc', 'lat', 'long', 'route', 'serviceMode', 'routeType']]
                all_show_cols = show_cols + custom_cols
                st.dataframe(df_result[all_show_cols].rename(columns={'seq': '站序', 'name_tc': '站點名稱'}), use_container_width=True)
            
            with col2:
                st.markdown("**🗺️ 路線地理軌跡分佈**")
                map_df = df_result[['lat', 'long']].dropna()
                map_df['lat'] = pd.to_numeric(map_df['lat'])
                map_df['long'] = pd.to_numeric(map_df['long'])
                st.map(map_df, use_container_width=True)
        else:
            st.warning("無對應路線資料。")
    else:
        st.error("⚠️ 資料庫內沒有撈到任何路線名稱，請確認您的 JSON 資料結構。")

with tab2:
    st.subheader("全面串聯數據庫資料庫（純繁體中文）")
    st.write(f"目前數據庫內共有 **{len(df_bus)}** 筆獨立紀錄。")
    df_warehouse_view = df_bus.drop(columns=['lat', 'long'], errors='ignore')
    st.dataframe(df_warehouse_view, use_container_width=True)
