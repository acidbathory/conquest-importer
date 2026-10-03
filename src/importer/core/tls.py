"""TLS context that trusts the operating system's certificate store.

Corporate networks often re-sign HTTPS traffic with a company CA that is in the
OS trust store but not in Python's bundled list. Using the OS store keeps
certificate verification ON while working on those networks.
"""

import ssl

import truststore


def system_tls_context() -> ssl.SSLContext:
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
