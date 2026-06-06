# Equivalent Circuit Model

This tool fits battery **cell** equivalent circuit model (ECM) parameters from HPPC test data, saves the fitted `R0`, `R1-C1`, and optional `R2-C2` values to CSV, and can evaluate or plot the model results. It is focused on single cells: curve fitting and evaluation only (no module, pack, or thermal models).

The code is organized by task so each part can be changed independently:

- `ecm/data/`: cell HPPC and cell discharge/evaluation data loaders.
- `ecm/hppc_curve_fit/`: one-RC and two-RC curve fitting plus RC parameter export.
- `ecm/hppc_curve_fit/algorithms/`: selectable curve-fitting algorithms.
- `ecm/equivalent_circuit/`: SOC, OCV, RC simulation, and model orchestration.
- `ecm/validation/`: discharge/evaluation simulation and error metrics.
- `ecm/plotting/`: plotting helpers, including RC parameters versus SOC.
- `main.py`: command-line script for normal use.

## Installation

```bash
python -m venv env
source env/bin/activate
pip install -r requirements.txt
pip install -e .
```

The project requires `numpy`, `pandas`, `scipy`, and `matplotlib`.

## Command Line Reference

```bash
# Fit 1-RC model, save CSV, and show HPPC fit plot
python main.py --rc-order 1

# Fit 2-RC model, save CSV, and show HPPC fit plot
python main.py --rc-order 2

# Choose curve-fitting algorithm
python main.py --rc-order 2 --fit-algorithm bounded_ls

# Choose parameter source (pulse = one row per SOC level incl. 100%)
python main.py --rc-order 1 --source pulse

# Set the cell capacity (Ah) so the SOC axis is correct
python main.py --rc-order 1 --source pulse --capacity 4.0

# Save fitted RC parameters to a chosen CSV file
python main.py --rc-order 2 --output-params results/my_cell_2rc.csv

# Save HPPC measured-vs-fitted voltage plot
python main.py --rc-order 2 --save-fit-plot results/cell_2rc_hppc_fit.png

# Plot fitted R/C/tau parameters versus SOC
python main.py --rc-order 2 --plot-params

# Save parameter-vs-SOC plot without opening plot windows
python main.py --rc-order 2 --save-param-plot results/cell_2rc_parameters.png --no-show

# Run fitting plus discharge evaluation
python main.py --rc-order 2 --evaluate data/Evaluate_data/cell-discharge-bitrode-1c.csv

# Run without showing the default HPPC fit plot
python main.py --rc-order 2 --no-fit-plot
```

## Basic HPPC Fit

Fit the default cell HPPC file with a two-RC ECM:

```bash
python main.py --rc-order 2
```

Fit the same file with a one-RC ECM:

```bash
python main.py --rc-order 1
```

After either command, the tool does two things by default:

- Saves fitted RC parameters to CSV.
- Shows an HPPC voltage fit plot comparing the measured HPPC voltage against the fitted ECM voltage.

The default CSV output paths are:

```text
results/cell_1rc_parameters.csv
results/cell_2rc_parameters.csv
```

For a one-RC fit, the CSV columns are:

```text
soc,tau1_s,r0_ohm,r1_ohm,c1_f
```

For a two-RC fit, the CSV columns are:

```text
soc,tau1_s,tau2_s,r0_ohm,r1_ohm,r2_ohm,c1_f,c2_f
```

## Choose Fitting Algorithm

Use `--fit-algorithm` to choose how each HPPC relaxation curve is fitted:

```bash
python main.py --rc-order 1 --fit-algorithm curve_fit
python main.py --rc-order 2 --fit-algorithm multi_start
python main.py --rc-order 2 --fit-algorithm bounded_ls
python main.py --rc-order 2 --fit-algorithm robust_ls
python main.py --rc-order 2 --fit-algorithm differential_evolution
```
```bash
# Estimate ECM from a specific file (positional argument)
python main.py data/HPPC_data/Channel_8-3.csv

# Pick RC order and algorithm
python main.py data/HPPC_data/Channel_8-3.csv --rc-order 2 --fit-algorithm bounded_ls

# Fit one file and validate against a discharge file
python main.py data/HPPC_data/Channel_8-3.csv --evaluate data/Evaluate_data/cell-discharge-bitrode-1c.csv

# --hppc still works as an alternative
python main.py --hppc data/HPPC_data/Channel_8-3.csv

# No argument falls back to the default file
python main.py
```
Available algorithms:

- `curve_fit`: standard SciPy nonlinear least-squares fitting. This is the default and closest to the original script.
- `multi_start`: runs `curve_fit` from many initial guesses and keeps the lowest-error fit.
- `bounded_ls`: uses bounded least squares so fit parameters stay physically meaningful.
- `robust_ls`: bounded least squares with robust loss, useful when HPPC data has spikes or noisy points.
- `differential_evolution`: global optimizer that searches a wider parameter space; usually slower but less dependent on initial guess.

Recommended starting points:

- Use `curve_fit` for fastest runs and comparison with the original workflow.
- Use `multi_start` or `bounded_ls` when the fit looks wrong or depends too much on the initial guess.
- Use `robust_ls` for noisy HPPC data.
- Use `differential_evolution` when local methods fail, because it is slower.

## Parameter Source (pulse vs discharge)

`--source` selects which part of the HPPC test the RC parameters come from:

```bash
python main.py data/HPPC_data/Channel_8-3.csv --source pulse      # default
python main.py data/HPPC_data/Channel_8-3.csv --source discharge
```

- `pulse` (default): parameters are extracted from the short current pulses.
  There is one pulse per SOC level, so you get a row for **every** level
  including **100%**.
- `discharge`: parameters are extracted from the constant-discharge relaxation
  between SOC levels. There is one fewer discharge than there are levels, so
  this produces one fewer row and **does not include 100%**.

Notes:

- The lowest SOC level is set by the test schedule (typically ~10%). A value at
  exactly **0%** cannot be characterized, because a cell at 0% cannot deliver a
  valid pulse; extrapolate from the trend if you need it.
- For short pulses a 1-RC fit (`--rc-order 1`) is usually better conditioned
  than 2-RC; if you use `--rc-order 2 --source pulse`, prefer
  `--fit-algorithm bounded_ls`.

## Cell Capacity and SOC

The SOC shown next to each parameter row is computed by coulomb counting, so it
depends on the cell capacity. Set it with `--capacity` (Ah):

```bash
python main.py data/HPPC_data/Channel_8-3.csv --source pulse --capacity 4.0
```

If you do not pass `--capacity`, the default (`30.6 Ah`) is used, which only
makes sense for the original ORNL cell. For any other cell, pass the correct
capacity or the SOC labels will be compressed.

## Choose Output CSV

Use `--output-params` to choose where the fitted RC values are saved:

```bash
python main.py \
  --hppc data/HPPC_data/cell-low-current-hppc-25c-2.csv \
  --rc-order 2 \
  --output-params results/my_cell_2rc.csv
```

## Plot HPPC Fit vs Real Data

The HPPC fit plot is shown automatically after fitting:

```bash
python main.py --rc-order 1
python main.py --rc-order 2
```

The plot shows:

- Real HPPC voltage data.
- Fitted ECM voltage.
- Absolute voltage error.

Save the HPPC fit plot to an image:

```bash
python main.py \
  --rc-order 2 \
  --save-fit-plot results/cell_2rc_hppc_fit.png
```

Run without opening any plot windows, useful for scripts or automated checks:

```bash
python main.py \
  --rc-order 2 \
  --save-fit-plot results/cell_2rc_hppc_fit.png \
  --no-show
```

Disable the default HPPC fit plot completely:

```bash
python main.py --rc-order 2 --no-fit-plot
```

## Plot RC Parameters vs SOC

Show plots of resistance, capacitance, and time constants versus SOC:

```bash
python main.py \
  --hppc data/HPPC_data/cell-low-current-hppc-25c-2.csv \
  --rc-order 2 \
  --plot-params
```

Save the plot image without opening a plot window:

```bash
python main.py \
  --hppc data/HPPC_data/cell-low-current-hppc-25c-2.csv \
  --rc-order 2 \
  --save-param-plot results/cell_2rc_parameters.png \
  --no-show
```

## Evaluate Against Discharge Data

Fit HPPC data, then simulate a discharge/evaluation file using the fitted parameters:

```bash
python main.py \
  --hppc data/HPPC_data/cell-low-current-hppc-25c-2.csv \
  --evaluate data/Evaluate_data/cell-discharge-bitrode-1c.csv \
  --rc-order 2
```

The script prints HPPC fit error and evaluation error as MAE/RMSE voltage values.

## Input Data Requirements

Cell HPPC CSV files are expected to contain:

- `Time(s)`
- `Current(A)`
- `Voltage(V)`
- `Data`

The `Data` column must contain `S` markers for the start and stop points used to identify pulse, discharge, and rest sections. The current loader assumes the same HPPC sequence pattern as the included ORNL/Nissan Leaf cell data.

Normal charge or discharge data can be used for evaluation, but HPPC pulse/rest data is needed to identify ECM parameters reliably.

## Data Folders

```text
data/
├── HPPC_data/       # cell HPPC files used to fit ECM parameters
└── Evaluate_data/   # cell discharge files used to validate the fit
```

The main supported workflow is `main.py`.

## Python API Example

```python
from ecm import CellHppcData, EcmConfig, fit_ecm_from_hppc, save_rctau_csv

data = CellHppcData("data/HPPC_data/cell-low-current-hppc-25c-2.csv")
config = EcmConfig()
model, result = fit_ecm_from_hppc(data, config, rc_order=2)

df = save_rctau_csv(result.rctau, "results/cell_2rc_parameters.csv", rc_order=2)
print(df)
```

## Notes

This is not yet a universal HPPC parser. For new battery data, check that column names, current sign convention, capacity, and `S` flag sequence match the assumptions in `ecm/data/hppc.py`.

## License

This code is available under the MIT License. See `LICENSE` for more information.
