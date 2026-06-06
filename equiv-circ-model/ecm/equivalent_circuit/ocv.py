import numpy as np


def interpolate_ocv(current, time, voltage, soc, anchor_indices=None, vz_points=None, pts=False):
    """
    Interpolate OCV from SOC/voltage anchor points.
    """
    if vz_points is not None:
        v_pts, z_pts = vz_points
        return np.interp(soc, z_pts[::-1], v_pts[::-1])

    if anchor_indices is None:
        raise ValueError("anchor_indices or vz_points must be provided for OCV interpolation")

    v_pts = np.append(voltage[anchor_indices], voltage[-1])
    z_pts = np.append(soc[anchor_indices], soc[-1])
    ocv = np.interp(soc, z_pts[::-1], v_pts[::-1])

    if pts:
        i_pts = np.append(current[anchor_indices], current[-1])
        t_pts = np.append(time[anchor_indices], time[-1])
        return ocv, i_pts, t_pts, v_pts, z_pts

    return ocv
