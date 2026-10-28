"""In-memory admission gate. The application binds a verified lease at startup."""

try:
    from ..localization import text as tr
except ImportError:
    from localization import text as tr
_gate = None


def bind_gate(gate):
    global _gate
    _gate = gate


def runtime_allowed():
    return _gate is None or _gate.allowed()


def enforce_student_capacity(connection):
    if _gate is None:
        return
    lease = _gate.require_lease()
    count = connection.execute("SELECT COUNT(*) FROM students WHERE capacity_archived=0").fetchone()[0]
    if count >= lease["student_limit"]:
        raise ValueError(tr("The subscription's student capacity is full. Contact support to increase capacity."))
