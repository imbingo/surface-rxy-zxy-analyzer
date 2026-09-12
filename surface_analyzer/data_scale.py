"""Read-only source/analysis/display provenance; unknown is never zero.

Legacy source counts are accepted only for paths known to finish a full scan.
Theoretical matrix positions are not evidence of valid measurement points.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Count:
    value: int | None = None
    precision: str = 'unknown'
    basis: str = ''

    def text(self):
        if self.value is None:
            return '未知'
        return ('≈' if self.precision == 'estimated' else '') + f'{self.value:,}'


def source_counts(info):
    """Return records, valid points, theoretical positions without mutating info."""
    records = Count()
    valid = Count()
    positions = Count()
    if info.get('source_record_rows') is not None:
        records = Count(int(info['source_record_rows']), 'exact', '解析器完整扫描数据记录')
    if info.get('source_matrix_positions', 0):
        positions = Count(int(info['source_matrix_positions']), 'exact', '理论格点，非有效点数')
    # Legacy stride can stop at the import limit; its source_valid_rows is
    # merely a scanned prefix, not a source total. Byte-position sampling is
    # also unknown unless a dedicated parser explicitly counted the source.
    trusted = (bool(info.get('height_matrix')) or
               info.get('source_format') in (
                   'Zygo XYZ Data File - Format 1',
                   'Precitec FSS Explorer SCAN PATH DATA') or
               info.get('sample_method_key') == 'spatial_grid')
    if trusted and info.get('source_valid_rows') is not None:
        valid = Count(int(info['source_valid_rows']), 'exact', '解析器完整扫描有效点')
    mapping = info.get('count_mapping')
    current_mapping = [info.get('mapping_x_col'), info.get('mapping_y_col'), info.get('mapping_z_col')]
    if mapping is not None and list(mapping) != current_mapping:
        valid = Count(basis='列映射已变化，原有效点统计不适用')
    if not info.get('sampled') and info.get('mapped_finite_points') is not None:
        valid = Count(int(info['mapped_finite_points']), 'exact', '全量读入后按当前映射统计有限XYZ')
    return records, valid, positions


def scale_summary(info, analysis_points=None, final_points=None, xy_points=None,
                  detail_points=None):
    records, valid, positions = source_counts(info)
    def number(value):
        return '未计算' if value is None else f'{int(value):,}'
    text = (f'源有效点 {valid.text()} → 分析输入 {number(analysis_points)}'
            f"{'（抽样）' if info.get('sampled') else ''} → 最终计算 {number(final_points)}"
            f' | XY显示 {number(xy_points)} | 3D/XZ/YZ显示 {number(detail_points)}')
    detail = (f'源总记录：{records.text()}（{records.basis or "未完整统计"}）\n'
              f'源有效点：{valid.text()}（{valid.basis or "未完整统计，不能用读入点数代替"}）\n'
              f'理论格点：{positions.text()}（不是有效点数）\n'
              f'实际读入记录：{info.get("import_rows", 0):,}\n'
              '显示抽样不改变分析输入和量测结果。')
    return text, detail
