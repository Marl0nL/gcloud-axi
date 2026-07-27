"""Duration and timestamp helpers.

Everything here is UTC. Timestamps are rendered RFC 3339 with a trailing ``Z``
so they compare lexicographically and paste straight into a logging filter.
"""

import datetime
import re

from .errors import UsageError

_DURATION = re.compile(r"^(\d+)\s*([smhdw])$", re.I)
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def parse_duration(value, flag="since"):
    """Parse ``30m`` / ``2h`` / ``7d`` into seconds."""
    if value is None:
        return None
    match = _DURATION.match(str(value).strip())
    if not match:
        raise UsageError(
            'flag --%s expects a duration like 30m, 2h or 7d, got "%s"' % (flag, value),
            code="INVALID_VALUE",
            help_lines=["Accepted units: s (seconds), m, h, d, w"],
        )
    return int(match.group(1)) * _UNITS[match.group(2).lower()]


def rfc3339(moment):
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(seconds):
    """RFC 3339 timestamp for ``seconds`` in the past."""
    return rfc3339(now() - datetime.timedelta(seconds=seconds))


def parse_timestamp(value):
    """Parse the RFC 3339 timestamps gcloud emits. Returns None if unparseable."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Python's parser rejects more than 6 fractional-second digits.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def short(value):
    """Compact a timestamp to ``YYYY-MM-DDTHH:MMZ`` for table cells."""
    parsed = parse_timestamp(value)
    if parsed is None:
        return value
    return parsed.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def relative(value, reference=None):
    """Human-scale age of a timestamp, e.g. ``4m``, ``3h``, ``12d``."""
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    reference = reference or now()
    delta = int((reference - parsed).total_seconds())
    if delta < 0:
        return "in " + relative_seconds(-delta)
    return relative_seconds(delta) + " ago"


def relative_seconds(delta):
    if delta < 60:
        return "%ds" % delta
    if delta < 3600:
        return "%dm" % (delta // 60)
    if delta < 86400:
        return "%dh" % (delta // 3600)
    return "%dd" % (delta // 86400)


def duration_between(start, end):
    a, b = parse_timestamp(start), parse_timestamp(end)
    if a is None or b is None:
        return None
    return relative_seconds(max(0, int((b - a).total_seconds())))
