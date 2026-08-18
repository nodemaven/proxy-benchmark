"""nmbench: a measurement harness for proxy providers, browser engines and
scraping targets.

It answers one question with numbers: at which layer does a target detect you,
and what does it cost to get through. The three layers are independent - the
address, the browser, and the TLS handshake - so the harness varies one at a
time. A single engine against a single target tells you that it failed, not
where.

The package is provider-agnostic on purpose. Adding a competitor is a config
entry; adding a framework is a module and a registry line. Nothing downstream
branches on either name, which is the only way a comparison stays honest.

Nothing here reaches the network at import time, and credentials are resolved on
first use, so the verdict logic, the scheduler and the query lists can be
imported and tested on a machine with no account at all.
"""
