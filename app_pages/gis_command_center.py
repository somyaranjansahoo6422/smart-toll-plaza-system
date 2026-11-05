import pandas as pd
import plotly.express as px
import streamlit as st

from auth import ROLE_DIRECTOR, require_role
from database import PLAZA_REGISTRY_FILE

# --- RBAC SECURITY PROTOCOL ---
require_role(ROLE_DIRECTOR)

INDIA_CENTER = {"lat": 22.9734, "lon": 78.6568}
# Generous bounding box around India; anything outside is a data-entry error.
INDIA_BOUNDS = {"lat": (6.0, 37.6), "lon": (68.0, 97.5)}
SUB_TYPE_COLORS = {"NATIONAL": "#0080FF", "STATE": "#FF8C00"}
TEXT_COLUMNS = ["PLAZA NAME", "CONCESSIONAIRE TYPE", "PLAZA SUB TYPE", "STATE", "CITY"]


def normalize_authority(raw):
    """The registry spells the same authority many ways (Conc., CONC, Concessio, PF., Public Fun...)."""
    key = str(raw).strip().upper().rstrip(".")
    if key.startswith("CONC"):
        return "Concessionaire"
    if key == "PF" or key.startswith("PUBLIC"):
        return "Public funded"
    if key in {"BOT", "TOT", "DBFOT", "NHAI"}:
        return key
    return key.title()


# --- HIGH-PERFORMANCE DATA LOADER ---
@st.cache_data(ttl=3600, show_spinner="Initializing GIS spatial engine...")
def load_national_plazas(path, modified_time):
    # modified_time is only part of the cache key, so editing the CSV invalidates the cache.
    df = pd.read_csv(path)
    df.columns = df.columns.str.strip()
    for col in TEXT_COLUMNS:
        df[col] = df[col].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    df["PLAZA CODE"] = df["PLAZA CODE"].astype(str).str.strip()
    df["CONCESSIONAIRE TYPE"] = df["CONCESSIONAIRE TYPE"].map(normalize_authority)
    df["PLAZA SUB TYPE"] = df["PLAZA SUB TYPE"].str.upper()

    df["LATITUDE"] = pd.to_numeric(df["LATITUDE"], errors="coerce")
    df["LONGITUDE"] = pd.to_numeric(df["LONGITUDE"], errors="coerce")
    in_india = df["LATITUDE"].between(*INDIA_BOUNDS["lat"]) & df["LONGITUDE"].between(*INDIA_BOUNDS["lon"])
    dropped = int((~in_india).sum())
    df = df[in_india].reset_index(drop=True)

    # Pre-compile HTML hover data for high performance
    df["Hover_Text"] = (
        "<b>" + df["PLAZA NAME"] + "</b><br>"
        + "Code: " + df["PLAZA CODE"] + "<br>"
        + "Location: " + df["CITY"] + ", " + df["STATE"] + "<br>"
        + "Authority: " + df["CONCESSIONAIRE TYPE"]
    )
    return df, dropped


st.title("Pan-India GIS infrastructure matrix")
st.caption("Geographical tracking of national and state highway toll nodes.")

if not PLAZA_REGISTRY_FILE.exists():
    st.error(f"Could not locate registry data `{PLAZA_REGISTRY_FILE.name}`.", icon=":material/error:")
    st.stop()

try:
    plaza_df, dropped_rows = load_national_plazas(str(PLAZA_REGISTRY_FILE), PLAZA_REGISTRY_FILE.stat().st_mtime)
except Exception as exc:
    st.error(f"Registry data `{PLAZA_REGISTRY_FILE.name}` could not be parsed: {exc}", icon=":material/error:")
    st.stop()

if dropped_rows:
    st.caption(f":material/warning: {dropped_rows} registry rows skipped due to invalid coordinates.")

# --- INTERACTIVE CONTROL PANEL ---
with st.container(border=True):
    f1, f2, f3, f4 = st.columns([1.2, 1.5, 1, 1.5])
    search = f1.text_input("Search plaza, city or code", placeholder="e.g. Manguli", key="gis_search")
    selected_states = f2.multiselect(
        "Regional hubs", sorted(plaza_df["STATE"].unique()), placeholder="All-India (default)", key="gis_states"
    )
    selected_types = f3.pills(
        "Highway class", sorted(plaza_df["PLAZA SUB TYPE"].unique()), selection_mode="multi", key="gis_types"
    )
    selected_authorities = f4.multiselect(
        "Operating authority", sorted(plaza_df["CONCESSIONAIRE TYPE"].unique()),
        placeholder="All authorities", key="gis_authorities",
    )

filtered_df = plaza_df
if selected_states:
    filtered_df = filtered_df[filtered_df["STATE"].isin(selected_states)]
if selected_types:
    filtered_df = filtered_df[filtered_df["PLAZA SUB TYPE"].isin(selected_types)]
if selected_authorities:
    filtered_df = filtered_df[filtered_df["CONCESSIONAIRE TYPE"].isin(selected_authorities)]
if search.strip():
    needle = search.strip()
    filtered_df = filtered_df[
        filtered_df["PLAZA NAME"].str.contains(needle, case=False, regex=False)
        | filtered_df["CITY"].str.contains(needle, case=False, regex=False)
        | filtered_df["PLAZA CODE"].str.contains(needle, case=False, regex=False)
    ]
filtered_df = filtered_df.reset_index(drop=True)

if filtered_df.empty:
    st.warning("No toll plazas match the current filters.", icon=":material/search_off:")
    st.stop()

if selected_states or search.strip():
    center = {"lat": filtered_df["LATITUDE"].mean(), "lon": filtered_df["LONGITUDE"].mean()}
    zoom_level = 5.5 if len(filtered_df) > 1 else 9
else:
    center, zoom_level = INDIA_CENTER, 4.2

# --- GIS MAP ENGINE ---
fig = px.scatter_map(
    filtered_df,
    lat="LATITUDE",
    lon="LONGITUDE",
    color="PLAZA SUB TYPE",
    color_discrete_map=SUB_TYPE_COLORS,
    custom_data=["PLAZA CODE", "Hover_Text"],
    zoom=zoom_level,
    center=center,
    height=650,
    map_style="carto-positron",
)
fig.update_traces(marker=dict(size=9, opacity=0.9), hovertemplate="%{customdata[1]}<extra></extra>")
fig.update_layout(
    margin={"r": 0, "t": 0, "l": 0, "b": 0},
    legend=dict(title="Highway class", yanchor="top", y=0.98, xanchor="left", x=0.01),
    hoverlabel=dict(bgcolor="#1E1E1E", font_size=14),
    # Keep the user's pan/zoom across reruns until the region filter changes.
    uirevision=str((tuple(selected_states), search.strip())),
)

map_event = st.plotly_chart(
    fig,
    key="gis_infrastructure_map",
    on_select="rerun",
    selection_mode="points",
    config={"scrollZoom": True},
)

points = map_event.selection.points if map_event else []
if points:
    point = points[0]
    customdata = point.get("customdata")
    if customdata:
        code = str(customdata[0])
    else:
        code = str(fig.data[point["curve_number"]].customdata[point["point_index"]][0])
    st.session_state.gis_selected_code = code

# --- DYNAMIC OPERATIONAL DATA DISPLAY ---
selected_code = st.session_state.get("gis_selected_code")
selected_rows = plaza_df[plaza_df["PLAZA CODE"] == selected_code] if selected_code else plaza_df.iloc[0:0]

if selected_rows.empty:
    st.info(
        "**Operator guidance:** click any toll plaza dot on the map to open its asset profile card.",
        icon=":material/touch_app:",
    )
else:
    node = selected_rows.iloc[0]
    with st.container(border=True):
        st.subheader(f":material/location_on: {node['PLAZA NAME']}")
        c1, c2, c3 = st.columns(3)
        c1.markdown(f"**Plaza registry code**  \n`{node['PLAZA CODE']}`")
        c1.markdown(f"**Administrative state**  \n{node['STATE']}")
        c2.markdown(f"**District / city node**  \n{node['CITY']}")
        c2.markdown(f"**Concession type**  \n{node['CONCESSIONAIRE TYPE']}")
        c3.markdown(f"**Highway class**  \n{node['PLAZA SUB TYPE']}")
        c3.markdown(f"**GPS coordinates**  \n{node['LATITUDE']:.6f}, {node['LONGITUDE']:.6f}")
        with st.container(horizontal=True):
            st.link_button(
                "Open in Google Maps",
                f"https://www.google.com/maps/search/?api=1&query={node['LATITUDE']},{node['LONGITUDE']}",
                icon=":material/open_in_new:",
            )
            if st.button("Clear selection", icon=":material/close:"):
                del st.session_state["gis_selected_code"]
                st.rerun()

# --- DASHBOARD ---
st.divider()
national_share = (filtered_df["PLAZA SUB TYPE"] == "NATIONAL").mean() * 100
with st.container(horizontal=True):
    st.metric("Toll plazas shown", f"{len(filtered_df):,}", border=True)
    st.metric("States / UTs", f"{filtered_df['STATE'].nunique()}", border=True)
    st.metric("Districts / cities", f"{filtered_df['CITY'].nunique():,}", border=True)
    st.metric("On national highways", f"{national_share:.0f}%", border=True)

with st.expander("Plazas by state"):
    by_state = filtered_df.groupby("STATE").size().rename("Plazas").sort_values(ascending=False).reset_index()
    st.bar_chart(by_state, x="STATE", y="Plazas", horizontal=True, sort="-Plazas", height=max(250, 22 * len(by_state)))

with st.expander("Raw infrastructure ledger"):
    display_df = filtered_df.drop(columns=["Hover_Text"])
    st.dataframe(display_df, hide_index=True, height=300)
    st.download_button(
        "Download filtered CSV",
        display_df.to_csv(index=False).encode("utf-8"),
        file_name="toll_plazas_filtered.csv",
        mime="text/csv",
        icon=":material/download:",
    )
