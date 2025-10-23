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

# Page config MUST be first
st.set_page_config(page_title="Fault Detection", page_icon="🔧", layout="wide")

# ========== USERNAME + PASSWORD AUTHENTICATION ==========
def check_credentials():
    """Returns `True` if the user has correct username and password."""
    
    # Define user (username: password_hash)
    USERS = {
        "admin": hashlib.sha256("Admin123!".encode()).hexdigest(),
    }
    
    def credentials_entered():
        """Checks credentials."""
        username = st.session_state.get("username", "")
        password = st.session_state.get("password", "")
        
        if username in USERS and hashlib.sha256(password.encode()).hexdigest() == USERS[username]:
            st.session_state["authenticated"] = True
            st.session_state["current_user"] = username
            del st.session_state["username"]
            del st.session_state["password"]
        else:
            st.session_state["authenticated"] = False

    if "authenticated" not in st.session_state:
        st.markdown("### 🔐 AC Compressor Dashboard - Login")
        st.markdown("---")
        
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.text_input("👤 Username", key="username", placeholder="Enter username")
            st.text_input("🔐 Password", type="password", key="password", placeholder="Enter password")
            st.button("🚀 Login", on_click=credentials_entered, use_container_width=True)
            
            with st.expander("ℹ️ Login Information"):
                st.info("Username: `admin` | Password: `Admin123!`")
        return False
        
    elif not st.session_state["authenticated"]:
        st.markdown("### 🔐 AC Compressor Dashboard - Login")
        st.markdown("---")
        
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
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

# Logout button
col1, col2 = st.columns([6, 1])
with col2:
    if st.button("🚪 Logout"):
        st.session_state["authenticated"] = False
        st.rerun()
# ========== END AUTHENTICATION ==========

# Load models only once
@st.cache_resource
def load_models():
    bearings = CatBoostClassifier()
    bearings.load_model('bearings_trained_model.cbm')
    
    wpump = CatBoostClassifier()
    wpump.load_model('wpump_trained_model.cbm')
    
    radiator = CatBoostClassifier()
    radiator.load_model('radiator_trained_model.cbm')
    
    exvalve = CatBoostClassifier()
    exvalve.load_model('exvalve_trained_model.cbm')
    
    return bearings, wpump, radiator, exvalve

bearings_model, wpump_model, radiator_model, exvalve_model = load_models()

# MQTT Config
MQTT_BROKER = "broker.emqx.io"
MQTT_PORT = 1883
MQTT_TOPIC = "esp32/sensor_data"

# Shared data class
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
        self.history = []
        self.lock = threading.Lock()

# Initialize shared data
@st.cache_resource
def get_sensor_data():
    return SensorData()

sensor_data = get_sensor_data()

# MQTT callbacks
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"✓ MQTT CONNECTED to {MQTT_BROKER}")
        client.subscribe(MQTT_TOPIC)

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        
        # Sri Lanka timezone using pytz (works on cloud servers)
        sri_lanka_tz = pytz.timezone('Asia/Colombo')
        current_time = datetime.now(sri_lanka_tz).strftime("%Y-%m-%d %H:%M:%S")
        
        with sensor_data.lock:
            sensor_data.data["noise_db"] = float(payload.get("noise_db", 0))
            sensor_data.data["expansion_valve_outlet_temp"] = float(payload.get("expansion_valve_outlet_temp", 0))
            sensor_data.data["condenser_inlet_temp"] = float(payload.get("condenser_inlet_temp", 0))
            sensor_data.data["ambient_temp"] = float(payload.get("ambient_temp", 0))
            sensor_data.data["humidity"] = float(payload.get("humidity", 0))
            sensor_data.data["voltage"] = float(payload.get("voltage", 0))
            sensor_data.data["current"] = float(payload.get("current", 0))
            sensor_data.data["power"] = float(payload.get("power", 0))
            sensor_data.data["last_update"] = current_time
            sensor_data.data["count"] += 1

            # Prevent duplicate entries
            if len(sensor_data.history) > 0:
                last = sensor_data.history[-1]
                if last["Timestamp"] == current_time:
                    return

            # Add new reading to history
            sensor_data.history.append({
                "Timestamp": current_time,
                "Noise_dB": sensor_data.data["noise_db"],
                "Expansion_Valve_Outlet_Temp_C": sensor_data.data["expansion_valve_outlet_temp"],
                "Condenser_Inlet_Temp_C": sensor_data.data["condenser_inlet_temp"],
                "Ambient_Temp_C": sensor_data.data["ambient_temp"],
                "Humidity_Percent": sensor_data.data["humidity"],
                "Voltage_V": sensor_data.data["voltage"],
                "Current_mA": sensor_data.data["current"],
                "Power_mW": sensor_data.data["power"]
            })
            
            print(f"✓ Message #{sensor_data.data['count']}")
    except Exception as e:
        print(f"✗ Error: {e}")

# Start MQTT only once
@st.cache_resource
def start_mqtt():
    client = mqtt.Client(client_id=f"Streamlit_{int(time.time())}")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    
    def loop():
        client.loop_forever()
    
    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return client

mqtt_client = start_mqtt()

# Function to create graph
def create_graph(df, column, title, y_label, color):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df['Timestamp'],
        y=df[column],
        mode='lines+markers',
        name=y_label,
        line=dict(color=color, width=2),
        marker=dict(size=6)
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

# ========== UI ==========

st.title("🔧 AC Compressor Fault Detection")

# Get current data safely
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
with c1:
    st.metric("📊 Records", history_len)
with c2:
    if st.button("🔄 Refresh"):
        st.rerun()
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
        sri_lanka_tz = pytz.timezone('Asia/Colombo')
        st.download_button("📥 CSV", csv, f"data_{datetime.now(sri_lanka_tz).strftime('%Y%m%d_%H%M%S')}.csv")

st.markdown("---")

# Sensor Readings with safe access
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

# Component Status Predictions
st.subheader("🔍 Component Status Predictions")

# Safely get values for prediction
noise_val = current.get('noise_db', 0.0)
exp_valve_temp = current.get('expansion_valve_outlet_temp', 0.0)
values = [noise_val, exp_valve_temp]

try:
    p1 = bearings_model.predict([values])[0]
    p2 = wpump_model.predict([values])[0]
    p3 = radiator_model.predict([values])[0]
    p4 = exvalve_model.predict([values])[0]
    
    pred_col1, pred_col2, pred_col3, pred_col4 = st.columns(4)
    
    with pred_col1:
        st.metric("Bearings", f"{'🟢' if p1 == 0 else '🔴'} {'Normal' if p1 == 0 else 'Fault'}")
    with pred_col2:
        st.metric("Water Pump", f"{'🟢' if p2 == 0 else '🔴'} {'Normal' if p2 == 0 else 'Fault'}")
    with pred_col3:
        st.metric("Radiator", f"{'🟢' if p3 == 0 else '🔴'} {'Normal' if p3 == 0 else 'Fault'}")
    with pred_col4:
        st.metric("Exhaust Valve", f"{'🟢' if p4 == 0 else '🔴'} {'Normal' if p4 == 0 else 'Fault'}")
    
    st.markdown("---")
    
    faults = sum([p1, p2, p3, p4])
    if faults == 0:
        st.success("✅ All components operating normally")
    else:
        st.warning(f"⚠️ {faults} component(s) showing abnormal behavior")
        
except Exception as e:
    st.error(f"Prediction Error: {e}")

# Graph View
with st.expander("📈 Graph View"):
    if history_len > 5:
        with sensor_data.lock:
            df_graph = pd.DataFrame(sensor_data.history.copy())
        df_graph["Count"] = range(1, len(df_graph) + 1)
        
        tab1, tab2, tab3, tab4, tab5 = st.tabs([
            "🌡️ Expansion Valve Outlet Temp", 
            "🌡️ Condenser Inlet Temp",
            "🌡️ Ambient Temperature", 
            "💧 Humidity", 
            "📈 Count Trend"
        ])
        
        with tab1:
            st.subheader("Expansion Valve Outlet Temperature Over Time")
            st.plotly_chart(create_graph(df_graph, 'Expansion_Valve_Outlet_Temp_C', 'Expansion Valve Outlet Temperature', 'Temperature (°C)', '#FF6B6B'), use_container_width=True)
        
        with tab2:
            st.subheader("Condenser Inlet Temperature Over Time")
            st.plotly_chart(create_graph(df_graph, 'Condenser_Inlet_Temp_C', 'Condenser Inlet Temperature', 'Temperature (°C)', '#FFA07A'), use_container_width=True)
        
        with tab3:
            st.subheader("Ambient Temperature Over Time")
            st.plotly_chart(create_graph(df_graph, 'Ambient_Temp_C', 'Ambient Temperature', 'Temperature (°C)', '#4ECDC4'), use_container_width=True)
        
        with tab4:
            st.subheader("Humidity Over Time")
            st.plotly_chart(create_graph(df_graph, 'Humidity_Percent', 'Humidity', 'Humidity (%)', '#95E1D3'), use_container_width=True)

        with tab5:
            st.subheader("📈 Data Reception Count Over Time")
            st.plotly_chart(create_graph(df_graph, 'Count', 'Data Reception Count', 'Message Count', '#FFB347'), use_container_width=True)
            
    else:
        st.info(f"📊 Collecting data... ({history_len}/5 readings). Graphs will appear once 5 or more readings are available.")

# Historical Data
with st.expander("📊 Historical Data"):
    if history_len > 0:
        with sensor_data.lock:
            df_history = pd.DataFrame(sensor_data.history.copy())
        st.dataframe(df_history.tail(100), use_container_width=True, height=400)
        st.caption(f"Showing last 100 of {history_len} records")
    else:
        st.info("No data yet")

# Auto-refresh every 4 seconds
time.sleep(4)
st.rerun()
