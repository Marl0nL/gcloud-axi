"""A deliberately small flag parser.

argparse is avoided on purpose: it prints its own usage to stderr, exits with
its own codes, and tolerates abbreviations. The contract here is stricter -
unknown flags fail loud with exit 2, and every message is ours.
"""

from .errors import UsageError, help_command, unknown_flag

BOOL = "bool"
VALUE = "value"
LIST = "list"


class Parsed(object):
    def __init__(self, values, positional):
        self._values = values
        self.positional = positional

    def __getitem__(self, name):
        return self._values[name]

    def get(self, name, default=None):
        value = self._values.get(name)
        return default if value is None else value

    def has(self, name):
        return self._values.get(name) is not None

    def int(self, name, default=None, minimum=None, maximum=None):
        raw = self._values.get(name)
        if raw is None:
            return default
        try:
            value = int(str(raw).strip())
        except ValueError:
            raise UsageError(
                'flag --%s expects an integer, got "%s"' % (name, raw),
                code="INVALID_VALUE",
            )
        if minimum is not None and value < minimum:
            raise UsageError(
                "flag --%s must be >= %d, got %d" % (name, minimum, value),
                code="INVALID_VALUE",
            )
        if maximum is not None and value > maximum:
            raise UsageError(
                "flag --%s must be <= %d, got %d" % (name, maximum, value),
                code="INVALID_VALUE",
            )
        return value


def parse(argv, spec, command, max_positional=None):
    """Parse ``argv`` against ``spec`` (``{"flag-name": KIND}``).

    Accepts both ``--flag value`` and ``--flag=value``. ``--`` ends flag
    parsing. Anything not declared in ``spec`` is a usage error.
    """
    values = {}
    for name, kind in spec.items():
        values[name] = [] if kind == LIST else None
    positional = []
    index = 0
    only_positional = False

    while index < len(argv):
        token = argv[index]
        index += 1

        if only_positional or not token.startswith("-") or token == "-":
            positional.append(token)
            continue
        if token == "--":
            only_positional = True
            continue

        if token.startswith("--"):
            body = token[2:]
        else:
            # Short flags are not part of this surface; reject them by name so
            # the message names the token the caller actually typed.
            raise unknown_flag(token, command)

        inline = None
        if "=" in body:
            body, inline = body.split("=", 1)

        if body not in spec:
            raise unknown_flag("--" + body, command)

        kind = spec[body]
        if kind == BOOL:
            if inline is not None:
                if inline.lower() in ("true", "1", "yes"):
                    values[body] = True
                    continue
                if inline.lower() in ("false", "0", "no"):
                    values[body] = False
                    continue
                raise UsageError(
                    'flag --%s is a boolean and does not take "%s"' % (body, inline),
                    code="INVALID_VALUE",
                )
            values[body] = True
            continue

        if inline is None:
            if index >= len(argv):
                raise UsageError(
                    "flag --%s expects a value" % body,
                    code="MISSING_VALUE",
                    help_lines=[
                        "Run `%s --help` to see supported flags" % help_command(command)
                    ],
                )
            inline = argv[index]
            index += 1

        if kind == LIST:
            values[body].append(inline)
        else:
            values[body] = inline

    if max_positional is not None and len(positional) > max_positional:
        raise UsageError(
            "unexpected argument \"%s\"" % positional[max_positional],
            code="UNEXPECTED_ARGUMENT",
            help_lines=[
                "Run `%s --help` to see the expected form" % help_command(command)
            ],
        )

    return Parsed(values, positional)


def wants_help(argv):
    return "--help" in argv or "-h" in argv
