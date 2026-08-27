"""Run the warm-up ladder unattended, and leave a log worth reading afterwards.

Written 2026-08-26 for a run nobody is watching. Four things it adds to typing
the `probe_and_hold.py` command by hand, and one thing it deliberately refuses
to add.

**It does not run the rungs one after another, and that refusal is the point.**
The request that produced this file asked for the rungs to be run in turn. That
is the one shape the ladder must not have. The hour is this repository's largest
confound - 69 points of pass rate fell to 52 over a single afternoon on
2026-08-13, and two hosts measured 39% and 0% in overlapping hours on
2026-08-26 - so four rungs run back to back would put L0 in the evening and L3
after midnight, and the difference between them would be the clock wearing the
ladder's name. `probe_and_hold.py --warm off,L1,L2,L3` already interleaves at
identity granularity inside one process: rung L0 identity #3 is followed by L1
identity #3, then L2, then L3, then back to L0 identity #4. So the ladder is one
command, and this script supervises that command rather than replacing it.

**The engine defaults to patchright and not to camoufox.** Measured today,
2026-08-26, through the pool at `google_serp`, counting every `probehold` row:
patchright 96 ok of 223, botasaurus 1 of 87, seleniumbase 0 of 86, camoufox
0 of 33. A ladder on an engine that cannot reach the target measures nothing -
all four rungs return zero and the comparison is between four zeroes. That was
confirmed rather than predicted: a camoufox arm run an hour before this file was
written returned 0 of 21 on Google, every row a redirect to `/sorry/`.

**A preflight that fails in seconds rather than at hour three.** Two checks,
both before the browser is ever launched: the plan is put through
`probe_and_hold.py --dry-run`, which is where a bad flag, an undeclared rung or
a target that does not offer L3 is refused; and `gateway.identify()` is called
so a dead pool or an expired credential is found now and not after the machine
has been left alone. A run that dies at second 3 costs an evening exactly as
much as one that dies at hour 3.

**A restart writes its own run file and is never pooled with the first.**
Restarting moves the hour, which is the confound the interleaving exists to
remove, so two attempts are two experiments. The retry is therefore deliberately
narrow: an attempt is restarted only if it died inside `EARLY_DEATH_S`. A run
that fell over in its first minutes died on startup - a browser that would not
install, a proxy that stopped answering - and the clock has barely moved, so
restarting is free. A run that fell over at hour two already holds most of its
rows, and throwing them away to start again both spends the evening twice and
splits the hour. That one is left dead with its rows on disk, and the log says
so.

What it does not do: it does not decide anything about the data. No verdict is
computed here and no row is written here. `data/runs/` is the record, the
analysis reads that, and this file only makes sure the command survives long
enough to fill it.
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nmbench.console import tolerate_unencodable_output

# Before the gateway import and before anything prints. This script reports the
# operator behind the preflight exit, and an ASN organisation name is written by
# whoever owns the address: `probehold_20260813T201805Z` ended four identities
# into a three-hour run on a `U+00DA` in one of them, with the gateway answering
# and the row already on disk. That is precisely the death this file exists to
# prevent, so it would be a poor joke to reintroduce it here.
tolerate_unencodable_output()

from nmbench import gateway

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "scripts" / "probes" / "probe_and_hold.py"
RUNS = ROOT / "data" / "runs"
LOGS = ROOT / "data" / "logs"

# An attempt that died inside this many seconds is treated as a startup failure
# and restarted. Above it, the rows already on disk are worth more than a second
# attempt at a different hour. Ten minutes is above a browser download and a
# gateway handshake and far below any rung completing.
EARLY_DEATH_S = 600

# Below this the run cannot answer the question it is being launched to answer,
# and it is refused rather than run.
#
# Measured on the first ladder run, `probehold_20260826T152748Z`, 12 identities
# a rung. It printed four cells reading 3, 4, 3 and 3 served, which reads as a
# clean negative until the denominators are looked at: errors left 11, 9, 7 and
# 10 judged attempts, and at 11 against 9 the smallest difference Fisher can
# separate at p<0.05 is 0/11 against 4/9. About 40 points. A test that coarse
# returns "no effect" for almost anything put in front of it, and the run would
# then have been quoted as evidence the warm-up does not work.
#
# This paragraph used to justify the floor by saying the ladder exists to test a
# move from 20% to 75%, so a 40-point resolution would miss half of it. There is
# no such effect size: 20% to 75% was never claimed by anyone, it was assembled
# here out of an operator's single 75% and this harness's own baseline. Corrected
# 2026-08-27, see NOTEBOOK.md. The floor is unchanged, because the argument for
# it never needed the number - 40 points is too coarse to act on whatever the
# effect turns out to be, and sizing an experiment against a guessed effect size
# is how you end up measuring the guess.
#
# 40 identities a rung brings the detectable difference to roughly 20 points,
# which is the smallest number worth acting on. The default is 60 because
# errors and short warm-ups take a share off the top before the test sees it -
# that first run lost 23% of its attempts that way.
#
# Overridable, because a deliberately underpowered smoke test is a legitimate
# thing to want. It just should not be the thing that gets left running
# overnight and then quoted.
MIN_IDENTITIES = 40


def build_command(args) -> list:
    """The one interleaved `probe_and_hold.py` invocation this script watches."""
    command = [
        sys.executable, str(PROBE),
        "--engines", args.engines,
        "--targets", args.targets,
        "--countries", args.countries,
        "--entry", args.entry,
        "--warm", args.warm,
        "--geo", args.geo,
        "--identities", str(args.identities),
        "--series", str(args.series),
        "--gap", args.gap,
        "--dwell", args.dwell,
        "--breaker", str(args.breaker),
        "--preset", args.preset,
    ]
    if args.headless:
        command.append("--headless")
    if args.seed is not None:
        command += ["--seed", str(args.seed)]
    return command


def child_env() -> dict:
    """The child's environment, with the one Windows fix that matters.

    The console here is cp1251, and `probe_and_hold.py` prints a query list that
    is not guaranteed to be encodable in it. Without this the run dies on a
    `UnicodeEncodeError` in the middle of a print, which looks like the probe
    failing rather than the terminal failing. Same fix as the obstacle-course
    runner needed on this host, noted 2026-08-25.
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def preflight(args) -> str:
    """Refuse the run now if it cannot work, and say which check said no.

    Returns an error string, or None when both checks pass.
    """
    if args.identities < MIN_IDENTITIES and not args.underpowered_ok:
        return (f"--identities {args.identities} cannot answer this question. "
                f"At {args.identities} a rung, after the errors and short "
                f"warm-ups that took 23% off the first ladder run, the "
                f"smallest difference a Fisher test can separate is larger "
                f"than the effect the warm-up is claimed to have - so a real "
                f"warm-up and no warm-up would both come back flat. Use "
                f"--identities {MIN_IDENTITIES} or more, or pass "
                f"--underpowered-ok if this is a smoke test whose numbers will "
                f"not be quoted.")

    dry = [*build_command(args), "--dry-run"]
    done = subprocess.run(dry, cwd=str(ROOT), env=child_env(), text=True,
                          encoding="utf-8", errors="replace",
                          capture_output=True)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip()
        return f"the plan was refused by probe_and_hold.py --dry-run:\n{detail}"

    if not args.direct_ok:
        try:
            seen = gateway.identify()
        except Exception as exc:
            return f"the gateway could not be reached: {type(exc).__name__}: {exc}"
        if not seen.get("exit_ip"):
            return (f"the gateway answered without an exit address, so the pool "
                    f"is not usable right now: {seen}")
        print(f"preflight : pool answers, exit {seen['exit_ip']} "
              f"{seen.get('org') or ''}".rstrip())
    return None


def stream(command, log_path: Path) -> int:
    """Run the command, copying its output to the console and to the log."""
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n=== {datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ} "
                  f"{' '.join(command)}\n")
        log.flush()
        proc = subprocess.Popen(
            command, cwd=str(ROOT), env=child_env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
            log.flush()
        return proc.wait()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=("Supervise one interleaved warm-up ladder run. The rungs "
                     "are NOT run in turn - see this file's docstring."))
    parser.add_argument("--engines", default="patchright",
                        help="default patchright: the only engine with a "
                             "non-trivial Google pass rate on 2026-08-26")
    parser.add_argument("--targets", default="google_serp")
    parser.add_argument("--countries", default="any")
    parser.add_argument("--entry", default="url")
    parser.add_argument("--warm", default="off,L1,L2,L3",
                        help="the whole ladder, interleaved in one process")
    parser.add_argument("--geo", default="off")
    parser.add_argument("--identities", type=int, default=60,
                        help="per rung. 12 is roughly 2h, 24 roughly 4.5h, "
                             "60 roughly 11h. See MIN_IDENTITIES for why the "
                             "default moved off 12 on 2026-08-26")
    parser.add_argument("--series", type=int, default=3)
    parser.add_argument("--gap", default="8,20")
    parser.add_argument("--dwell", default="20,45",
                        help="seconds spent on each warm-up page")
    parser.add_argument("--breaker", type=int, default=12,
                        help="left on for an unattended run so a rung that "
                             "meets a wall stops instead of burning every exit "
                             "behind it. At patchright's measured 43%% pass "
                             "rate a spurious trip needs 12 refusals in a row, "
                             "which is about one run in a thousand")
    parser.add_argument("--preset", default="none")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--retries", type=int, default=1,
                        help=f"restarts allowed, and only for an attempt that "
                             f"died inside {EARLY_DEATH_S}s")
    parser.add_argument("--direct-ok", action="store_true",
                        help="skip the gateway preflight")
    parser.add_argument("--underpowered-ok", action="store_true",
                        help=f"run with fewer than {MIN_IDENTITIES} identities "
                             f"a rung. A smoke test, not a measurement")
    parser.add_argument("--dry-run", action="store_true",
                        help="preflight only, launch nothing")
    args = parser.parse_args()

    LOGS.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    log_path = LOGS / f"ladder_{stamp}.log"

    print(f"log       : {log_path}")
    problem = preflight(args)
    if problem:
        print(f"\nrefused before launching anything: {problem}")
        return 2
    print("preflight : plan accepted\n")
    if args.dry_run:
        print("dry run: preflight only, nothing was sent")
        return 0

    before = {p.name for p in RUNS.glob("probehold_*.jsonl")}
    command = build_command(args)
    code, attempts = 1, 0
    while attempts <= args.retries:
        attempts += 1
        started = time.time()
        print(f"--- attempt {attempts} "
              f"{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}")
        try:
            code = stream(command, log_path)
        except KeyboardInterrupt:
            # Broken out of rather than returned from, so the summary below
            # still names the run file. A run stopped by hand has rows on disk
            # and the whole point of the summary is to say where they are.
            print("\ninterrupted by hand. The answered queries are on disk.")
            code = 130
            break
        elapsed = time.time() - started
        if code == 0:
            break
        if elapsed >= EARLY_DEATH_S:
            print(f"\nattempt {attempts} exited {code} after {elapsed / 60:.0f} "
                  f"min. Not restarted: the rows it already wrote are worth "
                  f"more than a second attempt at a different hour, which is "
                  f"the confound the interleaving exists to remove.")
            break
        if attempts > args.retries:
            print(f"\nattempt {attempts} exited {code} after {elapsed:.0f}s and "
                  f"no restarts are left.")
            break
        print(f"\nattempt {attempts} exited {code} after {elapsed:.0f}s, which "
              f"reads as a startup failure. Restarting - the clock has barely "
              f"moved, so the rungs stay comparable.")
        time.sleep(30)

    produced = sorted(p.name for p in RUNS.glob("probehold_*.jsonl")
                      if p.name not in before)
    print(f"\n{'=' * 70}")
    print(f"attempts  : {attempts}, last exit code {code}")
    print(f"log       : {log_path}")
    if produced:
        print("run files :")
        for name in produced:
            print(f"            data/runs/{name}")
        if len(produced) > 1:
            print("            more than one file: these are separate "
                  "attempts at different hours. Analyse them apart rather "
                  "than pooling them.")
    else:
        print("run files : none. Read the log - nothing reached the target.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
