import os
import sqlite3
import pandas as pd
import streamlit as st
import geopy.distance

st.set_page_config(page_title="香港巴士數據引擎", layout="wide")
st.title("🚌 香港巴士數據引擎中心")

DB_FILE = "bus_data_engine.db"
BASE_COORDS = (22.345415, 114.192640)

# 直接從與 database.py 同級的目錄撈取已經壓縮好的資料庫
current_dir = os.path.dirname(os.path.abspath(__file__))
db_path = os.path.join(current_dir, DB_FILE)

if not os.path.exists(db_path):
    # 備用路徑尋找 (如果檔案在最外層根目錄)
    db_path = os.path.join("/mount/src/hk-bus-web", DB_FILE)

def query_all_data():
    if not os.path.exists(db_path):
        st.error(f"❌ 找不到核心資料庫檔案 bus_data_engine.db，請確認已將其上傳！目前尋找路徑為: {db_path}")
        st.stop()
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT * FROM integrated_bus_data", conn)
    if 'route' in df.columns:
        df['route'] = df['route'].astype(str).str.strip()
    conn.close()
    return df

df_bus = query_all_data()

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
            st.caption("💡 距離欄位是以「竹園邨總站」為基準點進行背景地理空間計算後的結果。")
            
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
        st.error("⚠️ 資料庫內沒有撈到任何路線名稱。")

with tab2:
    st.subheader("全面串聯數據庫資料庫（純繁體中文）")
    st.write(f"目前數據庫內共有 **{len(df_bus)}** 筆獨立紀錄。")
    df_warehouse_view = df_bus.drop(columns=['lat', 'long'], errors='ignore')
    st.dataframe(df_warehouse_view, use_container_width=True)
