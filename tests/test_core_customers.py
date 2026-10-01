import pandas as pd
import pytest

from core import core_customers as cc


def core_df():
    return pd.DataFrame([
        # 분기 방문 있음, 분기 방문 때 해지징후 Y
        {'계약번호': 1001.0, '관리고객 명': '가나상사', '관리주체': '지사장', '지사': '강북', '설치주소': '서울 강북구 수유동 1',
         '월정료': 100000, '방문일자': '2026-02-01', '해지징후': 'N', '불만사항/요구사항/추가영업기회': '상반기 메모',
         '1회 방문일자_3Q 내': 46220, '해지징후 및 불만 여부': 'Y', '불만사항/요구사항/추가영업기회 (구체적으로 작성)': '3분기 메모',
         '2회 방문일자': None, '해지징후 및 불만 여부.1': '8월'},
        # 상반기에만 방문 -> 분기 기준으로는 미방문
        {'계약번호': 1002.0, '관리고객 명': '다라건설', '관리주체': '본부장', '지사': '중앙', '설치주소': None,
         '월정료': 200000, '방문일자': '2026-03-10', '해지징후': 'N', '불만사항/요구사항/추가영업기회': None,
         '1회 방문일자_3Q 내': None, '해지징후 및 불만 여부': None, '불만사항/요구사항/추가영업기회 (구체적으로 작성)': None,
         '2회 방문일자': None, '해지징후 및 불만 여부.1': None},
        # 분기 방문만
        {'계약번호': 1003.0, '관리고객 명': '마바물산', '관리주체': '지사장', '지사': '중앙', '설치주소': None,
         '월정료': 300000, '방문일자': None, '해지징후': None, '불만사항/요구사항/추가영업기회': None,
         '1회 방문일자_3Q 내': '2026-08-15', '해지징후 및 불만 여부': 'N', '불만사항/요구사항/추가영업기회 (구체적으로 작성)': None,
         '2회 방문일자': None, '해지징후 및 불만 여부.1': None},
    ])


def test_payload_reads_quarter_visits_and_any_sign():
    p = cc.build_core_payload(core_df(), kakao_key=None, log=lambda *a: None)
    rows = {r['관리고객명']: r for r in p['rows']}
    assert p['period'] == '3Q'

    a = rows['가나상사']
    assert a['계약번호'] == '1001'                    # 1001.0 -> '1001'
    assert a['3Q방문일자'] == '2026-07-17'            # 엑셀 날짜 일련번호
    assert a['해지징후'] == 'Y'                       # 첫 열은 N이지만 분기 방문 열이 Y
    assert a['활동상태'] == '해지징후'
    assert a['불만요구'] == '3분기 메모'              # 가장 최근 회차의 메모
    assert [h['회차'] for h in a['방문이력']] == ['이전', '3Q 1회']
    assert a['방문이력'][1]['징후'] == 'Y'            # '8월' 같은 값은 징후로 읽지 않는다

    assert rows['다라건설']['활동상태'] == '미방문'    # 상반기 방문만으로는 분기 방문완료가 아니다
    assert rows['마바물산']['활동상태'] == '방문완료'


def test_rows_are_in_branch_order():
    p = cc.build_core_payload(core_df(), kakao_key=None, log=lambda *a: None)
    assert [r['지사'] for r in p['rows']] == ['중앙', '중앙', '강북']


def test_voc_rows_with_blank_contract_belong_to_the_row_above():
    voc = pd.DataFrame([
        {'계약번호': 1001.0, '상태': '처리완료', 'VOC유형': 'A'},
        {'계약번호': None, '상태': '접수', 'VOC유형': 'B'},      # 엑셀 병합셀
        {'계약번호': 1003.0, '상태': None, 'VOC유형': None},      # VOC 없는 고객 줄
    ])
    p = cc.build_core_payload(core_df(), voc, kakao_key=None, log=lambda *a: None)
    rows = {r['관리고객명']: r for r in p['rows']}
    assert [v['유형'] for v in rows['가나상사']['VOC']] == ['A', 'B']
    assert rows['마바물산']['VOC'] == []
    assert p['voc_matched'] == 1


def test_no_key_means_no_coordinates_and_no_network():
    p = cc.build_core_payload(core_df(), kakao_key=None, log=lambda *a: None)
    assert all(r['lat'] is None for r in p['rows'])
    assert p['coord_stats']['없음'] == 3 and p['kakao_key_set'] is False


def test_coordinates_come_from_the_cache(monkeypatch):
    monkeypatch.setattr(cc, '_load_cache', lambda: {'서울 강북구 수유동 1': [37.64, 127.02, '동 단위']})
    p = cc.build_core_payload(core_df(), kakao_key=None, log=lambda *a: None)
    row = next(r for r in p['rows'] if r['관리고객명'] == '가나상사')
    assert (row['lat'], row['lng'], row['좌표출처']) == (37.64, 127.02, '카카오(동 단위)')
    assert p['coord_stats']['동단위'] == 1


@pytest.mark.parametrize('address, first', [
    ('서울 성동구 성수동1가 280-9번지 생각공장데시앙플렉스 20층', '서울 성동구 성수동1가 280-9'),
    ('서울 마포구 상암동 서울특별시 마포구 상암동 713번지 중앙건설현장 상암관사', '서울특별시 마포구 상암동 713'),
    ('경기 남양주시 별내면 덕송리 19-4 자동크린넷 제2집하장', '경기 남양주시 별내면 덕송리 19-4'),
])
def test_address_cleanup_cuts_at_the_lot_number(address, first):
    assert cc._address_candidates(address)[0] == first


def test_two_area_names_are_tried_one_at_a_time():
    cands = cc._address_candidates('경기 파주시 문산읍 당동리 문산리 7-6 구)터미널자리')
    assert '경기 파주시 문산리 7-6' in cands and '경기 파주시 당동리 7-6' in cands
    assert not any('터미널' in c for c in cands)


def test_scope_names_follow_branch_order_then_hq():
    assert cc.core_scope_names(core_df()) == ['중앙', '강북', '본부장']
    assert cc.core_scope_names(None) == []
