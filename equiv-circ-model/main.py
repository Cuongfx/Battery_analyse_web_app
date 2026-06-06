import argparse
from pathlib import Path

from ecm import (
    available_algorithms,
    CellDischargeData,
    CellHppcData,
    EcmConfig,
    fit_ecm_from_hppc,
    mean_absolute_error,
    plot_hppc_fit,
    plot_rc_params,
    root_mean_square_error,
    save_rctau_csv,
    simulate_evaluation_profile,
)


def _default_hppc_path():
    return Path("data/HPPC_data/cell-low-current-hppc-25c-2.csv")


def _default_params_output(hppc_path, rc_order):
    return Path("results") / f"{hppc_path.stem}_{rc_order}rc_parameters.csv"


def _print_ocv_points(v_pts, z_pts):
    print("\nOCV points")
    print(f"{'SOC [-]':>10} {'OCV [V]':>10}")
    for soc, voltage in zip(z_pts, v_pts):
        print(f"{soc:10.4f} {voltage:10.4f}")


def _print_rctau(rctau, rc_order, soc_points):
    print(f"\n{rc_order}-RC parameters")

    if rc_order == 1:
        print(f"{'SOC [-]':>10} {'tau1 [s]':>12} {'R0 [ohm]':>12} {'R1 [ohm]':>12} {'C1 [F]':>12}")
        for soc, row in zip(soc_points, rctau):
            tau1, r0, r1, c1 = row
            print(f"{soc:10.3f} {tau1:12.2f} {r0:12.5f} {r1:12.5f} {c1:12.1f}")
        return

    print(
        f"{'SOC [-]':>10} {'tau1 [s]':>12} {'tau2 [s]':>12} "
        f"{'R0 [ohm]':>12} {'R1 [ohm]':>12} {'R2 [ohm]':>12} "
        f"{'C1 [F]':>12} {'C2 [F]':>12}"
    )
    for soc, row in zip(soc_points, rctau):
        tau1, tau2, r0, r1, r2, c1, c2 = row
        print(
            f"{soc:10.3f} {tau1:12.2f} {tau2:12.2f} "
            f"{r0:12.5f} {r1:12.5f} {r2:12.5f} "
            f"{c1:12.1f} {c2:12.1f}"
        )


def build_parser():
    parser = argparse.ArgumentParser(
        description="Fit a battery equivalent circuit model from HPPC data."
    )
    parser.add_argument(
        "hppc",
        type=Path,
        nargs="?",
        default=_default_hppc_path(),
        help="Path to HPPC CSV data to fit.",
    )
    parser.add_argument(
        "--hppc",
        type=Path,
        dest="hppc_opt",
        help="Alternative way to specify the HPPC CSV (overrides the positional argument).",
    )
    parser.add_argument(
        "--evaluate",
        type=Path,
        help="Optional discharge/evaluation CSV data to simulate with fitted HPPC parameters.",
    )
    parser.add_argument(
        "--rc-order",
        type=int,
        choices=(1, 2),
        default=2,
        help="Number of RC branches to fit and simulate.",
    )
    parser.add_argument(
        "--source",
        choices=("pulse", "discharge"),
        default="pulse",
        help="Extract RC parameters from the short HPPC pulses (one row per SOC "
             "level incl. 100%%) or from the constant-discharge relaxations.",
    )
    parser.add_argument(
        "--capacity",
        type=float,
        help="Cell capacity in Ah used for SOC calculation (overrides the default).",
    )
    parser.add_argument(
        "--fit-algorithm",
        choices=available_algorithms(),
        default="curve_fit",
        help="Curve-fitting algorithm used for HPPC relaxation sections.",
    )
    parser.add_argument(
        "--output-params",
        type=Path,
        help="CSV path for fitted RC parameters. Defaults to results/cell_<order>rc_parameters.csv.",
    )
    parser.add_argument(
        "--plot-params",
        action="store_true",
        help="Plot fitted R/C/tau parameters versus SOC.",
    )
    parser.add_argument(
        "--save-param-plot",
        type=Path,
        help="Optional image path for the fitted parameter plot.",
    )
    parser.add_argument(
        "--save-fit-plot",
        type=Path,
        help="Optional image path for the HPPC measured-vs-fitted voltage plot.",
    )
    parser.add_argument(
        "--no-fit-plot",
        action="store_true",
        help="Do not create the default HPPC measured-vs-fitted voltage plot.",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="Do not open plot windows. Useful when only saving plots.",
    )
    return parser


def main():
    args = build_parser().parse_args()
    config = EcmConfig(q_cell=args.capacity) if args.capacity else EcmConfig()

    hppc_path = args.hppc_opt or args.hppc
    if not hppc_path.exists():
        raise SystemExit(f"HPPC file not found: {hppc_path}")

    hppc_data = CellHppcData(hppc_path)
    _, fit_result = fit_ecm_from_hppc(
        hppc_data,
        config,
        rc_order=args.rc_order,
        algorithm=args.fit_algorithm,
        source=args.source,
    )

    hppc_mae = mean_absolute_error(hppc_data.voltage, fit_result.vt)
    hppc_rmse = root_mean_square_error(hppc_data.voltage, fit_result.vt)

    print(f"\nFitted cell ECM from: {hppc_path}")
    print(f"RC order: {args.rc_order}  |  parameter source: {args.source}  |  capacity: {config.q_cell} Ah")
    print(f"Fit algorithm: {args.fit_algorithm}")
    print(f"HPPC MAE:  {hppc_mae:.6f} V")
    print(f"HPPC RMSE: {hppc_rmse:.6f} V")

    v_pts, z_pts = fit_result.ocv_points
    _print_ocv_points(v_pts, z_pts)
    _print_rctau(fit_result.rctau, args.rc_order, fit_result.soc_points)

    output_params = args.output_params or _default_params_output(hppc_path, args.rc_order)
    output_params.parent.mkdir(parents=True, exist_ok=True)
    param_df = save_rctau_csv(
        fit_result.rctau,
        output_params,
        rc_order=args.rc_order,
        soc_values=fit_result.soc_points,
    )
    print(f"\nSaved RC parameters: {output_params}")

    if not args.no_fit_plot:
        if args.save_fit_plot:
            args.save_fit_plot.parent.mkdir(parents=True, exist_ok=True)
        plot_hppc_fit(
            hppc_data.time,
            hppc_data.voltage,
            fit_result.vt,
            rc_order=args.rc_order,
            save_path=args.save_fit_plot,
            show=not args.no_show,
        )
        if args.save_fit_plot:
            print(f"Saved HPPC fit plot: {args.save_fit_plot}")

    if args.plot_params or args.save_param_plot:
        if args.save_param_plot:
            args.save_param_plot.parent.mkdir(parents=True, exist_ok=True)
        plot_rc_params(
            param_df,
            save_path=args.save_param_plot,
            show=not args.no_show,
        )
        if args.save_param_plot:
            print(f"Saved parameter plot: {args.save_param_plot}")

    if args.evaluate:
        eval_data = CellDischargeData.process_discharge_only(args.evaluate)
        eval_result = simulate_evaluation_profile(
            eval_data,
            config,
            fit_result,
            rc_order=args.rc_order,
            capacity_attr="q_cell",
        )
        print(f"\nEvaluation data: {args.evaluate}")
        print(f"Evaluation MAE:  {eval_result.mae:.6f} V")
        print(f"Evaluation RMSE: {eval_result.rmse:.6f} V")


if __name__ == "__main__":
    main()
