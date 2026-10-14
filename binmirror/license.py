"""Licenza Gumroad, stesso schema di DropCut.

verify() ha tre risultati: True valida, False non valida (blocca), None errore di
rete/SSL (si tollera per GRACE secondi dall'ultima verifica riuscita).
"""
import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

PRODUCT_ID = os.environ.get("BINMIRROR_GUMROAD_PRODUCT_ID", "REPLACE_WITH_PRODUCT_ID")
VERIFY_URL = "https://api.gumroad.com/v2/licenses/verify"
RECHECK = 24 * 3600
GRACE = 7 * 24 * 3600
log = logging.getLogger("binmirror")


def enabled():
    """Le build interne (product id non impostato) saltano il controllo."""
    return bool(PRODUCT_ID) and "REPLACE_WITH" not in PRODUCT_ID


def _ssl_ctx():
    # PyInstaller a volte non trova i certificati di sistema: certifi se c'è.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def verify(key):
    """Ritorna (ok, messaggio)."""
    data = urllib.parse.urlencode({"product_id": PRODUCT_ID, "license_key": key.strip(),
                                   "increment_uses_count": "false"}).encode()
    req = urllib.request.Request(VERIFY_URL, data=data, method="POST",
                                 headers={"User-Agent": "BinMirror/%s" % __version__})
    try:
        with urllib.request.urlopen(req, timeout=20, context=_ssl_ctx()) as resp:
            j = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8", "replace")).get("message")
        except Exception:
            msg = None
        log.warning("gumroad HTTP %s: %s", e.code, msg)
        return False, msg or "HTTP %s" % e.code
    except (urllib.error.URLError, ssl.SSLError, OSError, ValueError) as e:
        log.warning("gumroad rete/SSL: %s", e)
        return None, "%s: %s" % (type(e).__name__, e)
    if not j.get("success"):
        return False, j.get("message") or "Licenza non valida"
    p = j.get("purchase") or {}
    if p.get("refunded") or p.get("chargebacked") or p.get("disputed"):
        return False, "Licenza revocata (rimborso o contestazione)"
    return True, "OK"


def status(cfg, now=None):
    """Cosa fare all'avvio: 'ok', 'ask' (manca la chiave) o 'check' (da riverificare)."""
    if not enabled():
        return "ok"
    if not (cfg.get("license_key") or "").strip():
        return "ask"
    now = time.time() if now is None else now
    return "ok" if now - float(cfg.get("license_last_verified") or 0) < RECHECK else "check"


def offline_expired(cfg, now=None):
    now = time.time() if now is None else now
    return now - float(cfg.get("license_last_verified") or 0) > GRACE
