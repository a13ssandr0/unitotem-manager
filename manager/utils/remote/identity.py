"""
The identity of a node: a self-signed certificate and its key, generated once and kept for the life of the installation
(they are part of the backup). Pairing pins the peer's certificate, so nothing here is ever "renewed" behind a peer's
back: the validity is deliberately very long and starts in the past, because a kiosk with no battery-backed clock can
boot with a date far in the past and a certificate that is "not yet valid" would refuse every connection until the clock
is set.
"""
from __future__ import annotations

import datetime
import hashlib
from typing import Union

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

_NOT_BEFORE = datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc)
_LIFETIME = datetime.timedelta(days=365 * 80)


def generate_identity(common_name: str = 'unitotem-node') -> tuple[str, str]:
    """A new (certificate PEM, private key PEM). ECDSA P-256: instant even on a Raspberry Pi, unlike RSA-4096."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_NOT_BEFORE)
            .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + _LIFETIME)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM).decode(),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode())


def cert_der(cert: Union[str, bytes]) -> bytes:
    """The DER form of a certificate given as PEM (str/bytes) or already as DER (bytes). Raises ValueError otherwise."""
    if isinstance(cert, str):
        cert = cert.encode()
    try:
        if b'-----BEGIN' in cert:
            parsed = x509.load_pem_x509_certificate(cert)
        else:
            parsed = x509.load_der_x509_certificate(cert)
        return parsed.public_bytes(serialization.Encoding.DER)
    except Exception as e:  # cryptography raises several types for garbage input
        raise ValueError(f'Not a certificate: {e}') from e


def der_to_pem(der: bytes) -> str:
    return x509.load_der_x509_certificate(der).public_bytes(serialization.Encoding.PEM).decode()


def fingerprint(cert: Union[str, bytes]) -> str:
    """SHA-256 of the DER certificate, lowercase hex: what pairing pins and what the user is asked to compare."""
    return hashlib.sha256(cert_der(cert)).hexdigest()


def node_id(cert: Union[str, bytes]) -> str:
    """The identifier of a node, derived from its certificate so it follows the identity through a restore."""
    return fingerprint(cert)[:32]


def format_fingerprint(fp: str) -> str:
    """'ab12...' -> 'AB:12:...', the way browsers and `openssl x509 -fingerprint` show it."""
    fp = fp.upper()
    return ':'.join(fp[i:i + 2] for i in range(0, len(fp), 2))


def check_pair(cert_pem: str, key_pem: str) -> bool:
    """True if the key belongs to the certificate (a restored backup is only as good as its contents)."""
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode())
        key = serialization.load_pem_private_key(key_pem.encode(), None)
    except Exception:
        return False
    spki = serialization.PublicFormat.SubjectPublicKeyInfo
    der = serialization.Encoding.DER
    return cert.public_key().public_bytes(der, spki) == key.public_key().public_bytes(der, spki)
