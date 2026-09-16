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


class SessionBreaker:
    """The cell breaker for a runner whose unit of measurement is a session.

    `CircuitBreaker` counts consecutive failed *attempts*, which is right for a
    probe that draws a fresh exit per attempt and wrong for the matrix runner,
    where one batch is one browser holding one sticky session on one exit. Ten
    attempts down a dead exit is one observation repeated ten times, and the
    2026-09-11 run is what that costs: `--batch 10 --breaker 10` made the limit
    exactly one session, so a single bad exit stopped a cell permanently. Ten of
    the sixteen cells died on their first session ever, and every one of the
    sixteen ended on a session that scored 0/10 - which is not a coincidence,
    it is the stopping rule printing itself.

    The distortion is worse than a small sample, because the sample size is a
    function of the result. A cell that draws good exits keeps running and
    accumulates n; a cell that draws one bad exit stops at n=10. Every pass rate
    in that file is therefore conditioned on the cell having survived, and the
    ones that look worst are the ones with the least evidence behind them. Any
    stopping rule has this property to some degree; counting sessions is what
    makes the floor big enough that the number means something.

    So a cell is stopped by a run of dead *sessions*. One session is one sticky
    exit, measured - 24 of 24 sessions held exactly one exit prefix - so `limit`
    is also the number of distinct exits that have to fail before the cell is
    given up, and no single exit can end a cell at any setting above 1. What it
    costs is worth stating rather than burying: a target that refuses
    everything is now asked `limit * batch` times before the run concludes it,
    where the old rule concluded after one session. That is the price of a
    denominator, and it is paid against the target, so it is a pool-safety
    setting and not a patience setting.

    A session that never reached the target is counted separately and stops the
    cell sooner, on `unreachable_limit`. Two reasons, and the second is the one
    that matters. Retrying it cannot produce a verdict - there is no answer from
    the target's application layer to read - so the attempts buy nothing. And
    the stop reason has to survive into the output, because a cell that was
    never answered is not a cell that scored zero: reporting it as 0% is what
    the 2026-09-11 file did for three competitors' unpinned arms, which had
    asked for a country that does not exist and reached Amazon not once.
    """

    def __init__(self, cell: str, limit: int = 3, unreachable_limit: int = 2,
                 base_pause: float = 5.0, max_pause: float = 30.0):
        self.cell = cell
        self.limit = limit
        self.unreachable_limit = unreachable_limit
        self.base_pause = base_pause
        self.max_pause = max_pause
        self.sessions = 0
        self.consecutive_dead = 0
        self.consecutive_unreachable = 0
        self.consecutive_abandoned = 0
        self.alive_sessions = 0
        # Sessions where at least one attempt reached the target's application
        # layer, whatever it then answered. This is the denominator of every
        # rate the cell can support, and `no_verdict` is it being zero.
        self.arrived_sessions = 0
        # What the session running right now has produced so far.
        self.session_ok = 0
        self.session_attempts = 0
        self.session_arrived = 0
        self.consecutive = 0
        self.tripped = False
        self.reason = None

    def record(self, verdict: str, error=None) -> float:
        """Feed one verdict in, get the seconds to wait before the next attempt.

        Nothing here can trip the breaker. A cell is judged at the end of a
        session, and this method's only other job is the backoff, which is per
        attempt because that is the thing it paces.

        `error` is the row's raw error string and decides whether the attempt
        reached the target at all. It is taken rather than derived from the
        verdict because `error` is one verdict covering both, and the whole
        distinction this class adds is inside it.
        """
        self.session_attempts += 1
        if verdict != "error" or not is_transport_failure(error):
            self.session_arrived += 1
        if verdict == "ok":
            self.session_ok += 1
            self.consecutive = 0
            return self.base_pause

        self.consecutive += 1
        return min(self.base_pause * (2 ** (self.consecutive - 1)), self.max_pause)

    def end_session(self) -> str:
        """Close the current session, classify it, and stop the cell if it is
        time. Returns the classification, which the caller writes to the row.

        Three outcomes, and collapsing them is how a run starts reporting a
        gateway that never answered as a target that refused us:

          alive        at least one attempt was served
          dead         the target answered and refused every attempt
          unreachable  no attempt reached the target's application layer

        A session with no attempts in it is `abandoned`: a browser that would
        not launch or a session that died before its first query. It is not a
        session of the run and is not counted as one - no exit was measured
        through and the target was never asked - but a run of them still stops
        the cell, because relaunching a browser that will not start costs a
        minute a time and produces nothing to count.
        """
        attempts, ok, arrived = (self.session_attempts, self.session_ok,
                                 self.session_arrived)
        self.session_attempts = self.session_ok = self.session_arrived = 0
        self.consecutive = 0
        if not attempts:
            self.consecutive_abandoned += 1
            if self.consecutive_abandoned >= self.unreachable_limit:
                self.trip(f"{self.consecutive_abandoned} sessions in a row that "
                          f"sent nothing at all, so this is the launcher here "
                          f"and not an answer from anywhere")
            return "abandoned"
        self.consecutive_abandoned = 0

        self.sessions += 1
        if ok:
            self.alive_sessions += 1
            self.arrived_sessions += 1
            self.consecutive_dead = 0
            self.consecutive_unreachable = 0
            return "alive"

        self.consecutive_dead += 1
        if arrived:
            self.arrived_sessions += 1
            self.consecutive_unreachable = 0
            outcome = "dead"
        else:
            self.consecutive_unreachable += 1
            outcome = "unreachable"

        if self.consecutive_unreachable >= self.unreachable_limit:
            self.trip(
                f"{self.consecutive_unreachable} sessions in a row where no "
                f"attempt reached the target, so this cell has no verdict "
                f"rather than a low one")
        elif self.consecutive_dead >= self.limit:
            self.trip(f"{self.consecutive_dead} sessions in a row, on "
                      f"{self.consecutive_dead} different exits, with nothing "
                      f"served, over {self.sessions} sessions")
        return outcome

    @property
    def no_verdict(self) -> bool:
        """Did this cell ever get an answer out of the target.

        The flag an output needs before it prints a pass rate: a cell that was
        stopped without one attempt ever arriving has no denominator, and `0%`
        is a claim about the target that nothing here measured.

        Read off the session counters rather than off `reason`, because a cell
        can be stopped for one thing while being unmeasurable for another - a
        gateway that refuses every tunnel and then an engine that stops
        launching is tripped by the second and has no verdict because of the
        first. A stop reason is an account of why the run ended; this is a
        statement about what the rows can support.

        The session in flight counts too. A run stopped by the transport
        watchdog or by Ctrl-C never closes its last session, and the rows that
        session wrote are on disk all the same.
        """
        return self.arrived_sessions == 0 and self.session_arrived == 0

    def trip(self, reason: str) -> None:
        """Stop the cell for a reason that is not a run of sessions.

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
