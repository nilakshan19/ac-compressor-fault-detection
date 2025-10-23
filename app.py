import streamlit as st
from catboost import CatBoostClassifier
import json
import paho.mqtt.client as mqtt
import time
import threading
import pandas as pd
from datetime import datetime
import pytz
import plotly.graph_objects as go
import hashlib

# ===================== BASIC CONFIG =====================
st.set_page_config(page_title="Fault Detection", page_icon="🔧", layout="wide")

SL_TZ = pytz.timezone('Asia/Colombo')
MAX_ROWS = 5000  # cap history length

def safe_float(x, default=0.0):
    try:
        if x is None: return float(default)
        return float(x)
    except Exception:
        return float(default)

# ===================== AUTH =============================
def check_credentials():
    USERS = {"admin": hashlib.sha256("Admin123!".encode()).hexdigest()}

    def credentials_entered():
        u = st.session_state.get("username", "")
        p = st.session_state.get("password", "")
        if u in USERS and hashlib.sha256(p.encode()).hexdigest() == USERS[u]:
            st.session_state["authenticated"] = True
            st.session_state["current_user"] = u
            st.session_state.pop("username", None)
            st.session_state.pop("password", None)
        else:
            st.session_state["authenticated"] = False

    if "authenticated" not in st.session_state:
        st.markdown("### 🔐 AC Compressor Dashboard - Login")
        st.markdown("---")
        _, col, _ = st.columns([1, 2, 1])
        with col:
            st.text_input("👤 Username", key="username", placeholder="Enter username")
            st.text_input("🔐 Password", type="password", key="password", placeholder="Enter password")
            st.button("🚀 Login", on_click=credentials_entered, use_container_width=True)
            with st.expander("ℹ️ Login Information"):
                st.info("Username: `admin` | Password: `Admin123!`")
        return False
    elif not st.session_state["authenticated"]:
        st.markdown("### 🔐 AC Compressor Dashboard - Login")
        st.markdown("---")
        _, col, _ = st.columns([1, 2, 1])
        with col:
            st.error("❌ Invalid username or password. Please try again.")
            st.text_input("👤 Username", key="username", placeholder="Enter username")
            st.text_input("🔐 Password", type="password", key="password", placeholder="Enter password")
            st.button("🚀 Login", on_click=credentials_entered, use_container_width=True)
            with st.expander("ℹ️ Login Information"):
                st.info("Username: `admin` | Password: `Admin123!`")
        return False
    else:
        return True

if not check_credentials():
    st.stop()

# Logout
_, col_logout = st.columns([6, 1])
with col_logout:
    if st.button("🚪 Logout"):
        st.session_state["authenticated"] = False
        st.rerun()

# ===================== MODELS ===========================
@st.cache_resource
def load_models():
    bearings = CatBoostClassifier()
    bearings.load_model('bearings_trained_model.cbm')
    radiator = CatBoostClassifier()
    radiator.load_model('radiator_trained_model.cbm')
    return bearings, radiator

bearings_model, radiator_model = load_models()

# ===================== MQTT SETUP =======================
MQTT_BROKER = "broker.emqx.io"
MQTT_PORT = 1883
MQTT_TOPIC = "esp32/sensor_data"

class SensorData:
    def __init__(self):
        self.data = {
            "noise_db": 0.0,
            "expansion_valve_outlet_temp": 0.0,
            "condenser_inlet_temp": 0.0,
            "ambient_temp": 0.0,
            "humidity": 0.0,
            "voltage": 0.0,
            "current": 0.0,
            "power": 0.0,
            "last_update": "Waiting...",
            "count": 0
        }
        self.history = []  # list of dict rows
        self.lock = threading.Lock()

@st.cache_resource
def get_sensor_data():
    return SensorData()

sensor_data = get_sensor_data()

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"✓ MQTT CONNECTED to {MQTT_BROKER}")
        client.subscribe(MQTT_TOPIC)
    else:
        print(f"✗ MQTT connect failed rc={rc}")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))

        # Backward compatibility mappings
        if "water_outlet_temp" in payload and "expansion_valve_outlet_temp" not in payload:
            payload["expansion_valve_outlet_temp"] = payload["water_outlet_temp"]
        if "condenser_inlet_temp" not in payload:
            payload["condenser_inlet_temp"] = 0.0

        now_dt = datetime.now(SL_TZ)
        ts_hist = now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")  # microseconds
        ts_status = now_dt.strftime("%Y-%m-%d %H:%M:%S")    # seconds

        # ---- validate payload; skip empty/heartbeat frames ----
        keys_of_interest = [
            "noise_db", "expansion_valve_outlet_temp", "condenser_inlet_temp",
            "ambient_temp", "humidity", "voltage", "current", "power"
        ]
        has_any_value = any(k in payload for k in keys_of_interest)
        if not has_any_value:
            # do not append a blank row
            return

        with sensor_data.lock:
            sensor_data.data["noise_db"] = safe_float(payload.get("noise_db", 0))
            sensor_data.data["expansion_valve_outlet_temp"] = safe_float(payload.get("expansion_valve_outlet_temp", 0))
            sensor_data.data["condenser_inlet_temp"] = safe_float(payload.get("condenser_inlet_temp", 0))
            sensor_data.data["ambient_temp"] = safe_float(payload.get("ambient_temp", 0))
            sensor_data.data["humidity"] = safe_float(payload.get("humidity", 0))
            sensor_data.data["voltage"] = safe_float(payload.get("voltage", 0))
            sensor_data.data["current"] = safe_float(payload.get("current", 0))
            sensor_data.data["power"] = safe_float(payload.get("power", 0))
            sensor_data.data["last_update"] = ts_status
            sensor_data.data["count"] += 1

            # ------ append full row, including Count first ------
            sensor_data.history.append({
                "Count": sensor_data.data["count"],
                "Timestamp": ts_hist,
                "Noise (dB)": sensor_data.data["noise_db"],
                "Expansion Valve Outlet Temp (°C)": sensor_data.data["expansion_valve_outlet_temp"],
                "Condenser Inlet Temp (°C)": sensor_data.data["condenser_inlet_temp"],
                "Ambient Temp (°C)": sensor_data.data["ambient_temp"],
                "Humidity (%)": sensor_data.data["humidity"],
                "Voltage (V)": sensor_data.data["voltage"],
                "Current (mA)": sensor_data.data["current"],
                "Power (mW)": sensor_data.data["power"]
            })

            # Cap history
            if len(sensor_data.history) > MAX_ROWS:
                sensor_data.history = sensor_data.history[-MAX_ROWS:]

            print(f"✓ Message #{sensor_data.data['count']}")

    except Exception as e:
        print(f"✗ Error: {e}")

@st.cache_resource
def start_mqtt():
    client = mqtt.Client(client_id=f"Streamlit_{int(time.time())}")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    thread = threading.Thread(target=client.loop_forever, daemon=True)
    thread.start()
    return client

mqtt_client = start_mqtt()

# ===================== UI HELPERS =======================
def create_graph(df, column, title, y_label, color_hex):
    """Line-only Plotly graph (no markers)"""
    fig = go.Figure()
    if column not in df.columns:
        fig.add_annotation(text=f"Column '{column}' not found in data",
                           xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False)
    else:
        fig.add_trace(go.Scatter(
            x=df['Timestamp'],
            y=df[column],
            mode='lines',                     # <<< LINE ONLY
            name=y_label,
            line=dict(color=color_hex, width=2)
        ))
    fig.update_layout(
        title=title,
        xaxis_title="Time",
        yaxis_title=y_label,
        hovermode='x unified',
        template='plotly_white',
        height=400
    )
    return fig

# ===================== APP BODY =========================
st.title("🔧 AC Compressor Fault Detection")

with sensor_data.lock:
    current = sensor_data.data.copy()
    history_len = len(sensor_data.history)

# Status
if current["count"] > 0:
    st.success(f"🟢 LIVE | Messages: {current['count']} | Last: {current['last_update']}")
else:
    st.warning("🟡 Waiting for data from ESP32...")

st.markdown("---")

# Controls
c1, c2, c3, c4 = st.columns(4)
with c1: st.metric("📊 Records", history_len)
with c2:
    if st.button("🔄 Refresh"): st.rerun()
with c3:
    if st.button("🗑️ Clear"):
        with sensor_data.lock:
            sensor_data.history.clear()
        st.rerun()
with c4:
    if history_len > 0:
        with sensor_data.lock:
            df = pd.DataFrame(sensor_data.history.copy())
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button("📥 CSV", csv, f"data_{datetime.now(SL_TZ).strftime('%Y%m%d_%H%M%S')}.csv")

st.markdown("---")

# Current readings
col1, col2, col3 = st.columns(3)
with col1:
    st.subheader("📊 Sensor Readings")
    st.metric("Noise", f"{current.get('noise_db', 0.0):.2f} dB")
    st.metric("Expansion Valve Outlet Temp", f"{current.get('expansion_valve_outlet_temp', 0.0):.2f} °C")
    st.metric("Condenser Inlet Temp", f"{current.get('condenser_inlet_temp', 0.0):.2f} °C")
with col2:
    st.subheader("🌡️ Environmental")
    st.metric("Ambient Temp", f"{current.get('ambient_temp', 0.0):.2f} °C")
    st.metric("Humidity", f"{current.get('humidity', 0.0):.2f} %")
with col3:
    st.subheader("⚡ Power Measurements")
    st.metric("Voltage", f"{current.get('voltage', 0.0):.2f} V")
    st.metric("Current", f"{current.get('current', 0.0):.2f} mA")
    st.metric("Power", f"{current.get('power', 0.0):.2f} mW")

st.markdown("---")

# Predictions
st.subheader("🔍 Component Status Predictions")
noise_val = current.get('noise_db', 0.0)
exp_valve_temp = current.get('expansion_valve_outlet_temp', 0.0)
water_flow_placeholder = 0.0
values = [noise_val, exp_valve_temp, water_flow_placeholder]

try:
    p_bearings = int(bearings_model.predict([values])[0])
    p_radiator = int(radiator_model.predict([values])[0])

    pc1, pc2 = st.columns(2)
    with pc1:
        st.metric("🔩 Bearings", f"{'🟢 Normal' if p_bearings==0 else '🔴 Fault'}")
    with pc2:
        st.metric("🌡️ Radiator", f"{'🟢 Normal' if p_radiator==0 else '🔴 Fault'}")

    st.markdown("---")
    faults = p_bearings + p_radiator
    st.success("✅ All monitored components operating normally") if faults==0 \
        else st.warning(f"⚠️ {faults} component(s) showing abnormal behavior")
except Exception as e:
    st.error(f"Prediction Error: {e}")

# ===================== GRAPHS ===========================
with st.expander("📈 Graph View"):
    if history_len > 5:
        with sensor_data.lock:
            df_graph = pd.DataFrame(sensor_data.history.copy())

        # Drop rows where all sensor columns are NaN/None (safety)
        sensor_cols = [
            "Noise (dB)","Expansion Valve Outlet Temp (°C)","Condenser Inlet Temp (°C)",
            "Ambient Temp (°C)","Humidity (%)","Voltage (V)","Current (mA)","Power (mW)"
        ]
        if len(df_graph)>0:
            df_graph = df_graph.dropna(how="all", subset=sensor_cols)

        # Sort by time and reset index
        if "Timestamp" in df_graph.columns:
            df_graph = df_graph.sort_values("Timestamp").reset_index(drop=True)

        # Ensure Count is monotonic (for display)
        if "Count" not in df_graph.columns:
            df_graph["Count"] = range(1, len(df_graph)+1)

        tab1, tab2, tab3, tab4, tab5 = st.tabs([
            "🌡️ Expansion Valve Outlet Temp",
            "🌡️ Condenser Inlet Temp",
            "🌡️ Ambient Temperature",
            "💧 Humidity",
            "📈 Count Trend"
        ])

        with tab1:
            st.subheader("Expansion Valve Outlet Temperature Over Time")
            st.plotly_chart(create_graph(df_graph, 'Expansion Valve Outlet Temp (°C)',
                                         'Expansion Valve Outlet Temperature', 'Temperature (°C)', '#FF6B6B'),
                            use_container_width=True)

        with tab2:
            st.subheader("Condenser Inlet Temperature Over Time")
            st.plotly_chart(create_graph(df_graph, 'Condenser Inlet Temp (°C)',
                                         'Condenser Inlet Temperature', 'Temperature (°C)', '#FFA07A'),
                            use_container_width=True)

        with tab3:
            st.subheader("Ambient Temperature Over Time")
            st.plotly_chart(create_graph(df_graph, 'Ambient Temp (°C)',
                                         'Ambient Temperature', 'Temperature (°C)', '#4ECDC4'),
                            use_container_width=True)

        with tab4:
            st.subheader("Humidity Over Time")
            st.plotly_chart(create_graph(df_graph, 'Humidity (%)',
                                         'Humidity', 'Humidity (%)', '#95E1D3'),
                            use_container_width=True)

        with tab5:
            st.subheader("📈 Data Reception Count Over Time")
            st.plotly_chart(create_graph(df_graph, 'Count',
                                         'Data Reception Count', 'Message Count', '#FFB347'),
                            use_container_width=True)
    else:
        st.info(f"📊 Collecting data... ({history_len}/5 readings). Graphs will appear once 5 or more readings are available.")

# ===================== HISTORICAL TABLE =================
with st.expander("📊 Historical Data"):
    if history_len > 0:
        with sensor_data.lock:
            df_history = pd.DataFrame(sensor_data.history.copy())

        # Drop all-NaN sensor rows (prevents 'None' rows)
        sensor_cols = [
            "Noise (dB)","Expansion Valve Outlet Temp (°C)","Condenser Inlet Temp (°C)",
            "Ambient Temp (°C)","Humidity (%)","Voltage (V)","Current (mA)","Power (mW)"
        ]
        df_history = df_history.dropna(how="all", subset=sensor_cols)

        # Desired column order: Count, Timestamp, then sensors
        ordered_cols = ["Count", "Timestamp"] + sensor_cols
        available_cols = [c for c in ordered_cols if c in df_history.columns]
        df_display = df_history[available_cols].tail(100)

        st.dataframe(
            df_display,
            use_container_width=True,
            height=420,
            column_config={
                "Count": st.column_config.NumberColumn("Count", format="%d"),
                "Timestamp": st.column_config.TextColumn("Time (YYYY-MM-DD HH:MM:SS.ffffff)"),
                "Noise (dB)": st.column_config.NumberColumn("Noise (dB)", format="%.2f"),
                "Expansion Valve Outlet Temp (°C)": st.column_config.NumberColumn("Exp. Valve Temp (°C)", format="%.2f"),
                "Condenser Inlet Temp (°C)": st.column_config.NumberColumn("Condenser Temp (°C)", format="%.2f"),
                "Ambient Temp (°C)": st.column_config.NumberColumn("Ambient Temp (°C)", format="%.2f"),
                "Humidity (%)": st.column_config.NumberColumn("Humidity (%)", format="%.2f"),
                "Voltage (V)": st.column_config.NumberColumn("Voltage (V)", format="%.2f"),
                "Current (mA)": st.column_config.NumberColumn("Current (mA)", format="%.2f"),
                "Power (mW)": st.column_config.NumberColumn("Power (mW)", format="%.2f"),
            }
        )
        st.caption(f"Showing last {len(df_display)} of {len(df_history)} records")
    else:
        st.info("No data yet")

# ===================== AUTO REFRESH =====================
time.sleep(4)
st.rerun()
