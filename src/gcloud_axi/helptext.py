"""One renderer for every `--help` so they all read the same."""

from . import toon

GLOBAL_FLAGS = [
    "--project <id>   project to act on (flag > config PROJECT > gcloud's own configured project)",
    "--region <id>    region to act on (flag > config REGION > gcloud's run/region; omitted = all regions)",
    "--help           this text",
]


def render(usage, description=None, subcommands=None, flags=None, notes=None,
           examples=None, exits=True):
    out = toon.Out()
    out.raw("usage: " + usage)
    if description:
        out.field("description", description)
    if subcommands:
        out.list_lines("subcommands", subcommands)
    if flags:
        out.list_lines("flags", flags)
    if notes:
        out.list_lines("notes", notes)
    if examples:
        out.list_lines("examples", examples)
    if exits:
        out.raw("exit: 0 success, 1 error, 2 usage error (unknown flags fail loud)")
    return out
