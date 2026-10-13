"""
The TLS side of the link between a server and a client.

Both ends are authenticated by pinning: a context trusts exactly the certificates it was given at pairing time and
nothing else, so there is no CA, no hostname and no way for a proxy presenting a different certificate (an SSL-strip
attempt, a hostile device on the LAN) to get through: the handshake fails and no application data is ever exchanged.
OpenSSL does the verification; `peer_fingerprint` is only there for the application to know *which* paired node it is
talking to.
"""
from __future__ import annotations

import os
import ssl
import tempfile
from pathlib import Path
from typing import Iterable, Optional

from utils.remote.identity import fingerprint


def _load_identity(ctx: ssl.SSLContext, cert_pem: str, key_pem: str):
    # load_cert_chain only takes paths: the key goes through a private file on a tmpfs, for as long as it takes
    base = '/dev/shm' if Path('/dev/shm').is_dir() else None
    with tempfile.TemporaryDirectory(dir=base) as tmp:
        path = Path(tmp) / 'identity.pem'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(cert_pem + '\n' + key_pem)
        ctx.load_cert_chain(path)


def _pin(ctx: ssl.SSLContext, certs: Iterable[str]):
    certs = [c for c in certs if c]
    if certs:
        ctx.load_verify_locations(cadata='\n'.join(certs))
    # with nothing pinned the store stays empty and every peer is refused, which is the point
    ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN


def server_context(cert_pem: str, key_pem: str, trusted_clients: Iterable[str]) -> ssl.SSLContext:
    """The listener: presents this node's certificate and REQUIRES a client certificate that is one of the pinned ones."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    _load_identity(ctx, cert_pem, key_pem)
    ctx.verify_mode = ssl.CERT_REQUIRED
    _pin(ctx, trusted_clients)
    return ctx


def client_context(cert_pem: str, key_pem: str, server_cert_pem: str) -> ssl.SSLContext:
    """The uplink: presents this node's certificate and accepts only the pinned server certificate (no hostname check:
    the certificate is the identity, the address it is reached at may change)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = False
    _load_identity(ctx, cert_pem, key_pem)
    ctx.verify_mode = ssl.CERT_REQUIRED
    _pin(ctx, [server_cert_pem])
    return ctx


def peer_fingerprint(ssl_object: Optional[ssl.SSLObject]) -> Optional[str]:
    """Fingerprint of the certificate the other end presented on an established connection, if there is one."""
    if ssl_object is None:
        return None
    der = ssl_object.getpeercert(binary_form=True)
    return fingerprint(der) if der else None
