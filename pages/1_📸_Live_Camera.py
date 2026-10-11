import streamlit as st
import cv2
import numpy as np
import av
import queue
import re
import time
import random
from streamlit_webrtc import webrtc_streamer, VideoProcessorBase
from fast_alpr import ALPR
from database import cloud_db, get_dynamic_pricing, is_exempt

st.set_page_config(page_title="ANPR Engine", page_icon="📸", layout="wide")

# --- INITIALIZE ALPR MODEL ---
@st.cache_resource
def load_alpr_engine():
    return ALPR(
        detector_model="yolo-v9-t-384-license-plate-end2end",
        ocr_model="global-plates-mobile-vit-v2-model"
    )

alpr = load_alpr_engine()

# --- VALIDATION CONSTANTS ---
PLATE_REGEX = re.compile(r"^([A-Z]{2}\d{2}[A-Z]{1,2}\d{4}|\d{2}BH\d{4}[A-Z]{1,2}|\d{1,3}(CD|CC|UN)\d{1,4})$")
CONFIDENCE_THRESHOLD = 0.50 

if st.session_state.get('user_role') not in ["Plaza Operator", "Regional Director"]:
    st.error("🚨 RBAC Alert: Insufficient clearance. Terminal Locked.")
    st.stop()

st.header(f"Live ANPR Feed — {st.session_state.user_node}")

current_rates, is_surge, multiplier = get_dynamic_pricing()
vahan_df = cloud_db.get_dataframe("vahan_blacklist")

if is_surge:
    st.warning(f"⚡ **CONGESTION PRICING ACTIVE:** Peak hours detected. Tolls dynamically adjusted by +{(multiplier-1)*100:.0f}%.")

col_cam, col_sys = st.columns([1.2, 1])

# --- LIVE CONTINUOUS VIDEO STREAM ---
with col_cam:
    st.markdown("### 🎥 Hardware Video Feed")
    
    class VideoProcessor(VideoProcessorBase):
        def __init__(self):
            self.result_queue = queue.Queue(maxsize=5)
            self.frame_skip = 0
            
        def recv(self, frame: av.VideoFrame) -> av.VideoFrame:
            img = frame.to_ndarray(format="bgr24")
            self.frame_skip += 1
            if self.frame_skip % 3 != 0: return frame
            
            try:
                drawn = alpr.draw_predictions(img)
                if drawn.results:
                    for result in drawn.results:
                        if getattr(result, 'ocr', None) is not None:
                            text = result.ocr.text
                            conf = result.ocr.confidence
                            clean_text = re.sub(r'[^A-Z0-9]', '', str(text).upper())
                            
                            if conf >= CONFIDENCE_THRESHOLD and PLATE_REGEX.match(clean_text):
                                if self.result_queue.full():
                                    try: self.result_queue.get_nowait()
                                    except queue.Empty: pass
                                self.result_queue.put(clean_text)
                return av.VideoFrame.from_ndarray(drawn.image, format="bgr24")
            except Exception as e:
                return frame

    ctx = webrtc_streamer(key="anpr_stream", video_processor_factory=VideoProcessor, media_stream_constraints={"video": True, "audio": False})

with col_sys:
    st.markdown("### 🏛️ Processing Matrix")
    
    @st.fragment(run_every="1s")
    def poll_detection_queue():
        if ctx.state.playing and ctx.video_processor:
            try:
                detected = ctx.video_processor.result_queue.get_nowait()
                if 'detected_plate' not in st.session_state or st.session_state.detected_plate != detected:
                    st.session_state.detected_plate = detected
                    st.rerun()
            except queue.Empty: pass

    poll_detection_queue()

    if 'detected_plate' in st.session_state:
        plate = st.session_state.detected_plate
        st.success(f"**Plate Locked:** `{plate}`")
        
        v_type = list(current_rates.keys())[len(plate) % len(current_rates)]
        toll_amt = current_rates[v_type]
        
        if is_exempt(plate):
            st.info("🟢 **TOLL EXEMPT CATEGORY DISCOVERED**")
            if st.button("Log Free Passage & Open Gate", key="exempt_btn", type="primary"):
                cloud_db.insert_transaction(plate, v_type, "VIP Lane", "Exempt", st.session_state.user_node, 0.0, True)
                cloud_db.insert_audit("VIP Exemption Granted", plate, st.session_state.user_node)
                del st.session_state['detected_plate']
                st.rerun()
                
        else:
            match = vahan_df[vahan_df['Plate_Number'] == plate]
            if not match.empty:
                st.error(f"🚨 **SECURITY ANOMALY DETECTED**")
                st.write(f"**Infraction Flag:** {match.iloc[0]['Reason']}")
                if st.button("Acknowledge & Report"):
                    cloud_db.insert_audit(f"Interception: {match.iloc[0]['Reason']}", plate, st.session_state.user_node)
                    del st.session_state['detected_plate']
                    st.rerun()
            else:
                st.write(f"**Class:** {v_type} | **Tariff:** ₹{toll_amt:.2f}")
                
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("📡 FASTag Auto-Deduct"):
                        # --- NPCI BANKING API MOCK ---
                        with st.spinner("Connecting to NPCI FASTag Server..."):
                            time.sleep(random.uniform(0.8, 1.5)) # Mock Network Latency
                            
                            # 10% chance the FASTag is empty or blacklisted
                            if random.random() > 0.90:
                                st.error("❌ FASTag Declined: Insufficient Wallet Balance.")
                                cloud_db.insert_audit("FASTag Wallet Declined", plate, st.session_state.user_node)
                                time.sleep(2)
                            else:
                                st.success("✅ Payment Authorized via NETC.")
                                cloud_db.insert_transaction(plate, v_type, "Lane 1", "FASTag", st.session_state.user_node, toll_amt, False)
                                time.sleep(1)
                                del st.session_state['detected_plate']
                                st.rerun()
                with c2:
                    if st.button("💵 Process Cash"):
                        cloud_db.insert_transaction(plate, v_type, "Lane 1", "Cash", st.session_state.user_node, toll_amt, False)
                        del st.session_state['detected_plate']
                        st.rerun()
