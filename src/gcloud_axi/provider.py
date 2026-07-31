"""The provider's own status, and the only place this tool opens a socket.

The failure this module exists to prevent: a 5xx comes back, it gets read as a
problem with *this* identity or *this* request, and the wrong diagnosis is
relayed onward - while Google has an open incident saying otherwise. Checking
costs one unauthenticated GET against a public feed, so nothing about that
mistake is worth its price.

Three rules, for the same reason :mod:`gcloud_axi.gcloudcmd` has its own:

* **One place.** Every network read in the tool goes through here, so "does
  this command touch the network?" has a single answer.
* **Bounded, always.** A short timeout, one attempt, no retry, and a capped
  read. A diagnostic that hangs is worse than one that says it could not check.
* **Never fatal.** An unreachable feed is reported as a field, never raised.
  The provider being unreachable is not a reason to lose the error the operator
  was actually asking about.
"""

import json
import os

from . import timeutil, toon

DEFAULT_URL = "https://status.cloud.google.com/incidents.json"
INCIDENT_BASE = "https://status.cloud.google.com/"
DEFAULT_TIMEOUT = 4.0
DESC_LIMIT = 240

# How many open incidents to render before saying how many were left out.
SHOW_LIMIT = 5


def feed_url():
    return os.environ.get("GCLOUD_AXI_STATUS_URL") or DEFAULT_URL


def enabled():
    """False when the operator has switched the lookup off, or is offline."""
    setting = (os.environ.get("GCLOUD_AXI_PROVIDER_STATUS") or "").strip().lower()
    return setting not in ("0", "off", "false", "no", "never")


def timeout():
    raw = os.environ.get("GCLOUD_AXI_STATUS_TIMEOUT")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return value if value > 0 else DEFAULT_TIMEOUT


# One answer per (setting, url) for the life of the process: a single-shot CLI
# asking the same feed twice in one invocation would pay a second bounded
# timeout for the same bytes.
_fetch_memo = {}


def fetch():
    """Return ``(incidents, error)``. Exactly one of the two is meaningful."""
    key = (enabled(), feed_url())
    if key not in _fetch_memo:
        _fetch_memo[key] = _fetch()
    return _fetch_memo[key]


def _fetch():
    if not enabled():
        return None, "skipped (GCLOUD_AXI_PROVIDER_STATUS is off)"

    import urllib.error
    import urllib.request

    url = feed_url()
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "gcloud-axi", "Accept": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=timeout()) as response:
            body = response.read(4 * 1024 * 1024).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, "could not reach the status feed: %s" % _brief(exc)
    try:
        parsed = json.loads(body)
    except ValueError:
        return None, "the status feed returned something that is not JSON"
    if not isinstance(parsed, list):
        return None, "the status feed returned an unexpected shape"
    return parsed, None


def open_incidents(incidents):
    """Those with no end time - the ones still happening."""
    return [
        i for i in incidents
        if isinstance(i, dict) and not i.get("end") and not i.get("resolved")
    ]


def rows(incidents):
    """The minimal schema: enough to decide, not the whole incident record."""
    result = []
    for incident in incidents:
        desc, _ = toon.truncate(incident.get("external_desc") or "", DESC_LIMIT)
        result.append(
            {
                "severity": incident.get("severity") or "(unstated)",
                "service": incident.get("service_name") or "(unstated)",
                "began": incident.get("begin") or "(unstated)",
                "summary": desc or "(no description)",
            }
        )
    return result


def links(incidents):
    out = []
    for incident in incidents:
        uri = incident.get("uri")
        if uri:
            out.append(INCIDENT_BASE + str(uri).lstrip("/"))
    return out


def annotate(error):
    """Attach the provider's own answer to a server-side error.

    This is the whole of finding 2a: the operator gets the incident in the same
    read as the failure, rather than having to think of asking.
    """
    incidents, problem = fetch()
    if problem:
        error.fields.append(("providerStatus", problem))
        error.help_lines.append(
            "Check https://status.cloud.google.com/ by hand - the feed could not be read here"
        )
        return error

    live = open_incidents(incidents)
    error.fields.append(("providerStatusCheckedAt", timeutil.rfc3339(timeutil.now())))
    if not live:
        # A definitive zero. "No open incident" is an answer, and it is the one
        # that makes looking at the request itself the right next step.
        error.fields.append(
            ("providerOpenIncidents",
             "0 - Google publishes no open incident, so this 5xx is more likely "
             "transient or specific to this request")
        )
        error.help_lines.append(
            "Retry once; a 5xx with no published incident is usually transient"
        )
        return error

    error.fields.append(("providerOpenIncidents", len(live)))
    for incident in live[:SHOW_LIMIT]:
        summary, _ = toon.truncate(incident.get("external_desc") or "", DESC_LIMIT)
        error.fields.append(
            ("providerIncident",
             "%s: %s (%s)" % (
                 incident.get("service_name") or "(unstated service)",
                 summary or "(no description)",
                 incident.get("severity") or "severity unstated",
             ))
        )
    if len(live) > SHOW_LIMIT:
        error.fields.append(
            ("providerIncidentsOmitted", len(live) - SHOW_LIMIT)
        )
    for link in links(live[:SHOW_LIMIT]):
        error.help_lines.insert(0, "Read the open incident at %s" % link)
    error.help_lines.insert(
        0,
        "Google has %d open incident(s) - treat this failure as the provider's "
        "until that is ruled out" % len(live),
    )
    return error


def _brief(exc):
    text = str(exc)
    return text if len(text) <= 160 else text[:160].rstrip() + " ..."
