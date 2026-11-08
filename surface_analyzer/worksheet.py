"""Sparse Excel-style formula templates over an immutable analysis input.

References bind column identities, not presentation offsets. A filled million
row column is one rule; evaluation uses bounded vector batches, never widgets
or one formula object per input point.
"""
import ast
import copy
import re
from uuid import uuid4

import numpy as np
import pandas as pd

from .data_preview import check_cancel

CELL = re.compile(r'(?<![\w.])(\$?)([A-Za-z]+)(\$?)([1-9]\d*)')
ERRORS = {0: '', 1: '#EMPTY!', 2: '#VALUE!', 3: '#DIV/0!', 4: '#REF!', 5: '#CYCLE!', 6: '#NUM!'}
CHUNK = 65536


def letters(index):
    result = ''
    while index >= 0:
        index, digit = divmod(index, 26)
        result = chr(65 + digit) + result
        index -= 1
    return result


def column_number(text):
    result = 0
    for ch in text.upper():
        result = result*26 + ord(ch)-64
    return result-1


class Worksheet:
    def __init__(self, base, saved=None, legacy=()):
        if not base.columns.is_unique:
            raise ValueError('源列名重复，无法唯一匹配公式依赖。')
        self.base = base
        self.size = len(base)
        self.data = copy.deepcopy(saved) if saved else {
            'columns': [dict(id='source:'+str(c), name=str(c), source=str(c), unit='')
                        for c in base.columns if not str(c).startswith('_')], 'rules': {}, 'target_z': None}
        self.validate()
        if saved:
            known = {c.get('source') for c in self.columns}
            for name in base.columns:
                if not str(name).startswith('_') and name not in known:
                    self.columns.append(dict(id='source:'+str(name),name=str(name),source=str(name),unit=''))
        self.values = {}
        self.errors = {}
        if not saved:
            for d in legacy:
                column = self.insert(len(self.columns), d['name'], d['unit'])
                def replace(match):
                    position = next((i for i, c in enumerate(self.columns) if c['name'] == match[1]), None)
                    if position is None:
                        raise ValueError(f'公式依赖列不存在：{match[1]}')
                    return letters(position)+'2'
                formula = '='+re.sub(r'\[([^\]]+)\]', replace, d['expression'])
                self.edit(column['id'], 0, formula, end=None)

    @property
    def columns(self):
        return self.data['columns']

    def validate(self):
        columns = self.data.get('columns')
        if not isinstance(columns, list) or len(columns) > len(self.base.columns)+24:
            raise ValueError('工作表列结构无效，派生列最多 24 个。')
        names, ids = set(), set()
        for col in columns:
            if not isinstance(col, dict) or not isinstance(col.get('id'), str) or not isinstance(col.get('name'), str):
                raise ValueError('工作表列定义无效。')
            if col['id'] in ids or col['name'] in names or not col['name'] or col['name'].startswith('_'):
                raise ValueError('工作表列身份或名称重复 / 无效。')
            ids.add(col['id']); names.add(col['name'])
            if col.get('source') is not None and col['source'] not in self.base.columns:
                raise ValueError(f"工作表缺少源列：{col['source']}")
            if col.get('source') is not None and col['name'] != col['source']:
                raise ValueError('原始列名称不可修改。')
            if col.get('source') is None and col['name'] in self.base.columns:
                raise ValueError(f"派生列与源列重名：{col['name']}；请先重命名派生列。")
            if col.get('unit','') not in ('','mm','µm','nm'):
                raise ValueError('工作表单位必须为空、mm、µm 或 nm。')
        if self.data.get('target_z') is not None and self.data['target_z'] not in ids:
            raise ValueError('工作表分析 Z 指向不存在的列。')
        source_names = [c['source'] for c in columns if c.get('source') is not None]
        if sum(c.get('source') is None for c in columns) > 24:
            raise ValueError('派生列最多 24 个。')
        if len(source_names) != len(set(source_names)):
            raise ValueError('工作表源列映射重复。')
        rules = self.data.get('rules')
        if not isinstance(rules, dict):
            raise ValueError('工作表公式结构无效。')
        derived_ids = {c['id'] for c in columns if c.get('source') is None}
        for col_id, entries in rules.items():
            if col_id not in derived_ids or not isinstance(entries, list):
                raise ValueError('公式目标不是有效派生列。')
            for rule in entries:
                if (not isinstance(rule, dict) or type(rule.get('start')) is not int or rule['start'] < 0
                        or (rule.get('end') is not None and
                            (type(rule['end']) is not int or rule['end'] < rule['start']))):
                    raise ValueError('公式填充范围无效。')
                self._validate_node(rule.get('expr'))

    @staticmethod
    def _validate_node(node, depth=0):
        if depth > 64 or not isinstance(node, dict):
            raise ValueError('公式结构无效或嵌套过深。')
        kind = node.get('kind')
        if kind == 'number' and type(node.get('value')) in (float, int) and np.isfinite(node['value']):
            return
        if kind == 'ref' and isinstance(node.get('column'), str) and type(node.get('row')) is int and type(node.get('absolute_row')) is bool and type(node.get('absolute_col')) is bool:
            return
        if kind == 'neg':
            Worksheet._validate_node(node.get('value'), depth+1)
            return
        if kind in ('+', '-', '*', '/'):
            Worksheet._validate_node(node.get('left'), depth+1)
            Worksheet._validate_node(node.get('right'), depth+1)
            return
        raise ValueError('公式仅支持单元格引用、数字、四则运算和括号。')

    def insert(self, position, name=None, unit=''):
        if sum(c.get('source') is None for c in self.columns) >= 24:
            raise ValueError('派生列最多 24 个。')
        used = {c['name'] for c in self.columns}
        if name is None:
            index = 1
            while f'派生列{index}' in used:
                index += 1
            name = f'派生列{index}'
        if not name.strip() or name in used or name.startswith('_'):
            raise ValueError('列名不能为空、重复或以 _ 开头。')
        column = dict(id=uuid4().hex, name=name, source=None, unit=unit)
        self.columns.insert(position, column)
        self.data['rules'][column['id']] = []
        return column

    def rename(self, identity, name):
        col = self.column(identity)
        if col.get('source') is not None:
            raise ValueError('原始列只读。')
        name = name.strip()
        if not name or name.startswith('_') or any(c['name'] == name and c['id'] != identity for c in self.columns):
            raise ValueError('列名为空、重复或为内部保留名称。')
        col['name'] = name

    def column(self, identity):
        return next(c for c in self.columns if c['id'] == identity)

    def remove(self, identity):
        if self.column(identity).get('source') is not None:
            raise ValueError('原始列只读。')
        self.columns[:] = [c for c in self.columns if c['id'] != identity]
        self.data['rules'].pop(identity, None)
        if self.data.get('target_z') == identity:
            self.data['target_z'] = None

    def parse(self, formula, row):
        if len(formula) > 1000:
            raise ValueError('公式过长。')
        if not formula.startswith('='):
            if not formula.strip():
                return None
            try:
                number = float(formula)
            except ValueError:
                raise ValueError('派生单元格只能输入数字或以 = 开头的公式。') from None
            if not np.isfinite(number):
                raise ValueError('请输入有限数值。')
            return dict(kind='number', value=number)
        refs = {}
        def replace(match):
            index = column_number(match[2])
            key = f'v{len(refs)}'
            refs[key] = dict(kind='ref', column=self.columns[index]['id'] if index < len(self.columns) else 'missing:'+match[2],
                row=int(match[4])-2 if match[3] else int(match[4])-2-row,
                absolute_row=bool(match[3]), absolute_col=bool(match[1]))
            return key
        rewritten = CELL.sub(replace, formula[1:])
        try:
            root = ast.parse(rewritten, mode='eval')
        except (SyntaxError, RecursionError):
            raise ValueError('公式语法错误。') from None
        def convert(node):
            if isinstance(node, ast.Expression):
                return convert(node.body)
            if isinstance(node, ast.Name) and node.id in refs:
                return refs[node.id]
            if isinstance(node, ast.Constant) and type(node.value) in (float, int):
                return dict(kind='number', value=float(node.value))
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                value = convert(node.operand)
                return dict(kind='neg', value=value) if isinstance(node.op, ast.USub) else value
            operations = {ast.Add: '+', ast.Sub: '-', ast.Mult: '*', ast.Div: '/'}
            if isinstance(node, ast.BinOp) and type(node.op) in operations:
                return dict(kind=operations[type(node.op)], left=convert(node.left), right=convert(node.right))
            raise ValueError('仅支持 A1 引用、$、数字、四则运算和括号。')
        result = convert(root)
        self._validate_node(result)
        return result

    def edit(self, identity, row, formula, end='single'):
        if self.column(identity).get('source') is not None:
            raise ValueError('原始列只读。')
        expression = self.parse(formula, row)
        self.data['rules'][identity].append(dict(start=row, end=row if end == 'single' else end, expr=expression))
        # Blank rules use an invalid reference as an explicit, stable empty cell.
        if expression is None:
            self.data['rules'][identity][-1]['expr'] = dict(kind='ref', column='empty', row=0, absolute_row=False, absolute_col=False)

    def rule_at(self, identity, row):
        for rule in reversed(self.data['rules'].get(identity, [])):
            if row >= rule['start'] and (rule['end'] is None or row <= rule['end']):
                return rule
        return None

    def fill(self, identity, source_row, start, end):
        rule = self.rule_at(identity, source_row)
        if rule is None:
            raise ValueError('起始单元格为空，无法填充。')
        self.data['rules'][identity].append(dict(start=start, end=end, expr=copy.deepcopy(rule['expr'])))

    def formula(self, identity, row):
        rule = self.rule_at(identity, row)
        if not rule:
            return ''
        def render(node):
            kind = node['kind']
            if kind == 'number':
                return f"{node['value']:g}"
            if kind == 'ref':
                index = next((i for i, c in enumerate(self.columns) if c['id'] == node['column']), None)
                if index is None:
                    return '#REF!'
                number = (node['row'] if node['absolute_row'] else row+node['row'])+2
                return ('$' if node['absolute_col'] else '')+letters(index)+('$' if node['absolute_row'] else '')+str(number)
            if kind == 'neg':
                return '-'+render(node['value'])
            return '('+render(node['left'])+kind+render(node['right'])+')'
        text = render(rule['expr'])
        return text if rule['expr']['kind'] == 'number' else '='+text

    def evaluate(self, cancel=None, progress=None):
        """Resolve template dependency batches; unresolved fixed points are cycles."""
        self.validate()
        size = self.size
        values, errors, ready, rules = {}, {}, {}, {}
        for col in self.columns:
            check_cancel(cancel)
            identity = col['id']
            if col.get('source') is not None:
                value = pd.to_numeric(self.base[col['source']], errors='coerce').to_numpy(dtype=float)
                values[identity] = value
                errors[identity] = np.where(np.isfinite(value), 0, 2).astype(np.uint8)
                ready[identity] = np.ones(size, bool)
            else:
                values[identity] = np.full(size, np.nan)
                errors[identity] = np.ones(size, np.uint8)
                ready[identity] = np.ones(size, bool)
                assignment = np.full(size, -1, np.int32)
                entries = self.data['rules'].get(identity, [])
                for i, rule in enumerate(entries):
                    stop = size if rule['end'] is None else min(size, rule['end']+1)
                    assignment[rule['start']:stop] = i
                ready[identity][assignment >= 0] = False
                rules[identity] = [(r['expr'], np.flatnonzero(assignment == i)) for i, r in enumerate(entries)]
        def evaluate_node(node, rows):
            kind = node['kind']
            count = len(rows)
            if kind == 'number':
                return np.full(count, node['value']), np.zeros(count, np.uint8), np.ones(count, bool)
            if kind == 'ref':
                targets = np.full(count, node['row'], np.int64) if node['absolute_row'] else rows+node['row']
                valid = (targets >= 0) & (targets < size)
                val, err, done = np.full(count, np.nan), np.full(count, 4, np.uint8), np.ones(count, bool)
                identity = node['column']
                if identity == 'empty':
                    err[:] = 1
                elif identity in values:
                    val[valid] = values[identity][targets[valid]]
                    err[valid] = errors[identity][targets[valid]]
                    done[valid] = ready[identity][targets[valid]]
                return val, err, done
            if kind == 'neg':
                val, err, done = evaluate_node(node['value'], rows)
                return -val, err, done
            a, ea, da = evaluate_node(node['left'], rows)
            b, eb, db = evaluate_node(node['right'], rows)
            err = np.where(ea != 0, ea, eb)
            with np.errstate(all='ignore'):
                value = {'+': np.add, '-': np.subtract, '*': np.multiply, '/': np.divide}[kind](a, b)
            if kind == '/':
                err = np.where((err == 0) & (b == 0), 3, err).astype(np.uint8)
            err = np.where((err == 0) & ~np.isfinite(value), 6, err).astype(np.uint8)
            value[err != 0] = np.nan
            return value, err, da & db
        pending_total = sum(int((~ready[c]).sum()) for c in rules)
        resolved_total = 0
        while True:
            changed = 0
            for identity, entries in rules.items():
                for node, all_rows in entries:
                    pending = all_rows[~ready[identity][all_rows]]
                    for start in range(0, len(pending), CHUNK):
                        check_cancel(cancel)
                        rows = pending[start:start+CHUNK]
                        value, err, done = evaluate_node(node, rows)
                        completed = rows[done]
                        values[identity][completed] = value[done]
                        errors[identity][completed] = err[done]
                        ready[identity][completed] = True
                        changed += len(completed)
            resolved_total += changed
            if progress:
                progress(int(100*resolved_total/max(1, pending_total)), f'正在计算工作表 {resolved_total:,}/{pending_total:,}')
            if not changed:
                break
        for identity in rules:
            errors[identity][~ready[identity]] = 5
        check_cancel(cancel)
        self.values, self.errors = values, errors
        frame = self.base.copy(deep=False)
        for col in self.columns:
            if col.get('source') is None:
                frame[col['name']] = values[col['id']]
        visible = [c['name'] for c in self.columns]
        return frame[visible+[c for c in frame.columns if c not in visible]]

    def descriptors(self):
        return [dict(name=c['name'], unit=c.get('unit') or '',
                     expression='; '.join(f"{r['start']+2}:{'末行' if r['end'] is None else r['end']+2} {self.formula(c['id'], r['start'])}" for r in self.data['rules'].get(c['id'], [])))
                for c in self.columns if c.get('source') is None]
