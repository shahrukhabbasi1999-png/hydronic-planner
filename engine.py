"""
Hydronic layout engine
======================

Calculation core of the Hydronic Layout Planner. It uses the same equations as the
thesis Excel models:

* Darcy–Weisbach pipe losses written as  Δp = J·Q²  (Rossato's J-coefficient method)
* friction factor  f = 0.07 / (Re^0.13 · D^0.14)  for steel (medium roughness, Rossato)
  or Blasius  f = 0.316 / Re^0.25  for smooth pipes; laminar 64/Re below Re = 2300,
  linear blend between Re = 2300 and 4000
* valves and radiators from their flow coefficient:  J = 10^5 / Kv²
* direct return solved by series–parallel reduction (Rossato), friction refreshed each iteration
* reverse return (Tichelmann) solved by a fixed-point iteration on the loop pressure balances
* static balancing at design flow: pump Δp = most demanding path, lockshield presettings
  absorb the excess pressure in all other branches

Units used throughout:  Q in m³/h,  Δp in Pa,  J in Pa/(m³/h)²,  lengths in m,  diameters in m.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Water properties (saturated liquid; IAPWS-95 density, IAPWS-2008 viscosity)
# ---------------------------------------------------------------------------
_PROPS = [  # T °C, rho kg/m³, mu µPa·s, cp J/(kg·K)
    (40, 992.22, 653.0, 4179), (50, 988.03, 547.0, 4181), (60, 983.20, 466.5, 4185),
    (70, 977.76, 404.0, 4190), (80, 971.79, 354.4, 4197), (90, 965.31, 314.5, 4205),
]


@dataclass
class Fluid:
    rho: float           # kg/m³
    mu: float            # Pa·s
    cp: float            # J/(kg·K)
    model: str = "steel"  # friction correlation: "steel" or "smooth"
    allow: float = 0.0   # fittings allowance as a fraction of pipe length

    @property
    def nu(self) -> float:
        return self.mu / self.rho


def water_properties(T: float) -> Dict[str, float]:
    """Linear interpolation in the property table, clamped to 40–90 °C."""
    t = min(90.0, max(40.0, T))
    i = 0
    while i < len(_PROPS) - 2 and _PROPS[i + 1][0] <= t:
        i += 1
    a, b = _PROPS[i], _PROPS[i + 1]
    w = (t - a[0]) / (b[0] - a[0])
    return {"rho": a[1] + w * (b[1] - a[1]), "mu": (a[2] + w * (b[2] - a[2])) * 1e-6, "cp": a[3] + w * (b[3] - a[3])}


# Pipe catalogue: (nominal size, internal diameter m, indicative installed cost €/m)
PIPES: Dict[str, Dict] = {
    "steel": {"label": "Steel (medium series)", "model": "steel",
              "sizes": [("DN10", 0.0125, 14), ("DN15", 0.0158, 16), ("DN20", 0.0216, 20),
                        ("DN25", 0.0273, 26), ("DN32", 0.0359, 33), ("DN40", 0.0419, 40)]},
    "copper": {"label": "Copper", "model": "smooth",
               "sizes": [("15×1", 0.013, 12), ("18×1", 0.016, 15), ("22×1", 0.020, 19),
                         ("28×1.5", 0.025, 27), ("35×1.5", 0.032, 36), ("42×1.5", 0.039, 46)]},
    "multilayer": {"label": "Multilayer (PEX-AL-PEX)", "model": "smooth",
                   "sizes": [("16×2", 0.012, 6), ("20×2", 0.016, 8), ("26×3", 0.020, 12),
                             ("32×3", 0.026, 17), ("40×3.5", 0.033, 24)]},
}

# ---------------------------------------------------------------------------
# Elementary relations (thesis Chapter 2)
# ---------------------------------------------------------------------------


def reynolds(Q: float, D: float, c: Fluid) -> float:
    return 4 * abs(Q) / 3600 / (math.pi * D * c.nu)


def _f_turbulent(Re: float, D: float, model: str) -> float:
    if model == "steel":
        return 0.07 / (Re ** 0.13 * D ** 0.14)
    return 0.316 / Re ** 0.25


def friction(Q: float, D: float, c: Fluid) -> float:
    Re = reynolds(Q, D, c)
    if Re < 1e-9:
        return 0.0
    if Re < 2300:
        return 64 / Re
    if Re > 4000:
        return _f_turbulent(Re, D, c.model)
    return 64 / 2300 + (Re - 2300) / 1700 * (_f_turbulent(4000, D, c.model) - 64 / 2300)


def J_pipe(Q: float, L: float, D: float, c: Fluid, allow: Optional[float] = None) -> float:
    """Pipe resistance coefficient J = 8 f L ρ / (π² D⁵) / 3600²  [Pa/(m³/h)²]."""
    if L <= 0:
        return 0.0
    a = c.allow if allow is None else allow
    return 8 * friction(Q, D, c) * L * (1 + a) * c.rho / (math.pi ** 2 * D ** 5) / 3600 ** 2


def J_kv(Kv: Optional[float]) -> float:
    """Valve / radiator resistance from its flow coefficient: J = 10^5 / Kv²."""
    if Kv is None or not math.isfinite(Kv) or Kv <= 0:
        return 0.0
    return 1e5 / Kv ** 2


def velocity(Q: float, D: float) -> float:
    return Q / 3600 / (math.pi * D * D / 4)


def gradient(Q: float, D: float, c: Fluid) -> float:
    """Friction pressure gradient without fittings allowance [Pa/m]."""
    return J_pipe(Q, 1.0, D, c, allow=0.0) * Q * Q


# ---------------------------------------------------------------------------
# Network elements and solvers (any number of radiators)
# ---------------------------------------------------------------------------


@dataclass
class Pipe:
    L: float
    D: float
    mult: int = 1   # 2 = supply + return pipe of the same length and size


@dataclass
class Branch:
    tails: List[Pipe]
    kv: List[float]   # e.g. [Kv_TRV, Kv_radiator, Kv_lockshield]


def J_branch(b: Branch, q: float, c: Fluid) -> float:
    return sum(J_pipe(q, t.L, t.D, c) for t in b.tails) + sum(J_kv(k) for k in b.kv)


def J_main(m: Pipe, Q: float, c: Fluid) -> float:
    return m.mult * J_pipe(Q, m.L, m.D, c)


def solve_direct(mains: List[Pipe], branches: List[Branch], Q: float, c: Fluid, q_init=None):
    """Direct return with fixed total flow Q. mains[k] is the main section upstream of branch k.
    Series–parallel reduction from the far end (Rossato), repeated until the flows stop changing."""
    n = len(branches)
    q = list(q_init) if q_init else [Q / n] * n
    Jd1 = 0.0
    for it in range(200):
        Qm, s = [], Q
        for k in range(n):
            Qm.append(s)
            s -= q[k]
        Jm = [J_main(mains[k], Qm[k], c) for k in range(n)]
        Jb = [J_branch(branches[k], q[k], c) for k in range(n)]
        Jdown, Jpar = [0.0] * n, [0.0] * n
        Jdown[-1] = Jm[-1] + Jb[-1]
        Jpar[-1] = Jb[-1]
        for k in range(n - 2, -1, -1):
            Jpar[k] = 1 / (1 / math.sqrt(Jb[k]) + 1 / math.sqrt(Jdown[k + 1])) ** 2
            Jdown[k] = Jm[k] + Jpar[k]
        nq, rest = [0.0] * n, Q
        for k in range(n - 1):
            dp = Jpar[k] * rest * rest
            nq[k] = math.sqrt(dp / Jb[k])
            rest -= nq[k]
        nq[-1] = rest
        chg = max(abs(a - b) for a, b in zip(nq, q))
        q, Jd1 = nq, Jdown[0]
        if chg < 1e-13:
            break
    return {"q": q, "dp_net": Jd1 * Q * Q, "iterations": it + 1}


def paths_direct(mains, branches, q, Q, c) -> List[float]:
    """Pressure drop along each pump→radiator→pump path (equal in a solved network)."""
    out, cum, s = [], 0.0, Q
    for k, b in enumerate(branches):
        cum += J_main(mains[k], s, c) * s * s
        out.append(cum + J_branch(b, q[k], c) * q[k] ** 2)
        s -= q[k]
    return out


def balance_direct(mains, branches, qd, c, lsv_index=2):
    """Static balancing at design flows: pump Δp = max path; lockshield Kv for every branch."""
    Q, path, cum_arr, cum, s = sum(qd), [], [], 0.0, sum(qd)
    for k, b in enumerate(branches):
        cum += J_main(mains[k], s, c) * s * s
        cum_arr.append(cum)
        path.append(cum + J_branch(b, qd[k], c) * qd[k] ** 2)
        s -= qd[k]
    P = max(path)
    kv_set = [math.sqrt(1e5 / (J_kv(b.kv[lsv_index]) + (P - path[k]) / qd[k] ** 2)) for k, b in enumerate(branches)]
    return {"dp_net": P, "critical": path.index(P), "kv_set": kv_set, "path": path, "avail": [P - x for x in cum_arr]}


def paths_reverse(sup, ret, branches, q, Q, c) -> List[float]:
    """Reverse return: sup[k] before branch k carries Q − Σ_{j<k} q_j;
    ret[k] after branch k carries Σ_{j≤k} q_j, the last one returns the total flow."""
    n = len(branches)
    P, R, s, r = [], [], Q, 0.0
    for k in range(n):
        P.append(J_main(sup[k], s, c) * s * s)
        s -= q[k]
    for k in range(n):
        r += q[k]
        f = Q if k == n - 1 else r
        R.append(J_main(ret[k], f, c) * f * f)
    return [sum(P[:i + 1]) + J_branch(branches[i], q[i], c) * q[i] ** 2 + sum(R[i:]) for i in range(n)]


def solve_reverse(sup, ret, branches, Q, c, q_init=None):
    n = len(branches)
    q = list(q_init) if q_init else [Q / n] * n
    for it in range(2000):
        p = paths_reverse(sup, ret, branches, q, Q, c)
        m = sum(p) / n
        nq = [x * math.sqrt(m / p[i]) for i, x in enumerate(q)]
        s = sum(nq)
        nq = [x * Q / s for x in nq]
        chg = max(abs(a - b) for a, b in zip(nq, q))
        q = nq
        if chg < 1e-14:
            break
    p = paths_reverse(sup, ret, branches, q, Q, c)
    return {"q": q, "dp_net": sum(p) / n, "paths": p, "iterations": it + 1}


def balance_reverse(sup, ret, branches, qd, c, lsv_index=2):
    Q = sum(qd)
    path = paths_reverse(sup, ret, branches, qd, Q, c)
    P = max(path)
    kv_set = [math.sqrt(1e5 / (J_kv(b.kv[lsv_index]) + (P - path[k]) / qd[k] ** 2)) for k, b in enumerate(branches)]
    return {"dp_net": P, "kv_set": kv_set, "path": path}


def solve_manifold(branches, Q, c):
    """All branches in parallel between manifold supply and return."""
    n = len(branches)
    q, dp = [Q / n] * n, 0.0
    for _ in range(200):
        J = [J_branch(b, q[k], c) for k, b in enumerate(branches)]
        S = sum(1 / math.sqrt(j) for j in J)
        dp = (Q / S) ** 2
        nq = [math.sqrt(dp / j) for j in J]
        chg = max(abs(a - b) for a, b in zip(nq, q))
        q = nq
        if chg < 1e-14:
            break
    return {"q": q, "dp_net": dp}


# ---------------------------------------------------------------------------
# Heat-source efficiency (indicative, for running-cost comparison)
# ---------------------------------------------------------------------------


def generator_efficiency(source: str, T_sup: float, T_ret_gen: float) -> Dict[str, float]:
    if source == "heatpump":
        return {"kind": "COP", "value": min(5.5, 0.45 * (T_sup + 273.15) / max(5.0, T_sup - 7))}
    if source == "condensing":
        if T_ret_gen <= 30:
            e = 0.98
        elif T_ret_gen <= 55:
            e = 0.98 - 0.09 * (T_ret_gen - 30) / 25
        else:
            e = 0.88
        return {"kind": "eff", "value": e}
    return {"kind": "eff", "value": 0.88}


# ---------------------------------------------------------------------------
# House description → layout options
# ---------------------------------------------------------------------------


@dataclass
class Room:
    name: str
    area: float          # m²
    radiators: int = 1
    distance: float = 3  # pipe route from the boiler, m
    load_w: Optional[float] = None   # optional known heat load; otherwise area × W/m²


@dataclass
class Inputs:
    rooms: List[Room]
    w_per_m2: float = 70
    source: str = "condensing"        # "condensing", "boiler", "heatpump"
    T_sup: float = 70
    T_ret: float = 55
    pipe: str = "steel"               # "steel", "copper", "multilayer"
    allow: float = 0.30               # fittings allowance
    v_max: float = 0.8                # m/s
    R_max: float = 200                # Pa/m
    trv_dp: float = 5000              # Pa, TRV pressure drop at design flow (presettable TRV)
    kv_rad: float = 2.0
    kv_lsv: float = 1.8               # lockshield fully open
    gen_dp: float = 10000             # Pa, heat-source pressure drop at design flow
    pump_eff: float = 0.30
    hours: float = 4000               # pump operating hours per year
    flh: float = 1600                 # full-load heating hours per year
    fuel_price: float = 0.11          # €/kWh gas
    elec_price: float = 0.30          # €/kWh
    dev_tol: float = 0.10
    ps_ratio: float = 1.10
    lifetime: float = 30              # years
    upsize: int = 0                   # extra pipe-size steps on mains (used by "improvements")


@dataclass
class Radiator:
    id: str
    room: str
    phi: float      # W
    d: float        # m from boiler
    qd: float = 0.0  # design flow m³/h
    kv_trv: float = 0.0


@dataclass
class Section:
    name: str
    L: float
    size: str
    D: float
    Q: float
    v: float
    R: float
    mult: int
    cost: float


@dataclass
class Option:
    key: str
    name: str
    short: str
    family: str
    q: List[float]
    pumps: List[Dict]
    sections: List[Section]
    control: bool
    residual: float
    econ: Dict
    notes: List[str]
    lsv: Optional[List[float]] = None
    q_natural: Optional[List[float]] = None
    authority: Optional[List[float]] = None
    temps: Optional[List[Dict]] = None
    critical: Optional[str] = None
    # filled in by the assessment
    v_max: float = 0.0
    R_max: float = 0.0
    pipe_length: float = 0.0
    pipe_cost: float = 0.0
    max_dev: float = 0.0
    head_kpa: float = 0.0
    issues: List[str] = field(default_factory=list)
    acceptable: bool = True
    lcc: float = 0.0
    lcc_index: float = 1.0
    rank: int = 0


def radiators_from_rooms(rooms: List[Room], w_per_m2: float) -> List[Radiator]:
    rads = []
    for ri, r in enumerate(rooms):
        nr = max(1, int(round(r.radiators or 1)))
        load = r.load_w if (r.load_w and r.load_w > 0) else r.area * w_per_m2
        for j in range(nr):
            rads.append(Radiator(id="", room=r.name or f"Room {ri + 1}", phi=load / nr, d=max(0.5, r.distance) + 1.5 * j))
    rads.sort(key=lambda x: x.d)
    for i, x in enumerate(rads):
        x.id = f"R{i + 1}"
    return rads


def pick_size(Q, c, pipe_set, v_max, R_max):
    for s in pipe_set["sizes"]:
        if velocity(Q, s[1]) <= v_max and gradient(Q, s[1], c) <= R_max:
            return s
    return pipe_set["sizes"][-1]


def bump_size(s, pipe_set, steps):
    names = [x[0] for x in pipe_set["sizes"]]
    i = names.index(s[0])
    return pipe_set["sizes"][min(len(names) - 1, i + steps)]


@dataclass
class Result:
    rads: List[Radiator]
    qd: List[float]
    Q: float
    phi_tot: float
    fluid: Fluid
    options: List[Option]
    recommended: Optional[str]
    T_mean: float


def evaluate(inp: Inputs) -> Result:
    """Build and solve every layout option for the house and rank them by lifetime cost."""
    pipe_set = PIPES[inp.pipe]
    T_mean = (inp.T_sup + inp.T_ret) / 2
    pr = water_properties(T_mean)
    c = Fluid(rho=pr["rho"], mu=pr["mu"], cp=pr["cp"], model=pipe_set["model"], allow=inp.allow)
    rads = radiators_from_rooms(inp.rooms, inp.w_per_m2)
    n, dT = len(rads), inp.T_sup - inp.T_ret
    for r in rads:
        r.qd = r.phi * 3600 / (c.rho * c.cp * dT)
    qd = [r.qd for r in rads]
    Q, phi_tot = sum(qd), sum(r.phi for r in rads)

    def size(Qx):
        return bump_size(pick_size(Qx, c, pipe_set, inp.v_max, inp.R_max), pipe_set, inp.upsize)

    Jgen = inp.gen_dp / (Q * Q)
    tail = [pick_size(r.qd, c, pipe_set, inp.v_max, inp.R_max) for r in rads]
    for k, r in enumerate(rads):
        r.kv_trv = r.qd / math.sqrt(inp.trv_dp / 1e5)
    LSV = 2

    def branch_of(k):
        return Branch(tails=[Pipe(1, tail[k][1]), Pipe(1, tail[k][1])], kv=[rads[k].kv_trv, inp.kv_rad, inp.kv_lsv])

    def econ(pumps, T_ret_gen, extra=None):
        P_hyd = sum(p["Q"] / 3600 * p["dp"] for p in pumps)
        P_el = P_hyd / inp.pump_eff
        E_pump = P_el * inp.hours / 1000
        gen = generator_efficiency(inp.source, inp.T_sup, T_ret_gen)
        E_heat = phi_tot / 1000 * inp.flh
        E_fuel = E_heat / gen["value"]
        heat_cost = E_fuel * (inp.elec_price if gen["kind"] == "COP" else inp.fuel_price)
        pump_cost = E_pump * inp.elec_price
        d = {"P_hyd": P_hyd, "P_el": P_el, "E_pump": E_pump, "pump_cost": pump_cost, "heat_cost": heat_cost,
             "gen": gen, "E_heat": E_heat, "E_fuel": E_fuel, "total": heat_cost + pump_cost}
        d.update(extra or {})
        return d

    def sec(name, L, s, Qx, mult):
        return Section(name, L, s[0], s[1], Qx, velocity(Qx, s[1]), gradient(Qx, s[1], c), mult, L * mult * s[2])

    def tails_info():
        return [sec(f"{r.id} connections", 1, tail[k], r.qd, 2) for k, r in enumerate(rads)]

    options: List[Option] = []

    # --- direct-return geometry ---
    mains, main_info, prev, s = [], [], 0.0, Q
    for k, r in enumerate(rads):
        L, z = r.d - prev, size(s)
        mains.append(Pipe(L, z[1], 2))
        main_info.append(sec(f"Main {'boiler' if k == 0 else rads[k - 1].id} → {r.id}", L, z, s, 2))
        prev, s = r.d, s - r.qd
    branches = [branch_of(k) for k in range(n)]

    # A) direct return, unbalanced
    sol = solve_direct(mains, branches, Q, c, qd)
    paths = paths_direct(mains, branches, sol["q"], Q, c)
    dp = sol["dp_net"] + Jgen * Q * Q
    options.append(Option("dr-unbal", "Two-pipe, direct return (not balanced)", "Direct return, unbalanced", "direct",
                          sol["q"], [{"name": "Circulation pump", "Q": Q, "dp": dp}], main_info + tails_info(), True,
                          max(paths) - min(paths), econ([{"Q": Q, "dp": dp}], inp.T_ret),
                          ["Lockshield valves left fully open, as often found in practice.",
                           "Radiators near the boiler take more water than they need, the far ones less."]))

    # B) direct return, balanced
    bal = balance_direct(mains, branches, qd, c, LSV)
    kvb = [Branch(branches[k].tails, [rads[k].kv_trv, inp.kv_rad, bal["kv_set"][k]]) for k in range(n)]
    chk = solve_direct(mains, kvb, Q, c, qd)
    dp = bal["dp_net"] + Jgen * Q * Q
    options.append(Option("dr-bal", "Two-pipe, direct return, balanced", "Direct return, balanced", "direct",
                          chk["q"], [{"name": "Circulation pump", "Q": Q, "dp": dp}], main_info + tails_info(), True,
                          max(abs(x - qd[k]) for k, x in enumerate(chk["q"])), econ([{"Q": Q, "dp": dp}], inp.T_ret),
                          [f"Lockshields preset (Kv values in the radiator table); index radiator {rads[bal['critical']].id}.",
                           "The most common and cheapest correct design."],
                          lsv=bal["kv_set"], critical=rads[bal["critical"]].id,
                          authority=[J_kv(rads[k].kv_trv) * qd[k] ** 2 / bal["avail"][k] for k in range(n)]))

    # C) reverse return (Tichelmann)
    sup, ret, info, prev, s, acc = [], [], [], 0.0, Q, 0.0
    for k, r in enumerate(rads):
        z = size(s)
        sup.append(Pipe(r.d - prev, z[1], 1))
        info.append(sec(f"Supply {'boiler' if k == 0 else rads[k - 1].id} → {r.id}", r.d - prev, z, s, 1))
        prev, s = r.d, s - r.qd
    for k, r in enumerate(rads):
        acc += r.qd
        last = k == n - 1
        f = Q if last else acc
        L = r.d if last else rads[k + 1].d - r.d
        z = size(f)
        ret.append(Pipe(L, z[1], 1))
        info.append(sec(f"Return {r.id} → boiler" if last else f"Return {r.id} → {rads[k + 1].id}", L, z, f, 1))
    sol = solve_reverse(sup, ret, branches, Q, c, qd)
    dev = max(abs(x - qd[k]) / qd[k] for k, x in enumerate(sol["q"]))
    dp, lsv, q_show = sol["dp_net"] + Jgen * Q * Q, None, sol["q"]
    notes = ["Supply and return path lengths are about equal for every radiator.", "Needs a longer return main."]
    residual = max(sol["paths"]) - min(sol["paths"])
    if dev > inp.dev_tol:
        b2 = balance_reverse(sup, ret, branches, qd, c, LSV)
        lsv, dp, q_show, residual = b2["kv_set"], b2["dp_net"] + Jgen * Q * Q, list(qd), 0.0
        notes.append(f"Self-balancing was not enough here ({dev * 100:.1f} % deviation), so lockshield presets are included.")
    options.append(Option("rr", "Two-pipe, reverse return (Tichelmann)", "Reverse return (Tichelmann)", "reverse",
                          q_show, [{"name": "Circulation pump", "Q": Q, "dp": dp}], info + tails_info(), True,
                          residual, econ([{"Q": Q, "dp": dp}], inp.T_ret), notes, lsv=lsv, q_natural=sol["q"]))

    # D) manifold / home runs (balanced with flow setters)
    zf = size(Q)
    info = [sec("Boiler → manifold", 1, zf, Q, 2)]
    mb = []
    for k, r in enumerate(rads):
        z = size(r.qd)
        info.append(sec(f"Manifold → {r.id} (home run)", r.d, z, r.qd, 2))
        mb.append(Branch([Pipe(r.d + 1, z[1]), Pipe(r.d + 1, z[1])], [rads[k].kv_trv, inp.kv_rad, inp.kv_lsv]))
    kv_man = 16.7 if n <= 7 else 10.4
    fixedJ = 2 * J_pipe(Q, 1, zf[1], c) + 2 * J_kv(kv_man)
    req = [J_branch(b, qd[k], c) * qd[k] ** 2 for k, b in enumerate(mb)]
    P = max(req)
    kv_set = [math.sqrt(1e5 / (J_kv(inp.kv_lsv) + (P - req[k]) / qd[k] ** 2)) for k in range(n)]
    nat = solve_manifold(mb, Q, c)
    dp = P + fixedJ * Q * Q + Jgen * Q * Q
    options.append(Option("man", "Manifold system (home runs), balanced", "Manifold, balanced", "manifold",
                          list(qd), [{"name": "Circulation pump", "Q": Q, "dp": dp}], info, True, 0.0,
                          econ([{"Q": Q, "dp": dp}], inp.T_ret),
                          [f"One pipe pair per radiator from a central manifold (manifold Kv {kv_man} m³/h, after Rossato/Caleffi).",
                           "Flow setters on the manifold replace lockshield presets. Most pipe, simplest balancing."],
                          lsv=kv_set, q_natural=nat["q"]))

    # E) primary–secondary (secondary = balanced direct return)
    bal = balance_direct(mains, branches, qd, c, LSV)
    Qp = inp.ps_ratio * Q
    zp = size(Qp)
    dpP = Jgen * Qp * Qp + 2 * J_pipe(Qp, 2, zp[1], c) + J_kv(30) * Qp * Qp
    T_ret_gen = (Q * inp.T_ret + (Qp - Q) * inp.T_sup) / Qp
    pumps = [{"name": "Primary pump", "Q": Qp, "dp": dpP}, {"name": "Secondary pump", "Q": Q, "dp": bal["dp_net"]}]
    options.append(Option("ps", "Primary–secondary with low-loss header", "Primary–secondary", "ps",
                          list(qd), pumps, [sec("Primary loop (boiler ↔ header)", 2, zp, Qp, 2)] + main_info + tails_info(),
                          True, 0.0, econ(pumps, T_ret_gen, {"T_ret_gen": T_ret_gen}),
                          [f"Two pumps. Primary flow {(inp.ps_ratio - 1) * 100:.0f} % above secondary, so the generator return is "
                           f"{T_ret_gen:.1f} °C instead of {inp.T_ret:g} °C.",
                           "Useful when the heat source needs its own minimum flow (many heat pumps, older boilers)."],
                          lsv=bal["kv_set"]))

    # F) series one-pipe loop
    z = size(Q)
    info, segs, prev = [], [], 0.0
    for k, r in enumerate(rads):
        segs.append(Pipe(r.d - prev, z[1]))
        info.append(sec(f"Loop {'boiler' if k == 0 else rads[k - 1].id} → {r.id}", r.d - prev, z, Q, 1))
        prev = r.d
    segs.append(Pipe(prev, z[1]))
    info.append(sec(f"Loop {rads[-1].id} → boiler", prev, z, Q, 1))
    info += [sec(f"{r.id} connections", 1, z, Q, 2) for r in rads]
    J = sum(J_pipe(Q, p.L, p.D, c) for p in segs) + n * (2 * J_pipe(Q, 1, z[1], c) + J_kv(inp.kv_rad) + J_kv(inp.kv_lsv))
    dp = (J + Jgen) * Q * Q
    T_in, ref, temps = inp.T_sup, ((inp.T_sup + inp.T_ret) / 2 - 20) ** 1.3, []
    for r in rads:
        T_out = T_in - r.phi * 3600 / (c.rho * c.cp * Q)
        temps.append({"T_in": T_in, "T_out": T_out, "factor": ref / max(1.0, (T_in + T_out) / 2 - 20) ** 1.3})
        T_in = T_out
    options.append(Option("ser", "Series one-pipe loop", "Series loop", "series", [Q] * n,
                          [{"name": "Circulation pump", "Q": Q, "dp": dp}], info, False, 0.0,
                          econ([{"Q": Q, "dp": dp}], inp.T_ret),
                          ["All water passes every radiator, so no room can be controlled on its own.",
                           f"Water cools along the loop; the last radiator must be {temps[-1]['factor']:.2f}× larger for the same heat."],
                          temps=temps))

    # --- assessment and ranking ---
    for o in options:
        o.v_max = max(s.v for s in o.sections)
        o.R_max = max(s.R for s in o.sections)
        o.pipe_length = sum(s.L * s.mult for s in o.sections)
        o.pipe_cost = sum(s.cost for s in o.sections)
        o.max_dev = 0.0 if o.family == "series" else max(abs(x - qd[k]) / qd[k] for k, x in enumerate(o.q))
        o.head_kpa = sum(p["dp"] for p in o.pumps) / 1000
        o.issues = []
        if not o.control:
            o.issues.append("No individual room control")
        if o.max_dev > inp.dev_tol:
            o.issues.append(f"Flow deviation {o.max_dev * 100:.1f} % (limit {inp.dev_tol * 100:.0f} %)")
        if o.family == "series" and o.temps[-1]["factor"] > 1.25:
            o.issues.append("Last radiators need oversizing")
        if any(p["dp"] > 60000 for p in o.pumps):
            o.issues.append("Pump head above 60 kPa – beyond a domestic circulator")
        o.acceptable = not o.issues
        o.lcc = o.pipe_cost + inp.lifetime * o.econ["total"]
    best = min(o.lcc for o in options)
    for o in options:
        o.lcc_index = o.lcc / best
    ok = sorted([o for o in options if o.acceptable], key=lambda o: o.lcc)
    bad = sorted([o for o in options if not o.acceptable], key=lambda o: o.lcc)
    ranked = ok + bad
    for i, o in enumerate(ranked):
        o.rank = i + 1
    return Result(rads, qd, Q, phi_tot, c, ranked, ok[0].key if ok else None, T_mean)


def improvements(inp: Inputs, key: str) -> List[Dict]:
    """What-if changes for one option: saving per year, extra cost, payback."""
    from dataclasses import replace
    base = evaluate(inp)
    b = next(o for o in base.options if o.key == key)
    out = []

    def try_it(label, change, extra_cost, detail):
        r = evaluate(replace(inp, **change))
        o = next(x for x in r.options if x.key == key)
        save = b.econ["total"] - o.econ["total"]
        cost = extra_cost(o) if callable(extra_cost) else extra_cost
        out.append({"label": label, "detail": detail(o) if callable(detail) else detail, "save": save, "cost": cost,
                    "payback": cost / save if save > 0 and cost > 0 else None})

    if inp.pump_eff < 0.3:
        try_it("High-efficiency circulator (EEI ≤ 0.23)", {"pump_eff": 0.30}, 180,
               f"Overall efficiency 30 % instead of {inp.pump_eff * 100:.0f} %.")
    try_it("Main pipes one size larger", {"upsize": inp.upsize + 1}, lambda o: o.pipe_cost - b.pipe_cost,
           lambda o: f"Pump head {o.head_kpa:.1f} kPa instead of {b.head_kpa:.1f} kPa.")
    regimes = [(80, 60), (75, 65), (70, 55), (55, 45), (45, 35)]
    if (inp.T_sup, inp.T_ret) in regimes:
        i = regimes.index((inp.T_sup, inp.T_ret))
        if i < len(regimes) - 1:
            s, t = regimes[i + 1]
            fsize = (((inp.T_sup + inp.T_ret) / 2 - 20) / ((s + t) / 2 - 20)) ** 1.3
            try_it(f"Lower temperatures {s}/{t} °C", {"T_sup": s, "T_ret": t}, 0,
                   f"Radiators must be about {fsize:.2f}× larger than for {inp.T_sup:g}/{inp.T_ret:g} °C. Gains come from the heat source.")
    if inp.w_per_m2 > 40:
        try_it("Insulation upgrade (−25 % heat demand)", {"w_per_m2": inp.w_per_m2 * 0.75}, 0,
               "Smaller flows, pump power and heat demand. Cost depends on the building.")
    return out


# ---------------------------------------------------------------------------
# Validation against the corrected thesis Excel models
# ---------------------------------------------------------------------------
EXCEL_REFERENCE = {"Parallel (direct return), pump ΔP": 27394.2076, "Tichelmann, pump ΔP": 30561.2846,
                   "Series, pump ΔP": 74620.3525, "Primary–secondary, primary pump ΔP": 33285.9844}


def thesis_validation() -> List[Dict]:
    """Run the engine on the thesis inputs (80 °C, Kv 1.2 / 2, steel, no fittings allowance)."""
    c = Fluid(rho=971.82, mu=0.354e-3, cp=4197, model="steel", allow=0.0)
    B = [Branch([Pipe(1, 0.0158), Pipe(1, 0.0158)], [1.2, 2]) for _ in range(3)]
    mains = [Pipe(3, 0.0273, 2), Pipe(5, 0.0273, 2), Pipe(7, 0.0216, 2)]
    sup = [Pipe(3, 0.0273), Pipe(5, 0.0273), Pipe(7, 0.0216)]
    ret = [Pipe(5, 0.0216), Pipe(7, 0.0273), Pipe(15, 0.0273)]
    ser = (sum(J_pipe(0.5, L, 0.0216, c) for L in (4, 7, 9, 1, 15)) + 3 * (J_kv(1.2) + J_kv(2))) * 0.25
    prim = J_kv(3) * 1.65 ** 2 + 2 * J_pipe(1.65, 5, 0.0273, c) * 1.65 ** 2 + J_pipe(0.15, 0.5, 0.0273, c) * 0.15 ** 2
    tool = {"Parallel (direct return), pump ΔP": solve_direct(mains, B, 1.5, c)["dp_net"],
            "Tichelmann, pump ΔP": solve_reverse(sup, ret, B, 1.5, c)["dp_net"],
            "Series, pump ΔP": ser, "Primary–secondary, primary pump ΔP": prim}
    return [{"model": k, "excel": v, "tool": tool[k], "difference": tool[k] - v} for k, v in EXCEL_REFERENCE.items()]


EXAMPLE_ROOMS = [Room("Living room", 28, 2, 4), Room("Kitchen", 14, 1, 8), Room("Bedroom 1", 16, 1, 11),
                 Room("Bedroom 2", 12, 1, 14), Room("Bathroom", 7, 1, 17)]
