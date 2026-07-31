"""TOON-style output rendering.

TOON (Token-Optimized Object Notation) drops the braces, quotes and commas that
JSON spends tokens on while staying unambiguous to a language model:

    services[2]{name,region,status}:
      api,us-central1,READY
      worker,us-central1,DEGRADED

Everything this tool prints goes through :class:`Out` so the shape stays
consistent across subcommands.
"""

_ALWAYS_QUOTE = ('"', "\n", "\r", "\t")


def scalar(value, in_row=False):
    """Render a single value in TOON scalar form.

    ``in_row`` marks a table cell, where a comma would be read as a field
    separator and therefore forces quoting. A comma in a ``key: value`` line is
    unambiguous, so those stay unquoted and cheaper to read.
    """
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "":
        return '""'
    if text != text.strip():
        return _quote(text)
    if in_row and "," in text:
        return _quote(text)
    for ch in _ALWAYS_QUOTE:
        if ch in text:
            return _quote(text)
    if text[0] in "\"'[{" or text in ("null", "true", "false"):
        return _quote(text)
    return text


def _quote(text):
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return '"' + escaped + '"'


def truncate(text, limit, label="chars"):
    """Return ``(text, hint)`` where hint is None when nothing was cut.

    The hint always states the full size so the caller knows what it is missing.
    """
    if text is None:
        return None, None
    text = str(text)
    if limit is None or len(text) <= limit:
        return text, None
    hint = "(truncated, %d %s total - use --full for the complete value)" % (
        len(text),
        label,
    )
    return text[:limit].rstrip(), hint


class Out(object):
    """Accumulates output lines, then renders them once."""

    def __init__(self):
        self.lines = []

    # -- primitives ---------------------------------------------------------

    def raw(self, line=""):
        self.lines.append(line)
        return self

    def field(self, key, value, indent=0):
        self.lines.append("%s%s: %s" % ("  " * indent, key, scalar(value)))
        return self

    def block(self, name, pairs, indent=0):
        """A named group of key/value pairs.

        ``pairs`` is a sequence of ``(key, value)`` tuples; ``None`` entries are
        skipped so callers can build them conditionally.
        """
        self.lines.append("%s%s:" % ("  " * indent, name))
        for pair in pairs:
            if pair is None:
                continue
            key, value = pair
            self.field(key, value, indent=indent + 1)
        return self

    def table(self, name, fields, rows, indent=0):
        """A list of records sharing one schema.

        ``rows`` is a sequence of dicts or of sequences aligned with ``fields``.
        An empty ``rows`` renders the definitive empty state instead.
        """
        rows = list(rows)
        if not rows:
            return self.empty(name, indent=indent)
        pad = "  " * indent
        self.lines.append(
            "%s%s[%d]{%s}:" % (pad, name, len(rows), ",".join(fields))
        )
        for row in rows:
            if isinstance(row, dict):
                cells = [scalar(row.get(f), in_row=True) for f in fields]
            else:
                cells = [scalar(cell, in_row=True) for cell in row]
            self.lines.append("%s  %s" % (pad, ",".join(cells)))
        return self

    def empty(self, name, indent=0):
        """The definitive empty state: an explicit zero, never silence."""
        pad = "  " * indent
        self.lines.append("%scount: 0" % pad)
        self.lines.append("%s%s: []" % (pad, name))
        return self

    def list_lines(self, name, items, indent=0):
        """A counted list of free-form lines, e.g. ``help[2]:``."""
        items = [i for i in items if i]
        if not items:
            return self
        pad = "  " * indent
        self.lines.append("%s%s[%d]:" % (pad, name, len(items)))
        for item in items:
            self.lines.append("%s  %s" % (pad, item))
        return self

    def note(self, text):
        """A single free-form annotation line (truncation hints, caps, ...)."""
        if text:
            self.lines.append("note: %s" % text)
        return self

    def warnings(self, items):
        return self.list_lines("warnings", items)

    def help(self, items):
        """Contextual next-step templates, printed last."""
        return self.list_lines("help", items)

    def insert_before_help(self, lines):
        """Splice lines in ahead of the trailing ``help[]``.

        Something learned while a command ran - that a read fell back to another
        credential, say - has to reach the reader without displacing the
        next-step hints from the end, where every other command puts them.
        """
        lines = [line for line in lines if line is not None]
        if not lines:
            return self
        cut = len(self.lines)
        for index in range(len(self.lines) - 1, -1, -1):
            if self.lines[index].startswith("help["):
                cut = index
                break
        self.lines[cut:cut] = lines
        return self

    # -- rendering ----------------------------------------------------------

    def text(self):
        return "\n".join(self.lines)

    def emit(self, stream=None):
        import sys

        stream = stream or sys.stdout
        body = self.text()
        if body:
            stream.write(body + "\n")
        return self
