from scipy.optimize import curve_fit

from .common import default_guess, fit_setup


def fit(t_curve, v_curve, rc_order, guess=None, bounds=None, maxfev=None):
    """
    Standard SciPy nonlinear least-squares curve_fit.
    """
    func, _ = fit_setup(rc_order)
    p0 = default_guess(v_curve, rc_order) if guess is None else guess

    kwargs = {"p0": p0}
    if bounds is not None:
        kwargs["bounds"] = bounds
    if maxfev is not None:
        kwargs["maxfev"] = maxfev

    params, _ = curve_fit(func, t_curve, v_curve, **kwargs)
    return params
