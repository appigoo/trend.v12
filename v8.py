import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time
from datetime import datetime

# --- 頁面配置 ---
st.set_page_config(page_title="Pro-Trade Lite", layout="wide")

# --- 核心運算函數 (純 Pandas 實現) ---
def fetch_data(ticker, interval):
    try:
        # 抓取 5 天數據以確保 RSI 初始計算穩定
        data = yf.download(ticker, period="5d", interval=interval, progress=False)
        if data.empty: return None
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        return data
    except:
        return None

def calculate_rsi_pure(series, period=14):
    """純 Pandas 實現的 RSI (Wilder's Smoothing)"""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    # 避免除以零
    rs = gain / loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))

def get_vix_status():
    vix = fetch_data("^VIX", "2m")
    if vix is None or len(vix) < 2: return 20.0, 0.0
    curr_v = float(vix['Close'].iloc[-1])
    v_chg = curr_v - float(vix['Close'].iloc[-2])
    return curr_v, v_chg

def analyze_stock(df, ema_f_p, ema_s_p):
    if df is None or len(df) < 30: return None, None
    
    df = df.copy()
    # 1. 指標計算
    df['EMA_F'] = df['Close'].ewm(span=ema_f_p, adjust=False).mean()
    df['EMA_S'] = df['Close'].ewm(span=ema_s_p, adjust=False).mean()
    df['RSI'] = calculate_rsi_pure(df['Close'])
    df['Vol_MA'] = df['Volume'].rolling(window=10).mean()
    
    # 2. 支撐壓力 (Pivot Point)
    last_row = df.iloc[-1]
    h, l, c = float(last_row['High']), float(last_row['Low']), float(last_row['Close'])
    pivot = (h + l + c) / 3
    res, sup = (2 * pivot) - l, (2 * pivot) - h

    # 3. 趨勢與量能
    vol_ratio = float(last_row['Volume'] / last_row['Vol_MA']) if last_row['Vol_MA'] != 0 else 1.0
    trend = " Bullish" if last_row['EMA_F'] > last_row['EMA_S'] else " Bearish"
    
    # 4. 交叉訊號
    prev_row = df.iloc[-2]
    msg, level = "監控中", "success"
    if prev_row['EMA_F'] <= prev_row['EMA_S'] and last_row['EMA_F'] > last_row['EMA_S']:
        msg, level = "↗️ 黃金交叉", "error" 
    elif prev_row['EMA_F'] >= prev_row['EMA_S'] and last_row['EMA_F'] < last_row['EMA_S']:
        msg, level = "↘️ 死亡交叉", "error"
    elif c >= res * 0.998:
        msg, level = "🧱 接近壓力", "warning"

    info = {
        "price": c,
        "chg_pct": ((c - df['Open'].iloc[-1]) / df['Open'].iloc[-1]) * 100,
        "rsi": float(last_row['RSI']),
        "vol_ratio": vol_ratio,
        "trend": trend,
        "res": res, "sup": sup,
        "msg": msg, "level": level
    }
    return df, info

# --- UI 介面 ---
st.sidebar.header("⚙️ 參數設定")
input_symbols = st.sidebar.text_input("監控代碼", "AAPL, NVDA, 2330.TW")
symbols = [s.strip().upper() for s in input_symbols.split(",")]
interval = st.sidebar.selectbox("週期", ("1m", "2m", "5m", "15m"))
ema_f = st.sidebar.slider("快速 EMA", 5, 20, 9)
ema_s = st.sidebar.slider("慢速 EMA", 21, 60, 21)

placeholder = st.empty()

while True:
    with placeholder.container():
        v_val, v_chg = get_vix_status()
        st.write(f"⏱️ **最後更新:** {datetime.now().strftime('%H:%M:%S')} | **VIX:** {v_val:.2f} ({v_chg:+.2f})")
        
        # 顯示警報
        st.subheader("🔔 實時警報摘要")
        cols = st.columns(len(symbols))
        stock_cache = {}

        for i, sym in enumerate(symbols):
            data = fetch_data(sym, interval)
            df, info = analyze_stock(data, ema_f, ema_s)
            stock_cache[sym] = (df, info)
            with cols[i]:
                if info:
                    if info['level'] == "error": st.error(f"**{sym}**\n\n{info['msg']}")
                    elif info['level'] == "warning": st.warning(f"**{sym}**\n\n{info['msg']}")
                    else: st.success(f"**{sym}**\n\n穩定")
                else: st.write(f"❌ {sym} 載入中")

        st.divider()

        # 顯示圖表 (30根K線)
        for sym in symbols:
            df, info = stock_cache[sym]
            if df is not None:
                # 只取最後 30 根畫圖
                df_plot = df.tail(30)
                with st.expander(f"🔍 {sym} 詳情 (Price: {info['price']:.2f})", expanded=True):
                    c1, c2 = st.columns([1, 4])
                    with c1:
                        st.metric("漲跌幅", f"{info['chg_pct']:.2f}%")
                        st.write(f"RSI: {info['rsi']:.1f}")
                        st.write(f"量能: x{info['vol_ratio']:.1f}")
                    with c2:
                        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.05)
                        # K線
                        fig.add_trace(go.Candlestick(x=df_plot.index, open=df_plot['Open'], high=df_plot['High'], low=df_plot['Low'], close=df_plot['Close']), row=1, col=1)
                        # EMA
                        fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['EMA_F'], line=dict(color='orange', width=1)), row=1, col=1)
                        fig.add_trace(go.Scatter(x=df_plot.index, y=df_plot['EMA_S'], line=dict(color='cyan', width=1)), row=1, col=1)
                        # 成交量
                        v_colors = ['red' if df_plot['Close'].iloc[j] < df_plot['Open'].iloc[j] else 'green' for j in range(len(df_plot))]
                        fig.add_trace(go.Bar(x=df_plot.index, y=df_plot['Volume'], marker_color=v_colors), row=2, col=1)
                        
                        fig.update_layout(height=350, margin=dict(t=0,b=0,l=0,r=0), xaxis_rangeslider_visible=False, showlegend=False)
                        st.plotly_chart(fig, use_container_width=True)

    time.sleep(60)
    st.rerun()
