"""Bounded prefix traversal; yields every physical record without filtering it."""
from pathlib import Path
import time
from .import_preflight import IMPORT_GUARD_POLICY
from .workers import TaskCancelled


class PrefixSearchLimitError(ValueError):
    """Search evidence was incomplete; this is not a format mismatch."""


def blank_record(line):
    return not line.strip().strip('\ufeff').strip(' \t,;；|"\r\n')


def prefix_lines(path, encoding, cancel_event=None, progress=None,
                 phase='定位正文', record_limit=None, budget_start_line=0,
                 policy=IMPORT_GUARD_POLICY):
    """Bound bytes / wall time / meaningful records, not leading physical blanks.

    Manual lower bounds waive the record counter before the selected line,
    never the byte/time limits. TextIO preserves UTF-16 and physical newlines.
    Buffer byte position includes its small read-ahead buffer.
    """
    started = time.monotonic()
    count = 0
    limit = policy.prefix_max_records if record_limit is None else min(record_limit, policy.prefix_max_records)
    def stop(reason, physical):
        raise PrefixSearchLimitError(
            f'{phase}搜索达到资源限制（{reason}），已检查 {physical:,} 个物理行；'
            '尚不能确认文件格式错误。请核对正文搜索起始行、格式和矩阵尺寸；'
            f'搜索预算为 {policy.prefix_max_bytes//1024:,} KB / {policy.prefix_max_seconds:g} 秒 / {limit:,} 个非空记录。')
    with Path(path).open('r',encoding=encoding,errors='strict') as handle:
        number = 0
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise TaskCancelled()
            if time.monotonic()-started >= policy.prefix_max_seconds:
                stop('耗时',number)
            line = handle.readline(policy.prefix_max_line_chars+1)
            if not line:
                return
            if len(line) > policy.prefix_max_line_chars:
                stop('单行过长',number+1)
            consumed = handle.buffer.tell()
            if consumed > policy.prefix_max_bytes:
                stop('字节数',number+1)
            if number >= budget_start_line and not blank_record(line):
                count += 1
                if count > limit:
                    stop('非空记录数',number+1)
            if progress is not None and number % 256 == 0:
                progress(12,f'正在{phase}：{number+1:,} 物理行，约 {consumed//1024:,} KB（有界搜索，可取消）')
            yield number,line
            number += 1
