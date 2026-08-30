"""Run Jarvis with the same Python interpreter that installed it."""
from __future__ import annotations

import ipaddress
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import uvicorn

from jarvis.config import Settings, get_settings


def _is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _local_ip_addresses(host: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = {
        ipaddress.ip_address("127.0.0.1"),
        ipaddress.ip_address("::1"),
    }

    if host not in {"", "0.0.0.0", "::"}:
        try:
            addresses.add(ipaddress.ip_address(host))
        except ValueError:
            pass

    try:
        hostname = socket.gethostname()
        for value in socket.gethostbyname_ex(hostname)[2]:
            addresses.add(ipaddress.ip_address(value))
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            addresses.add(ipaddress.ip_address(sock.getsockname()[0]))
    except OSError:
        pass

    return addresses


def _certificate_hosts(settings: Settings) -> set[str]:
    hosts = {"localhost", socket.gethostname()}

    if settings.jarvis_bind_host not in {"", "0.0.0.0", "::"}:
        hosts.add(settings.jarvis_bind_host)

    for origin in settings.allowed_origins:
        parsed = urlparse(origin)
        if parsed.hostname:
            hosts.add(parsed.hostname)

    return hosts


def ensure_local_certificate(settings: Settings) -> tuple[Path, Path]:
    certfile = settings.jarvis_ssl_certfile
    keyfile = settings.jarvis_ssl_keyfile

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError as exc:
        raise RuntimeError(
            "HTTPS mode requires cryptography. Reinstall Jarvis with the current pyproject."
        ) from exc

    expected_hosts = _certificate_hosts(settings)
    expected_addresses = _local_ip_addresses(settings.jarvis_bind_host)

    for host in expected_hosts:
        try:
            expected_addresses.add(ipaddress.ip_address(host))
        except ValueError:
            pass

    if certfile.exists() and keyfile.exists():
        try:
            certificate = x509.load_pem_x509_certificate(certfile.read_bytes())
            san = certificate.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value
            dns_names = set(san.get_values_for_type(x509.DNSName))
            ip_addresses = set(san.get_values_for_type(x509.IPAddress))
            if (
                all(
                    host in dns_names
                    for host in expected_hosts
                    if not _is_ip_address(host)
                )
                and expected_addresses.issubset(ip_addresses)
            ):
                return certfile, keyfile
        except Exception:
            pass

    certfile.parent.mkdir(parents=True, exist_ok=True)
    keyfile.parent.mkdir(parents=True, exist_ok=True)

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    names: list[x509.GeneralName] = []

    for host in expected_hosts:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            names.append(x509.DNSName(host))

    for address in expected_addresses:
        names.append(x509.IPAddress(address))

    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "Jarvis Local"),
        ]
    )

    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .sign(private_key, hashes.SHA256())
    )

    keyfile.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    certfile.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))

    return certfile, keyfile


def main() -> None:
    settings = get_settings()
    kwargs = {}
    if settings.jarvis_https:
        certfile, keyfile = ensure_local_certificate(settings)
        kwargs["ssl_certfile"] = str(certfile)
        kwargs["ssl_keyfile"] = str(keyfile)
    uvicorn.run(
        "jarvis.main:app",
        host=settings.jarvis_bind_host,
        port=settings.jarvis_port,
        **kwargs,
    )


if __name__ == "__main__":
    main()
