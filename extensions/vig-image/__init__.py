"""Optional VIG image-plane analysis module."""

from .engine import compute_vig_image
from .module import VigImageModule


def create_module(host):
    return VigImageModule(host)


def self_check():
    import numpy as np

    x = np.array([-0.2, 0.0, 0.2])
    y = np.array([-0.1, 0.1, -0.1])
    two = np.full(3, 0.4)
    t_up = np.full(3, 150.0)
    t_mid = np.full(3, 150.0)
    t_low = np.full(3, 150.0)
    low = np.array([180.0, 160.0, 190.0])
    up = np.array([40.0, 52.0, 64.0])
    record = lambda name, z: {
        'x': x, 'y': y, 'z': np.asarray(z, dtype=float) / 1000.0,
        'name': name, 'n': len(x), 'source': name,
    }
    result = compute_vig_image(
        record('two-layer', low + t_mid + t_low),
        record('three-layer', up + low + t_mid + t_low + t_up),
        {'up': 150.0, 'mid': 150.0, 'low': 150.0},
        tolerance_mm=0.01,
    )
    return {
        'ok': result['n_points'] == 3,
        'n_points': result['n_points'],
        'rx': result['plane']['rx'],
        'ry': result['plane']['ry'],
    }


__all__ = ['create_module', 'self_check', 'compute_vig_image']
