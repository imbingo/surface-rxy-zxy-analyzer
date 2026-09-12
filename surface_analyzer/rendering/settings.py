"""Backward compatible, display-only Recipe options; no cache is serialized."""
def validate_display(data):
    if not isinstance(data, dict):
        raise ValueError('显示配置必须为对象')
    mode = data.get('xy_mode', 'height')
    if mode not in ('height', 'density', 'missing'):
        raise ValueError('未知XY显示模式')
    side = int(data.get('xy_raster_max_side', 1200))
    if side not in (600, 800, 1200):
        raise ValueError('XY最大分辨率须为600、800或1200')
    return {'xy_mode': mode, 'xy_raster_max_side': side}
