import numpy as np
from ecm.hppc_curve_fit.algorithms import get_algorithm
from ecm.hppc_curve_fit.algorithms.common import func_otc, func_ttc


def _fit_setup(rc_order):
    if rc_order == 1:
        return func_otc, 3
    if rc_order == 2:
        return func_ttc, 5
    raise ValueError("rc_order must be 1 or 2")


def curve_fit_coefficients(
    time,
    voltage,
    discharge_indices,
    rc_order=2,
    trim_last=False,
    include_endpoint=False,
    algorithm="curve_fit",
    guess=None,
    maxfev=None,
):
    """
    Fit HPPC voltage relaxation data at each SOC section.
    """
    func, ncoeff = _fit_setup(rc_order)
    _, _, id2, _, id4 = discharge_indices

    if trim_last:
        id2 = id2[:-1]

    coeff = np.zeros((len(id2), ncoeff))
    fit_algorithm = get_algorithm(algorithm)

    for i, (start, end) in enumerate(zip(id2, id4)):
        stop = end + 1 if include_endpoint else end
        t_curve = time[start:stop]
        v_curve = voltage[start:stop]
        t_scale = t_curve - t_curve[0]

        coeff[i] = fit_algorithm(
            t_scale,
            v_curve,
            rc_order,
            guess=guess,
            maxfev=maxfev,
        )

    return coeff
