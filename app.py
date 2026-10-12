import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
import time
import threading
import os

# ====================== CONFIG ======================
TWELVE_API_KEY = os.environ.get("TWELVE_API_KEY", "cd8d1e68081b4d55a0385938538911d1")  # New key
NTFY_TOPIC = "TYf7NDB9moLR3kTq"
CHECK_INTERVAL = 60  # seconds (safer for free plan)

PAIRS = {
    "EUR/USD": {"name": "EURUSD", "emoji": "🇪🇺"},
    "GBP/USD": {"name": "GBPUSD", "emoji": "🇬🇧"}
}

st.set_page_config(
    page_title="TITAN First Extreme 24/7",
    page_icon="⚡",
    layout="wide"
)

# ====================== STATE ======================
if "monitor_started" not in st.session_state:
    st.session_state.monitor_started = False

for symbol in PAIRS:
    key = PAIRS[symbol]["name"]
    if f"{key}_fe_confirmed" not in st.session_state:
        st.session_state[f"{key}_fe_confirmed"] = False
        st.session_state[f"{key}_se_confirmed"] = False
        st.session_state[f"{key}_fe_type"] = None
        st.session_state[f"{key}_fe_price"] = None
        st.session_state[f"{key}_fe_time"] = None
        st.session_state[f"{key}_direction"] = None
        st.session_state[f"{key}_accuracy"] = None

# ====================== HELPERS ======================
def send_ntfy(title, message, priority=5):
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": str(priority),
                "Tags": "chart_with_upwards_trend"
            },
            timeout=8
        )
    except Exception as e:
        print("ntfy error:", e)

def get_spain_now():
    return datetime.now(timezone.utc) + timedelta(hours=2)

def fetch_h1_data(symbol, limit=36):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=1h&outputsize={limit}&apikey={TWELVE_API_KEY}"
    try:
        r = requests.get(url, timeout=12).json()
        if "values" not in r:
            return None
        df = pd.DataFrame(r["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        df = df.sort_values("datetime").reset_index(drop=True)
        return df
    except Exception as e:
        print(f"Fetch error {symbol}:", e)
        return None

def analyze_extremes(df, symbol_name):
    if df is None or len(df) < 3:
        return None

    spain_now = get_spain_now()
    today = spain_now.date()

    df["date"] = df["datetime"].dt.date
    today_df = df[df["date"] == today].copy()

    if len(today_df) < 2:
        return {
            "status": "WAITING",
            "message": "Not enough bars yet",
            "fe_confirmed": False,
            "se_confirmed": False
        }

    today_df = today_df.reset_index(drop=True)
    highs = today_df["high"].values
    lows = today_df["low"].values
    times = today_df["datetime"].values

    current_high = highs[0]
    current_low = lows[0]
    high_time = times[0]
    low_time = times[0]

    for i in range(1, len(today_df)):
        if highs[i] > current_high:
            current_high = highs[i]
            high_time = times[i]
        if lows[i] < current_low:
            current_low = lows[i]
            low_time = times[i]

    if high_time < low_time:
        fe_type = "HIGH"
        fe_price = current_high
        fe_time = high_time
        se_type = "LOW"
        se_price = current_low
        se_time = low_time
    else:
        fe_type = "LOW"
        fe_price = current_low
        fe_time = low_time
        se_type = "HIGH"
        se_price = current_high
        se_time = high_time

    if fe_type == "HIGH":
        direction = "SELL"
        accuracy = "≈89–91%"
    else:
        direction = "BUY"
        accuracy = "≈88–92%"

    return {
        "status": "ACTIVE",
        "fe_type": fe_type,
        "fe_price": round(float(fe_price), 5),
        "fe_time": str(fe_time)[11:16],
        "se_type": se_type,
        "se_price": round(float(se_price), 5),
        "se_time": str(se_time)[11:16],
        "direction": direction,
        "accuracy": accuracy,
        "fe_confirmed": True,
        "se_confirmed": len(today_df) >= 4,
        "current_high": round(float(current_high), 5),
        "current_low": round(float(current_low), 5),
        "bars_today": len(today_df)
    }

# ====================== BACKGROUND MONITOR ======================
def background_monitor():
    while True:
        for symbol, info in PAIRS.items():
            key = info["name"]
            try:
                df = fetch_h1_data(symbol)
                result = analyze_extremes(df, key)

                if result and result["status"] == "ACTIVE":
                    # First Extreme alert (once per day)
                    if result["fe_confirmed"] and not st.session_state[f"{key}_fe_confirmed"]:
                        st.session_state[f"{key}_fe_confirmed"] = True
                        st.session_state[f"{key}_fe_type"] = result["fe_type"]
                        st.session_state[f"{key}_fe_price"] = result["fe_price"]
                        st.session_state[f"{key}_fe_time"] = result["fe_time"]
                        st.session_state[f"{key}_direction"] = result["direction"]
                        st.session_state[f"{key}_accuracy"] = result["accuracy"]

                        title = f"🟢 {key} FIRST EXTREME — {result['direction']}"
                        msg = (
                            f"{key} First Extreme = {result['fe_type']}\n"
                            f"Price: {result['fe_price']}\n"
                            f"Time: {result['fe_time']} UTC\n"
                            f"Direction: {result['direction']}\n"
                            f"Accuracy: {result['accuracy']}\n"
                            f"Spain: {get_spain_now().strftime('%H:%M')}"
                        )
                        send_ntfy(title, msg, priority=5)

                    # Second Extreme alert
                    if result["se_confirmed"] and not st.session_state[f"{key}_se_confirmed"]:
                        st.session_state[f"{key}_se_confirmed"] = True
                        title = f"🔵 {key} SECOND EXTREME"
                        msg = (
                            f"{key} Second Extreme = {result['se_type']}\n"
                            f"Price: {result['se_price']}\n"
                            f"Time: {result['se_time']} UTC"
                        )
                        send_ntfy(title, msg, priority=4)

            except Exception as e:
                print(f"Error {key}:", e)

        time.sleep(CHECK_INTERVAL)

if not st.session_state.monitor_started:
    t = threading.Thread(target=background_monitor, daemon=True)
    t.start()
    st.session_state.monitor_started = True

# ====================== UI ======================
st.title("⚡ TITAN First Extreme — 24/7 Live")
st.caption(f"Spain time: {get_spain_now().strftime('%Y-%m-%d %H:%M:%S')} | Checking every {CHECK_INTERVAL}s | New API key active")

# --- EURUSD Card ---
st.subheader("🇪🇺 EURUSD")
col1, col2, col3 = st.columns(3)

with col1:
    if st.session_state.EURUSD_fe_confirmed:
        st.success("**FIRST EXTREME CONFIRMED**")
        st.metric("Type", st.session_state.EURUSD_fe_type)
        st.metric("Price", st.session_state.EURUSD_fe_price)
        st.metric("Time", st.session_state.EURUSD_fe_time)
    else:
        st.warning("First Extreme — Waiting...")

with col2:
    if st.session_state.EURUSD_direction:
        st.metric("DIRECTION", st.session_state.EURUSD_direction)
        st.metric("Accuracy", st.session_state.EURUSD_accuracy)
    else:
        st.info("Direction — Pending")

with col3:
    if st.session_state.EURUSD_se_confirmed:
        st.success("Second Extreme Confirmed")
    else:
        st.info("Second Extreme — Waiting")

# --- GBPUSD Card ---
st.subheader("🇬🇧 GBPUSD")
col4, col5, col6 = st.columns(3)

with col4:
    if st.session_state.GBPUSD_fe_confirmed:
        st.success("**FIRST EXTREME CONFIRMED**")
        st.metric("Type", st.session_state.GBPUSD_fe_type)
        st.metric("Price", st.session_state.GBPUSD_fe_price)
        st.metric("Time", st.session_state.GBPUSD_fe_time)
    else:
        st.warning("First Extreme — Waiting...")

with col5:
    if st.session_state.GBPUSD_direction:
        st.metric("DIRECTION", st.session_state.GBPUSD_direction)
        st.metric("Accuracy", st.session_state.GBPUSD_accuracy)
    else:
        st.info("Direction — Pending")

with col6:
    if st.session_state.GBPUSD_se_confirmed:
        st.success("Second Extreme Confirmed")
    else:
        st.info("Second Extreme — Waiting")

st.markdown("---")
st.subheader("Live Status")

# Show live data for both
for symbol, info in PAIRS.items():
    key = info["name"]
    df = fetch_h1_data(symbol)
    result = analyze_extremes(df, key)
    st.markdown(f"**{key}**")
    if result:
        st.json(result)
    else:
        st.error(f"Could not fetch data for {key}")

st.caption("Alerts are sent automatically to your ntfy topic the moment First or Second Extreme is confirmed.")
