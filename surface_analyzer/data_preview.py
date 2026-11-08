"""Bounded data previews and safe, vectorized derived-column expressions.

Source files are never modified. Expressions deliberately support arithmetic
only, with bracketed source names, rather than Python eval or pandas eval.
"""
import ast
import operator
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .workers import TaskCancelled

PAGE_SIZE = 200
SOURCE_BYTE_LIMIT = 256 * 1024
MAX_DERIVED_COLUMNS = 24


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise TaskCancelled()


def derive_columns(frame, definitions, cancel=None):
    """Return a new frame, validating every expression without executing code."""
    check_cancel(cancel)
    if not isinstance(definitions, list) or len(definitions) > MAX_DERIVED_COLUMNS:
        raise ValueError('派生列最多 24 个。')
    result = frame.copy(deep=False)
    operations = {ast.Add: operator.add, ast.Sub: operator.sub,
                  ast.Mult: operator.mul, ast.Div: operator.truediv}
    for definition in definitions:
        check_cancel(cancel)
        if not isinstance(definition, dict):
            raise ValueError('派生列定义必须为对象。')
        name = str(definition.get('name', '')).strip()
        expression = str(definition.get('expression', '')).strip()
        if not name or name in result.columns or name.startswith('_'):
            raise ValueError(f'派生列名为空、重复或保留名称：{name}')
        if definition.get('unit') not in ('mm', 'µm', 'nm'):
            raise ValueError('请明确选择派生列输出单位。')
        if len(expression) > 1000:
            raise ValueError('公式过长。')
        references = []
        def replace(match):
            references.append(match.group(1))
            return f'v{len(references)-1}'
        rewritten = re.sub(r'\[([^\[\]]+)\]', replace, expression)
        try:
            tree = ast.parse(rewritten, mode='eval')
        except SyntaxError as exc:
            raise ValueError('公式语法无效，请使用 [列名]、四则运算和括号。') from exc
        values = {}
        for i, column in enumerate(references):
            if column not in result.columns:
                raise ValueError(f'公式依赖列不存在：{column}')
            values[f'v{i}'] = pd.to_numeric(result[column], errors='coerce').to_numpy(dtype=float)
        def visit(node):
            check_cancel(cancel)
            if isinstance(node, ast.Expression):
                return visit(node.body)
            if isinstance(node, ast.Name) and node.id in values:
                return values[node.id]
            if isinstance(node, ast.Constant) and type(node.value) in (float, int):
                return float(node.value)
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                return visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
            if isinstance(node, ast.BinOp) and type(node.op) in operations:
                return operations[type(node.op)](visit(node.left), visit(node.right))
            raise ValueError('公式仅支持 [列名]、数字、+ - * / 和括号。')
        with np.errstate(all='ignore'):
            value = np.broadcast_to(visit(tree), (len(result),)).copy()
        value[~np.isfinite(value)] = np.nan
        result[name] = value
    return result


def source_text_page(path, offset=0, encoding='utf-8', cancel=None):
    """Read at most 200 physical lines / 256 KiB, never scan to count rows.

    A long physical line may be continued on the next page, explicitly marked.
    Byte offsets make Next O(page size) and work even on GB-sized inputs.
    """
    check_cancel(cancel)
    source = Path(path)
    if source.suffix.lower() not in ('.csv', '.txt', '.tsv', '.dat', '.asc', '.xyz'):
        raise ValueError('此格式通过专用解析器预览分析输入，不作为文本重复解析。')
    encoding = encoding if encoding and encoding != 'auto' else 'utf-8'
    with source.open('rb') as stream:
        stream.seek(offset)
        lines, consumed = [], 0
        while len(lines) < PAGE_SIZE and consumed < SOURCE_BYTE_LIMIT:
            check_cancel(cancel)
            line = stream.readline(SOURCE_BYTE_LIMIT - consumed)
            if not line:
                break
            lines.append(line.decode(encoding, errors='replace').rstrip('\r\n'))
            consumed += len(line)
        end = stream.tell()
        eof = end >= source.stat().st_size
    return lines, end, eof


def analysis_states(owner, frame):
    """Join on preserved DataFrame identities; never infer source row numbers."""
    states = pd.Series('无效 / 未映射', index=frame.index, dtype=object)
    mapped = getattr(owner, 'df_raw', None)
    if mapped is None:
        return states
    states.loc[states.index.isin(mapped.index)] = '分析输入'
    manual = getattr(owner, 'manual_mask', None)
    if manual is not None and len(manual) == len(mapped):
        states.loc[states.index.isin(mapped.index[~np.asarray(manual, dtype=bool)])] = '手动删除'
        states.loc[states.index.isin(mapped.index[np.asarray(manual, dtype=bool)])] = 'ROI / 滤波排除'
    active = getattr(owner, 'active_idx', None)
    if getattr(owner, '_pending_sigma_reanalysis', False) or getattr(owner, '_task_thread', None) is not None:
        states.loc[states.eq('ROI / 滤波排除')] = '等待计算完成'
    elif active is not None and getattr(owner, 'last_metrics', None) is not None:
        states.loc[states.index.isin(mapped.index[np.asarray(active, dtype=int)])] = '参与计算'
    return states
