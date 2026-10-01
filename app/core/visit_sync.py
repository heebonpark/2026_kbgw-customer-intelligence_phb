"""
방문 조치결과 등록 / 관리자 실시간 현황 -- 코어고객 리포트가 쓰는 공용 저장소(Supabase) 연결.

리포트는 정적 HTML이라 여러 사람이 등록한 결과를 모으려면 인터넷의 저장소가 필요하다.
표(kbgw_visit_results)는 RLS로 전부 막혀 있고, 리포트는 함수(kbgw_visit_submit / _list /
_delete)만 부른다. 함수는 토큰이 맞을 때만 동작한다:

- user 토큰: 등록, 그리고 자기 이름으로 등록한 것만 조회.
- admin 토큰: 전체 조회·삭제 (관리자 실시간 현황).

연결 정보(주소·공개 키·작업공간·두 토큰)는 '연결 코드' 한 줄로 묶어 이 PC에만 저장한다
(~/.dataintelligence_pro/report_settings.json). 다른 PC에서 같은 데이터를 쓰려면 GUI의
'방문등록 연결 코드' 칸에 같은 코드를 넣는다. 저장소(공개 repo)에는 넣지 않는다.

리포트 안에서는: user 토큰은 그대로(리포트 자체가 암호화되어 있다), admin 토큰은 관리자
비밀번호로 한 번 더 암호화해 싣는다 -- 일반 비밀번호로 연 사람은 관리자 화면을 열 수 없다.
"""

import base64
import json
import os

SETTINGS_PATH = os.path.join(os.path.expanduser("~/.dataintelligence_pro"), "report_settings.json")
CODE_PREFIX = "KBGW1."
FIELDS = ('url', 'key', 'workspace', 'user_token', 'admin_token')
ADMIN_KDF_ITERATIONS = 150_000


def _b64(data):
    return base64.b64encode(data).decode('ascii')


def encode_code(sync):
    """연결 정보(dict) -> 다른 PC에 붙여 넣을 한 줄."""
    raw = json.dumps([sync[k] for k in FIELDS], separators=(',', ':')).encode('utf-8')
    return CODE_PREFIX + base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def decode_code(code):
    """연결 코드 -> dict. 형식이 틀리면 ValueError."""
    code = (code or '').strip()
    if not code.startswith(CODE_PREFIX):
        raise ValueError("방문등록 연결 코드는 'KBGW1.'로 시작해야 합니다.")
    body = code[len(CODE_PREFIX):]
    try:
        values = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)).decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("방문등록 연결 코드를 읽을 수 없습니다 (복사가 잘렸는지 확인하세요).")
    if not isinstance(values, list) or len(values) != len(FIELDS) or not all(isinstance(v, str) and v for v in values):
        raise ValueError("방문등록 연결 코드의 내용이 올바르지 않습니다.")
    sync = dict(zip(FIELDS, values))
    if not sync['url'].startswith('https://'):
        raise ValueError("방문등록 연결 코드의 주소가 올바르지 않습니다.")
    return sync


def load_visit_sync():
    """이 PC에 저장된 연결 정보(dict) 또는 None."""
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            code = json.load(f).get('visit_sync_code')
        return decode_code(code) if code else None
    except (FileNotFoundError, ValueError, OSError):
        return None


def load_visit_code():
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            return json.load(f).get('visit_sync_code') or None
    except (FileNotFoundError, ValueError, OSError):
        return None


def save_visit_code(code):
    """연결 코드를 저장한다 (빈 값이면 지운다). 형식이 틀리면 ValueError."""
    code = (code or '').strip() or None
    if code:
        decode_code(code)
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            settings = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        settings = {}
    settings['visit_sync_code'] = code
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def report_config(sync, admin_password=None):
    """리포트에 싣는 설정(dict): user 토큰은 그대로, admin 토큰은 관리자 비밀번호로 암호화
    (PBKDF2-SHA256 -> AES-256-GCM; 브라우저는 WebCrypto로 푼다). 관리자 비밀번호가 없으면
    관리자 화면 없이 등록만 된다."""
    config = {"url": sync['url'].rstrip('/'), "key": sync['key'], "ws": sync['workspace'], "userToken": sync['user_token']}
    if admin_password:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.hashes import SHA256
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        salt, iv = os.urandom(16), os.urandom(12)
        key = PBKDF2HMAC(algorithm=SHA256(), length=32, salt=salt, iterations=ADMIN_KDF_ITERATIONS).derive(admin_password.encode('utf-8'))
        config["adminEnc"] = {"salt": _b64(salt), "iv": _b64(iv), "iter": ADMIN_KDF_ITERATIONS,
                              "data": _b64(AESGCM(key).encrypt(iv, sync['admin_token'].encode('utf-8'), None))}
    return config
