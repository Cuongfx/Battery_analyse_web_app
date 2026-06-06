import numpy as np


def calculate_soc(current, time, capacity_ah, eta_chg=0.98, eta_dis=1.0):
    """
    Calculate state of charge by Coulomb counting.
    """
    current = np.asarray(current)
    time = np.asarray(time)

    q_as = capacity_ah * 3600
    dt = np.diff(time)
    soc = np.ones(len(current))

    for k in range(1, len(current)):
        eta = eta_chg if current[k] > 0 else eta_dis
        soc[k] = soc[k - 1] + ((eta * current[k] * dt[k - 1]) / q_as)

    return soc
