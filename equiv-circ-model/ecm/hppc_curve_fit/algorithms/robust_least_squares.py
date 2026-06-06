from scipy.optimize import least_squares

from .common import clip_guess_to_bounds, default_bounds, default_guess, fit_setup, residuals


def fit(t_curve, v_curve, rc_order, guess=None, bounds=None, maxfev=None):
    """
    Bounded least-squares fit with robust loss for noisy HPPC points.
    """
    func, _ = fit_setup(rc_order)
    fit_bounds = default_bounds(v_curve, rc_order) if bounds is None else bounds
    p0 = default_guess(v_curve, rc_order) if guess is None else guess
    p0 = clip_guess_to_bounds(p0, fit_bounds)

    result = least_squares(
        residuals,
        p0,
        bounds=fit_bounds,
        args=(func, t_curve, v_curve),
        loss="soft_l1",
        f_scale=0.01,
        max_nfev=maxfev,
    )

    if not result.success:
        raise RuntimeError(result.message)

    return result.x
