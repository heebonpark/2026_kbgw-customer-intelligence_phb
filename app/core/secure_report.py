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

# The fixed admin password lives only on each PC (never in this public repo --
# anyone who reads it from the source could decrypt every published report).
SETTINGS_PATH = os.path.join(os.path.expanduser("~/.dataintelligence_pro"), "report_settings.json")
# Ambiguous characters (0/O, 1/l/I) left out so a password read aloud or
# retyped from a message doesn't fail on a look-alike.
PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"


def generate_strong_password(length=12):
    """12 chars from a 56-symbol alphabet is ~70 bits -- out of reach for an
    offline guessing attack even against a publicly hosted file."""
    return ''.join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def load_admin_password():
    """The admin password saved on this PC, or None if none is set."""
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            value = json.load(f).get('admin_password')
        return value or None
    except (FileNotFoundError, ValueError, OSError):
        return None


def save_admin_password(password):
    """Saves (or, with a blank value, clears) this PC's fixed admin password."""
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            settings = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        settings = {}
    settings['admin_password'] = password or None
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


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
                // __dimOuterOk: this password just decrypted the report, so the inner lock can trust it without
                // carrying the admin password in the page itself (report.py checkPassword).
                window.__dimOuterOk = pwd;
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


# ---------------------------------------------------------------------------
# 범위(지사)별 잠금 -- 한 파일, 한 링크인데 비밀번호에 따라 볼 수 있는 데이터가 다르다.
#
# 화면 틀(shell: CSS·스크립트, 데이터 없음)은 틀 키로 한 번만 암호화하고, 데이터는 범위마다
# 따로 암호화한다. 범위의 비밀번호로 풀리는 것은 그 범위의 꾸러미(틀 키 + 그 범위 데이터)뿐이라,
# 중앙지사 비밀번호로는 강북지사 데이터를 풀 방법이 없다 (화면에서 가리는 것이 아니다).
# 관리자 범위는 전체 데이터를 담는다.
# ---------------------------------------------------------------------------

SCOPE_PLACEHOLDER = "__SCOPE_DATA_7c1f__"
SCOPE_SETTING = 'core_scope_passwords'


def load_scope_passwords():
    """이 PC에 저장된 범위별 비밀번호 {이름: 비밀번호} -- 다시 배포해도 같은 비밀번호가 유지된다."""
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            value = json.load(f).get(SCOPE_SETTING)
        return dict(value) if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def save_scope_passwords(passwords):
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            settings = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        settings = {}
    settings[SCOPE_SETTING] = {k: v for k, v in passwords.items() if v}
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def ensure_scope_passwords(names):
    """names 각각에 비밀번호가 있게 한다 (없는 것만 새로 만들어 저장) -> {이름: 비밀번호} (names 순서)."""
    saved = load_scope_passwords()
    missing = [n for n in names if not saved.get(n)]
    for n in missing:
        saved[n] = generate_strong_password(10)
    if missing:
        save_scope_passwords(saved)
    return {n: saved[n] for n in names}


def _wrap_slots(content_key, passwords):
    slots = []
    for pwd in dict.fromkeys(p for p in passwords if p):
        salt, iv = os.urandom(16), os.urandom(12)
        wrapped = AESGCM(_derive_key(pwd, salt, PBKDF2_ITERATIONS)).encrypt(iv, content_key, None)
        slots.append({"salt": _b64(salt), "iv": _b64(iv), "key": _b64(wrapped)})
    return slots


def encrypt_scoped_report(shell_html, scopes, expiry_date=None, title="Data Intel PRO 보안 리포트"):
    """shell_html: SCOPE_PLACEHOLDER가 데이터 자리에 들어 있는 리포트 틀.
    scopes: [{"id", "group"(로그인 화면의 관리주체), "label"(지사 이름 등), "role"('user'|'admin'),
              "passwords": [...], "data": 그 범위의 데이터(JSON 문자열)}]
    -> 로그인 화면 HTML."""
    if SCOPE_PLACEHOLDER not in shell_html:
        raise ValueError("리포트 틀에 데이터 자리가 없습니다.")
    shell_key = AESGCM.generate_key(bit_length=256)
    shell_iv = os.urandom(12)
    shell_data = AESGCM(shell_key).encrypt(shell_iv, shell_html.encode('utf-8'), None)

    packed = []
    for scope in scopes:
        slots_key = AESGCM.generate_key(bit_length=256)
        slots = _wrap_slots(slots_key, scope["passwords"])
        if not slots:
            raise ValueError(f"'{scope['label']}'의 비밀번호가 없습니다.")
        iv = os.urandom(12)
        bundle = json.dumps({"shellKey": _b64(shell_key), "data": scope["data"]}, ensure_ascii=False).encode('utf-8')
        packed.append({"id": scope["id"], "group": scope["group"], "label": scope["label"], "role": scope["role"],
                       "slots": slots, "iv": _b64(iv), "data": _b64(AESGCM(slots_key).encrypt(iv, bundle, None))})

    payload = {"v": 2, "iter": PBKDF2_ITERATIONS, "placeholder": SCOPE_PLACEHOLDER,
               "shell": {"iv": _b64(shell_iv), "data": _b64(shell_data)}, "scopes": packed}
    expiry_html = f"<p class=\"exp\">만료일: {html.escape(expiry_date)}</p>" if expiry_date else ""
    return SCOPED_UNLOCK_TEMPLATE.replace('__TITLE__', html.escape(title)) \
        .replace('__EXPIRY__', expiry_html) \
        .replace('__PAYLOAD__', json.dumps(payload, separators=(',', ':'), ensure_ascii=False).replace('<', '\\u003c'))


SCOPED_UNLOCK_TEMPLATE = """<!DOCTYPE html>
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
.card { width: 100%; max-width: 400px; background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 30px 28px; }
h1 { font-size: 20px; margin: 0 0 6px; text-align: center; }
.exp, .hint { font-size: 12.5px; color: var(--muted); margin: 0 0 16px; text-align: center; }
label { display: block; font-size: 12px; font-weight: 700; color: var(--muted); margin: 12px 0 5px; }
select, input { width: 100%; font-size: 16px; padding: 11px 13px; border: 1px solid var(--border); border-radius: 10px; background: var(--bg); color: var(--text); font-family: inherit; }
button { width: 100%; margin-top: 16px; font-size: 15px; font-weight: 700; padding: 12px; border: 0; border-radius: 10px; background: var(--brand); color: #fff; cursor: pointer; font-family: inherit; }
button:disabled { opacity: .6; cursor: progress; }
.err { min-height: 18px; margin-top: 10px; font-size: 13px; color: var(--error); text-align: center; }
[hidden] { display: none; }
</style>
</head>
<body>
<form class="card" id="unlockForm" autocomplete="off">
    <h1>🔒 __TITLE__</h1>
    __EXPIRY__
    <p class="hint">관리주체와 지사를 고르고 받은 비밀번호를 입력하세요. 해당 범위의 자료만 열립니다.</p>
    <label for="scopeGroup">관리주체</label>
    <select id="scopeGroup"></select>
    <div id="branchField">
        <label for="scopeBranch">지사</label>
        <select id="scopeBranch"></select>
    </div>
    <label for="unlockPwd">비밀번호</label>
    <input type="password" id="unlockPwd" placeholder="비밀번호">
    <button type="submit" id="unlockBtn">열기</button>
    <div class="err" id="unlockErr" role="alert"></div>
</form>
<script type="application/json" id="encPayload">__PAYLOAD__</script>
<script>
(function () {
    const P = JSON.parse(document.getElementById('encPayload').textContent);
    const b64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
    const $ = id => document.getElementById(id);
    const form = $('unlockForm'), btn = $('unlockBtn'), err = $('unlockErr'), group = $('scopeGroup'), branch = $('scopeBranch');
    const remember = { get: () => { try { return JSON.parse(localStorage.getItem('dim-scope') || 'null'); } catch (e) { return null; } },
                       set: v => { try { localStorage.setItem('dim-scope', JSON.stringify(v)); } catch (e) { /* 저장 불가 */ } } };

    const groups = [];
    P.scopes.forEach(s => { if (!groups.includes(s.group)) groups.push(s.group); });
    groups.forEach(g => group.appendChild(new Option(g, g)));
    function fillBranches(pick) {
        const list = P.scopes.filter(s => s.group === group.value);
        branch.textContent = '';
        list.forEach(s => branch.appendChild(new Option(s.label, s.id)));
        if (pick && list.some(s => s.id === pick)) branch.value = pick;
        $('branchField').hidden = list.length <= 1;   // 본부장·관리자는 지사를 고르지 않는다
    }
    const last = remember.get();
    if (last && groups.includes(last.group)) group.value = last.group;
    fillBranches(last && last.id);
    group.addEventListener('change', () => { fillBranches(); err.textContent = ''; });
    $('unlockPwd').focus();

    const decode = buf => new TextDecoder().decode(buf);
    async function openScope(scope, pwd) {
        const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(pwd), 'PBKDF2', false, ['deriveKey']);
        let raw = null;
        for (const slot of scope.slots) {
            const kek = await crypto.subtle.deriveKey({ name: 'PBKDF2', salt: b64(slot.salt), iterations: P.iter, hash: 'SHA-256' },
                base, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
            try { raw = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: b64(slot.iv) }, kek, b64(slot.key)); break; } catch (e) { /* 다음 비밀번호 칸 */ }
        }
        if (!raw) return null;
        const key = await crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['decrypt']);
        const bundle = JSON.parse(decode(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: b64(scope.iv) }, key, b64(scope.data))));
        const shellKey = await crypto.subtle.importKey('raw', b64(bundle.shellKey), 'AES-GCM', false, ['decrypt']);
        const shell = decode(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: b64(P.shell.iv) }, shellKey, b64(P.shell.data)));
        return shell.split(P.placeholder).join(bundle.data);
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const pwd = $('unlockPwd').value;
        const scope = P.scopes.find(s => s.id === branch.value);
        if (!pwd || !scope) return;
        if (!window.crypto || !crypto.subtle) {
            err.textContent = '이 브라우저에서는 열 수 없습니다 (https 주소 또는 최신 브라우저가 필요합니다).';
            return;
        }
        btn.disabled = true; btn.textContent = '여는 중...'; err.textContent = '';
        try {
            const reportHtml = await openScope(scope, pwd);
            if (!reportHtml) throw new Error('bad password');
            remember.set({ group: scope.group, id: scope.id });
            document.open();
            document.write(reportHtml);
            document.close();
            const unlock = () => {
                const input = document.getElementById('pwd');
                // 안쪽 잠금(report.py checkPassword)에 방금 확인된 비밀번호와 권한을 넘긴다
                window.__dimOuterOk = pwd;
                window.__dimRole = scope.role;
                if (input && typeof window.checkPassword === 'function') { input.value = pwd; window.checkPassword(); }
            };
            if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', unlock); else unlock();
        } catch (ex) {
            btn.disabled = false; btn.textContent = '열기';
            err.textContent = (scope.group === '관리자' ? '관리자' : scope.label) + ' 비밀번호가 올바르지 않습니다.';
        }
    });
})();
</script>
</body>
</html>"""
