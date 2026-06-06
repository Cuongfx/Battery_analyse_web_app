import numpy as np
import pandas as pd


def _aligned_step_indices(discharge_indices, n_rows):
    id0, id1, id2, _, _ = discharge_indices
    return id0[:n_rows], id1[:n_rows], id2[:n_rows]


def rctau_from_coefficients(current, time, voltage, discharge_indices, coeff, rc_order=2):
    """
    Convert fitted HPPC coefficients into R/C/tau parameters.

    For rc_order=1 columns are: tau1, r0, r1, c1.
    For rc_order=2 columns are: tau1, tau2, r0, r1, r2, c1, c2.
    """
    id0, id1, id2 = _aligned_step_indices(discharge_indices, len(coeff))

    if rc_order == 1:
        rctau = np.zeros((len(coeff), 4))
    elif rc_order == 2:
        rctau = np.zeros((len(coeff), 7))
    else:
        raise ValueError("rc_order must be 1 or 2")

    for k in range(len(coeff)):
        di = abs(current[id1[k]] - current[id0[k]])
        dt = time[id2[k]] - time[id0[k]]
        dv = abs(voltage[id1[k]] - voltage[id0[k]])

        if di == 0:
            raise ValueError("Current step is zero; cannot calculate resistance")

        r0 = dv / di

        if rc_order == 1:
            _, b, alpha = coeff[k]
            tau1 = 1 / alpha
            r1 = b / ((1 - np.exp(-dt / tau1)) * di)
            c1 = tau1 / r1
            rctau[k] = tau1, r0, r1, c1
        else:
            _, b, c, alpha, beta = coeff[k]
            tau1 = 1 / alpha
            tau2 = 1 / beta
            r1 = b / ((1 - np.exp(-dt / tau1)) * di)
            r2 = c / ((1 - np.exp(-dt / tau2)) * di)
            c1 = tau1 / r1
            c2 = tau2 / r2
            rctau[k] = tau1, tau2, r0, r1, r2, c1, c2

    return rctau


def rctau_to_dataframe(rctau, rc_order=2, soc_values=None):
    """
    Convert RC parameters to a labeled table for export or plotting.
    """
    if soc_values is None:
        soc_values = np.arange(0.1, 1.0, 0.1)[::-1]

    if rc_order == 1:
        columns = ["tau1_s", "r0_ohm", "r1_ohm", "c1_f"]
    elif rc_order == 2:
        columns = ["tau1_s", "tau2_s", "r0_ohm", "r1_ohm", "r2_ohm", "c1_f", "c2_f"]
    else:
        raise ValueError("rc_order must be 1 or 2")

    df = pd.DataFrame(rctau, columns=columns)
    df.insert(0, "soc", soc_values[:len(df)])
    return df


def save_rctau_csv(rctau, path, rc_order=2, soc_values=None):
    """
    Save RC parameters versus SOC to a CSV file.
    """
    df = rctau_to_dataframe(rctau, rc_order=rc_order, soc_values=soc_values)
    df.to_csv(path, index=False)
    return df
