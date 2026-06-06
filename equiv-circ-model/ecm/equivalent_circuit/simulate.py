import numpy as np


def infer_rc_order(rctau):
    cols = rctau.shape[1]
    if cols == 4:
        return 1
    if cols == 7:
        return 2
    raise ValueError("rctau must have 4 columns for 1-RC or 7 columns for 2-RC")


def _default_soc_points(n_rows):
    """
    Fallback SOC grid when explicit parameter SOC points are not provided.
    """
    return np.arange(0.1, 1.0, 0.1)[::-1][:n_rows]


def get_rc_values(rctau, soc_value, soc_points=None, rc_order=None):
    """
    Select the R/C/tau row whose SOC is closest to `soc_value`.

    `soc_points` is the SOC associated with each row of `rctau`. When omitted a
    legacy 0.9..0.1 grid is used.
    """
    if rc_order is None:
        rc_order = infer_rc_order(rctau)

    if soc_points is None:
        soc_points = _default_soc_points(len(rctau))
    soc_points = np.asarray(soc_points)
    idx = abs(soc_points - soc_value).argmin()

    if rc_order == 1:
        tau1, r0, r1, _ = rctau[idx]
        return tau1, r0, r1

    tau1, tau2, r0, r1, r2, _, _ = rctau[idx]
    return tau1, tau2, r0, r1, r2


def simulate_voltage(time, current, soc, ocv, rctau, soc_points=None, rc_order=None):
    """
    Simulate terminal voltage for a 1-RC or 2-RC equivalent circuit.
    """
    if rc_order is None:
        rc_order = infer_rc_order(rctau)
    if rc_order not in (1, 2):
        raise ValueError("rc_order must be 1 or 2")

    if soc_points is None:
        soc_points = _default_soc_points(len(rctau))

    dt = np.diff(time)
    n_steps = len(current)
    v0 = np.zeros(n_steps)
    v1 = np.zeros(n_steps)
    v2 = np.zeros(n_steps)

    for k in range(1, n_steps):
        i = current[k]

        if rc_order == 1:
            tau1, r0, r1 = get_rc_values(rctau, soc[k], soc_points, rc_order=1)
            v0[k] = r0 * i
            v1[k] = (
                v1[k - 1] * np.exp(-dt[k - 1] / tau1)
                + r1 * (1 - np.exp(-dt[k - 1] / tau1)) * i
            )
        else:
            tau1, tau2, r0, r1, r2 = get_rc_values(rctau, soc[k], soc_points, rc_order=2)
            v0[k] = r0 * i
            v1[k] = (
                v1[k - 1] * np.exp(-dt[k - 1] / tau1)
                + r1 * (1 - np.exp(-dt[k - 1] / tau1)) * i
            )
            v2[k] = (
                v2[k - 1] * np.exp(-dt[k - 1] / tau2)
                + r2 * (1 - np.exp(-dt[k - 1] / tau2)) * i
            )

    return ocv + v0 + v1 + v2
