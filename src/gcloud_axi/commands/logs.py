"""`gcloud-axi logs <service|job>` - bounded, truncated log reads."""

from .. import flags, helptext, resources, timeutil, toon
from ..errors import UsageError

LOG_FLAGS = {
    "project": flags.VALUE,
    "region": flags.VALUE,
    "since": flags.VALUE,
    "severity": flags.VALUE,
    "query": flags.VALUE,
    "limit": flags.VALUE,
    "type": flags.VALUE,
    "full": flags.BOOL,
}

SEVERITIES = [
    "DEFAULT",
    "DEBUG",
    "INFO",
    "NOTICE",
    "WARNING",
    "ERROR",
    "CRITICAL",
    "ALERT",
    "EMERGENCY",
]

MESSAGE_LIMIT = 200


def help_out():
    return helptext.render(
        "gcloud-axi logs <service|job> [flags]",
        description="Recent log entries for one Cloud Run service or job, bounded and truncated.",
        flags=helptext.GLOBAL_FLAGS
        + [
            "--since <dur>       how far back to look, e.g. 30m, 2h, 3d (default 1h)",
            "--severity <level>  minimum severity: %s" % "|".join(s.lower() for s in SEVERITIES),
            "--query <text>      substring match against the message body",
            "--limit <n>         maximum entries returned (default 20, max 1000)",
            "--type <kind>       narrow to `service` or `job`; default matches either",
            "--full              print complete messages instead of truncating",
        ],
        notes=[
            "Messages are truncated to %d characters by default; the full length is always "
            "reported in the size hint" % MESSAGE_LIMIT,
            "The result always states the window and filter used, so an empty result is "
            "unambiguous rather than silent",
        ],
        examples=[
            "gcloud-axi logs my-service",
            "gcloud-axi logs my-service --since 6h --severity error",
            "gcloud-axi logs my-job --query timeout --limit 50 --full",
        ],
    )


def _build_filter(name, kind, since_seconds, severity, query):
    clauses = []
    if kind == "service":
        clauses.append(
            '(resource.type="cloud_run_revision" AND resource.labels.service_name="%s")'
            % name
        )
    elif kind == "job":
        clauses.append(
            '(resource.type="cloud_run_job" AND resource.labels.job_name="%s")' % name
        )
    else:
        clauses.append(
            '((resource.type="cloud_run_revision" AND resource.labels.service_name="%s")'
            ' OR (resource.type="cloud_run_job" AND resource.labels.job_name="%s"))'
            % (name, name)
        )
    clauses.append('timestamp>="%s"' % timeutil.ago(since_seconds))
    if severity:
        clauses.append("severity>=%s" % severity)
    if query:
        escaped = query.replace('"', '\\"')
        clauses.append(
            '(textPayload:"%s" OR jsonPayload.message:"%s")' % (escaped, escaped)
        )
    return " AND ".join(clauses)


def _message(entry):
    text = entry.get("textPayload")
    if text:
        return str(text)
    payload = entry.get("jsonPayload")
    if isinstance(payload, dict):
        for key in ("message", "msg", "error", "event"):
            if payload.get(key):
                return str(payload[key])
        return ", ".join(
            "%s=%s" % (k, payload[k]) for k in sorted(payload) if payload[k] is not None
        )
    proto = entry.get("protoPayload")
    if isinstance(proto, dict):
        return str(
            resources.dig(proto, "status", "message")
            or proto.get("methodName")
            or "protoPayload"
        )
    return ""


def dispatch(ctx_factory, argv):
    if flags.wants_help(argv) or not argv:
        if not argv:
            raise UsageError(
                "`logs` needs the name of a Cloud Run service or job",
                code="MISSING_ARGUMENT",
                help_lines=[
                    "Run `gcloud-axi run status` to list services",
                    "Run `gcloud-axi jobs` to list jobs",
                    "Run `gcloud-axi logs <service|job> --since 1h`",
                ],
            )
        return help_out(), 0

    args = flags.parse(argv, LOG_FLAGS, "logs", max_positional=1)
    if not args.positional:
        raise UsageError(
            "`logs` needs the name of a Cloud Run service or job",
            code="MISSING_ARGUMENT",
            help_lines=["Run `gcloud-axi logs <service|job> --since 1h`"],
        )
    ctx = ctx_factory(args)
    name = args.positional[0]

    kind = (args.get("type") or "any").lower()
    if kind not in ("any", "service", "job"):
        raise UsageError(
            'flag --type expects "service" or "job", got "%s"' % args.get("type"),
            code="INVALID_VALUE",
        )

    since_raw = args.get("since", "1h")
    since_seconds = timeutil.parse_duration(since_raw, flag="since")

    severity = args.get("severity")
    if severity:
        severity = severity.upper()
        if severity not in SEVERITIES:
            raise UsageError(
                'flag --severity expects one of %s, got "%s"'
                % ("|".join(s.lower() for s in SEVERITIES), args.get("severity")),
                code="INVALID_VALUE",
            )

    limit = args.int("limit", default=20, minimum=1, maximum=1000)
    log_filter = _build_filter(name, kind, since_seconds, severity, args.get("query"))

    entries = (
        ctx.call(
            [
                "logging",
                "read",
                log_filter,
                "--limit=%d" % limit,
                "--order=desc",
            ]
        )
        or []
    )
    if not isinstance(entries, list):
        entries = [entries]

    out = toon.Out()
    out.block(
        "query",
        [
            ("target", name),
            ("type", kind),
            ("project", ctx.project()),
            ("since", since_raw),
            ("window", "%s .. now" % timeutil.ago(since_seconds)),
            ("severity", severity or "(any)"),
            ("match", args.get("query")),
            ("limit", limit),
        ],
    )

    if not entries:
        out.empty("entries")
        out.note(
            "0 entries for %s in the last %s%s - the window is correct, there is nothing in it"
            % (name, since_raw, " at severity >= %s" % severity if severity else "")
        )
        out.help(
            [
                "Run `gcloud-axi logs %s --since 24h` to widen the window" % name,
                "Run `gcloud-axi logs %s --severity warning` to lower the severity floor"
                % name,
                "Run `gcloud-axi run status %s` to confirm the name is right" % name,
            ]
        )
        return out, 0

    rows = []
    truncated = 0
    longest = 0
    for entry in entries:
        message = _message(entry)
        longest = max(longest, len(message))
        if not args.get("full"):
            shown, hint = toon.truncate(message, MESSAGE_LIMIT)
            if hint:
                truncated += 1
            message = shown
        rows.append(
            {
                "time": timeutil.short(entry.get("timestamp")),
                "severity": entry.get("severity") or "DEFAULT",
                "revision": resources.dig(entry, "resource", "labels", "revision_name")
                or resources.dig(entry, "resource", "labels", "job_name"),
                "message": message,
            }
        )

    out.table("entries", ["time", "severity", "revision", "message"], rows)
    if truncated:
        out.note(
            "%d of %d messages truncated at %d chars (longest %d) - use --full for complete text"
            % (truncated, len(rows), MESSAGE_LIMIT, longest)
        )
    if len(entries) >= limit:
        out.note(
            "result capped at --limit %d; older entries in this window were not read"
            % limit
        )
    out.help(
        [
            "Run `gcloud-axi logs %s --since %s --full` for complete messages"
            % (name, since_raw),
            "Run `gcloud-axi logs %s --severity error --since 24h` to isolate failures"
            % name,
            "Run `gcloud-axi run revisions %s` to see which revision was serving" % name,
        ]
    )
    return out, 0
