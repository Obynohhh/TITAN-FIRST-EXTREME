import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
import time
import threading
import os
import json
from pathlib import Path

# ====================== CONFIG ======================
TWELVE_API_KEY = os.environ.get("TWELVE_API_KEY", "")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
CHECK_INTERVAL = 60  # seconds

PAIRS = {
    "EURUSD": {"biquote": "EURUSD", "twelve": "EUR/USD", "emoji": "🇪🇺"},
    "GBPUSD": {"biquote": "GBPUSD", "twelve": "GBP/USD", "emoji": "🇬🇧"}
}

st.set_page_config(
    page_title="TITAN First Extreme 24/7",
    page_icon="⚡",
    layout="wide"
)

# ====================== STATE ======================
if "monitor_started" not in st.session_state:
    st.session_state.monitor_started = False

for key in PAIRS:
    if f"{key}_fe_confirmed" not in st.session_state:
        st.session_state[f"{key}_fe_confirmed"] = False
        st.session_state[f"{key}_se_confirmed"] = False
        st.session_state[f"{key}_fe_type"] = None
        st.session_state[f"{key}_fe_price"] = None
        st.session_state[f"{key}_fe_time"] = None
        st.session_state[f"{key}_direction"] = None
        st.session_state[f"{key}_accuracy"] = None
        st.session_state[f"{key}_source"] = None

# ====================== HELPERS ======================
def send_ntfy(title, message, priority=5):
    if not NTFY_TOPIC:
        print("ntfy not configured: set NTFY_TOPIC in the hosting environment")
        return
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

def fetch_biquote(symbol, limit=48):
    """Primary free source - no API key needed"""
    url = f"https://biquote.io/api/{symbol}/ohlc?interval=1h&limit={limit}"
    try:
        r = requests.get(url, timeout=10).json()
        if "bars" not in r or not r["bars"]:
            return None
        df = pd.DataFrame(r["bars"])
        df["datetime"] = pd.to_datetime(df["openTime"])
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        df = df.sort_values("datetime").reset_index(drop=True)
        return df
    except Exception as e:
        print(f"Biquote error {symbol}:", e)
        return None

def fetch_twelve(symbol, limit=48):
    """Fallback"""
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=1h&outputsize={limit}&apikey={TWELVE_API_KEY}"
    try:
        r = requests.get(url, timeout=10).json()
        if "values" not in r:
            return None
        df = pd.DataFrame(r["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        df = df.sort_values("datetime").reset_index(drop=True)
        return df
    except Exception as e:
        print(f"Twelve Data error {symbol}:", e)
        return None

def fetch_h1(symbol_key):
    """Try Biquote first, then Twelve Data"""
    info = PAIRS[symbol_key]
    df = fetch_biquote(info["biquote"])
    source = "Biquote"
    if df is None or len(df) < 3:
        df = fetch_twelve(info["twelve"])
        source = "Twelve Data"
    return df, source

def analyze_extremes(df):
    if df is None or len(df) < 3:
        return None

    spain_now = get_spain_now()
    today = spain_now.date()

    df = df.copy()
    df["date"] = df["datetime"].dt.date
    today_df = df[df["date"] == today].copy()

    if len(today_df) < 2:
        return {
            "status": "WAITING",
            "message": "Not enough bars yet for today",
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
        for key in PAIRS:
            try:
                df, source = fetch_h1(key)
                result = analyze_extremes(df)

                if result and result["status"] == "ACTIVE":
                    # First Extreme alert (once)
                    if result["fe_confirmed"] and not st.session_state[f"{key}_fe_confirmed"]:
                        st.session_state[f"{key}_fe_confirmed"] = True
                        st.session_state[f"{key}_fe_type"] = result["fe_type"]
                        st.session_state[f"{key}_fe_price"] = result["fe_price"]
                        st.session_state[f"{key}_fe_time"] = result["fe_time"]
                        st.session_state[f"{key}_direction"] = result["direction"]
                        st.session_state[f"{key}_accuracy"] = result["accuracy"]
                        st.session_state[f"{key}_source"] = source

                        title = f"🟢 {key} FIRST EXTREME — {result['direction']}"
                        msg = (
                            f"{key} First Extreme = {result['fe_type']}\n"
                            f"Price: {result['fe_price']}\n"
                            f"Time: {result['fe_time']} UTC\n"
                            f"Direction: {result['direction']}\n"
                            f"Accuracy: {result['accuracy']}\n"
                            f"Source: {source}\n"
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
                            f"Time: {result['se_time']} UTC\n"
                            f"Source: {source}"
                        )
                        send_ntfy(title, msg, priority=4)

            except Exception as e:
                print(f"Monitor error {key}:", e)

        time.sleep(CHECK_INTERVAL)

if not st.session_state.monitor_started:
    t = threading.Thread(target=background_monitor, daemon=True)
    t.start()
    st.session_state.monitor_started = True

# ====================== UI ======================
st.title("⚡ TITAN First Extreme — 24/7 Live")
st.caption(f"Spain time: {get_spain_now().strftime('%Y-%m-%d %H:%M:%S')} | Primary: Biquote (free) | Fallback: Twelve Data")

# --- EURUSD ---
st.subheader("🇪🇺 EURUSD")
col1, col2, col3 = st.columns(3)

with col1:
    if st.session_state.EURUSD_fe_confirmed:
        st.success("**FIRST EXTREME CONFIRMED**")
        st.metric("Type", st.session_state.EURUSD_fe_type)
        st.metric("Price", st.session_state.EURUSD_fe_price)
        st.metric("Time (UTC)", st.session_state.EURUSD_fe_time)
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

# --- GBPUSD ---
st.subheader("🇬🇧 GBPUSD")
col4, col5, col6 = st.columns(3)

with col4:
    if st.session_state.GBPUSD_fe_confirmed:
        st.success("**FIRST EXTREME CONFIRMED**")
        st.metric("Type", st.session_state.GBPUSD_fe_type)
        st.metric("Price", st.session_state.GBPUSD_fe_price)
        st.metric("Time (UTC)", st.session_state.GBPUSD_fe_time)
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

for key in PAIRS:
    df, source = fetch_h1(key)
    result = analyze_extremes(df)
    st.markdown(f"**{key}** (Source: {source})")
    if result:
        st.json(result)
    else:
        st.error(f"Could not fetch data for {key}")

# ====================== PERFORMANCE HISTORY ======================
st.markdown("---")
st.subheader("📊 Performance History — Validated Edge")

col_a, col_b, col_c, col_d = st.columns(4)

with col_a:
    st.metric("FE = HIGH → SELL", "89.1%", "Full sample")
with col_b:
    st.metric("FE = LOW → BUY", "88.5%", "Full sample")
with col_c:
    st.metric("FE by 08:00 LOW → BUY", "92.2%", "Best cell")
with col_d:
    st.metric("FE by 08:00 HIGH → SELL", "91.1%", "Best cell")

st.markdown("### Detailed Validated Results (2010–2025)")

perf_data = {
    "Rule": [
        "FE = HIGH → day SELL",
        "FE = LOW → day BUY",
        "FE by 08:00 = HIGH → SELL",
        "FE by 08:00 = LOW → BUY",
        "Asian HIGH → SELL",
        "Asian LOW → BUY"
    ],
    "Train 2010-18": ["89.03%", "88.92%", "91.41%", "92.23%", "93.5%", "94.0%"],
    "Test 2019-25": ["88.83%", "88.38%", "90.11%", "92.20%", "94.6%", "94.2%"],
    "Full Sample": ["89.08%", "88.54%", "91.06%", "92.03%", "94.1%", "94.1%"],
    "Residual": ["0.11", "0.11", "0.09", "0.08", "0.05-0.07", "0.06"]
}

st.dataframe(pd.DataFrame(perf_data), use_container_width=True, hide_index=True)

st.markdown("""
**Key Notes:**
- Best cell: **FE by 08:00 = LOW → BUY at 92.2%** with almost zero train→test degradation.
- Large body days (Body/ATR ≥ 0.50) push accuracy to **98–100%**.
- Small body / doji days are the main source of the residual reversals.
- Data source: Full Dukascopy H1 2010–2026 (113,207 bars).
""")

# ====================== LIVE DAILY LOG ======================
st.markdown("---")
st.subheader("📅 Live Daily Log (Auto-recorded)")

LOG_FILE = "daily_performance_log.csv"

def load_log():
    if os.path.exists(LOG_FILE):
        try:
            return pd.read_csv(LOG_FILE)
        except:
            return pd.DataFrame(columns=["Date", "Pair", "FE Type", "Predicted Direction", "Actual Close Direction", "Result", "FE Price", "Source"])
    return pd.DataFrame(columns=["Date", "Pair", "FE Type", "Predicted Direction", "Actual Close Direction", "Result", "FE Price", "Source"])

def save_log(df):
    df.to_csv(LOG_FILE, index=False)

log_df = load_log()

# Evaluate and record today's result for each pair
spain_now = get_spain_now()
today_str = spain_now.strftime("%Y-%m-%d")

for key in PAIRS:
    df, source = fetch_h1(key)
    result = analyze_extremes(df)

    if result and result.get("fe_confirmed") and st.session_state.get(f"{key}_fe_confirmed"):
        # Check if already logged today
        already = ((log_df["Date"] == today_str) & (log_df["Pair"] == key)).any() if not log_df.empty else False

        if not already and len(df) > 0:
            # Get today's actual close direction
            today_bars = df[df["datetime"].dt.date == spain_now.date()]
            if len(today_bars) >= 2:
                day_open = today_bars.iloc[0]["open"]
                day_close = today_bars.iloc[-1]["close"]
                actual_dir = "BUY" if day_close > day_open else "SELL"

                predicted = st.session_state[f"{key}_direction"]
                outcome = "CORRECT" if predicted == actual_dir else "WRONG"

                new_row = {
                    "Date": today_str,
                    "Pair": key,
                    "FE Type": st.session_state[f"{key}_fe_type"],
                    "Predicted Direction": predicted,
                    "Actual Close Direction": actual_dir,
                    "Result": outcome,
                    "FE Price": st.session_state[f"{key}_fe_price"],
                    "Source": source
                }

                log_df = pd.concat([log_df, pd.DataFrame([new_row])], ignore_index=True)
                save_log(log_df)

# Display the log
if not log_df.empty:
    st.dataframe(log_df.sort_values("Date", ascending=False), use_container_width=True, hide_index=True)

    # Summary stats
    total = len(log_df)
    correct = len(log_df[log_df["Result"] == "CORRECT"])
    accuracy = (correct / total * 100) if total > 0 else 0

    colx, coly, colz = st.columns(3)
    with colx:
        st.metric("Total Days Logged", total)
    with coly:
        st.metric("Correct", correct)
    with colz:
        st.metric("Live Accuracy", f"{accuracy:.1f}%")
else:
    st.info("No days recorded yet. The system will automatically log each day once First Extreme is confirmed and the day progresses.")


# ====================== READ-ONLY TITAN SNAPSHOT EXPORT ======================
# Publishes selected platform outputs for the central TITAN Research AI.
# This export is read-only: it does not place trades, change alerts, or expose secrets.
def publish_titan_snapshot():
    generated_utc = datetime.now(timezone.utc)
    generated_spain = get_spain_now()
    pair_snapshots = {}

    for pair_key in PAIRS:
        live_df, live_source = fetch_h1(pair_key)
        live_result = analyze_extremes(live_df)

        latest_bar_time = None
        latest_close = None
        if live_df is not None and not live_df.empty:
            latest = live_df.iloc[-1]
            latest_bar_time = str(latest["datetime"])
            latest_close = round(float(latest["close"]), 5)

        pair_snapshots[pair_key] = {
            "data_status": "AVAILABLE" if live_df is not None and not live_df.empty else "UNAVAILABLE",
            "data_source": live_source if live_df is not None else None,
            "latest_bar_time": latest_bar_time,
            "latest_close": latest_close,
            "bars_received": int(len(live_df)) if live_df is not None else 0,
            "today_analysis": live_result,
            "monitor_state": {
                "first_extreme_confirmed": bool(st.session_state.get(f"{pair_key}_fe_confirmed", False)),
                "first_extreme_type": st.session_state.get(f"{pair_key}_fe_type"),
                "first_extreme_price": st.session_state.get(f"{pair_key}_fe_price"),
                "first_extreme_time": st.session_state.get(f"{pair_key}_fe_time"),
                "direction": st.session_state.get(f"{pair_key}_direction"),
                "displayed_accuracy": st.session_state.get(f"{pair_key}_accuracy"),
                "source": st.session_state.get(f"{pair_key}_source"),
                "second_extreme_confirmed": bool(st.session_state.get(f"{pair_key}_se_confirmed", False)),
            },
        }

    # Keep platform-published backtest figures distinct from independently verified results.
    platform_claims = {
        "label": "Figures displayed by the TITAN First Extreme platform; not independently verified by this connector",
        "period_displayed": "2010–2025",
        "metrics": {
            "FE_HIGH_to_SELL_full_sample": "89.08%",
            "FE_LOW_to_BUY_full_sample": "88.54%",
            "FE_by_0800_HIGH_to_SELL_full_sample": "91.06%",
            "FE_by_0800_LOW_to_BUY_full_sample": "92.03%",
            "Asian_HIGH_to_SELL_full_sample": "94.1%",
            "Asian_LOW_to_BUY_full_sample": "94.1%",
        },
        "train_test_table": perf_data,
    }

    history_summary = None
    if "log_df" in globals() and isinstance(log_df, pd.DataFrame) and not log_df.empty:
        total = int(len(log_df))
        correct = int((log_df["Result"] == "CORRECT").sum()) if "Result" in log_df.columns else 0
        history_summary = {
            "rows": total,
            "correct": correct,
            "accuracy_percent": round(correct / total * 100, 2) if total else None,
            "latest_date": str(log_df["Date"].max()) if "Date" in log_df.columns else None,
        }

    snapshot = {
        "schema_version": "1.0",
        "platform_id": "titan_first_extreme",
        "platform_name": "TITAN First Extreme 24/7",
        "generated_at_utc": generated_utc.isoformat(),
        "generated_at_spain": generated_spain.isoformat(),
        "refresh_interval_seconds": CHECK_INTERVAL,
        "pairs": pair_snapshots,
        "platform_published_backtest_claims": platform_claims,
        "live_history_summary": history_summary,
        "safety": {
            "read_only_export": True,
            "not_an_execution_interface": True,
            "unavailable_data_must_not_be_interpreted_as_no_signal": True,
        },
    }

    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    target = static_dir / "titan_first_extreme_snapshot.json"
    temporary = static_dir / "titan_first_extreme_snapshot.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=2, default=str)
    temporary.replace(target)

# Export once on each Streamlit script run, after the platform's calculations.
try:
    publish_titan_snapshot()
except Exception as snapshot_error:
    st.warning(f"Read-only TITAN snapshot export unavailable: {snapshot_error}")
