"""JA4 from a raw ClientHello, computed here rather than asked for.

`scripts/probes/tls_echo.py` reads the same fingerprint off `tls.peet.ws`, which
is an external host. That is a dependency this repository cannot pay any more:
the workstation reaches the network through a VPN gateway, so no number that
depends on a live site may be measured from it, and the echo's certificate was
measured broken on 2026-08-18. It is also the wrong shape - the echo answers
once, out of band, about a connection nobody benchmarked, while the handshake
that matters is the one the engine sent during the run being recorded.

The ClientHello is already passing through this process. `nmbench.relay` copies
it byte for byte on its way to the gateway, so reading it costs one parse per
tunnel and no request at all. What `tls_echo.py`'s own docstring establishes is
what makes that equivalent: a CONNECT proxy tunnels TLS end to end, so the bytes
at the relay are the bytes the far side receives.

**Cross-checked against an independent implementation, 2026-09-02.** The spec
below is transcribed, and a transcription that is wrong in a detail still
produces a plausible 36-character string, so it was checked rather than trusted.
`data/runs/tls_echo_20260901T112110Z.jsonl` holds a JA4 that `tls.peet.ws`
computed for this repository's `http` engine. Running that same client into a
socket that only reads the first record and computing the fingerprint here gives
`t13d1812h1_85036bcba153_b26ce05bbdd6`, character for character the stored
value. `tests/test_tlsfp.py` pins it.

Two things about that check are worth more than the match itself:

- **It exercised the asymmetry rather than stepping around it.** Pointed at a
  bare `127.0.0.1` the same client gives `t13i1811h1_85036bcba153_b26ce05bbdd6`:
  the SNI character flips, the extension count drops by exactly one, and the
  extension **hash does not move**. The single missing extension is `0x0000`.
  That is direct evidence for the rule below that `server_name` is counted and
  not hashed, which is the part of the spec most likely to be transcribed wrong
  and the part a matching pair of hostname runs would never have tested.
- **A cheaper check was tried first and failed, and the failure said nothing.**
  The stored Chrome-family rows carry the counts but not the lists, so the first
  attempt reconstructed Chrome's extension list from memory and searched for a
  preimage of `d8a2da3f94cd`. It found none. That is a fact about the
  reconstruction, not about this file, and had it been read the other way it
  would have sent a correct parser back for repair. A cross-check is only
  evidence when the input is known rather than guessed.

`ja4_r`, the readable form the hash is taken over, is returned alongside the
digest regardless. A JA4 is comparable across tools; the raw string is the only
thing that makes a disagreement between two of them resolvable without re-running
the engine that produced it.

Spec, FoxIO JA4, transcribed 2026-09-02:

    a_b_c

    a = protocol char, TLS version, SNI char, cipher count, extension count,
        the first and last character of the first ALPN value
    b = sha256 of the cipher list, GREASE removed, sorted, comma delimited,
        truncated to 12 hex characters
    c = sha256 of the extension list, GREASE removed, `server_name` and
        `application_layer_protocol_negotiation` removed, sorted, comma
        delimited, then an underscore, then the signature algorithms in the
        order they were sent, comma delimited; truncated to 12 characters

Two asymmetries in that are easy to read past and are the most likely place for
this file to be wrong. The extension **count** in `a` includes `server_name` and
ALPN; the extension **hash** in `c` excludes them. And the ciphers and extensions
are sorted while the signature algorithms are not - their order is itself a
tell, so sorting them would throw the thing away.
"""
import hashlib
import struct

# RFC 8701. A browser sends these to keep the ecosystem tolerant of unknown
# values; most tooling does not, which makes their presence the cheapest tell in
# the whole handshake. JA4 removes them everywhere precisely so that the
# fingerprint is stable across the random pick a client makes per connection -
# leaving them in would give one client a different JA4 on every connection.
GREASE = frozenset({0x0a0a, 0x1a1a, 0x2a2a, 0x3a3a, 0x4a4a, 0x5a5a, 0x6a6a,
                    0x7a7a, 0x8a8a, 0x9a9a, 0xaaaa, 0xbaba, 0xcaca, 0xdada,
                    0xeaea, 0xfafa})

EXT_SERVER_NAME = 0x0000
EXT_ALPN = 0x0010
EXT_SIGNATURE_ALGORITHMS = 0x000d
EXT_SUPPORTED_VERSIONS = 0x002b

# The two-character version field. `s3`/`s2` are in the spec and cannot be
# produced by anything this harness runs; they are here so the mapping is the
# spec's rather than a subset of it that happens to cover our engines.
VERSIONS = {0x0304: "13", 0x0303: "12", 0x0302: "11", 0x0301: "10",
            0x0300: "s3", 0x0002: "s2"}

# A ClientHello is under 3 KB in practice - Chrome 151 sends about 1.7 KB with
# the post-quantum key share. The cap is what stops a peer that never completes
# a record from being buffered indefinitely by a relay that is only watching.
MAX_HELLO_BYTES = 16384


class _Reader:
    """Bounds-checked forward reader.

    Every length in a ClientHello comes from the peer, so every read here is a
    read of attacker-controlled arithmetic. Raising on overrun and catching once
    at the top is what keeps `ja4` total: this parser runs inside the relay's
    byte-copy loop, and a malformed hello has to cost the fingerprint and never
    the tunnel.
    """

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, count: int) -> bytes:
        if count < 0 or self.pos + count > len(self.data):
            raise ValueError("truncated")
        chunk = self.data[self.pos:self.pos + count]
        self.pos += count
        return chunk

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def u24(self) -> int:
        raw = self.take(3)
        return (raw[0] << 16) | (raw[1] << 8) | raw[2]

    def block8(self) -> bytes:
        return self.take(self.u8())

    def block16(self) -> bytes:
        return self.take(self.u16())


def _u16_list(raw: bytes) -> list:
    return [struct.unpack(">H", raw[i:i + 2])[0]
            for i in range(0, len(raw) - 1, 2)]


def hello_is_complete(data: bytes) -> bool:
    """Whether `data` holds a whole TLS record carrying a ClientHello.

    The relay sees the wire, not messages: a hello can arrive split across two
    reads, and Chrome's is large enough that it sometimes does. Parsing a
    partial one produces a plausible-looking JA4 with a short cipher list, which
    is worse than no fingerprint because nothing about it looks wrong.

    False for anything that is not a handshake record, so a caller can use it to
    decide there is nothing here to wait for.
    """
    if len(data) < 5:
        return False
    if data[0] != 0x16:                  # handshake record
        return False
    length = struct.unpack(">H", data[3:5])[0]
    return len(data) >= 5 + length


def _alpn_chars(values: list) -> str:
    """The first and last character of the first ALPN value.

    `h2` gives `h2` and `http/1.1` gives `h1`, which is the whole point: two
    characters that separate the common cases without carrying the list.
    """
    if not values:
        return "00"
    first = values[0]
    if not first:
        return "00"
    head, tail = first[0:1], first[-1:]
    if head.isalnum() and tail.isalnum():
        return (head + tail).decode("ascii")
    # A non-alphanumeric ALPN is not something a browser sends, and the spec
    # asks for hex rather than for whatever bytes happen to be there, so that
    # the field stays two printable characters.
    return f"{first[0]:02x}"[0] + f"{first[-1]:02x}"[1]


def _digest(text: str) -> str:
    """12 hex characters, or twelve zeroes when there was nothing to hash.

    The zeroes are in the spec and they carry information: a client that sent no
    extensions is a different client from one whose extensions hashed to
    something, and both have to be representable.
    """
    if not text:
        return "000000000000"
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def parse_client_hello(data: bytes) -> dict:
    """The fields JA4 is built from, or `None` if this is not a ClientHello.

    Returns the lists rather than the fingerprint so that the parse and the
    convention can fail separately. `tests/test_tlsfp.py` asserts on this
    dictionary for handshakes it generates itself, which is a check the hash
    format cannot give: the ciphers and ALPN are chosen by the test, so a wrong
    answer is visible without trusting any published vector.
    """
    try:
        reader = _Reader(data)
        if reader.u8() != 0x16:
            return None
        reader.u16()                                       # record version
        body = _Reader(reader.block16())
        if body.u8() != 0x01:                              # ClientHello
            return None
        body.u24()                                         # handshake length
        legacy_version = body.u16()
        body.take(32)                                      # random
        body.block8()                                      # session id
        ciphers = [c for c in _u16_list(body.block16()) if c not in GREASE]
        body.block8()                                      # compression methods

        extensions, alpn, sigalgs = [], [], []
        version = None
        # A ClientHello with no extension block at all is legal and pre-dates
        # TLS 1.2. It reaches here as an exhausted reader rather than as an
        # error, so the absence is not a parse failure.
        if body.pos < len(body.data):
            block = _Reader(body.block16())
            while block.pos < len(block.data):
                kind = block.u16()
                payload = block.block16()
                if kind in GREASE:
                    continue
                extensions.append(kind)
                if kind == EXT_SUPPORTED_VERSIONS:
                    offered = [v for v in _u16_list(payload[1:])
                               if v not in GREASE]
                    # The highest offered, not the first. A client lists them in
                    # preference order and a server picks one; JA4 records what
                    # the client is willing to speak, and its ceiling is the
                    # part that separates a modern browser from a legacy stack.
                    if offered:
                        version = max(offered)
                elif kind == EXT_SIGNATURE_ALGORITHMS:
                    sigalgs = [s for s in _u16_list(payload[2:])
                               if s not in GREASE]
                elif kind == EXT_ALPN:
                    names = _Reader(payload)
                    names.u16()                            # list length
                    while names.pos < len(names.data):
                        alpn.append(names.block8())
    except (ValueError, struct.error, IndexError):
        return None

    if version is None:
        version = legacy_version
    return {"version": version, "ciphers": ciphers, "extensions": extensions,
            "sigalgs": sigalgs, "alpn": alpn,
            "sni": EXT_SERVER_NAME in extensions}


def ja4(data: bytes, *, transport: str = "t") -> tuple:
    """`(ja4, ja4_r)` for one ClientHello, or `(None, None)`.

    `transport` is `t` for TCP and `q` for QUIC. It is a parameter and not
    something sniffed from the bytes because the bytes cannot say: the caller
    knows which socket they came off and the record header does not. Every
    caller in this repository passes the default, because a CONNECT tunnel is
    TCP by construction.

    The raw form is returned with the hash and not instead of it. A JA4 is 36
    characters and comparable across tools; `ja4_r` is a few hundred and is the
    only thing that makes a disagreement between two tools resolvable without
    re-running the engine that produced it.
    """
    parsed = parse_client_hello(data)
    if parsed is None:
        return None, None

    ciphers = [f"{c:04x}" for c in parsed["ciphers"]]
    # Counted before `server_name` and ALPN are dropped, which is the asymmetry
    # named in the module docstring.
    extension_count = len(parsed["extensions"])
    hashed = [f"{e:04x}" for e in parsed["extensions"]
              if e not in (EXT_SERVER_NAME, EXT_ALPN)]
    sigalgs = [f"{s:04x}" for s in parsed["sigalgs"]]

    prefix = (f"{transport}"
              f"{VERSIONS.get(parsed['version'], '00')}"
              f"{'d' if parsed['sni'] else 'i'}"
              f"{min(len(ciphers), 99):02d}"
              f"{min(extension_count, 99):02d}"
              f"{_alpn_chars(parsed['alpn'])}")

    cipher_text = ",".join(sorted(ciphers))
    extension_text = ",".join(sorted(hashed))
    if sigalgs:
        extension_text += "_" + ",".join(sigalgs)
    readable = f"{prefix}_{cipher_text}_{extension_text}"
    return f"{prefix}_{_digest(cipher_text)}_{_digest(extension_text)}", readable
