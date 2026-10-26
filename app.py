"""
Hydronic Layout Planner – Streamlit interface
=============================================

Run with:
    pip install -r requirements.txt
    streamlit run app.py

The user enters the rooms and radiators, answers a few questions about the house,
heat source, pipes, pump and prices. The planner then designs six layouts
(direct return unbalanced / balanced, Tichelmann, manifold, primary–secondary,
series loop), sizes every pipe, solves each network and ranks the layouts by
lifetime cost.
"""
from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

import engine as E
from schematic import draw

st.set_page_config(page_title="Hydronic Layout Planner", layout="wide")

st.title("Hydronic Layout Planner")
st.caption("Describe the house, answer a few questions, and compare every radiator layout by flow balance, pump energy and cost.")

# ---------------------------------------------------------------------------
# Questions (sidebar)
# ---------------------------------------------------------------------------
INSULATION = {"Not renovated, before 1980 (≈ 100 W/m²)": 100, "Average, 1980–2000 (≈ 70 W/m²)": 70,
              "Renovated or after 2000 (≈ 50 W/m²)": 50, "New, low-energy (≈ 35 W/m²)": 35}
SOURCES = {"Condensing gas boiler": "condensing", "Standard boiler": "boiler", "Heat pump": "heatpump"}
REGIMES = ["80/60", "75/65", "70/55", "55/45", "45/35"]
PIPE_LABELS = {"Steel": "steel", "Copper": "copper", "Multilayer (PEX-AL-PEX)": "multilayer"}
PUMPS = {"High-efficiency, EEI ≤ 0.23 (≈ 30 % overall)": 0.30, "Older fixed-speed (≈ 15 % overall)": 0.15}
HINT = {"condensing": "Condensing boilers save most with return water below about 55 °C.",
        "boiler": "A standard boiler does not condense; its efficiency barely depends on the return temperature.",
        "heatpump": "Heat pumps work best at low supply temperatures (45/35 °C or lower)."}

with st.sidebar:
    st.header("2 · Building")
    ins = st.selectbox("How well is the house insulated?", list(INSULATION), index=1)
    st.header("3 · Heat source")
    src_label = st.selectbox("Heat source", list(SOURCES))
    source = SOURCES[src_label]
    regime = st.selectbox("Supply / return temperature (°C)", REGIMES, index=4 if source == "heatpump" else 2)
    st.caption(HINT[source])
    gen_dp = st.number_input("Heat-source pressure drop at design flow (kPa)", 0.0, 100.0, 20.0 if source == "heatpump" else 10.0, 1.0)
    st.header("4 · Pipes")
    pipe = PIPE_LABELS[st.selectbox("Pipe material", list(PIPE_LABELS))]
    st.caption("Every pipe section is sized automatically: the smallest size within the velocity and gradient limits.")
    st.header("5 · Pump, prices and use")
    pump_eff = PUMPS[st.selectbox("Circulation pump", list(PUMPS))]
    c1, c2 = st.columns(2)
    elec = c1.number_input("Electricity €/kWh", 0.0, 2.0, 0.30, 0.01)
    fuel = c2.number_input("Gas €/kWh", 0.0, 2.0, 0.11, 0.01)
    hours = c1.number_input("Pump hours/year", 0, 8760, 4000, 100)
    flh = c2.number_input("Full-load heating hours", 0, 8760, 1600, 100)
    life = st.number_input("Life of the system (years)", 1, 60, 30, 1)
    with st.expander("Advanced design settings"):
        v_max = st.number_input("Max velocity (m/s)", 0.2, 2.0, 0.8, 0.05)
        R_max = st.number_input("Max gradient (Pa/m)", 20.0, 1000.0, 200.0, 10.0)
        allow = st.number_input("Fittings allowance (%)", 0.0, 100.0, 30.0, 5.0) / 100
        dev_tol = st.number_input("Acceptable flow deviation (%)", 1.0, 50.0, 10.0, 1.0) / 100
        trv_dp = st.number_input("TRV pressure drop at design (kPa)", 0.5, 30.0, 5.0, 0.5) * 1000
        kv_rad = st.number_input("Radiator Kv (m³/h)", 0.1, 20.0, 2.0, 0.1)
        kv_lsv = st.number_input("Lockshield Kv, open (m³/h)", 0.1, 20.0, 1.8, 0.1)
        ps_ratio = st.number_input("Primary/secondary flow ratio", 1.0, 2.0, 1.1, 0.05)

# ---------------------------------------------------------------------------
# Step 1: rooms
# ---------------------------------------------------------------------------
st.subheader("1 · Rooms and radiators")
st.caption("Distance = pipe route from the boiler to the room. Leave the heat load empty to estimate it from the floor area. "
           "An example house is loaded; edit or add rows.")
if "rooms" not in st.session_state:
    st.session_state.rooms = pd.DataFrame([{"Room": r.name, "Area (m²)": r.area, "Radiators": r.radiators,
                                            "Distance (m)": r.distance, "Heat load (W, optional)": None}
                                           for r in E.EXAMPLE_ROOMS])
rooms_df = st.data_editor(st.session_state.rooms, num_rows="dynamic", width="stretch", key="rooms_editor",
                          column_config={"Area (m²)": st.column_config.NumberColumn(min_value=1, step=1),
                                         "Radiators": st.column_config.NumberColumn(min_value=1, max_value=6, step=1),
                                         "Distance (m)": st.column_config.NumberColumn(min_value=0.5, step=0.5),
                                         "Heat load (W, optional)": st.column_config.NumberColumn(min_value=0, step=50)})
if st.button("Load example house"):
    del st.session_state["rooms"]
    st.rerun()

rooms = []
for i, row in rooms_df.iterrows():
    try:
        area = float(row["Area (m²)"]); dist = float(row["Distance (m)"])
    except (TypeError, ValueError):
        continue
    if pd.isna(area) or pd.isna(dist):
        continue
    load = row.get("Heat load (W, optional)")
    load = float(load) if load is not None and not pd.isna(load) and float(load) > 0 else None
    nrad = row["Radiators"]
    nrad = int(nrad) if nrad is not None and not pd.isna(nrad) else 1
    rooms.append(E.Room(str(row["Room"] or f"Room {i + 1}"), max(1.0, area), max(1, min(6, nrad)), max(0.5, dist), load))
if not rooms:
    st.warning("Add at least one room with an area and a distance.")
    st.stop()

T_sup, T_ret = map(float, regime.split("/"))
inp = E.Inputs(rooms=rooms, w_per_m2=INSULATION[ins], source=source, T_sup=T_sup, T_ret=T_ret, pipe=pipe, allow=allow,
               v_max=v_max, R_max=R_max, trv_dp=trv_dp, kv_rad=kv_rad, kv_lsv=kv_lsv, gen_dp=gen_dp * 1000,
               pump_eff=pump_eff, hours=hours, flh=flh, fuel_price=fuel, elec_price=elec, dev_tol=dev_tol,
               ps_ratio=ps_ratio, lifetime=life)
res = E.evaluate(inp)
opts = {o.key: o for o in res.options}
rec = opts.get(res.recommended)

# ---------------------------------------------------------------------------
# Summary and recommendation
# ---------------------------------------------------------------------------
size_f = (50 / ((T_sup + T_ret) / 2 - 20)) ** 1.3
gen = rec.econ["gen"] if rec else res.options[0].econ["gen"]
m = st.columns(6)
m[0].metric("Heat load", f"{res.phi_tot / 1000:.2f} kW")
m[1].metric("Radiators", len(res.rads))
m[2].metric("Design flow", f"{res.Q * 1000:.0f} l/h")
m[3].metric("Water", f"{T_sup:g}/{T_ret:g} °C")
m[4].metric("Radiator size vs 75/65", f"{size_f:.2f}×")
m[5].metric("Heat source", f"COP {gen['value']:.2f}" if gen["kind"] == "COP" else f"{gen['value'] * 100:.1f} %")

if rec:
    alt = next((o for o in res.options if o.acceptable and o.key != rec.key), None)
    st.success(f"**Recommended: {rec.name}.** Lowest {life}-year cost of the layouts that give every room its design flow "
               f"and individual control: pipes ≈ {rec.pipe_cost:,.0f} €, pump {rec.econ['E_pump']:.0f} kWh/year "
               f"({rec.econ['pump_cost']:.2f} €)."
               + (f" Next best: {alt.short}, {alt.lcc - rec.lcc:,.0f} € more over {life} years." if alt else ""))
else:
    st.error("No layout meets all criteria. Relax a limit in the advanced settings or change the house data.")

# ---------------------------------------------------------------------------
# Options table and chart
# ---------------------------------------------------------------------------
st.subheader("Layout options")
table = pd.DataFrame([{
    "Rank": o.rank, "Layout": o.short + ("  ✓ recommended" if o.key == res.recommended else ""),
    "Pump head (kPa)": round(o.head_kpa, 1), "Pumps": len(o.pumps),
    "Pump energy (kWh/a)": round(o.econ["E_pump"], 1), "Pump power (W)": round(o.econ["P_el"], 1),
    "Max flow deviation (%)": round(o.max_dev * 100, 1), "Room control": "yes" if o.control else "no",
    "Pipe length (m)": round(o.pipe_length), "Pipe cost (€)": round(o.pipe_cost),
    f"{life}-yr distribution cost (€)": round(o.pipe_cost + o.econ["pump_cost"] * life),
    f"{life}-yr total cost (€)": round(o.lcc), "Issues": "; ".join(o.issues) or "–"} for o in res.options])
st.dataframe(table, hide_index=True, width="stretch")

fig, ax = plt.subplots(figsize=(9, 0.55 * len(res.options) + 0.8))
names = [o.short for o in res.options][::-1]
pipes = [o.pipe_cost for o in res.options][::-1]
pumpc = [o.econ["pump_cost"] * life for o in res.options][::-1]
ax.barh(names, pipes, color="#9a6418", label="Pipes (indicative)")
ax.barh(names, pumpc, left=pipes, color="#2459b8", label=f"Pump electricity, {life} years")
ax.set_xlabel("€"); ax.legend(loc="lower right", fontsize=8, frameon=False)
ax.spines[["top", "right"]].set_visible(False); ax.grid(axis="x", alpha=0.3)
ax.set_title(f"{life}-year distribution cost (heating fuel excluded; it is almost the same for all layouts)", fontsize=9, loc="left")
fig.tight_layout()
st.pyplot(fig)
plt.close(fig)

# ---------------------------------------------------------------------------
# Selected option
# ---------------------------------------------------------------------------
labels = {o.key: f"{o.rank}. {o.short}" for o in res.options}
keys = [o.key for o in res.options]
sel = st.selectbox("Show the design of", keys, index=keys.index(res.recommended) if res.recommended else 0,
                   format_func=lambda k: labels[k])
o = opts[sel]
st.subheader(o.name)
f2 = draw(o, res, source)
st.pyplot(f2)
plt.close(f2)

cols = st.columns(len(o.pumps) + 3)
for i, p in enumerate(o.pumps):
    cols[i].metric(p["name"], f"{p['dp'] / 1000:.1f} kPa", f"{p['Q'] * 1000:.0f} l/h", delta_color="off")
cols[len(o.pumps)].metric("Pump electricity", f"{o.econ['P_el']:.1f} W", f"{o.econ['E_pump']:.0f} kWh/a", delta_color="off")
g = o.econ["gen"]
cols[len(o.pumps) + 1].metric("Running cost per year", f"{o.econ['total']:,.0f} €",
                              f"heat {o.econ['heat_cost']:,.0f} € + pump {o.econ['pump_cost']:.2f} €", delta_color="off")
cols[len(o.pumps) + 2].metric("Highest velocity", f"{o.v_max:.2f} m/s")
for note in o.notes:
    st.markdown(f"- {note}")

rad_rows = []
for k, r in enumerate(res.rads):
    row = {"Radiator": r.id, "Room": r.room, "Heat (W)": round(r.phi), "Design flow (l/h)": round(r.qd * 1000, 1),
           ("Loop flow (l/h)" if o.family == "series" else "Actual flow (l/h)"): round(o.q[k] * 1000, 1)}
    if o.family == "series":
        row["Water in → out (°C)"] = f"{o.temps[k]['T_in']:.1f} → {o.temps[k]['T_out']:.1f}"
        row["Size factor"] = round(o.temps[k]["factor"], 2)
    else:
        row["Deviation (%)"] = round((o.q[k] - r.qd) / r.qd * 100, 2)
        row["TRV Kv"] = round(r.kv_trv, 3)
        row["Lockshield Kv"] = f"{o.lsv[k]:.3f}" if o.lsv else f"open {kv_lsv:g}"
    if o.authority:
        row["TRV authority"] = round(o.authority[k], 2)
    rad_rows.append(row)
rad_df = pd.DataFrame(rad_rows)
st.dataframe(rad_df, hide_index=True, width="stretch")
if o.q_natural:
    worst = max(abs(x - res.qd[k]) / res.qd[k] for k, x in enumerate(o.q_natural)) * 100
    st.caption(f"Without {'flow setters' if o.family == 'manifold' else 'lockshield presets'} the flows would deviate up to {worst:.1f} % from design.")

pipe_df = pd.DataFrame([{"Pipe section": s.name + (" (supply + return)" if s.mult == 2 else ""), "Length (m)": round(s.L, 1),
                         "Size": s.size, "Flow (l/h)": round(s.Q * 1000), "Velocity (m/s)": round(s.v, 2),
                         "Gradient (Pa/m)": round(s.R)} for s in o.sections])
st.dataframe(pipe_df, hide_index=True, width="stretch")
d1, d2 = st.columns(2)
d1.download_button("Download radiator table (CSV)", rad_df.to_csv(index=False).encode("utf-8"), f"{o.key}_radiators.csv", "text/csv")
d2.download_button("Download pipe table (CSV)", pipe_df.to_csv(index=False).encode("utf-8"), f"{o.key}_pipes.csv", "text/csv")

# ---------------------------------------------------------------------------
# Improvements
# ---------------------------------------------------------------------------
st.subheader("Ways to improve this design")
imp = E.improvements(inp, o.key)
st.dataframe(pd.DataFrame([{"Change": x["label"], "Saving per year (€)": round(x["save"], 2),
                            "Extra cost (€)": f"{x['cost']:,.0f}" if x["cost"] > 0 else "depends",
                            "Payback (years)": f"{x['payback']:,.0f}" if x["payback"] else "–", "What it means": x["detail"]}
                           for x in imp]), hide_index=True, width="stretch")
st.caption("Savings are recalculated with the same model. Pipe costs are indicative; the heat-source efficiency is a simple "
           "seasonal estimate (condensing boiler 88–98 % by return temperature; heat-pump COP from supply temperature).")

# ---------------------------------------------------------------------------
# Method and validation
# ---------------------------------------------------------------------------
with st.expander("Method and check against the thesis spreadsheets", expanded=False):
    st.markdown("Each layout is built from the radiator positions, every pipe is sized, and the network is solved with the same "
                "equations as the thesis spreadsheets: Darcy–Weisbach losses with f = 0.07/(Re^0.13·D^0.14) for steel or Blasius "
                "for smooth pipes, valve losses from Kv, series–parallel reduction for direct return and loop pressure balance for "
                "reverse return. Water properties are taken at the mean water temperature.")
    val = pd.DataFrame(E.thesis_validation())
    val["match"] = val["difference"].abs() < 0.1
    st.dataframe(val.rename(columns={"model": "Thesis model", "excel": "Excel (Pa)", "tool": "This tool (Pa)", "difference": "Difference (Pa)"}),
                 hide_index=True, width="stretch")
