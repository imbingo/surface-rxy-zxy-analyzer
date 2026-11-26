"""Optional orthogonal XYZ plane analysis."""

from .engine import fit_plane


def create_module(host):
    # Published V5.0.8 ignores unfamiliar manifest fields.
    if 'xyz_selection_v1' not in getattr(host, 'capabilities', ()):
        raise ValueError('主程序缺少 XYZ 点集快照接口，请使用支持 xyz_selection_v1 的版本。')
    from .module import GenericPlaneFitModule

    return GenericPlaneFitModule(host)


def self_check():
    fit = fit_plane([[2, 0, 0], [2, 3, 0], [2, 0, 4]])
    return {'ok': fit.rx_deg is None and fit.ry_deg == -90.0,
            'normal': fit.normal.tolist()}
