"""Local-process invocation admission; elapsed work never resets the deadline."""
from contextlib import contextmanager
from contextvars import ContextVar
import math
import time


_ACTIVE = ContextVar('supervisor_invocation_budget', default=None)


class InvocationBudgetExpired(TimeoutError):
    pass


class ModelDispatchDeferred(RuntimeError):
    """Raised only before the proposal subprocess is invoked."""


def active_budget():
    return _ACTIVE.get()


def _number(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError('finite invocation timing required')
    return float(value)


class InvocationBudget:
    def __init__(self, deadline, *, clock=None):
        self.clock=clock or time.monotonic
        self.last=_number(self.clock())
        self.deadline=_number(deadline)
        if not 0 < self.deadline-self.last <= 240:
            raise ValueError('invocation deadline must be within 240 seconds')
        self.reserve=0.0

    def remaining(self):
        current=_number(self.clock())
        if current<self.last:raise ValueError('invocation clock regressed')
        self.last=current
        return max(0.0,self.deadline-current)

    def can_fit(self, seconds, *, reserve=0):
        seconds,reserve=_number(seconds),_number(reserve)
        if seconds<=0 or reserve<0:raise ValueError('positive work and nonnegative reserve required')
        return self.remaining() >= seconds+reserve

    def timeout(self, maximum, *, reserve=None):
        maximum=_number(maximum)
        tail=self.reserve if reserve is None else _number(reserve)
        if maximum<=0 or tail<0:raise ValueError('positive work and nonnegative reserve required')
        available=self.remaining()-tail
        if available<=0:raise InvocationBudgetExpired('invocation reconciliation reserve reached')
        return min(maximum,available)

    def model_timeout(self, maximum):
        maximum=_number(maximum)
        if maximum<=0:raise ValueError('positive model timeout required')
        if self.remaining()<maximum+40:
            raise ModelDispatchDeferred('invocation budget cannot admit model dispatch')
        return maximum

    @contextmanager
    def activate(self):
        token=_ACTIVE.set(self)
        try:yield self
        finally:_ACTIVE.reset(token)

    @contextmanager
    def phase(self, *, reserve):
        reserve=_number(reserve)
        if reserve<0:raise ValueError('nonnegative reserve required')
        previous=self.reserve;self.reserve=reserve
        try:yield self
        finally:self.reserve=previous
