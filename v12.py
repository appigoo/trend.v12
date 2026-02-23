import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
import time
import requests
import re

# --- 1. 頁面配置與 CSS ---
st.set_page_config(page_title="多股實時監控系統", layout="wide")

st.markdown("""
<style>
@keyframes blink {
    0% { border-color: #444; box-shadow: none; }
    50% { border-color: #ff4b4b; box-shadow: 0 0 15px #ff4b4b; }
    100% { border-color: #444; box-shadow: none; }
}
.blink-bull { border: 3px solid #00ff00 !important; animation: blink 1s infinite; background-color: rgba(0, 255, 0, 0.05); }
.blink-bear { border: 3px solid #ff4b4b !important; animation: blink 1s infinite; background-color: rgba(255, 75, 75, 0.05); }
</style>
""", unsafe_allow_html=True)

# --- 2. Telegram 通知 ---
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
        requests.get(url, params=params, timeout=5)
    except Exception as e:
        st.error(f"Telegram 發送失敗: {e}")

# --- 3. 數據獲取 ---
def fetch_data(symbol, p, i):
    try:
        df = yf.download(symbol, period=p, interval=i, progress=False)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.loc[:, ~df.columns.duplicated()].copy()
        close = df['Close'].squeeze()
        df['EMA20'] = close.ewm(span=20, adjust=False).mean()
        df['EMA60'] = close.ewm(span=60, adjust=False).mean()
        df['EMA200'] = close.ewm(span=200, adjust=False).mean()
        df['Vol_Avg'] = df['Volume'].rolling(window=20).mean()
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df['MACD'] = ema12 - ema26
        df['Sig'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['Hist'] = df['MACD'] - df['Sig']
        return df
    except: return None

# --- 4. 價格水平預警解析與判定 ---
def check_custom_alerts(sym, price, alert_str):
    # 支援格式: TSLA>420, AAPL<150 (逗號或換行分隔)
    alerts = re.split(r'[,\n]', alert_str)
    for a in alerts:
        a = a.strip().upper()
        if not a: continue
        # 解析 "代碼>價格" 或 "代碼升穿價格" 等邏輯
        match = re.search(rf"{sym}\s*([><]|升穿|跌穿)\s*(\d+\.?\d*)", a)
        if match:
            op = match.group(1)
            target_price = float(match.group(2))
            if (op in ['>', '升穿'] and price >= target_price) or \
               (op in ['<', '跌穿'] and price <= target_price):
                return True, f"🎯 自定義價格預警: {a} (現價:{price:.2f})"
    return False, ""

# --- 5. 綜合信號判定 ---
def get_signal(df, p_limit, v_limit, sym, use_breakout, use_macd_flip, custom_alert_input):
    if len(df) < 10: return "⏳ 載入中", "#aaaaaa", "數據不足", False, ""
    
    last = df.iloc[-1]
    prev = df.iloc[-2]
    price = float(last['Close'])
    p_change = ((price - float(prev['Close'])) / float(prev['Close'])) * 100
    v_ratio = float(last['Volume']) / float(last['Vol_Avg']) if last['Vol_Avg'] > 0 else 1
    
    reasons = []
    trigger_alert = False
    action_type = ""
    card_style = ""

    # [1] 自定義價格水平監控 (優先觸發)
    hit_custom, custom_reason = check_custom_alerts(sym, price, custom_alert_input)
    if hit_custom:
        trigger_alert, action_type, card_style = True, "🎯 價格觸達", "blink-bull" if p_change > 0 else "blink-bear"
        reasons.append(custom_reason)

    # [2] 均線量價/5K突破/MACD翻轉邏輯
    is_bull_trend = price > last['EMA200'] and last['EMA20'] > last['EMA60']
    is_bear_trend = price < last['EMA200'] and last['EMA20'] < last['EMA60']
    
    # 判斷各種子條件
    base_bull = is_bull_trend and p_change >= p_limit and v_ratio >= v_limit
    base_bear = is_bear_trend and p_change <= -p_limit and v_ratio >= v_limit
    
    is_break_high, is_break_low = False, False
    if use_breakout:
        max_h5 = df.iloc[-6:-1]['High'].max(); min_l5 = df.iloc[-6:-1]['Low'].min()
        is_break_high, is_break_low = price > max_h5, price < min_l5

    macd_bull_flip, macd_bear_flip = False, False
    if use_macd_flip and len(df) >= 8:
        hist_window = df['Hist'].iloc[-8:].values
        macd_bull_flip = all(x < 0 for x in hist_window[:-1]) and hist_window[-1] > 0
        macd_bear_flip = all(x > 0 for x in hist_window[:-1]) and hist_window[-1] < 0

    # 彙整預警 (做多類)
    if base_bull or (use_breakout and is_break_high) or macd_bull_flip:
        trigger_alert, action_type = True, "🚀 強勢做多"
        card_style = "blink-bull"
        if base_bull: reasons.append("✅ 趨勢量價達標")
        if is_break_high: reasons.append("🔥 突破前5K高點")
        if macd_bull_flip: reasons.append("🌈 MACD: 7負轉1正")

    # 彙整預警 (做空類)
    elif base_bear or (use_breakout and is_break_low) or macd_bear_flip:
        trigger_alert, action_type = True, "🔻 強勢做空"
        card_style = "blink-bear"
        if base_bear: reasons.append("❌ 趨勢量價達標")
        if is_break_low: reasons.append("📉 跌破前5K低點")
        if macd_bear_flip: reasons.append("Waves MACD: 7正轉1負")

    if trigger_alert:
        send_telegram_msg(sym, action_type, "\n".join(reasons), price, p_change, v_ratio)

    status, color = ("🚀 做多", "#00ff00") if is_bull_trend else ("🔻 做空", "#ff4b4b") if is_bear_trend else ("⚖️ 觀望", "#aaaaaa")
    if action_type: status = action_type

    alert_msgs = []
    if hit_custom: alert_msgs.append("🎯 價格達標")
    if abs(p_change) >= p_limit: alert_msgs.append(f"⚠️ 價異: {p_change:+.2f}%")
    if v_ratio >= v_limit: alert_msgs.append(f"🔥 量爆: {v_ratio:.1f}x")
    if is_break_high or is_break_low: alert_msgs.append("📈 5K突破")
    if macd_bull_flip or macd_bear_flip: alert_msgs.append("⚡ MACD翻轉")
    
    return status, color, "<br>".join(alert_msgs) if alert_msgs else "正常", card_style

# --- 6. 側邊欄配置 ---
with st.sidebar:
    st.header("⚙️ 參數設定")
    input_symbols = st.text_input("股票代碼", value="TSLA, NIO, TSLL, XPEV, META, GOOGL, AAPL, NVDA, AMZN, MSFT, TSM, BTC-USD").upper()
    symbols = [s.strip() for s in input_symbols.split(",") if s.strip()]
    c1, c2 = st.columns(2)
    with c1: sel_period = st.selectbox("範圍", ["1d", "5d", "1mo", "1y"], index=1)
    with c2: sel_interval = st.selectbox("週期", ["1m", "5m", "15m", "1h", "1d"], index=1)
    refresh_rate = st.slider("刷新頻率 (秒)", 60, 600, 300)
    
    st.divider()
    # 新功能：自定義價格預警區 (NEW)
    st.subheader("🎯 自定義價格預警")
    custom_alert_input = st.text_area("格式: 代碼 升穿/跌穿 價格\n(如: TSLA 升穿 420)", value="", placeholder="TSLA 升穿 420\nAAPL 跌穿 200")
    
    st.divider()
    vol_threshold = st.number_input("成交量異常倍數", value=2.0, step=0.5)
    price_threshold = st.number_input("股價單根異動 (%)", value=1.0, step=0.1)
    use_breakout = st.checkbox("5K 突破監控", value=False)
    use_macd_flip = st.checkbox("MACD 7+1 反轉監控", value=False)

# --- 7. 主介面 ---
st.title("📈 智能監控與 Telegram 預警系統")
placeholder = st.empty()

while True:
    all_data = {}
    with placeholder.container():
        st.subheader("🔍 即時警報摘要")
        if symbols:
            cols = st.columns(len(symbols))
            for i, sym in enumerate(symbols):
                df = fetch_data(sym, sel_period, sel_interval)
                if df is not None:
                    all_data[sym] = df
                    # 傳入自定義預警參數 (MODIFIED)
                    status, color, alert_msg, card_style = get_signal(df, price_threshold, vol_threshold, sym, use_breakout, use_macd_flip, custom_alert_input)
                    cols[i].markdown(f"""
                        <div class='{card_style}' style='border:1px solid #444; padding:15px; border-radius:10px; text-align:center;'>
                            <h3 style='margin:0;'>{sym}</h3>
                            <h2 style='color:{color}; margin:10px 0;'>{status}</h2>
                            <p style='font-size:1.3em; margin:0;'><b>{df['Close'].iloc[-1]:.2f}</b></p>
                            <hr style='margin:10px 0; border:0.5px solid #333;'>
                            <p style='font-size:0.8em; color:#ffa500;'>{alert_msg}</p>
                        </div>
                    """, unsafe_allow_html=True)
        st.divider()
        if all_data:
            tabs = st.tabs(list(all_data.keys()))
            for i, (sym, df) in enumerate(all_data.items()):
                with tabs[i]:
                    plot_df = df.tail(35).copy()
                    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.7, 0.3])
                    fig.add_trace(go.Candlestick(x=plot_df.index, open=plot_df['Open'], high=plot_df['High'], low=plot_df['Low'], close=plot_df['Close'], name='K線'), row=1, col=1)
                    fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['EMA20'], name='EMA20', line=dict(color='yellow', width=1)), row=1, col=1)
                    fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['EMA200'], name='EMA200', line=dict(color='red', width=1.5)), row=1, col=1)
                    colors = ['#00ff00' if x >= 0 else '#ff4b4b' for x in plot_df['Hist']]
                    fig.add_trace(go.Bar(x=plot_df.index, y=plot_df['Hist'], name='MACD Hist', marker_color=colors), row=2, col=1)
                    fig.update_layout(height=500, template="plotly_dark", xaxis_rangeslider_visible=False, margin=dict(l=10,r=10,t=10,b=10))
                    st.plotly_chart(fig, use_container_width=True, key=f"fig_{sym}")
        st.caption(f"📅 更新: {datetime.now().strftime('%H:%M:%S')}")
    time.sleep(refresh_rate)
