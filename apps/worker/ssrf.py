import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


async def validate_public_http_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("HTTP node URLs must use http or https and include a hostname.")
    if parsed.username or parsed.password:
        raise ValueError("Credentials are not allowed in HTTP node URLs.")
    if parsed.port not in (None, 80, 443):
        raise ValueError("HTTP node URLs may only use ports 80 and 443.")

    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as error:
        raise ValueError("The HTTP node hostname could not be resolved.") from error
    if not addresses:
        raise ValueError("The HTTP node hostname did not resolve to an address.")
    for address in {item[4][0] for item in addresses}:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if not ip.is_global:
            raise ValueError("HTTP node URLs must resolve only to public IP addresses.")
