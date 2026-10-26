# Hydronic Layout Planner (Python)

Design tool for domestic radiator heating. The user enters the rooms and radiators and answers a few
questions (insulation, heat source, temperatures, pipe material, pump, prices). The planner then:

1. designs six layouts automatically: two-pipe direct return (unbalanced and balanced), reverse return
   (Tichelmann), manifold (home runs), primary–secondary, and a series one-pipe loop;
2. sizes every pipe section (smallest size within the velocity and pressure-gradient limits);
3. solves each network: radiator flows, deviation from design, pump head, lockshield presettings, TRV authority;
4. calculates pump power, yearly electricity, running cost and a lifetime cost, and ranks the layouts;
5. suggests improvements (bigger pipes, lower temperatures, better pump, insulation) with savings and payback.

The calculation engine uses the same equations as the thesis Excel models and reproduces them exactly
(see `thesis_validation()` in `hydronic_planner/engine.py` and `tests/test_engine.py`).

## Install and run

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app opens in the browser at http://localhost:8501.

## Run the tests

```bash
pip install pytest
python -m pytest tests
```

## Files

| File | Content |
|---|---|
| `app.py` | Streamlit interface (questions, ranking, design details, improvements, validation) |
| `hydronic_planner/engine.py` | Calculation engine: fluid properties, friction, J coefficients, network solvers, layouts, costs |
| `hydronic_planner/schematic.py` | Layout drawings (matplotlib) |
| `tests/test_engine.py` | Checks against the thesis Excel results and solver consistency |

## Main assumptions

* Steady state at design load; TRVs sized for a chosen pressure drop at design flow (presettable TRVs).
* Fittings as a percentage allowance on pipe length.
* Pipe costs per metre are indicative and can be edited in `PIPES` in `engine.py`.
* Heat-source efficiency is a simple seasonal estimate (condensing boiler 88–98 % by return temperature,
  heat-pump COP = 0.45 × Carnot COP at 7 °C source temperature).
* Pump electricity is calculated at design power for the given operating hours.
