
import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
import time
import requests

# --- 1. 頁面配置與 CSS 閃爍動畫 ---
st.set_page_config(page_title="多股實時監控系統", layout="wide")

st.markdown("""
<style>
@keyframes blink {
    0% { border-color: #444; box-shadow: none; }
    50% { border-color: #ff4b4b; box-shadow: 0 0 15px #ff4b4b; } /* 預設閃爍顏色 */
    100% { border-color: #444; box-shadow: none; }
}
.blink-bull {
    border: 3px solid #00ff00 !important;
    animation: blink 1s infinite;
    background-color: rgba(0, 255, 0, 0.05);
}
.blink-bear {
    border: 3px solid #ff4b4b !important;
    animation: blink 1s infinite;
    background-color: rgba(255, 75, 75, 0.05);
}
</style>
""", unsafe_allow_html=True)

# --- 2. Telegram 通知函式 ---
def send_telegram_msg(sym, action, reason, price, p_change, v_ratio):
    try:
        token = st.secrets["TELEGRAM_BOT_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
        message = (
            f"🔔 【{action}預警】: {sym}\n"
            f"現價: {price:.2f} ({p_change:+.2f}%)\n"
            f"量比: {v_ratio:.1f}x\n"
            f"--------------------\n"
            f"📋 判定根據:\n{reason}"
        )
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        params = {"chat_id": chat_id, "text": message}
        requests.get(url, params=params)
    except Exception as e:
        st.error(f"Telegram 發送失敗，請檢查 Secrets 設定: {e}")

# --- 3. 數據獲取與指標計算 ---
def fetch_data(symbol, p, i):
    try:
        df = yf.download(symbol, period=p, interval=i, progress=False)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.loc[:, ~df.columns.duplicated()].copy()
        
        # 指標計算
        close = df['Close'].squeeze()
        df['EMA20'] = close.ewm(span=20, adjust=False).mean()
        df['EMA60'] = close.ewm(span=60, adjust=False).mean()
        df['EMA200'] = close.ewm(span=200, adjust=False).mean()
        df['Vol_Avg'] = df['Volume'].rolling(window=20).mean()
        
        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df['MACD'] = ema12 - ema26
        df['Sig'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['Hist'] = df['MACD'] - df['Sig']
        
        return df
    except:
        return None

# --- 4. 信號判定與理由生成 ---
def get_signal(df, p_limit, v_limit, sym):
    if len(df) < 2: return "⏳ 載入中", "#aaaaaa", "數據不足", False, ""
    
    last = df.iloc[-1]
    prev = df.iloc[-2]
    price = float(last['Close'])
    ema20, ema60, ema200 = float(last['EMA20']), float(last['EMA60']), float(last['EMA200'])
    
    # 趨勢判定
    is_bullish = price > ema200 and ema20 > ema60
    is_bearish = price < ema200 and ema20 < ema60
    
    # 異動計算
    p_change = ((price - float(prev['Close'])) / float(prev['Close'])) * 100
    v_ratio = float(last['Volume']) / float(last['Vol_Avg']) if last['Vol_Avg'] > 0 else 1
    
    trigger_alert = False
    action_type = ""
    reasons = []
    card_style = ""

    # 做多判斷
    if is_bullish and p_change >= p_limit and v_ratio >= v_limit:
        trigger_alert, action_type, card_style = True, "🚀 強勢做多", "blink-bull"
        reasons = [f"✅ 價 > EMA200 ({ema200:.2f})", f"✅ 均線多頭", f"✅ 漲幅 {p_change:.2f}%", f"✅ 放量 {v_ratio:.1f}x"]
    # 做空判斷
    elif is_bearish and p_change <= -p_limit and v_ratio >= v_limit:
        trigger_alert, action_type, card_style = True, "🔻 強勢做空", "blink-bear"
        reasons = [f"❌ 價 < EMA200 ({ema200:.2f})", f"❌ 均線空頭", f"❌ 跌幅 {p_change:.2f}%", f"❌ 放量 {v_ratio:.1f}x"]

    if trigger_alert:
        send_telegram_msg(sym, action_type, "\n".join(reasons), price, p_change, v_ratio)

    # UI 顯示
    status, color = ("🚀 做多", "#00ff00") if is_bullish else ("🔻 做空", "#ff4b4b") if is_bearish else ("⚖️ 觀望", "#aaaaaa")
    if action_type: status = action_type # 若觸發強烈訊號則蓋過狀態

    alert_msgs = []
    if abs(p_change) >= p_limit: alert_msgs.append(f"⚠️ 價異: {p_change:+.2f}%")
    if v_ratio >= v_limit: alert_msgs.append(f"🔥 量爆: {v_ratio:.1f}x")
    
    return status, color, "<br>".join(alert_msgs) if alert_msgs else "正常", card_style

# --- 5. 側邊欄配置 ---
with st.sidebar:
    st.header("⚙️ 參數設定")
    input_symbols = st.text_input("股票代碼 (逗號分隔)", value="TSLA, NIO, TSLL, XPEV, META, GOOGL, AAPL, NVDA, AMZN, MSFT, TSM, BTC-USD").upper()
    symbols = [s.strip() for s in input_symbols.split(",") if s.strip()]
    
    c1, c2 = st.columns(2)
    with c1:
        sel_period = st.selectbox("範圍", ["1d", "5d", "1mo", "1y"], index=1)
    with c2:
        sel_interval = st.selectbox("週期", ["1m", "5m", "15m", "1h", "1d"], index=1)
        
    refresh_rate = st.slider("刷新頻率 (秒)", 60, 600, 300)
    
    st.divider()
    vol_threshold = st.number_input("成交量異常倍數", value=2.0, step=0.5)
    price_threshold = st.number_input("股價單根異動 (%)", value=1.0, step=0.1)

# --- 6. 主介面循環 ---
st.title("📈 智能監控與 Telegram 預警系統")
placeholder = st.empty()

while True:
    all_data = {}
    with placeholder.container():
        st.subheader("🔍 即時警報摘要")
        cols = st.columns(len(symbols)) if symbols else [st.empty()]
        
        for i, sym in enumerate(symbols):
            df = fetch_data(sym, sel_period, sel_interval)
            if df is not None:
                all_data[sym] = df
                status, color, alert_msg, card_style = get_signal(df, price_threshold, vol_threshold, sym)
                
                cols[i].markdown(f"""
                    <div class='{card_style}' style='border:1px solid #444; padding:15px; border-radius:10px; text-align:center;'>
                        <h3 style='margin:0;'>{sym}</h3>
                        <h2 style='color:{color}; margin:10px 0;'>{status}</h2>
                        <p style='font-size:1.3em; margin:0;'><b>{df['Close'].iloc[-1]:.2f}</b></p>
                        <hr style='margin:10px 0; border:0.5px solid #333;'>
                        <p style='font-size:0.9em; color:#ffa500;'>{alert_msg}</p>
                    </div>
                """, unsafe_allow_html=True)

        st.divider()

        if all_data:
            tabs = st.tabs(list(all_data.keys()))
            for i, (sym, df) in enumerate(all_data.items()):
                with tabs[i]:
                    plot_df = df.tail(30).copy()
                    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.7, 0.3])
                    # K線與均線
                    fig.add_trace(go.Candlestick(x=plot_df.index, open=plot_df['Open'], high=plot_df['High'], low=plot_df['Low'], close=plot_df['Close'], name='K線'), row=1, col=1)
                    fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['EMA20'], name='EMA20', line=dict(color='yellow', width=1)), row=1, col=1)
                    fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['EMA200'], name='EMA200', line=dict(color='red', width=1.5)), row=1, col=1)
                    # MACD Hist
                    fig.add_trace(go.Bar(x=plot_df.index, y=plot_df['Hist'], name='MACD Hist', marker_color='orange'), row=2, col=1)
                    
                    fig.update_layout(height=500, template="plotly_dark", xaxis_rangeslider_visible=False, margin=dict(l=10,r=10,t=10,b=10))
                    st.plotly_chart(fig, use_container_width=True, key=f"fig_{sym}")

        st.caption(f"📅 最後更新: {datetime.now().strftime('%H:%M:%S')} | 模式: {sel_interval}")

    time.sleep(refresh_rate)
