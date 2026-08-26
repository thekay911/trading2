"""매매 기록. 시그널·주문·기각 사유·포지션 관리 동작을 JSONL 로 남긴다.

실전에서 "왜 안 들어갔는가" 를 나중에 확인할 수 있어야 하므로,
체결된 주문뿐 아니라 **기각된 시그널까지** 전부 기록한다.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any


def _plain(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if is_dataclass(v) and not isinstance(v, type):
        return {k: _plain(x) for k, x in asdict(v).items()}
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


class Journal:
    def __init__(self, path: str | None = None, echo: bool = True):
        self.path = path
        self.echo = echo
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

    def write(self, kind: str, **fields: Any) -> dict:
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "kind": kind}
        rec.update({k: _plain(v) for k, v in fields.items()})
        if self.path:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if self.echo:
            print(self._line(rec), file=sys.stderr, flush=True)
        return rec

    @staticmethod
    def _line(rec: dict) -> str:
        head = f"[{rec['ts']}] {rec['kind']:<10}"
        rest = " ".join(f"{k}={v}" for k, v in rec.items() if k not in ("ts", "kind"))
        return f"{head} {rest}"
