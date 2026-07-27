"""Normalisers for the gcloud JSON shapes this tool reads.

gcloud has shipped more than one JSON shape for Cloud Run over the years (the
Knative-style `apiVersion`/`spec`/`status` documents and the flatter v2 ones).
Every accessor here tolerates both and returns ``None`` rather than raising, so
one unexpected field can never take down a whole listing.
"""


def dig(obj, *path, **kwargs):
    """Walk nested dicts/lists safely. ``dig(d, "a", "b", 0)``."""
    default = kwargs.get("default")
    current = obj
    for key in path:
        if current is None:
            return default
        if isinstance(key, int):
            if not isinstance(current, (list, tuple)) or len(current) <= key:
                return default
            current = current[key]
        else:
            if not isinstance(current, dict):
                return default
            current = current.get(key)
    return default if current is None else current


def condition(obj, kind="Ready"):
    """Return the named condition dict from either shape."""
    conditions = dig(obj, "status", "conditions") or dig(obj, "conditions") or []
    if not isinstance(conditions, list):
        return None
    for entry in conditions:
        if isinstance(entry, dict) and entry.get("type") == kind:
            return entry
    return None


def ready_state(obj):
    """``READY`` / ``NOT_READY`` / a reason string / ``UNKNOWN``."""
    cond = condition(obj, "Ready")
    if not cond:
        return "UNKNOWN"
    status = cond.get("status")
    if status == "True":
        return "READY"
    reason = cond.get("reason")
    if status == "False":
        return reason or "NOT_READY"
    return reason or "PENDING"


def ready_message(obj):
    cond = condition(obj, "Ready")
    if not cond:
        return None
    if cond.get("status") == "True":
        return None
    return cond.get("message")


def short_name(name):
    """`projects/123/secrets/foo` -> `foo`; leaves bare names alone."""
    if not name:
        return name
    return str(name).rsplit("/", 1)[-1]


# -- Cloud Run services -----------------------------------------------------


def service_name(svc):
    return dig(svc, "metadata", "name") or short_name(dig(svc, "name"))


def service_region(svc):
    return (
        dig(svc, "metadata", "labels", "cloud.googleapis.com/location")
        or dig(svc, "region")
        or dig(svc, "metadata", "region")
    )


def service_url(svc):
    return dig(svc, "status", "url") or dig(svc, "uri")


def service_containers(svc):
    containers = dig(svc, "spec", "template", "spec", "containers")
    if containers is None:
        containers = dig(svc, "template", "containers")
    return containers if isinstance(containers, list) else []


def service_image(svc):
    return dig(service_containers(svc), 0, "image")


def image_digest(image):
    """The `sha256:...` part of a pinned image reference, else None."""
    if not image or "@" not in str(image):
        return None
    return str(image).split("@", 1)[1]


def image_repo(image):
    if not image:
        return None
    return str(image).split("@", 1)[0].split(":", 1)[0]


def service_traffic(svc):
    """``[{revision, percent, tag}]`` from either shape."""
    raw = dig(svc, "status", "traffic")
    if raw is None:
        raw = dig(svc, "traffic")
    if not isinstance(raw, list):
        return []
    out = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        out.append(
            {
                "revision": entry.get("revisionName") or entry.get("revision"),
                "percent": entry.get("percent"),
                "tag": entry.get("tag"),
                "latest": bool(entry.get("latestRevision")),
            }
        )
    return out


def serving_revision(svc):
    """The revision taking the largest share of traffic."""
    traffic = [t for t in service_traffic(svc) if t.get("percent")]
    if traffic:
        best = max(traffic, key=lambda t: t.get("percent") or 0)
        if best.get("revision"):
            return best["revision"]
    latest = dig(svc, "status", "latestReadyRevisionName") or dig(
        svc, "latestReadyRevision"
    )
    return short_name(latest) if latest else None


def service_last_deploy(svc):
    return (
        dig(svc, "metadata", "annotations", "serving.knative.dev/lastModifiedTime")
        or dig(svc, "status", "conditions", 0, "lastTransitionTime")
        or dig(svc, "updateTime")
        or dig(svc, "metadata", "creationTimestamp")
    )


def service_last_modifier(svc):
    return dig(
        svc, "metadata", "annotations", "serving.knative.dev/lastModifier"
    ) or dig(svc, "lastModifier")


def service_account_of(svc):
    return dig(svc, "spec", "template", "spec", "serviceAccountName") or dig(
        svc, "template", "serviceAccount"
    )


def env_names(svc):
    """Environment variable NAMES only, split into plain and secret-backed.

    Values are deliberately never read: a plain env var can hold anything, and
    this tool does not surface configuration payloads.
    """
    plain, from_secret = [], []
    for container in service_containers(svc):
        for entry in dig(container, "env", default=[]) or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not name:
                continue
            ref = dig(entry, "valueFrom", "secretKeyRef") or dig(
                entry, "valueSource", "secretKeyRef"
            )
            if ref:
                from_secret.append(
                    {
                        "env": name,
                        "secret": short_name(ref.get("name") or ref.get("secret")),
                        "version": ref.get("key") or ref.get("version"),
                    }
                )
            else:
                plain.append(name)
    return plain, from_secret


# -- Cloud Run revisions ----------------------------------------------------


def revision_name(rev):
    return dig(rev, "metadata", "name") or short_name(dig(rev, "name"))


def revision_created(rev):
    return dig(rev, "metadata", "creationTimestamp") or dig(rev, "createTime")


def revision_image(rev):
    containers = dig(rev, "spec", "containers")
    if containers is None:
        containers = dig(rev, "containers")
    return dig(containers, 0, "image")


# -- Cloud Run jobs ---------------------------------------------------------


def job_name(job):
    return dig(job, "metadata", "name") or short_name(dig(job, "name"))


def job_region(job):
    return dig(job, "metadata", "labels", "cloud.googleapis.com/location") or dig(
        job, "region"
    )


def job_image(job):
    containers = dig(job, "spec", "template", "spec", "template", "spec", "containers")
    if containers is None:
        containers = dig(job, "template", "template", "containers")
    return dig(containers, 0, "image")


def execution_job_name(execution):
    return (
        dig(execution, "metadata", "labels", "run.googleapis.com/job")
        or dig(execution, "spec", "jobName")
        or dig(execution, "job")
    )


def execution_name(execution):
    return dig(execution, "metadata", "name") or short_name(dig(execution, "name"))


def execution_started(execution):
    return (
        dig(execution, "status", "startTime")
        or dig(execution, "startTime")
        or dig(execution, "metadata", "creationTimestamp")
    )


def execution_completed(execution):
    return dig(execution, "status", "completionTime") or dig(execution, "completionTime")


def execution_result(execution):
    """SUCCEEDED / FAILED / RUNNING, derived from counts then conditions."""
    succeeded = dig(execution, "status", "succeededCount") or dig(
        execution, "succeededCount"
    )
    failed = dig(execution, "status", "failedCount") or dig(execution, "failedCount")
    if failed:
        return "FAILED"
    if succeeded:
        return "SUCCEEDED"
    state = ready_state(execution)
    if state == "READY":
        return "SUCCEEDED"
    if state in ("UNKNOWN", "PENDING"):
        return "RUNNING"
    return state
