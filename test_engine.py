"""Run with:  python -m pytest tests"""
import math

from hydronic_planner import engine as E


def test_matches_thesis_excel_models():
    for row in E.thesis_validation():
        assert abs(row["difference"]) < 0.01, row


def test_direct_return_paths_are_equal():
    c = E.Fluid(rho=971.82, mu=0.354e-3, cp=4197)
    B = [E.Branch([E.Pipe(1, 0.0158), E.Pipe(1, 0.0158)], [1.2, 2]) for _ in range(3)]
    mains = [E.Pipe(3, 0.0273, 2), E.Pipe(5, 0.0273, 2), E.Pipe(7, 0.0216, 2)]
    sol = E.solve_direct(mains, B, 1.5, c)
    p = E.paths_direct(mains, B, sol["q"], 1.5, c)
    assert max(p) - min(p) < 1e-6
    assert math.isclose(sum(sol["q"]), 1.5, rel_tol=1e-12)


def test_balanced_settings_give_design_flows():
    r = E.evaluate(E.Inputs(rooms=E.EXAMPLE_ROOMS))
    o = next(x for x in r.options if x.key == "dr-bal")
    assert max(abs(q - qd) / qd for q, qd in zip(o.q, r.qd)) < 1e-6


def test_example_house_ranking():
    r = E.evaluate(E.Inputs(rooms=E.EXAMPLE_ROOMS))
    assert r.recommended == "dr-bal"
    assert not next(x for x in r.options if x.key == "ser").acceptable


def test_any_number_of_radiators():
    rooms = [E.Room(f"R{i}", 15, 1, 3 + 3 * i) for i in range(12)]
    r = E.evaluate(E.Inputs(rooms=rooms))
    assert len(r.rads) == 12 and len(r.options) == 6
