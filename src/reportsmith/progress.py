"""Terminal progress bar utilities with tqdm support and smooth ASCII/ANSI fallback."""

import sys
from typing import Optional

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


class ProgressBar:
    """Terminal progress bar wrapper with tqdm support and smooth ASCII fallback."""

    def __init__(self, total: int, desc: str = "Progress", unit: str = "sec"):
        self.total = max(1, total)
        self.current = 0
        self.desc = desc
        self.unit = unit
        self._postfix = ""
        self._tqdm = None

        if tqdm is not None:
            try:
                self._tqdm = tqdm(
                    total=self.total,
                    desc=self.desc,
                    unit=self.unit,
                    dynamic_ncols=True,
                    leave=True,
                    bar_format="{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]",
                )
            except Exception:
                self._tqdm = None

        if self._tqdm is None:
            self._render(status="Starting...")

    def update(self, n: int = 1, status: str = ""):
        self.current = min(self.total, self.current + n)
        if status:
            self._postfix = status
        if self._tqdm is not None:
            if status:
                self._tqdm.set_postfix_str(status[:35])
            if n > 0:
                self._tqdm.update(n)
        else:
            self._render(status=status)

    def set_status(self, status: str):
        self._postfix = status
        if self._tqdm is not None:
            self._tqdm.set_postfix_str(status[:35])
        else:
            self._render(status=status)

    def _render(self, status: str = ""):
        pct = (self.current / self.total) * 100
        bar_len = 25
        filled = int(bar_len * (self.current / self.total))
        bar = "=" * filled + (">" if filled < bar_len else "") + " " * max(0, bar_len - filled - (1 if filled < bar_len else 0))
        msg = f"\r[PROGRESS] {self.desc}: |{bar}| {self.current}/{self.total} ({pct:.0f}%)"
        disp_status = status or self._postfix
        if disp_status:
            msg += f" - {disp_status[:35]}"
        sys.stderr.write(msg)
        sys.stderr.flush()

    def close(self):
        if self._tqdm is not None:
            self._tqdm.close()
        else:
            sys.stderr.write("\n")
            sys.stderr.flush()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

