"""
Real encryption for the shared HTML report.

The report's own lock screen only hides the page -- the password and every
customer row (상호/계약번호/설치주소/담당자) sit in the HTML source in plain
text, so anyone who gets the file (or its URL) can read everything with
"view source". That's fine for a file passed hand to hand inside the team,
not for anything hosted on the web.

encrypt_report() wraps the finished report in a small unlock page instead:

- the whole report is encrypted once with a random 256-bit content key
  (AES-256-GCM);
- that content key is then wrapped separately for each password (user /
  admin) with a key derived by PBKDF2-SHA256 -- either password unlocks the
  same report, and which one was used still decides whether the admin-only
  매칭설정 panel shows;
- the browser does the reverse with WebCrypto, then document.write()s the
  decrypted report and feeds the typed password to its existing lock screen.

Nothing readable is left in the file without a password, so the security
now rests on password strength -- hence generate_strong_password().
"""

import base64
import html
import json
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITERATIONS = 310_000
# Ambiguous characters (0/O, 1/l/I) left out so a password read aloud or
# retyped from a message doesn't fail on a look-alike.
PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"


def generate_strong_password(length=12):
    """12 chars from a 56-symbol alphabet is ~70 bits -- out of reach for an
    offline guessing attack even against a publicly hosted file."""
    return ''.join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def _b64(data):
    return base64.b64encode(data).decode('ascii')


def _derive_key(password, salt, iterations):
    kdf = PBKDF2HMAC(algorithm=SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(password.encode('utf-8'))


def encrypt_report(report_html, passwords, expiry_date=None, title="Data Intel PRO 보안 리포트"):
    """passwords: iterable of passwords that may unlock the report (the same
    content key is wrapped once per password). Returns the unlock-page HTML."""
    content_key = AESGCM.generate_key(bit_length=256)
    content_iv = os.urandom(12)
    ciphertext = AESGCM(content_key).encrypt(content_iv, report_html.encode('utf-8'), None)

    slots = []
    for pwd in dict.fromkeys(p for p in passwords if p):
        salt, iv = os.urandom(16), os.urandom(12)
        wrapped = AESGCM(_derive_key(pwd, salt, PBKDF2_ITERATIONS)).encrypt(iv, content_key, None)
        slots.append({"salt": _b64(salt), "iv": _b64(iv), "key": _b64(wrapped)})
    if not slots:
        raise ValueError("암호화에 사용할 비밀번호가 없습니다.")

    payload = {
        "v": 1, "iter": PBKDF2_ITERATIONS, "slots": slots,
        "iv": _b64(content_iv), "data": _b64(ciphertext),
    }
    payload_json = json.dumps(payload, separators=(',', ':'))
    expiry_html = f"<p class=\"exp\">만료일: {html.escape(expiry_date)}</p>" if expiry_date else ""

    return UNLOCK_PAGE_TEMPLATE.replace('__TITLE__', html.escape(title)) \
        .replace('__EXPIRY__', expiry_html) \
        .replace('__PAYLOAD__', payload_json)


UNLOCK_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex, nofollow">
<title>__TITLE__</title>
<style>
:root { --bg: #f8fafc; --card: #ffffff; --text: #0f172a; --muted: #64748b; --border: #e2e8f0; --brand: #2563eb; --error: #d03b3b; }
@media (prefers-color-scheme: dark) { :root { --bg: #0f172a; --card: #1e293b; --text: #f1f5f9; --muted: #94a3b8; --border: #334155; } }
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center; background: var(--bg); color: var(--text);
       font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, 'Malgun Gothic', sans-serif; padding: 16px; }
.card { width: 100%; max-width: 380px; background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 32px 28px; text-align: center; }
h1 { font-size: 20px; margin: 0 0 6px; }
.exp, .hint { font-size: 12.5px; color: var(--muted); margin: 0 0 18px; }
input { width: 100%; font-size: 16px; padding: 12px 14px; border: 1px solid var(--border); border-radius: 10px; background: var(--bg); color: var(--text); }
button { width: 100%; margin-top: 12px; font-size: 15px; font-weight: 700; padding: 12px; border: 0; border-radius: 10px; background: var(--brand); color: #fff; cursor: pointer; }
button:disabled { opacity: .6; cursor: progress; }
.err { min-height: 18px; margin-top: 10px; font-size: 13px; color: var(--error); }
</style>
</head>
<body>
<form class="card" id="unlockForm" autocomplete="off">
    <h1>🔒 __TITLE__</h1>
    __EXPIRY__
    <p class="hint">공유받은 비밀번호를 입력하세요. 데이터는 암호화되어 있습니다.</p>
    <input type="password" id="unlockPwd" placeholder="비밀번호" autofocus>
    <button type="submit" id="unlockBtn">열기</button>
    <div class="err" id="unlockErr"></div>
</form>
<script type="application/json" id="encPayload">__PAYLOAD__</script>
<script>
(function () {
    const payload = JSON.parse(document.getElementById('encPayload').textContent);
    const b64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
    const form = document.getElementById('unlockForm');
    const btn = document.getElementById('unlockBtn');
    const err = document.getElementById('unlockErr');

    async function unwrapContentKey(pwd) {
        const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(pwd), 'PBKDF2', false, ['deriveKey']);
        for (const slot of payload.slots) {
            const kek = await crypto.subtle.deriveKey(
                { name: 'PBKDF2', salt: b64(slot.salt), iterations: payload.iter, hash: 'SHA-256' },
                base, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
            try {
                return await crypto.subtle.decrypt({ name: 'AES-GCM', iv: b64(slot.iv) }, kek, b64(slot.key));
            } catch (e) { /* not this slot -- try the next password slot */ }
        }
        return null;
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const pwd = document.getElementById('unlockPwd').value;
        if (!pwd) return;
        if (!window.crypto || !crypto.subtle) {
            err.textContent = '이 브라우저에서는 열 수 없습니다 (https 주소 또는 최신 브라우저가 필요합니다).';
            return;
        }
        btn.disabled = true; btn.textContent = '여는 중...'; err.textContent = '';
        try {
            const rawKey = await unwrapContentKey(pwd);
            if (!rawKey) throw new Error('bad password');
            const key = await crypto.subtle.importKey('raw', rawKey, 'AES-GCM', false, ['decrypt']);
            const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: b64(payload.iv) }, key, b64(payload.data));
            const reportHtml = new TextDecoder().decode(plain);
            document.open();
            document.write(reportHtml);
            document.close();
            // Hand the same password to the report's own lock screen: it still
            // enforces the expiry date and decides user vs admin view.
            const unlock = () => {
                const input = document.getElementById('pwd');
                if (input && typeof window.checkPassword === 'function') { input.value = pwd; window.checkPassword(); }
            };
            if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', unlock); else unlock();
        } catch (ex) {
            btn.disabled = false; btn.textContent = '열기';
            err.textContent = '비밀번호가 올바르지 않습니다.';
        }
    });
})();
</script>
</body>
</html>"""
