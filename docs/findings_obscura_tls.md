# Is the installed Obscura the stealth build, and what does its TLS look like

Measured 2026-08-11, obscura 0.2.0 on Windows x86_64.

## Contents

- [The question this started from](#the-question-this-started-from) - a flag the
  plain build also accepts, so the flag proves nothing
- [Method](#method) - the build acting as its own control, no reference
  fingerprint needed
- [Result](#result) - JA4, JA3, the HTTP/2 fingerprint and the counts, both ways
- [What this establishes](#what-this-establishes) ·
  [What this does not establish](#what-this-does-not-establish)
- [Cost](#cost)

## The question this started from

`nmbench/engines/obscura.py` passes `--stealth` on every launch. The engine's own
documentation warns that the stealth transport ships only in the `-stealth`
release archives, and that **the plain archive accepts the flag and runs without
TLS impersonation anyway**. The flag is therefore not evidence that the feature
is present - only the build is.

That matters because a mislabelled build is invisible downstream: every row would
be written as a stealth run while measuring a binary with no TLS impersonation in
it, nothing in the output would show the discrepancy, and the Obscura column
would be attributing a result to a feature that was never compiled in.

An earlier version of this document added that the archive on the measuring
machine was named `obscura-win.zip`, with no `-stealth` suffix. That was
mis-transcribed and is withdrawn on 2026-08-19: the v0.2.0 release publishes
`obscura-x86_64-windows.zip` and `obscura-x86_64-windows-stealth.zip` and no
asset by the recorded name, and the archive is no longer on the machine to
re-read. It changes nothing below. A file name was never the evidence - the
handshake is - which is the point the rest of this document is about.

## Method

Two fetches of the same TLS-echo endpoint, one with the flag and one without, so
the comparison needs no external reference fingerprint: the build is its own
control. Both went out **direct, with no gateway**, so this cost nothing from the
traffic quota and the exit was the operator's own address.

    obscura fetch --stealth --quiet https://tls.peet.ws/api/all
    obscura fetch --quiet https://tls.peet.ws/api/all

## Result

| | `--stealth` | no flag |
|---|---|---|
| JA4 | `t13d1516h2_8daaf6152771_d8a2da3f94cd` | `t13d1011h1_61a7ad8aa9b6_3fcd1a44f3e3` |
| JA3 hash | `1a12f109f41ce9151a78ff627b70512d` | `6a299af22b8c6e28dddaffc01426446b` |
| peetprint hash | `1d4ffe9b0e34acac0bd883fa7f79d7b5` | `2b6a7b012ebaa2e751d4ab91c639d1a4` |
| Akamai h2 fingerprint | `1:65536;2:0;4:6291456;6:262144\|15663105\|0\|m,a,s,p` | absent, the connection was HTTP/1.1 |
| User-Agent | Chrome/145.0.0.0 | Chrome/143.0.0.0 |

Every fingerprint differs, so the impersonation code path is compiled in and
running. The installed build is the stealth build despite the archive name.

Reading the JA4 strings rather than comparing them as opaque hashes:

- `t13d**1516**h2` - TLS 1.3, SNI is a domain, **15** ciphers, **16** extensions,
  ALPN negotiated **h2**.
- `t13d**1011**h1` - the same TLS version with **10** ciphers, **11** extensions,
  and no h2 offered at all.

The cipher hash `8daaf6152771` is the value a current desktop Chrome produces.

What the stealth build adds, from the extension list on the same capture:

- GREASE values in four separate places: ciphers, extensions, supported groups
  and supported versions. The non-stealth handshake has none, and absent GREASE
  is a single-condition test that no real browser fails.
- `X25519MLKEM768` as the first key share, the post-quantum group Chrome has sent
  since 131.
- Encrypted Client Hello, ALPS (`application_settings`), `compress_certificate:
  brotli`, `status_request`, `signed_certificate_timestamp`.
- At the HTTP layer, `sec-ch-ua` client hints and
  `accept-encoding: zstd,gzip,deflate,br`, with an h2 SETTINGS frame and priority
  matching Chrome's.

## What this establishes

The Obscura column of the benchmark can be labelled as a stealth run honestly.
No reinstall is needed before the measurement.

It also sets the interpretation of that column. Obscura is the only engine in the
matrix whose TLS layer is *synthesised*: Camoufox is Firefox and the
Chromium-family engines are Chromium, so their handshakes are genuine as a
side effect of being real browsers. The stealth feature does not give Obscura an
advantage over them at this layer, it brings it to parity on a layer the others
get for free. Conversely, a non-stealth Obscura would be separable from a browser
by the handshake alone, before any page is served - `t13d1011h1` with no GREASE
and no h2 is not a shape any browser produces.

## What this does not establish

- **Nothing about detectability.** A matching JA4 means the handshake resembles
  Chrome's to the fields JA4 hashes. It does not mean a commercial anti-bot
  vendor cannot separate the two on fields JA4 ignores, on TCP/IP characteristics,
  or on the correlation between the handshake and the browser signals above it.
- **Nothing about the other engines.** Their handshakes were not measured here.
  The claim that they are genuine rests on their being real browser binaries, not
  on a capture.
- **One target, one moment.** A single endpoint on one date. The impersonation
  profile is pinned to a Chrome version and will drift as Chrome moves; the
  User-Agent reported here, Chrome/145, is one minor version ahead of what the
  non-stealth build sends, so the two are not even claiming the same browser.
- **Nothing measured through the gateway.** These fetches were direct. A proxy in
  the path does not change the ClientHello, but that was assumed here, not shown.

## Cost

Two requests, direct, roughly 30 KB total. No quota consumed.
