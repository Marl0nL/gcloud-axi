"""Top-level dispatch.

Every command returns ``(Out, exit_code)`` or raises an ``AxiError``. Rendering
and exit-code selection happen here, once, so the contract cannot drift between
subcommands.
"""

import sys

from . import __version__, context
from .commands import (
    builds,
    grant,
    iam,
    jobs,
    ledger,
    logs,
    overview,
    revoke,
    run,
    secrets,
    sql,
    status,
)
from .errors import AxiError, UsageError

COMMANDS = {
    "overview": overview.dispatch,
    "run": run.dispatch,
    "logs": logs.dispatch,
    "jobs": jobs.dispatch,
    "sql": sql.dispatch,
    "secrets": secrets.dispatch,
    "iam": iam.dispatch,
    "builds": builds.dispatch,
    "grant": grant.dispatch,
    "ledger": ledger.dispatch,
    "revoke": revoke.dispatch,
}


def _context_factory():
    """Build a Context from a parsed args object, reusing one config load."""
    cache = {}

    def factory(args):
        if "config" not in cache:
            from . import config as config_mod

            cache["config"] = config_mod.load()
        return context.Context(
            cfg=cache["config"],
            project_flag=args.get("project"),
            region_flag=args.get("region"),
        )

    return factory


def main(argv):
    if argv and argv[0] in ("-v", "-V", "--version"):
        sys.stdout.write("gcloud-axi %s\n" % __version__)
        return 0

    factory = _context_factory()

    try:
        if not argv or argv[0].startswith("-"):
            out, code = status.dispatch(factory, argv)
        else:
            command, rest = argv[0], argv[1:]
            handler = COMMANDS.get(command)
            if handler is None:
                raise UsageError(
                    'unknown command "%s"' % command,
                    code="UNKNOWN_COMMAND",
                    help_lines=[
                        "Known commands: %s" % ", ".join(sorted(COMMANDS)),
                        "Run `gcloud-axi --help` for the full list",
                        "Run `gcloud-axi` with no arguments for ambient state",
                    ],
                )
            out, code = handler(factory, rest)
    except AxiError as exc:
        exc.render().emit()
        return exc.exit_code
    except KeyboardInterrupt:
        AxiError("interrupted", code="INTERRUPTED").render().emit()
        return 1
    except Exception as exc:
        AxiError(
            "unexpected internal error: %s" % exc,
            code="INTERNAL",
            help_lines=[
                "Re-run the command; if this recurs, report the exact invocation "
                "at the project's issue tracker",
            ],
            fields=[("exceptionType", type(exc).__name__)],
        ).render().emit()
        return 1

    out.emit()
    return code
