import json

import pandas as pd
import pytest

from core import secure_report as sr
from core.report import generate_core_report, generate_html_report
from helpers import core_rows, open_report, open_scope, payload_of
from test_core_customers import core_df

quiet = lambda *a: None


def test_either_password_opens_and_nothing_is_readable_without_one():
    page = sr.encrypt_report('<p>비밀 고객명</p>', ['user-pw', 'admin-pw'], expiry_date='2026-12-31')
    assert '비밀 고객명' not in page and 'user-pw' not in page
    assert open_report(page, 'user-pw') == '<p>비밀 고객명</p>'
    assert open_report(page, 'admin-pw') == '<p>비밀 고객명</p>'
    assert open_report(page, 'wrong') is None


def test_scope_password_opens_only_its_own_slice():
    shell = f'<html>{sr.SCOPE_PLACEHOLDER}</html>'
    scopes = [
        {'id': 'a', 'group': '지사장', 'label': '중앙', 'role': 'user', 'passwords': ['pw-a'], 'data': '중앙 데이터'},
        {'id': 'b', 'group': '지사장', 'label': '강북', 'role': 'user', 'passwords': ['pw-b'], 'data': '강북 데이터'},
        {'id': 'z', 'group': '관리자', 'label': '관리자', 'role': 'admin', 'passwords': ['pw-z'], 'data': '전체 데이터'},
    ]
    page = sr.encrypt_scoped_report(shell, scopes)
    assert '데이터' not in page
    assert open_scope(page, '중앙', 'pw-a') == ('<html>중앙 데이터</html>', 'user')
    assert open_scope(page, '강북', 'pw-a') is None        # 다른 지사 비밀번호
    assert open_scope(page, '중앙', 'pw-z') is None        # 관리자 비밀번호도 지사 칸에서는 안 열린다
    assert open_scope(page, '관리자', 'pw-z') == ('<html>전체 데이터</html>', 'admin')
    assert open_scope(page, '관리자', 'pw-a') is None


def test_scope_passwords_are_generated_once_and_kept():
    first = sr.ensure_scope_passwords(['중앙', '강북'])
    again = sr.ensure_scope_passwords(['중앙', '강북', '원주'])
    assert again['중앙'] == first['중앙'] and again['강북'] == first['강북']
    assert len(set(again.values())) == 3


def test_core_report_slices_rows_by_branch_and_hq():
    pw = {'중앙': 'pw-jungang', '강북': 'pw-gangbuk', '본부장': 'pw-hq'}
    page, returned, expiry, admin, count = generate_core_report(
        core_df(), admin_password='pw-admin', scope_passwords=pw, expiry_date='2026-12-31', kakao_key='', kakao_js_key='', log=quiet)
    assert returned == pw and admin == 'pw-admin' and count == 3
    assert '가나상사' not in page and 'pw-admin' not in page

    names = lambda label, password: sorted(r['관리고객명'] for r in core_rows(open_scope(page, label, password)[0]))
    assert names('중앙', 'pw-jungang') == ['다라건설', '마바물산']
    assert names('강북', 'pw-gangbuk') == ['가나상사']
    assert names('본부장', 'pw-hq') == ['다라건설']
    assert names('관리자', 'pw-admin') == ['가나상사', '다라건설', '마바물산']
    assert open_scope(page, '강북', 'pw-jungang') is None
    # 중앙으로 연 페이지 어디에도 강북 고객이 없다 (화면에서 가린 것이 아니라 들어 있지 않다)
    assert '가나상사' not in open_scope(page, '중앙', 'pw-jungang')[0]
    # 로그인 화면 순서: 지사장(지사 순) -> 본부장 -> 관리자
    assert [s['label'] for s in payload_of(page)['scopes']] == ['중앙', '강북', '본부장', '관리자']


def test_admin_password_is_not_inside_the_opened_core_report():
    page, pw, *_ = generate_core_report(core_df(), password='user-pw', admin_password='admin-pw', kakao_key='', kakao_js_key='', log=quiet)
    inner = open_report(page, 'user-pw')
    assert inner is not None and 'admin-pw' not in inner
    assert open_report(page, 'admin-pw') is not None


def test_admin_password_is_not_inside_the_opened_main_report():
    df = pd.DataFrame({'상호': ['x', 'y'], '계약번호': [1, 2], '활동대상구분': ['SP', 'SE'], '활동유무': ['처리완료', None],
                       '지사': ['중앙', '강북'], '관리지사': ['중앙', '강북'], '관리본부': ['강북/강원'] * 2, '월정료': [10, 20]})
    page, pw, expiry, admin = generate_html_report(df, password='user-pw', admin_password='admin-pw', log=quiet)
    inner = open_report(page, 'user-pw')
    assert inner is not None and 'admin-pw' not in inner
    assert 'const CORRECT_PWD = "user-pw"' in inner
    assert open_report(page, 'admin-pw') is not None
