"""Server-side device authentication and source rate limiting.

The actor identity used for idempotency always comes from the verified token.
Nothing in the request body can name a different device.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable


MAX_TOKEN_BYTES = 128

ERROR_MESSAGES = {
    "unauthorized": "设备凭据无效或已撤销",
    "protocol_incompatible": "协议版本不兼容",
    "invalid_request": "请求格式无效",
    "invalid_cursor": "游标无效",
    "cursor_ahead": "游标超出当前范围",
    "session_not_found": "会话不存在",
    "run_not_found": "回合不存在",
    "approval_not_found": "审批不存在",
    "approval_expired": "审批已失效",
    "approval_details_insufficient": "审批详情不足，不能允许",
    "resource_changed": "状态已变化，请刷新",
    "decision_conflict": "已有相反的审批决定",
    "idempotency_conflict": "请求标识已用于不同请求",
    "operation_not_found": "操作不存在",
    "active_run_exists": "已有活动回合",
    "thread_unavailable": "会话线程不可用",
    "turn_not_bound": "回合尚未绑定",
    "active_slot_mismatch": "活动回合状态不一致",
    "execution_unavailable": "执行环境不可用",
    "command_result_unknown": "受理结果未知，请用原请求标识查询",
    "dispatch_signal_unavailable": "派发唤醒失败，操作已受理可查询",
    "host_stopped": "Host 正在停止",
    "request_cancelled": "请求已取消",
    "payload_too_large": "请求内容超限",
    "rate_limited": "请求过于频繁",
    "pairing_unavailable": "配对不可用或秘密已失效",
    "host_unavailable": "Host 当前不可用",
    "internal_error": "Host 内部错误",
}

RETRYABLE = frozenset({
    "execution_unavailable", "command_result_unknown",
    "dispatch_signal_unavailable", "rate_limited", "internal_error",
})


def error_body(code: str, *, request_id: str | None = None) -> dict:
    body = {"error": {"code": code,
                      "message": ERROR_MESSAGES.get(code, code),
                      "retryable": code in RETRYABLE}}
    if request_id:
        body["error"]["request_id"] = request_id
    return body


class RateLimiter:
    """Two fixed-capacity sliding windows: per source and globally."""

    def __init__(self, *, per_source: int = 5, global_limit: int = 20,
                 window_seconds: float = 60.0,
                 clock: Callable[[], float] = time.monotonic):
        if (type(per_source) is not int or per_source < 1
                or type(global_limit) is not int or global_limit < 1
                or not isinstance(window_seconds, (int, float))
                or isinstance(window_seconds, bool) or window_seconds <= 0):
            raise ValueError("invalid_rate_limit")
        self._per_source = per_source
        self._global = global_limit
        self._window = float(window_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._sources: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()

    def allow(self, source: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        now = self._clock()
        cutoff = now - self._window
        with self._lock:
            while self._all and self._all[0] <= cutoff:
                self._all.popleft()
            bucket = self._sources.setdefault(source, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._per_source or len(self._all) >= self._global:
                waits = []
                if bucket:
                    waits.append(bucket[0] + self._window - now)
                if self._all:
                    waits.append(self._all[0] + self._window - now)
                retry = max(1, int(min(waits) + 0.999)) if waits else 1
                return False, retry
            bucket.append(now)
            self._all.append(now)
            return True, 0

    def forget(self, source: str) -> None:
        """Drop one source bucket; used when its window drains naturally."""
        with self._lock:
            self._sources.pop(source, None)