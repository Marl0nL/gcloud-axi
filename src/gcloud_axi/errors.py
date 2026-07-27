"""Structured errors and exit codes.

Exit codes are part of the contract:

* ``0`` success
* ``1`` error (anything that went wrong at runtime)
* ``2`` usage error - an unknown flag, a missing argument, an unknown command

Errors are written to *stdout* as structured output, not to stderr; stderr is
reserved for debug noise from the underlying tools.
"""

from . import toon


class AxiError(Exception):
    """An error the caller is expected to read and act on."""

    exit_code = 1
    code = "ERROR"

    def __init__(self, message, code=None, help_lines=None, fields=None):
        Exception.__init__(self, message)
        self.message = message
        if code:
            self.code = code
        self.help_lines = list(help_lines or [])
        # Extra key/value context rendered between `code:` and `help[]`.
        self.fields = list(fields or [])

    def render(self):
        out = toon.Out()
        out.field("error", self.message)
        out.field("code", self.code)
        for key, value in self.fields:
            out.field(key, value)
        out.help(self.help_lines)
        return out


class UsageError(AxiError):
    """Unknown flag, missing argument, unknown subcommand."""

    exit_code = 2
    code = "USAGE_ERROR"


class ConfigError(AxiError):
    code = "CONFIG_ERROR"


class NotConfiguredError(AxiError):
    """A capability that requires declarative configuration has none."""

    code = "NOT_CONFIGURED"


class NotFoundError(AxiError):
    code = "NOT_FOUND"


class PermissionError_(AxiError):
    code = "PERMISSION_DENIED"


class CredentialExpiredError(AxiError):
    code = "CREDENTIAL_EXPIRED"


class GcloudError(AxiError):
    code = "GCLOUD_ERROR"


def help_command(command):
    """`gcloud-axi run status` / `gcloud-axi` - the label to point --help at."""
    return ("gcloud-axi " + (command or "")).strip()


def unknown_flag(flag, command):
    """The loud failure for an unrecognised flag - never silently ignored."""
    return UsageError(
        'unknown flag "%s"' % flag,
        code="UNKNOWN_FLAG",
        help_lines=["Run `%s --help` to see supported flags" % help_command(command)],
    )
