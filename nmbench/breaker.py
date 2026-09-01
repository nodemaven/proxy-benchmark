"""Circuit breakers, at two scopes.

A run of consecutive failures heats the pool: every retry confirms automation to
the target and degrades the exit ranges for every other customer on the account.
A cell that has failed `limit` times in a row is stopped and stays stopped. The
caller records that it was stopped rather than rotating a sid and trying again.

`TransportWatch` is the same idea one level up, and it exists because the cell
breaker provably cannot see the failure that matters most. On 2026-08-11 a
broken tunnel produced 46% errors for four and a half hours: the cell breaker
fired 28 times, once per cell, and every one of those stops was written down as
a target refusing us. Nothing stopped the run.

`is_transport_failure` is the third scope, per attempt, and it is what both
breakers above are working around: neither can tell a refusal from an attempt
that never arrived, so they are reduced to counting and to waiting for a
pattern. Where the error message names the layer that failed, the single
attempt can be classified on its own.
"""
from collections import deque

# Errors that mean the request never reached the target, so no verdict about
# the target can be read off the attempt.
#
# Derived 2026-08-28 by counting every `error` string in `data/runs/*.jsonl`
# rather than from memory. What is deliberately *not* here matters more than
# what is:
#
# - `ERR_EMPTY_RESPONSE`, 509 rows, the largest single error class on disk. A
#   connection closed after the handshake with no bytes. The gateway can do
#   that and so can the target, and nothing in the message says which. Putting
#   it here would silently drop the biggest error class out of every
#   denominator on an assumption.
# - Every timeout - `Timeout 60000ms exceeded` at 669 rows, `ERR_TIMED_OUT` at
#   43, `NS_ERROR_NET_TIMEOUT` at 22. A target that stalls a client on purpose
#   produces exactly this, and that is a finding rather than a lost tunnel.
# - `ERR_NETWORK_CHANGED`, 10 rows. This machine's own network, not the
#   gateway's. It is not a measurement either, but it is not evidence about
#   the transport under test and redrawing an exit will not help it.
#
# `ERR_SSL_PROTOCOL_ERROR` is here on narrower grounds than the rest and the
# distinction is worth keeping. The proxy errors below name the layer outright.
# The SSL one only establishes that no HTTP response was received: Chrome
# prints the same string whether the gateway wrote plaintext into the tunnel,
# a TLS peer sent an alert, or the far side hung up silently.
#
# **`tls_repro.py` has run, on 2026-08-28, and it did not settle it - it
# eliminated the answer this comment was leaning towards.** 6 sessions of 8
# handshakes to `www.google.com:443`, each a fresh CONNECT through the same
# gateway and a full handshake driven by hand, both gateway backends
# represented: 48 of 48 completed. Nothing on the raw path is broken, so the
# browser is failing on something the probe does not do.
#
# What the browser rows say, counted over the three runs that carry the error -
# `probehold_20260826T152748Z`, `probehold_20260827T201123Z`,
# `probehold_20260828T085727Z`, 89 failures in 829 navigations:
#
# - **0 of 198 first navigations of a browser context failed**, including 121
#   first navigations to `www.google.com` itself. Every failure is the second
#   navigation or later.
# - It is one host. `www.google.com` 82 of 600 (13.7%); `translate.google.com`
#   0 of 66, `scholar.google.com` 0 of 42, `news.google.com` 0 of 24,
#   `wikihow.com` 0 of 37, `theverge.com` 1 of 49. Other Google origins on the
#   same exits in the same sessions do not do this.
# - Median 90 ms, range 64-209 ms with two outliers. Too fast for a handshake
#   with a remote exit to have been attempted and refused.
#
# So it is neither a fresh handshake nor a property of the address: it needs
# state that a new browser context does not have and that only `www.google.com`
# accumulates. What that state is has not been measured, and this comment is
# not going to name a cause it has not checked.
#
# **The previous version of this note said it was "a property of the exit, the
# right response is a fresh one". That was wrong.** The evidence given was
# clustering: in `probehold_20260827T201123Z`, 6 exits carried 3 or more
# failures where independence predicts 0.6. What the mistake looked like from
# the inside: that run drew **111 exits across 111 identities, with no exit in
# two identities and no identity on two exits**, so "clustered by exit" and
# "clustered by session" are the same sentence about the same data and the test
# could not have told them apart. It was written up as though it had. The
# session reading is the one that survives - the failure is positional inside a
# session, and the first navigation is where the clustering test had no
# discrimination at all.
#
# Redrawing a fresh exit is measured, not assumed, and it is about half a
# remedy. `probehold_20260828T085727Z` has four redraw chains: two where three
# exits in three countries and three ASNs reproduced the failure at identical
# navigation positions, and two where the next exit cleared it. A redraw costs
# a whole identity, so that is an open question and not a settled policy.
#
# **Three further runs, 2026-08-28 to 2026-09-01. The invariant holds and one
# clause of the sentence above does not.** First navigations are now **0 of
# 543** across six runs, every origin, warm pages and probes alike. But "state
# that only `www.google.com` accumulates" is wrong: the `N1` rung opens on
# `theverge.com` and then goes to `www.google.com`, and in
# `probehold_20260828T213141Z` that second navigation - the context's *first*
# visit to Google - failed **8 of 16**. Holding the host fixed and pooling the
# six runs:
#
#   context's first navigation            0 / 272    0.0%
#   later, first visit to this origin    36 / 270   13.3%
#   later, origin already visited       170 / 1224  13.9%
#
# Fisher p = 0.85 between the last two, so revisiting the origin does nothing.
# Every other origin is 0/271, 0/515 and 1/30 in the same three buckets. The
# state is therefore accumulated by the *context*, whatever it navigated to,
# and `www.google.com` is the only host that reacts to it. That is a different
# thing to go looking for than what this comment said on 2026-08-28.
#
# An earlier cut of that table made the opposite error, and it is the same
# error as the clustering one above. Pooled over all origins, "new origin" read
# 4.6% against "repeat origin" 13.6%, p = 6e-12. That is entirely a host
# confound - the repeat bucket is 1224 of 1254 `www.google.com`, because Google
# is the only origin the ladder visits twice in one context - so the contrast
# measured Google against not-Google and was one edit away from being written
# up as new against repeat.
#
# **The client-side network is strongly associated with the rate and is not
# established as its cause.** The operator reports two of the three recent
# ladder runs went out over free public wi-fi and one over a secured 5G link.
# On `www.google.com` navigations, each run truncated to the 5G run's own 81
# minutes so that its being the shortest cannot be the reason:
#
#   run 1  free wi-fi  20260828T213141Z   25 / 105   23.8%
#   run 2  free wi-fi  20260831T222129Z   18 /  99   18.2%
#   run 3  secured 5G  20260831T084544Z    2 / 122    1.6%
#
# Truncating makes the gap wider rather than narrower, so "the 5G run stopped
# before the bad part" is refuted by the data instead of left open. Stratified
# on navigation index the Mantel-Haenszel odds ratio is 9.0. The failures have
# the same timing signature on both networks - medians 85, 92 and 96 ms - so it
# is one phenomenon at two rates and not two phenomena.
#
# What stops that being a finding: **there is one 5G run.** The p-values treat
# navigations as independent and they are not - they share one client, one
# evening, one exit pool and one wall-clock hour. The 5G run is the only
# daytime one, and time of day cannot be separated from network across three
# runs. The calendar order is free, 5G, free, which rules out a monotone trend
# and nothing else.
#
# And no row records any of it: **51 columns and not one describes the near end
# of the path.** The variable that currently best predicts this failure is
# absent from `data/runs/`, which is the same gap the `BROWSER_HEADERS` note in
# `engines/http.py` ends on. What would settle it is two short runs back to
# back on one evening, one per network.
#
# The marker stays in the list on the one ground that did survive: whatever
# produced it, the attempt carries no answer from the target's application
# layer, so no verdict about the target can be read off it.
TRANSPORT_MARKERS = (
    "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_PROXY_CONNECTION_FAILED",
    "ERR_SSL_PROTOCOL_ERROR",
    # Firefox says the same thing in its own vocabulary and without the `net::`
    # prefix, so a Chromium-only list would classify one failure two ways
    # depending on which engine met it. Both of these are on disk from camoufox
    # rows, 11 and 10 of them.
    "NS_ERROR_PROXY_CONNECTION_REFUSED",
    "NS_ERROR_PROXY_GATEWAY_TIMEOUT",
    # curl_cffi, which reaches the gateway without a browser. 5 rows.
    "Proxy CONNECT aborted",
)
# Every name above was read off a row. Firefox and Chromium both have further
# proxy-layer codes - `NS_ERROR_PROXY_BAD_GATEWAY` is the obvious next one -
# and they are not here because this gateway has not produced them. Add one
# when a run does, not because it plausibly could: an unmet marker cannot be
# checked against anything, and this list decides what leaves the denominator.


def is_transport_failure(error) -> bool:
    """Did this attempt fail below the application layer.

    Takes the raw error string off a row, so callers do not have to agree on
    how to normalise one. Substring matching and not equality: every producer
    here wraps the marker in its own prose - Playwright prefixes the call,
    Chromium prefixes `net::`, and camoufox appends a multi-line call log.
    """
    if not error:
        return False
    return any(marker in error for marker in TRANSPORT_MARKERS)


class CircuitBreaker:
    # The default stays 5 because `probes/google_429.py` relies on it and every
    # google_429 run committed to data/runs/ was measured at 5: raising it here
    # would silently make new rows incomparable with the recorded ones. The
    # matrix runner passes its own limit and does not read this value.
    def __init__(self, cell: str, limit: int = 5,
                 base_pause: float = 5.0, max_pause: float = 30.0):
        self.cell = cell
        self.limit = limit
        self.base_pause = base_pause
        self.max_pause = max_pause
        self.consecutive = 0
        self.tripped = False
        self.reason = None

    def record(self, verdict: str) -> float:
        """Feed one verdict in, get the seconds to wait before the next attempt."""
        if verdict == "ok":
            self.consecutive = 0
            return self.base_pause

        self.consecutive += 1
        if self.consecutive >= self.limit:
            self.trip(f"{self.limit} consecutive failures")
        return min(self.base_pause * (2 ** (self.consecutive - 1)), self.max_pause)

    def trip(self, reason: str) -> None:
        """Stop the cell for a reason that is not a run of verdicts.

        An engine whose binary will not start, or a session that left from the
        operator's own address instead of the pool, will do exactly the same on
        the next batch. Without this the cell is retried once per remaining
        batch - hundreds of browser launches that produce no measurement and,
        in the second case, hundreds of requests to the target from an address
        that was never supposed to reach it.

        The first reason wins. What stopped a cell is the first thing that went
        wrong with it, not the last.
        """
        if not self.tripped:
            self.tripped = True
            self.reason = reason


class TransportWatch:
    """Stops the whole run once the errors stop being about the targets.

    Two conditions, and both are needed. The error share over a recent window
    has to be high, and the errors have to be spread over several cells. The
    second is what separates a transport failure from a real measurement: one
    engine that cannot start, or one target that refuses everything, is a
    finding and belongs to that cell's breaker. Five unrelated engine stacks
    failing in the same minutes are not five findings, they are one, and it is
    not about the targets.

    `error` is the only verdict counted. A cell answering `block` or `captcha`
    is the benchmark working, however unhappy the number looks, and a watchdog
    that stopped on refusals would delete the measurement it was built to
    protect.

    The window is deliberately short. The cost of stopping a healthy run by
    mistake is one command to restart it with `--resume`; the cost of not
    stopping a broken one is a night.

    **The default share is 0.85 and was 0.6 until 2026-08-19, when 0.6 stopped
    an eight-engine run that had been behaving identically for eight hours.**
    The arithmetic is the whole of the argument. This threshold is an absolute
    line, so what it actually asks is "is the recent window improbable under the
    run's own error rate", and the answer depends on a baseline it does not
    know. The gateway's unanswered-CONNECT floor put that baseline at 34% on the
    server. At 34%, a 20-attempt window reaching 12 errors has probability
    ~0.017, which is rare once and certain over the 8000 windows of a four-day
    run: the watchdog was guaranteed to fire on a healthy run, and it did, at
    attempt 564 of 8000.

    0.85 is 17 of 20, probability ~3e-7 at the same baseline, so about 0.2%
    over a whole run. It costs nothing in detection: a transport failure is not
    60% errors, it is essentially all of them - every row in `data/runs/` from
    an interception or a dead gateway is ~100% - so 17 of 20 still trips inside
    one window, about 17 minutes at the observed pace.

    The general point is worth more than the constant. A fixed share cannot
    distinguish "errors are new" from "errors have always been this high", and
    the second is the normal condition on this gateway. If the floor moves
    again, this number has to be re-derived against it rather than nudged.
    """

    def __init__(self, window: int = 20, share: float = 0.85, spread: int = 3):
        self.window = window
        self.share = share
        self.spread = spread
        self.recent = deque(maxlen=window)
        self.tripped = False
        self.reason = None

    def record(self, cell: str, verdict: str) -> None:
        if self.tripped:
            return
        self.recent.append((cell, verdict))
        if len(self.recent) < self.window:
            return

        errors = [c for c, v in self.recent if v == "error"]
        if len(errors) < self.share * self.window:
            return
        if len(set(errors)) < self.spread:
            return

        self.tripped = True
        self.reason = (
            f"{len(errors)} of the last {self.window} attempts errored, across "
            f"{len(set(errors))} cells. Attempts that never completed are not "
            f"evidence about the targets, so the run stopped rather than "
            f"spending the night recording them"
        )
