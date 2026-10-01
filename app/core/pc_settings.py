"""
이 PC에만 저장되는 설정을 다른 PC로 옮기기 (맥 <-> 윈도우).

리포트를 만드는 PC마다 따로 저장되는 값이 여럿이다: 카카오 키 두 개, 관리자 비밀번호, 지사별
비밀번호, 방문등록 연결 코드, 배포 계정, 그리고 주소 -> 좌표 캐시. 하나라도 빠진 PC에서 배포하면
지도가 안 나오거나, 지사별 비밀번호가 새로 만들어져 이미 나눠 준 비밀번호가 무효가 된다.

export_settings()는 이 값들을 파일 하나로 묶는다. 관리자 권한에 해당하는 값이 들어 있으므로
암호(옮길 때 정하는 값)로 암호화한다 -- 파일만으로는 내용을 볼 수 없다.
"""

import base64
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

APP_DIR = os.path.expanduser("~/.dataintelligence_pro")
SETTINGS_PATH = os.path.join(APP_DIR, "report_settings.json")
GEOCODE_CACHE_PATH = os.path.join(APP_DIR, "geocode_cache.json")
ITERATIONS = 310_000
FORMAT = "dataintel-settings"

# 화면에 보여 줄 이름 (어떤 값이 옮겨졌는지 알리기 위해)
LABELS = {
    'kakao_rest_key': '카카오 REST 키', 'kakao_js_key': '카카오 JavaScript 키', 'admin_password': '관리자 비밀번호',
    'core_scope_passwords': '지사별 비밀번호', 'visit_sync_code': '방문등록 연결 코드', 'github_owner': '배포 계정·조직',
}


def _read_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            value = json.load(f)
        return value if isinstance(value, type(default)) else default
    except (FileNotFoundError, ValueError, OSError):
        return default


def _key(passphrase, salt):
    return PBKDF2HMAC(algorithm=SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(passphrase.encode('utf-8'))


def export_settings(path, passphrase):
    """이 PC의 설정 + 좌표 캐시를 암호화해 path에 쓴다 -> 담긴 항목 이름 목록."""
    if not passphrase:
        raise ValueError("옮길 때 쓸 암호를 입력하세요.")
    settings = {k: v for k, v in _read_json(SETTINGS_PATH, {}).items() if v}
    if not settings:
        raise ValueError("이 PC에 저장된 설정이 없습니다.")
    cache = {k: v for k, v in _read_json(GEOCODE_CACHE_PATH, {}).items() if v}
    body = json.dumps({"settings": settings, "geocode_cache": cache}, ensure_ascii=False).encode('utf-8')
    salt, iv = os.urandom(16), os.urandom(12)
    out = {"format": FORMAT, "v": 1, "iter": ITERATIONS,
           "salt": base64.b64encode(salt).decode('ascii'), "iv": base64.b64encode(iv).decode('ascii'),
           "data": base64.b64encode(AESGCM(_key(passphrase, salt)).encrypt(iv, body, None)).decode('ascii')}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f)
    return [LABELS.get(k, k) for k in settings] + ([f"주소 좌표 {len(cache):,}건"] if cache else [])


def import_settings(path, passphrase):
    """export_settings()로 만든 파일을 이 PC에 적용한다 -> 적용된 항목 이름 목록.
    파일에 있는 설정은 덮어쓰고, 좌표 캐시는 이 PC에 없는 주소만 더한다."""
    try:
        with open(path, encoding='utf-8') as f:
            packed = json.load(f)
    except (ValueError, OSError):
        raise ValueError("설정 파일을 읽을 수 없습니다.")
    if not isinstance(packed, dict) or packed.get("format") != FORMAT:
        raise ValueError("Data Intel PRO 설정 파일이 아닙니다.")
    try:
        salt, iv, data = (base64.b64decode(packed[k]) for k in ("salt", "iv", "data"))
        body = AESGCM(PBKDF2HMAC(algorithm=SHA256(), length=32, salt=salt, iterations=int(packed["iter"]))
                      .derive((passphrase or '').encode('utf-8'))).decrypt(iv, data, None)
    except InvalidTag:
        raise ValueError("암호가 맞지 않습니다.")
    except (KeyError, ValueError, TypeError):
        raise ValueError("설정 파일이 손상되었습니다.")
    content = json.loads(body.decode('utf-8'))
    incoming = {k: v for k, v in (content.get("settings") or {}).items() if v}

    os.makedirs(APP_DIR, exist_ok=True)
    settings = _read_json(SETTINGS_PATH, {})
    settings.update(incoming)
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

    cache = _read_json(GEOCODE_CACHE_PATH, {})
    added = 0
    for addr, point in (content.get("geocode_cache") or {}).items():
        if point and not cache.get(addr):
            cache[addr] = point
            added += 1
    if added:
        with open(GEOCODE_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
    return [LABELS.get(k, k) for k in incoming] + ([f"주소 좌표 {added:,}건 추가"] if added else [])
