import numpy as np


def func_otc(t, a, b, alpha):
    """
    One-time-constant exponential fit function.
    """
    return a - b * np.exp(-alpha * t)


def func_ttc(t, a, b, c, alpha, beta):
    """
    Two-time-constant exponential fit function.
    """
    return a - b * np.exp(-alpha * t) - c * np.exp(-beta * t)


def fit_setup(rc_order):
    if rc_order == 1:
        return func_otc, 3
    if rc_order == 2:
        return func_ttc, 5
    raise ValueError("rc_order must be 1 or 2")


def default_guess(v_curve, rc_order):
    if rc_order == 1:
        return np.array((v_curve[-1], 0.01, 0.01), dtype=float)
    return np.array((v_curve[-1], 0.01, 0.01, 0.001, 0.01), dtype=float)


def default_bounds(v_curve, rc_order):
    """
    Bounds keep relaxation amplitudes and decay rates positive.
    """
    v_min = float(np.min(v_curve))
    v_max = float(np.max(v_curve))
    voltage_margin = max(0.2, abs(v_max - v_min) * 2)

    if rc_order == 1:
        lower = (v_min - voltage_margin, 1e-8, 1e-6)
        upper = (v_max + voltage_margin, 1.0, 10.0)
    else:
        lower = (v_min - voltage_margin, 1e-8, 1e-8, 1e-6, 1e-6)
        upper = (v_max + voltage_margin, 1.0, 1.0, 10.0, 10.0)

    return np.array(lower, dtype=float), np.array(upper, dtype=float)


def clip_guess_to_bounds(guess, bounds):
    lower, upper = bounds
    return np.minimum(np.maximum(guess, lower + 1e-12), upper - 1e-12)


def rmse(y_true, y_pred):
    error = y_true - y_pred
    return float(np.sqrt(np.mean(error ** 2)))


def residuals(params, func, t_curve, v_curve):
    return func(t_curve, *params) - v_curve


def score_fit(params, func, t_curve, v_curve):
    return rmse(v_curve, func(t_curve, *params))


def multistart_guesses(v_curve, rc_order):
    """
    Deterministic initial guesses covering slow/fast relaxation behavior.
    """
    base_voltage = float(v_curve[-1])
    amplitudes = (0.002, 0.01, 0.03, 0.08)
    rates = (0.0005, 0.002, 0.01, 0.05)

    guesses = []
    if rc_order == 1:
        for amp in amplitudes:
            for rate in rates:
                guesses.append((base_voltage, amp, rate))
    else:
        for amp1 in amplitudes:
            for amp2 in amplitudes:
                for rate1, rate2 in ((0.0005, 0.01), (0.002, 0.05), (0.01, 0.002), (0.05, 0.0005)):
                    guesses.append((base_voltage, amp1, amp2, rate1, rate2))

    return [np.array(guess, dtype=float) for guess in guesses]
