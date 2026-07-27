"""`gcloud-axi iam audit` - "what can this member actually do?" in one call.

gcloud answers that question one policy at a time. This pre-joins the project
policy, every service-account policy and every bucket policy into a single
member-keyed view, which is the shape the question is actually asked in.
"""

from .. import flags, helptext, resources, toon
from ..errors import UsageError

AUDIT_FLAGS = {
    "project": flags.VALUE,
    "member": flags.VALUE,
    "role": flags.VALUE,
    "scope": flags.VALUE,
    "max-resources": flags.VALUE,
    "full": flags.BOOL,
}

SCOPES = ("project", "sa", "bucket")


def help_out():
    return helptext.render(
        "gcloud-axi iam audit [flags]",
        description="IAM bindings pre-joined by member across project, service-account and bucket policies.",
        subcommands=["audit  - the member-keyed binding view"],
        flags=[
            "--project <id>        project to act on (flag > config PROJECT > gcloud's own configured project)",
            "--member <m>          only this member; matches a substring, so `--member my-sa` works",
            "--role <r>            only bindings for this role; matches a substring",
            "--scope <a,b>         which policies to read: %s (default all)" % ",".join(SCOPES),
            "--max-resources <n>   cap on service accounts / buckets inspected per scope (default 25)",
            "--full                list every role per member instead of the first few",
            "--help                this text",
        ],
        notes=[
            "Reading service-account and bucket policies costs one call per resource; the cap "
            "keeps a wide project from turning into hundreds of calls, and any skipped resource "
            "is reported explicitly",
            "This reads policies only. It never edits a binding",
            "Conditional bindings are flagged in the `conditional` column - their effective "
            "scope is narrower than the role name suggests",
        ],
        examples=[
            "gcloud-axi iam audit",
            "gcloud-axi iam audit --member my-sa@my-project.iam.gserviceaccount.com",
            "gcloud-axi iam audit --scope project --role storage.admin",
        ],
    )


def dispatch(ctx_factory, argv):
    if not argv or flags.wants_help(argv[:1]):
        return help_out(), 0
    sub, rest = argv[0], argv[1:]
    if sub != "audit":
        raise UsageError(
            'unknown subcommand "iam %s"' % sub,
            code="UNKNOWN_SUBCOMMAND",
            help_lines=["Run `gcloud-axi iam --help` to see the subcommands"],
        )
    return audit(ctx_factory, rest)


def _collect(policy, resource_kind, resource_name, sink):
    for binding in resources.dig(policy, "bindings", default=[]) or []:
        if not isinstance(binding, dict):
            continue
        role = binding.get("role")
        conditional = bool(binding.get("condition"))
        for member in binding.get("members") or []:
            sink.setdefault(member, []).append(
                {
                    "role": role,
                    "scope": resource_kind,
                    "resource": resource_name,
                    "conditional": conditional,
                }
            )


def _filtered(bindings, member_filter, role_filter):
    """Yield ``(member, entries)`` surviving the substring filters, sorted."""
    for member in sorted(bindings):
        if member_filter and member_filter not in member.lower():
            continue
        entries = bindings[member]
        if role_filter:
            entries = [e for e in entries if role_filter in str(e["role"]).lower()]
        if entries:
            yield member, entries


def audit(ctx_factory, argv):
    if flags.wants_help(argv):
        return help_out(), 0
    args = flags.parse(argv, AUDIT_FLAGS, "iam audit", max_positional=0)
    ctx = ctx_factory(args)
    project = ctx.project()
    cap = args.int("max-resources", default=25, minimum=1, maximum=500)

    scope_raw = args.get("scope") or ",".join(SCOPES)
    scopes = [s.strip().lower() for s in scope_raw.split(",") if s.strip()]
    for scope in scopes:
        if scope not in SCOPES:
            raise UsageError(
                'flag --scope expects any of %s, got "%s"' % (",".join(SCOPES), scope),
                code="INVALID_VALUE",
            )

    bindings = {}
    warnings = []
    notes = []
    calls = 0

    if "project" in scopes:
        policy = ctx.call(["projects", "get-iam-policy", project])
        calls += 1
        _collect(policy, "project", project, bindings)

    if "sa" in scopes:
        result = ctx.invoke(["iam", "service-accounts", "list"])
        calls += 1
        if not result.ok:
            warnings.append(
                "service-account policies skipped: %s"
                % getattr(result.error, "message", "unknown")
            )
        else:
            accounts = [a for a in (result.data or []) if isinstance(a, dict)]
            selected = accounts[:cap]
            if len(accounts) > len(selected):
                notes.append(
                    "%d of %d service accounts inspected (--max-resources %d); "
                    "%d not read" % (len(selected), len(accounts), cap,
                                     len(accounts) - len(selected))
                )
            for account in selected:
                email = account.get("email")
                if not email:
                    continue
                sub = ctx.invoke(["iam", "service-accounts", "get-iam-policy", email])
                calls += 1
                if sub.ok:
                    _collect(sub.data, "serviceAccount", email, bindings)
                else:
                    warnings.append(
                        "policy unreadable for service account %s" % email
                    )

    if "bucket" in scopes:
        result = ctx.invoke(["storage", "buckets", "list"])
        calls += 1
        if not result.ok:
            warnings.append(
                "bucket policies skipped: %s"
                % getattr(result.error, "message", "unknown")
            )
        else:
            buckets = [b for b in (result.data or []) if isinstance(b, dict)]
            selected = buckets[:cap]
            if len(buckets) > len(selected):
                notes.append(
                    "%d of %d buckets inspected (--max-resources %d); %d not read"
                    % (len(selected), len(buckets), cap, len(buckets) - len(selected))
                )
            for bucket in selected:
                url = bucket.get("storage_url") or bucket.get("id") or bucket.get("name")
                if not url:
                    continue
                if not str(url).startswith("gs://"):
                    url = "gs://%s" % str(url).rstrip("/")
                sub = ctx.invoke(["storage", "buckets", "get-iam-policy", url])
                calls += 1
                if sub.ok:
                    _collect(sub.data, "bucket", str(url).rstrip("/"), bindings)
                else:
                    warnings.append("policy unreadable for bucket %s" % url)

    member_filter = (args.get("member") or "").lower()
    role_filter = (args.get("role") or "").lower()

    rows = []
    total_bindings = 0
    for member, entries in _filtered(bindings, member_filter, role_filter):
        total_bindings += len(entries)
        roles = sorted({e["role"] for e in entries})
        shown = roles if args.get("full") else roles[:4]
        role_text = ";".join(r.split("/")[-1] for r in shown)
        if len(shown) < len(roles):
            role_text += ";(+%d more, --full)" % (len(roles) - len(shown))
        rows.append(
            {
                "member": member,
                "roles": role_text,
                "roleCount": len(roles),
                "scopes": ";".join(sorted({e["scope"] for e in entries})),
                "conditional": any(e["conditional"] for e in entries),
            }
        )

    out = toon.Out()
    out.block(
        "audit",
        [
            ("project", project),
            ("scopes", ",".join(scopes)),
            ("gcloudCalls", calls),
            ("memberFilter", args.get("member")),
            ("roleFilter", args.get("role")),
            ("totalBindings", total_bindings),
        ],
    )

    if not rows:
        out.empty("members")
        out.note(
            "0 members matched%s across %s"
            % (
                " the given filters" if (member_filter or role_filter) else "",
                ",".join(scopes),
            )
        )
        out.warnings(warnings)
        out.help(
            [
                "Run `gcloud-axi iam audit` with no filters to see every member",
                "Run `gcloud-axi iam audit --scope project` to read only the project policy",
            ]
        )
        return out, 0

    out.table(
        "members", ["member", "roles", "roleCount", "scopes", "conditional"], rows
    )

    if args.get("full"):
        for member, entries in _filtered(bindings, member_filter, role_filter):
            out.raw("")
            out.field("member", member)
            out.table(
                "bindings",
                ["role", "scope", "resource", "conditional"],
                sorted(entries, key=lambda e: (e["scope"], str(e["role"]))),
                indent=1,
            )

    for note in notes:
        out.note(note)
    out.warnings(warnings)
    out.raw("")
    out.help(
        [
            "Run `gcloud-axi iam audit --member <member>` to focus one identity",
            "Run `gcloud-axi iam audit --full` for the resource behind every binding",
        ]
    )
    return out, 0
