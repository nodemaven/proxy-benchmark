"""Resource blocking presets.

Firefox has no --host-resolver-rules, so blocking happens through Playwright's
request interception instead of Chrome command line flags.

resource_type values seen in practice:
    document, stylesheet, image, media, font, script, xhr, fetch, other
"""

PRESETS = {
    # everything through: the honest worst case
    "none": None,
    # kill the heavy static payload, keep the page working
    "light": {"image", "media", "font"},
    # server-rendered targets only need the HTML document. This blocks `script`,
    # so a target that renders its results in the browser returns its no-JS page
    # instead: measured 2026-08-10 against Google, which answers a scriptless
    # client with a 92 KB "enable JavaScript" stub and no results at all. Using
    # this preset on such a target measures the preset, not the target.
    "aggressive": {"image", "media", "font", "stylesheet", "script", "xhr", "fetch", "other"},
}

# Presets that stop a client-rendered target from ever producing results.
# Which targets those are is the target's own business: see
# `nmbench.targets.NEEDS_SCRIPT`.
SCRIPT_BLOCKING = {name for name, types in PRESETS.items()
                   if types and "script" in types}


def install(page, preset: str, counter: dict) -> None:
    """Attach a route handler that aborts blocked resource types."""
    blocked = PRESETS.get(preset)
    if not blocked:
        return

    def handler(route):
        rtype = route.request.resource_type
        if rtype in blocked:
            counter["blocked"] = counter.get("blocked", 0) + 1
            route.abort()
        else:
            counter["allowed"] = counter.get("allowed", 0) + 1
            route.continue_()

    page.route("**/*", handler)


def install_counter(page, counter: dict) -> None:
    """Count response bytes reported via Content-Length.

    A lower bound, and not a uniform one. It ignores headers and TLS overhead,
    and it misses a chunked response entirely, because a chunked response
    carries no `Content-Length` at all. That is the normal way a large HTML
    document is served, so the resource this counter misses first is the biggest
    one on the page.

    Read over every row on disk on 2026-08-15, median bytes per attempt, the
    same targets counted both ways:

        google_serp, ok      598 KB here     2052 KB through the relay
        amazon_search, ok    776 KB here     8136 KB through the relay
        amazon_search, not     0 KB here      149 KB through the relay

    So this figure runs 3 to 10 times under the socket count. Confounded by
    engine - only the engines declaring `needs_relay` are counted the other way -
    so read it as the size of the gap between the two methods and never as an
    engine difference. It is why `relayed` is on every row and why the two must
    never be pooled: `nmbench.relay` counts sockets and is the figure that
    matches what the provider bills.
    """

    def on_response(response):
        length = response.headers.get("content-length")
        if length and length.isdigit():
            counter["bytes"] = counter.get("bytes", 0) + int(length)
        counter["responses"] = counter.get("responses", 0) + 1

    page.on("response", on_response)
