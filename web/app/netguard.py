"""Netzwerk-Stufen.

Drei Stufen, absteigend:
  lan     — Heimnetz. Darf alles, auch kritische Aktionen (Rollen, Kick, Bann).
  vpn     — Tailscale o. ä. Darf alles außer den kritischen Aktionen.
  extern  — alles andere. Darf nichts.

Die echte Client-IP steht hinter einem Reverse-Proxy (WebHafen/Caddy) im
Header X-Forwarded-For. Diesem Header darf man nur glauben, wenn die direkte
Gegenstelle selbst aus einem vertrauenswürdigen Netz kommt — sonst könnte sich
jeder von außen als Heimnetz ausgeben.
"""
from __future__ import annotations

import ipaddress

from fastapi import Request

from . import settings

LAN = "lan"
VPN = "vpn"
EXTERN = "extern"

_RANK = {EXTERN: 0, VPN: 1, LAN: 2}


def _networks(raw: list[str]) -> list[ipaddress._BaseNetwork]:
    nets = []
    for entry in raw:
        try:
            nets.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return nets


def _parse_ip(value: str):
    value = (value or "").strip()
    if not value:
        return None
    if value.startswith("[") and "]" in value:          # [::1]:1234
        value = value[1 : value.index("]")]
    elif value.count(":") == 1 and "." in value:        # 1.2.3.4:1234
        value = value.split(":", 1)[0]
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _classify(ip, lan_nets, vpn_nets) -> str:
    if ip is None:
        return EXTERN
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if any(ip in net for net in lan_nets):
        return LAN
    if any(ip in net for net in vpn_nets):
        return VPN
    return EXTERN


def client_tier(request: Request) -> str:
    """Stufe des Aufrufers. Bei einer Kette von Proxies zählt die schwächste
    Stufe aller beteiligten Adressen — eine Anfrage ist nie vertrauenswürdiger
    als die unsicherste Station auf ihrem Weg."""
    from . import config

    lan_nets = _networks(settings.get_list("net.lan") or config.TRUSTED_LAN)
    vpn_nets = _networks(settings.get_list("net.vpn") or config.TRUSTED_VPN)

    peer = _parse_ip(request.client.host if request.client else "")
    tier = _classify(peer, lan_nets, vpn_nets)

    forwarded = request.headers.get("x-forwarded-for", "")
    if config.TRUST_PROXY and forwarded and tier != EXTERN:
        for part in forwarded.split(","):
            hop = _classify(_parse_ip(part), lan_nets, vpn_nets)
            if _RANK[hop] < _RANK[tier]:
                tier = hop
    return tier


def allows(tier: str, required: str) -> bool:
    return _RANK.get(tier, 0) >= _RANK.get(required, 0)


def tier_label(tier: str) -> str:
    return {LAN: "Heimnetz", VPN: "VPN", EXTERN: "Externes Netz"}.get(tier, tier)
