def config_ax(ax, xylabels=None, title=None, loc=None):
    """
    Configure a Matplotlib axis with the project default style.
    """
    ax.grid(True, color="0.9")
    ax.set_frame_on(False)
    ax.tick_params(color="0.9")

    if xylabels is not None:
        ax.set_xlabel(xylabels[0])
        ax.set_ylabel(xylabels[1])

    if title is not None:
        ax.set_title(title)

    if loc is not None:
        ax.legend(loc=loc)


def plot_hppc_fit(time, measured_voltage, fitted_voltage, *, rc_order=None, save_path=None, show=True):
    """
    Plot measured HPPC voltage against the fitted ECM voltage.
    """
    import matplotlib.pyplot as plt

    title = "HPPC Voltage Fit"
    if rc_order is not None:
        title = f"HPPC Voltage Fit ({rc_order}-RC)"

    fig, (ax_voltage, ax_error) = plt.subplots(2, 1, figsize=(9, 6), sharex=True, tight_layout=True)

    ax_voltage.plot(time, measured_voltage, color="C3", label="HPPC data")
    ax_voltage.plot(time, fitted_voltage, color="k", linestyle="--", label="ECM fit")
    config_ax(ax_voltage, xylabels=("", "Voltage [V]"), title=title, loc="best")

    ax_error.plot(time, abs(measured_voltage - fitted_voltage), color="C0", label="absolute error")
    config_ax(ax_error, xylabels=("Time [s]", "Abs. error [V]"), loc="best")

    if save_path is not None:
        fig.savefig(save_path, dpi=150)

    if show:
        plt.show()

    return fig


def plot_rc_params(param_df, *, save_path=None, show=True):
    """
    Plot ECM R/C/tau parameters versus SOC.
    """
    import matplotlib.pyplot as plt

    groups = [
        ("Resistance vs SOC", [col for col in ("r0_ohm", "r1_ohm", "r2_ohm") if col in param_df]),
        ("Capacitance vs SOC", [col for col in ("c1_f", "c2_f") if col in param_df]),
        ("Time Constant vs SOC", [col for col in ("tau1_s", "tau2_s") if col in param_df]),
    ]
    groups = [(title, columns) for title, columns in groups if columns]

    fig, axes = plt.subplots(len(groups), 1, figsize=(8, 3.2 * len(groups)), tight_layout=True)
    if len(groups) == 1:
        axes = [axes]

    for ax, (title, columns) in zip(axes, groups):
        for column in columns:
            ax.plot(param_df["soc"], param_df[column], marker="o", label=column)
        ax.invert_xaxis()
        config_ax(ax, xylabels=("SOC [-]", "Value"), title=title, loc="best")

    if save_path is not None:
        fig.savefig(save_path, dpi=150)

    if show:
        plt.show()

    return fig
