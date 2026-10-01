"""Python-side reimplementation of what the unlock pages do in the browser,
so tests can open an encrypted report and look inside."""
import base64
import json
import re

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

b64 = base64.b64decode


def payload_of(page):
    return json.loads(re.search(r'<script type="application/json" id="encPayload">(.*?)</script>', page, re.S).group(1))


def _unwrap(slots, password, iterations):
    for slot in slots:
        kek = PBKDF2HMAC(algorithm=SHA256(), length=32, salt=b64(slot['salt']), iterations=iterations).derive(password.encode('utf-8'))
        try:
            return AESGCM(kek).decrypt(b64(slot['iv']), b64(slot['key']), None)
        except InvalidTag:
            continue
    return None


def open_report(page, password):
    """encrypt_report() page -> decrypted report html, or None for a wrong password."""
    p = payload_of(page)
    key = _unwrap(p['slots'], password, p['iter'])
    if key is None:
        return None
    return AESGCM(key).decrypt(b64(p['iv']), b64(p['data']), None).decode('utf-8')


def open_scope(page, scope_label, password):
    """encrypt_scoped_report() page -> (decrypted report html, role), or None."""
    p = payload_of(page)
    scope = next(s for s in p['scopes'] if s['label'] == scope_label)
    key = _unwrap(scope['slots'], password, p['iter'])
    if key is None:
        return None
    bundle = json.loads(AESGCM(key).decrypt(b64(scope['iv']), b64(scope['data']), None).decode('utf-8'))
    shell = AESGCM(b64(bundle['shellKey'])).decrypt(b64(p['shell']['iv']), b64(p['shell']['data']), None).decode('utf-8')
    return shell.replace(p['placeholder'], bundle['data']), scope['role']


def core_rows(report_html):
    return json.loads(re.search(r'<script type="application/json" id="coreData">(.*?)</script>', report_html, re.S).group(1))['rows']
