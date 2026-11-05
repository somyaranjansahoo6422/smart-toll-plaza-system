import itertools
import queue
import random
import time

import av
import cv2
import streamlit as st
from streamlit_webrtc import VideoHTMLAttributes, WebRtcMode, webrtc_streamer

from auth import ALL_ROLES, require_role
from database import (
    VEHICLE_CLASSES,
    classify_plate,
    cloud_db,
    get_dynamic_pricing,
    is_exempt,
    normalize_plate,
    validate_indian_plate,
)

require_role(*ALL_ROLES)

# --- VALIDATION CONSTANTS ---
CONFIDENCE_THRESHOLD = 0.50
FRAME_STRIDE = 3            # run inference on every 3rd frame
REDETECT_COOLDOWN_S = 60    # ignore a plate this long after it was processed (car still in frame)
LANES = ["Lane 1", "Lane 2", "Lane 3", "Lane 4"]
PLATE_FORMAT_LABELS = {"standard": "Standard", "bharat": "Bharat series", "diplomatic": "Diplomatic / UN"}


# --- INITIALIZE ALPR MODEL ---
@st.cache_resource(show_spinner="Loading ANPR vision models (first run downloads the weights)...")
def load_alpr_engine():
    from fast_alpr import ALPR

    return ALPR(
        detector_model="yolo-v9-t-384-license-plate-end2end",
        # Must support 10 characters: standard Indian plates are 10 long, and the older
        # global-plates-mobile-vit-v2-model stops at 9, so it can never read them.
        ocr_model="cct-xs-v2-global-model",
    )


def ocr_confidence(ocr):
    # fast-alpr reports either one score or one score per character.
    conf = ocr.confidence
    if isinstance(conf, (list, tuple)):
        return sum(conf) / len(conf) if conf else 0.0
    return float(conf)


plaza = st.session_state.user_node
st.session_state.setdefault("detected_plate", None)
st.session_state.setdefault("recently_processed", {})
# Per-session objects shared with the WebRTC worker thread (it cannot touch session_state).
st.session_state.setdefault("anpr_queue", queue.Queue(maxsize=5))
st.session_state.setdefault("anpr_overlay", {"detections": []})
st.session_state.setdefault("anpr_frame_counter", itertools.count())


def is_cooling_down(plate):
    processed_at = st.session_state.recently_processed.get(plate)
    return processed_at is not None and time.time() - processed_at < REDETECT_COOLDOWN_S


def finish_vehicle(plate, message, icon):
    st.session_state.recently_processed[plate] = time.time()
    st.session_state.detected_plate = None
    st.session_state.flash = (message, icon)
    st.rerun()


flash = st.session_state.pop("flash", None)
if flash:
    st.toast(flash[0], icon=flash[1])

st.title("Live ANPR feed")
st.caption(f":material/location_on: {plaza}")

current_rates, is_surge, multiplier = get_dynamic_pricing()
if is_surge:
    st.warning(
        f"**Congestion pricing active:** peak hours detected. Tolls adjusted by +{(multiplier - 1) * 100:.0f}%.",
        icon=":material/bolt:",
    )

try:
    alpr = load_alpr_engine()
    alpr_error = None
except Exception as exc:  # missing package, no internet for first download, bad ONNX runtime...
    alpr = None
    alpr_error = exc

col_cam, col_sys = st.columns([1.2, 1])

# --- LIVE CONTINUOUS VIDEO STREAM ---
with col_cam:
    st.subheader(":material/videocam: Hardware video feed")
    ctx = None

    if alpr is None:
        st.error(
            f"ANPR engine unavailable: {alpr_error}. Manual plate entry still works.",
            icon=":material/error:",
        )
    else:
        result_queue = st.session_state.anpr_queue
        overlay = st.session_state.anpr_overlay
        frame_counter = st.session_state.anpr_frame_counter

        def video_frame_callback(frame: av.VideoFrame) -> av.VideoFrame:
            img = frame.to_ndarray(format="bgr24")

            if next(frame_counter) % FRAME_STRIDE == 0:
                try:
                    results = alpr.predict(img)
                except Exception:
                    results = None
                if results is not None:
                    detections = []
                    for result in results:
                        text, conf = "", 0.0
                        if result.ocr is not None and result.ocr.text:
                            text = normalize_plate(result.ocr.text)
                            conf = ocr_confidence(result.ocr)
                        valid = conf >= CONFIDENCE_THRESHOLD and validate_indian_plate(text) is not None
                        if valid:
                            if result_queue.full():
                                try:
                                    result_queue.get_nowait()
                                except queue.Empty:
                                    pass
                            result_queue.put_nowait(text)
                        detections.append((result.detection.bounding_box, text, conf, valid))
                    overlay["detections"] = detections

            # Redraw the latest boxes on every frame so the overlay doesn't flicker.
            for bbox, text, conf, valid in overlay["detections"]:
                color = (36, 255, 12) if valid else (0, 200, 255)
                cv2.rectangle(img, (bbox.x1, bbox.y1), (bbox.x2, bbox.y2), color, 2)
                if text:
                    label = f"{text} {conf * 100:.0f}%"
                    org = (bbox.x1, max(bbox.y1 - 10, 20))
                    cv2.putText(img, label, org, cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 5, cv2.LINE_AA)
                    cv2.putText(img, label, org, cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2, cv2.LINE_AA)
            return av.VideoFrame.from_ndarray(img, format="bgr24")

        ctx = webrtc_streamer(
            key="anpr_stream",
            mode=WebRtcMode.SENDRECV,
            video_frame_callback=video_frame_callback,
            media_stream_constraints={"video": True, "audio": False},
            rtc_configuration={"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]},
            video_html_attrs=VideoHTMLAttributes(autoPlay=True, controls=False, style={"width": "100%"}),
            async_processing=True,
        )
        st.caption("Green box: valid plate locked for processing. Amber box: unreadable or invalid format.")

    @st.fragment(run_every="1s")
    def poll_detection_queue():
        if ctx is None or not ctx.state.playing:
            st.caption(":material/videocam_off: Camera idle.")
            return
        st.caption(":material/radar: Scanning for plates…")

        latest = None
        while True:
            try:
                candidate = result_queue.get_nowait()
            except queue.Empty:
                break
            if not is_cooling_down(candidate):
                latest = candidate
        # Never replace a vehicle the operator is still handling.
        if latest and st.session_state.detected_plate is None:
            st.session_state.detected_plate = latest
            st.rerun()

    poll_detection_queue()

# --- PROCESSING MATRIX ---
with col_sys:
    st.subheader(":material/account_balance: Processing matrix")
    plate = st.session_state.detected_plate

    if plate is None:
        st.info("Waiting for a vehicle. Start the camera or enter a plate manually.", icon=":material/hourglass_empty:")
    else:
        with st.container(border=True):
            st.markdown(f"#### :material/directions_car: Plate locked: `{plate}`")
            st.caption(f"Format: {PLATE_FORMAT_LABELS.get(classify_plate(plate), 'Unknown')}")

            blacklist_entry = cloud_db.get_blacklist_entry(plate)
            if blacklist_entry:
                st.error("**Security anomaly detected**", icon=":material/report:")
                st.markdown(f"**Infraction flag:** {blacklist_entry['Reason']}")
                challan = float(blacklist_entry.get("Pending_Challan") or 0)
                if challan:
                    st.markdown(f"**Pending challan:** ₹{challan:,.2f}")
                if st.button("Acknowledge & report", type="primary", icon=":material/policy:"):
                    cloud_db.insert_audit(f"Interception: {blacklist_entry['Reason']}", plate, plaza)
                    finish_vehicle(plate, f"{plate} reported to the security audit log.", ":material/policy:")

            else:
                lane = st.selectbox("Lane", LANES, key="lane")
                v_type = st.selectbox("Vehicle class (verify visually)", VEHICLE_CLASSES, key=f"class_{plate}")

                if is_exempt(plate):
                    st.info("**Toll-exempt category** (diplomatic / consular / UN).", icon=":material/verified:")
                    with st.container(horizontal=True):
                        grant = st.button("Log free passage & open gate", type="primary", icon=":material/lock_open:")
                        discard = st.button("Discard read", icon=":material/close:")
                    if grant:
                        cloud_db.insert_transaction(plate, v_type, lane, "Exempt", plaza, 0.0, True)
                        cloud_db.insert_audit("VIP Exemption Granted", plate, plaza)
                        finish_vehicle(plate, f"Free passage logged for {plate}.", ":material/verified:")

                else:
                    toll_amt = current_rates[v_type]
                    st.metric("Tariff", f"₹{toll_amt:,.2f}")
                    with st.container(horizontal=True):
                        pay_fastag = st.button("FASTag auto-deduct", type="primary", icon=":material/contactless:")
                        pay_cash = st.button("Process cash", icon=":material/payments:")
                        discard = st.button("Discard read", icon=":material/close:")

                    if pay_fastag:
                        # --- NPCI BANKING API MOCK ---
                        with st.spinner("Connecting to NPCI FASTag server..."):
                            time.sleep(random.uniform(0.8, 1.5))  # Mock network latency
                        # 10% chance the FASTag wallet is empty
                        if random.random() < 0.10:
                            cloud_db.insert_audit("FASTag Wallet Declined", plate, plaza)
                            st.error(
                                "FASTag declined: insufficient wallet balance. Collect cash instead.",
                                icon=":material/credit_card_off:",
                            )
                        else:
                            cloud_db.insert_transaction(plate, v_type, lane, "FASTag", plaza, toll_amt, False)
                            finish_vehicle(plate, f"₹{toll_amt:,.2f} authorised via NETC for {plate}.", ":material/check_circle:")

                    if pay_cash:
                        cloud_db.insert_transaction(plate, v_type, lane, "Cash", plaza, toll_amt, False)
                        finish_vehicle(plate, f"₹{toll_amt:,.2f} cash collected from {plate}.", ":material/payments:")

                if discard:
                    finish_vehicle(plate, f"Discarded read {plate}.", ":material/close:")

    with st.form("manual_entry", clear_on_submit=True):
        manual_text = st.text_input("Manual plate entry", placeholder="e.g. OD02AB1234")
        submitted = st.form_submit_button("Lock plate", icon=":material/keyboard:")
    if submitted:
        manual_plate = validate_indian_plate(manual_text)
        if manual_plate is None:
            st.error("Not a recognised Indian plate format.", icon=":material/error:")
        else:
            st.session_state.detected_plate = manual_plate
            st.rerun()

st.subheader("Recent transactions at this plaza")
recent_df = cloud_db.get_dataframe("transactions", plaza).head(10)
if recent_df.empty:
    st.caption("No transactions recorded at this plaza yet.")
else:
    st.dataframe(
        recent_df.drop(columns=["Plaza_Location"]),
        hide_index=True,
        column_config={"Toll_Collected": st.column_config.NumberColumn("Toll", format="₹%.2f")},
    )
