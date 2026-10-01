import json
import os

import pytest

import deploy_report
from core import pc_settings, secure_report, visit_sync

SYNC = {'url': 'https://example.supabase.co', 'key': 'sb_publishable_x', 'workspace': 'kbgw-core-test',
        'user_token': 'user-token-value', 'admin_token': 'admin-token-value'}


def test_connection_code_round_trip_and_rejects_bad_codes():
    code = visit_sync.encode_code(SYNC)
    assert code.startswith('KBGW1.') and visit_sync.decode_code(code) == SYNC
    for bad in ['', 'nope', 'KBGW1.@@@', code[:-6]]:
        with pytest.raises(ValueError):
            visit_sync.decode_code(bad)
    visit_sync.save_visit_code(code)
    assert visit_sync.load_visit_sync() == SYNC
    with pytest.raises(ValueError):
        visit_sync.save_visit_code('not-a-code')
    assert visit_sync.load_visit_sync() == SYNC      # 잘못된 코드는 저장된 값을 건드리지 않는다


def test_admin_token_in_the_report_needs_the_admin_password():
    import base64
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.hashes import SHA256
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    cfg = visit_sync.report_config(SYNC, 'admin-pw')
    assert cfg['userToken'] == 'user-token-value' and 'admin-token-value' not in json.dumps(cfg)
    enc = cfg['adminEnc']
    key = lambda pw: PBKDF2HMAC(algorithm=SHA256(), length=32, salt=base64.b64decode(enc['salt']), iterations=enc['iter']).derive(pw.encode())
    assert AESGCM(key('admin-pw')).decrypt(base64.b64decode(enc['iv']), base64.b64decode(enc['data']), None) == b'admin-token-value'
    with pytest.raises(InvalidTag):
        AESGCM(key('user-pw')).decrypt(base64.b64decode(enc['iv']), base64.b64decode(enc['data']), None)
    assert 'adminEnc' not in visit_sync.report_config(SYNC, None)


def test_settings_move_to_another_pc(tmp_path, monkeypatch):
    secure_report.save_admin_password('admin-pw')
    secure_report.save_scope_passwords({'중앙': 'pw-a'})
    os.makedirs(os.path.dirname(pc_settings.GEOCODE_CACHE_PATH), exist_ok=True)
    with open(pc_settings.GEOCODE_CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump({'서울 A': [37.5, 127.0, '정확'], '못 찾은 주소': None}, f, ensure_ascii=False)

    out = str(tmp_path / 'move.dipset')
    items = pc_settings.export_settings(out, 'passphrase')
    raw = open(out, encoding='utf-8').read()
    assert 'admin-pw' not in raw and 'pw-a' not in raw and '서울' not in raw
    assert '관리자 비밀번호' in items and '주소 좌표 1건' in items

    # 다른 PC: 빈 설정 폴더
    other = tmp_path / 'other'
    monkeypatch.setattr(pc_settings, 'APP_DIR', str(other))
    monkeypatch.setattr(pc_settings, 'SETTINGS_PATH', str(other / 'report_settings.json'))
    monkeypatch.setattr(pc_settings, 'GEOCODE_CACHE_PATH', str(other / 'geocode_cache.json'))
    with pytest.raises(ValueError, match='암호가 맞지 않습니다'):
        pc_settings.import_settings(out, 'wrong')
    pc_settings.import_settings(out, 'passphrase')
    moved = json.load(open(other / 'report_settings.json', encoding='utf-8'))
    assert moved['admin_password'] == 'admin-pw' and moved['core_scope_passwords'] == {'중앙': 'pw-a'}
    assert json.load(open(other / 'geocode_cache.json', encoding='utf-8')) == {'서울 A': [37.5, 127.0, '정확']}


def test_deploy_refuses_an_unencrypted_report(tmp_path):
    plain = tmp_path / 'plain.html'
    plain.write_text('<html>고객 데이터</html>', encoding='utf-8')
    with pytest.raises(deploy_report.DeployError, match='암호화되지 않은'):
        deploy_report.deploy(str(plain))


def test_link_folder_is_reused_unless_a_new_link_is_asked_for(monkeypatch):
    monkeypatch.setattr(deploy_report, '_remote_slug', lambda full: 'abcdefgh23')
    quiet = lambda *a: None
    assert deploy_report._pick_slug('o/r', True, False, quiet) == 'abcdefgh23'
    fresh = deploy_report._pick_slug('o/r', True, True, quiet)
    assert fresh != 'abcdefgh23' and deploy_report.SLUG_RE.match(fresh)
    assert deploy_report._pick_slug('o/r', False, False, quiet) is None
    monkeypatch.setattr(deploy_report, '_remote_slug', lambda full: None)
    assert deploy_report.SLUG_RE.match(deploy_report._pick_slug('o/r', True, False, quiet))


def test_report_goes_under_the_folder_and_the_root_is_empty(tmp_path, monkeypatch):
    report = tmp_path / 'r.html'
    report.write_text('<html id="encPayload">암호문</html>', encoding='utf-8')
    seen = {}

    def fake_run(cmd, cwd=None, check=True):
        if cmd[:2] == ['git', 'push']:
            seen['files'] = sorted(os.path.relpath(os.path.join(d, f), cwd) for d, _, fs in os.walk(cwd) for f in fs if '.git' + os.sep not in os.path.join(d, f))
            seen['root'] = open(os.path.join(cwd, 'index.html'), encoding='utf-8').read()
        return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
    monkeypatch.setattr(deploy_report, '_run', fake_run)
    deploy_report._push_single_commit(str(report), 'o/r', lambda *a: None, 'abcdefgh23')
    assert os.path.join('abcdefgh23', 'index.html') in seen['files'] and 'index.html' in seen['files']
    assert '암호문' not in seen['root'] and 'noindex' in seen['root']
