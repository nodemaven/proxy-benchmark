# Quarantined runs

Rows here were produced by a defective instrument. They are kept because
deleting measurements hides the defect, and moved because analyze_429.py must
not read them as evidence.

## google_429_20260810T165425Z.jsonl

The harness advertised `Accept-Encoding: gzip, deflate, br` while the `brotli`
package was not installed. Every response the server chose to compress with
brotli came back to `requests` as undecoded bytes. The content-based verdict
found none of its markers in those bytes and returned its fallback, `block`.

Consequence: every `block` in this file is unattributable. It may have been a
served result page, a challenge, or a real block - the evidence was destroyed
before the verdict ran. Affected: all of step 1, the 200-status rows of step 2,
all of step 3 bing.

The `captcha` rows are unaffected: Google serves the /sorry/ page uncompressed
at around 3.3 KB and those bodies decoded correctly.

Fixed in `engines.supported_encodings` (advertise only what can be decoded) and
`engines.looks_undecoded` (refuse to judge a body that did not decode, rather
than reporting a false block). `brotli` is now a pinned dependency.
