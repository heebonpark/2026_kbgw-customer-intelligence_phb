"""
9. 코어고객 활동관리 -- an independent section (never merged onto 총괄DB):
관리주체 / 지사 / 활동내역(방문·불만·요구·해지징후, 9-1 VOC매칭) + a map of
설치주소.

build_core_payload() turns the uploaded sheet(s) into plain rows the report's
JS renders (report.py renderCoreSection). The web version builds the same rows
in the browser (web_app.py buildCorePayload) -- keep the field names in sync.

Map coordinates, in order: a coordinate column in the file itself -> this
PC's geocode cache -> Kakao 주소검색 API (REST key saved on this PC, never in
the repo or the report). Only the address string is sent to Kakao -- not the
customer name or contract number -- and each address only once (cached).
"""

import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import pandas as pd

from .handlers import _first_matching_col, BRANCH_ORDER

APP_DIR = os.path.expanduser("~/.dataintelligence_pro")
GEOCODE_CACHE_PATH = os.path.join(APP_DIR, "geocode_cache.json")
SETTINGS_PATH = os.path.join(APP_DIR, "report_settings.json")

# 표준 필드 -> 파일에서 찾을 열 이름 (정확히 같은 이름 우선, 없으면 이름이 이것으로 시작하는 열)
CORE_FIELDS = {
    '계약번호': ['계약번호'],
    '관리고객명': ['관리고객 명', '관리고객명', '고객명', '상호'],
    '관리주체': ['관리주체'],
    '본부': ['본부', '관리본부'],
    '지사': ['지사', '관리지사'],
    '설치주소': ['설치주소', '주소'],
    '영업구역': ['영업구역', '영업구역정보'],
    '영업구역담당': ['영업구역담당'],
    '관리고객담당자': ['관리고객담당자'],
    '영업자': ['영업자'],
    '시설수': ['시설수'],
    '월정료': ['월정료'],
    '재계약대상시설수': ['재계약대상시설수'],
    '재계약대상월정료': ['재계약대상월정료'],
    '계약종료일': ['계약종료일'],
    '방문일자': ['방문일자'],
    '3Q방문일자': ['1회 방문일자'],
    '2회방문일자': ['2회 방문일자'],
    '방문대상': ['방문대상'],
    '방문자': ['방문자'],
    '해지징후': ['해지징후'],
    '불만요구': ['불만사항/요구사항/추가영업기회'],
    '요약정리': ['요약정리'],
    '약정여부': ['약정여부'],
    '해지건수': ['해지건수'],
    '해지월정료': ['해지월정료'],
    # 분기 방문(1회·2회)마다 따로 적는 해지징후 / 메모 열 -- 엑셀에서 같은 제목이 반복돼 두 번째는 '.1'
    '3Q징후': ['해지징후 및 불만 여부'],
    '2회징후': ['해지징후 및 불만 여부.1'],
    '3Q불만': ['불만사항/요구사항/추가영업기회 (구체적으로 작성)'],
    '2회불만': ['불만사항/요구사항/추가영업기회 (구체적으로 작성).1'],
    '약정시설수': ['약정시설수'],
    '약정월정료': ['약정월정료'],
    '해지일자': ['해지일자'],
    '수동재계약': ['수동재계약'],
    '만기비중': ['만기도래 비중 금액', '만기도래 비중'],
    '업셀링': ['업셀링('],
    '업셀링금액': ['업셀링 금액'],
}
EXACT_ONLY_FIELDS = {'3Q징후', '2회징후', '3Q불만', '2회불만'}  # 비슷한 이름의 다른 열로 번지지 않게
COORD_COL_CANDIDATES = ['위치좌표(위도,경도)', '위치좌표', '좌표']
LAT_CANDIDATES, LNG_CANDIDATES = ['위도', 'lat', 'LAT'], ['경도', 'lng', 'LNG', 'lon']


def _find_col(df, names, exact=False):
    col = _first_matching_col(df, names)
    if col or exact:
        return col
    return next((c for c in df.columns for n in names if str(c).startswith(n)), None)


def _clean(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, str):
        v = v.replace('\xa0', ' ').strip()
        return v or None
    return v


def _date(v):
    """엑셀 날짜(46100 같은 일련번호 / Timestamp / 문자열) -> 'YYYY-MM-DD'. '9999-99-99'는 없음."""
    v = _clean(v)
    if v is None:
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime('%Y-%m-%d')
    if isinstance(v, (int, float)) and 20000 < float(v) < 80000:
        return (datetime(1899, 12, 30) + timedelta(days=float(v))).strftime('%Y-%m-%d')
    s = str(v)
    if s.startswith('9999'):
        return None
    m = re.match(r'^(\d{4})[-./](\d{1,2})[-./](\d{1,2})', s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    try:
        f = float(s)
        if 20000 < f < 80000:
            return (datetime(1899, 12, 30) + timedelta(days=f)).strftime('%Y-%m-%d')
    except ValueError:
        pass
    return s


def _num(v):
    v = _clean(v)
    if v is None:
        return None
    try:
        return float(str(v).replace(',', ''))
    except ValueError:
        return None


def _contract(v):
    v = _clean(v)
    if v is None:
        return None
    s = str(v)
    return s[:-2] if s.endswith('.0') else s


def _file_coord(row, cols):
    coord_col, lat_col, lng_col = cols
    if coord_col:
        v = _clean(row.get(coord_col))
        if isinstance(v, str) and ',' in v:
            try:
                lat, lng = (float(x) for x in v.split(',')[:2])
                return lat, lng
            except ValueError:
                pass
    if lat_col and lng_col:
        lat, lng = _num(row.get(lat_col)), _num(row.get(lng_col))
        if lat and lng:
            return lat, lng
    return None


# ---------------------------------------------------------------- settings / cache (this PC only)

def load_kakao_key():
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            return json.load(f).get('kakao_rest_key') or None
    except (FileNotFoundError, ValueError, OSError):
        return None


def save_kakao_key(key):
    os.makedirs(APP_DIR, exist_ok=True)
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            settings = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        settings = {}
    settings['kakao_rest_key'] = key or None
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def _load_cache():
    try:
        with open(GEOCODE_CACHE_PATH, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save_cache(cache):
    os.makedirs(APP_DIR, exist_ok=True)
    with open(GEOCODE_CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)


def _kakao_get(endpoint, query, key):
    url = f"https://dapi.kakao.com/v2/local/search/{endpoint}.json?" + urllib.parse.urlencode({'query': query, 'size': 1})
    req = urllib.request.Request(url, headers={'Authorization': f'KakaoAK {key}'})
    with urllib.request.urlopen(req, timeout=8) as resp:
        docs = json.loads(resp.read().decode('utf-8')).get('documents') or []
    if docs:
        return float(docs[0]['y']), float(docs[0]['x'])
    return None


_REGION_STARTS = ('서울', '경기', '강원', '인천', '충북', '충남', '충청', '전북', '전남', '전라', '경북', '경남', '경상',
                  '부산', '대구', '광주', '대전', '울산', '세종', '제주')
_LOT_RE = re.compile(r'^(산)?\d+(-\d+)?$')
_AREA_RE = re.compile(r'(동|리|가|읍|면)$')


def _clean_address(address):
    """'280-9번지' -> '280-9', 괄호 메모('구)터미널자리') 제거, 주소가 두 번 들어간 경우
    ('서울 마포구 상암동 서울특별시 마포구 상암동 713') 뒤쪽 온전한 주소만."""
    tokens = [t.replace('번지', '') for t in address.split()]
    tokens = [t for t in tokens if t and '(' not in t and ')' not in t]
    starts = [i for i, t in enumerate(tokens) if i > 0 and t.startswith(_REGION_STARTS)]
    if starts:
        tokens = tokens[starts[-1]:]
    return tokens


def _address_candidates(address):
    tokens = _clean_address(address)
    cands = []
    lot = next((i for i, t in enumerate(tokens) if _LOT_RE.match(t)), None)
    if lot is not None:
        head = tokens[:lot + 1]
        cands.append(' '.join(head))
        # 동/리가 두 개 이상이면(예: 당동리 문산리 7-6) 하나씩만 남겨서도
        areas = [i for i, t in enumerate(head[:-1]) if _AREA_RE.search(t) and i >= 2]
        if len(areas) > 1:
            for keep in areas:
                cands.append(' '.join(t for i, t in enumerate(head) if i not in areas or i == keep))
    cands += [' '.join(tokens[:n]) for n in range(len(tokens), max(2, len(tokens) - 3), -1)]
    seen = set()
    return [c for c in cands if c and not (c in seen or seen.add(c))]


def geocode_kakao(address, key):
    """주소 -> (위도, 경도, 정밀도). 정리한 주소 후보들로 주소검색 -> 키워드검색 -> 그래도 없으면
    동·리 단위 위치('동 단위'). 못 찾으면 None. 키 오류는 예외로 올린다."""
    for q in _address_candidates(address):
        hit = _kakao_get('address', q, key)
        if hit:
            return hit[0], hit[1], '정확'
    hit = _kakao_get('keyword', ' '.join(_clean_address(address)), key)
    if hit:
        return hit[0], hit[1], '정확'
    tokens = _clean_address(address)
    area = max((i for i, t in enumerate(tokens) if _AREA_RE.search(t) and i >= 1), default=None)
    if area is not None:
        hit = _kakao_get('address', ' '.join(tokens[:area + 1]), key)
        if hit:
            return hit[0], hit[1], '동 단위'
    return None


# ---------------------------------------------------------------- payload

def build_core_payload(core_df, voc_df=None, kakao_key=None, log=print):
    """-> {"rows": [...], "coord_stats": {...}, "voc_matched": n} 또는 None.
    rows 필드 이름은 report.py renderCoreSection / web_app.py buildCorePayload와 같다."""
    if core_df is None or core_df.empty:
        return None
    cols = {k: _find_col(core_df, v, exact=k in EXACT_ONLY_FIELDS) for k, v in CORE_FIELDS.items()}
    # '1회 방문일자_3Q 내' -> '3Q' (화면 문구용). 분기 방문 열이 없으면 방문일자 하나로만 본다.
    quarter = re.search(r'(\d)\s*Q', str(cols['3Q방문일자'] or ''))
    period = f"{quarter.group(1)}Q" if quarter else ('분기' if cols['3Q방문일자'] else None)
    if not cols['관리고객명'] and not cols['계약번호']:
        return None
    coord_cols = (_find_col(core_df, COORD_COL_CANDIDATES), _find_col(core_df, LAT_CANDIDATES), _find_col(core_df, LNG_CANDIDATES))

    vocs = {}
    if voc_df is not None and not voc_df.empty and '계약번호' in voc_df.columns:
        last = None
        for _, v in voc_df.iterrows():
            # 엑셀 병합셀로 내보내면 같은 고객의 두 번째 VOC부터는 계약번호가 비어 있다 -> 바로 위 고객
            contract = _contract(v.get('계약번호')) or last
            last = contract
            kind = _clean(v.get('VOC유형')) or _clean(v.get('VOC유형대'))
            if not kind and not _clean(v.get('상태')):
                continue  # VOC 없는 고객 줄
            vocs.setdefault(contract, []).append({
                '상태': _clean(v.get('상태')), '유형': kind, '처리내용': _clean(v.get('처리내용')),
                '접수일': _date(v.get('접수일시')), '처리자': _clean(v.get('처리자')),
            })

    rows = []
    for _, r in core_df.iterrows():
        g = lambda k: _clean(r.get(cols[k])) if cols[k] else None
        visit3q = _date(g('3Q방문일자'))
        visit = _date(g('방문일자'))
        visit2 = _date(g('2회방문일자'))
        yn = lambda k: (lambda v: v if v in ('Y', 'N') else None)(str(g(k) or '').strip().upper())
        note = lambda k: (lambda v: None if v is None else str(v))(g(k))
        # 방문 회차별 기록 (이전 -> 분기 1회 -> 분기 2회)
        history = [h for h in (
            {'회차': '이전' if period else '방문', '일자': visit, '징후': yn('해지징후'), '내용': note('불만요구'),
             '대상': g('방문대상'), '방문자': g('방문자')},
            {'회차': f'{period} 1회', '일자': visit3q, '징후': yn('3Q징후'), '내용': note('3Q불만')},
            {'회차': f'{period} 2회', '일자': visit2, '징후': yn('2회징후'), '내용': note('2회불만')},
        ) if h['일자'] or h['내용'] or h['징후']]
        signs = [h['징후'] for h in history]
        sign = 'Y' if 'Y' in signs else ('N' if 'N' in signs else '')
        notes = [h['내용'] for h in history if h['내용']]
        in_period = (visit3q or visit2) if period else visit
        contract = _contract(g('계약번호'))
        row = {
            '계약번호': contract, '관리고객명': g('관리고객명'), '관리주체': g('관리주체') or '미지정',
            '본부': g('본부'), '지사': g('지사') or '미지정', '설치주소': g('설치주소'),
            '영업구역': g('영업구역'), '영업구역담당': g('영업구역담당'), '관리고객담당자': g('관리고객담당자'),
            '영업자': g('영업자'), '시설수': _num(g('시설수')), '월정료': _num(g('월정료')),
            '재계약대상시설수': _num(g('재계약대상시설수')), '재계약대상월정료': _num(g('재계약대상월정료')),
            '계약종료일': _date(g('계약종료일')), '방문일자': visit, '3Q방문일자': visit3q,
            '2회방문일자': visit2, '방문대상': g('방문대상'), '방문자': g('방문자'),
            '해지징후': sign or None,
            '불만요구': notes[-1] if notes else None,  # 가장 최근 회차의 메모
            '방문이력': history, '최근방문': max([d for d in (visit, visit3q, visit2) if d], default=None),
            '요약정리': g('요약정리'), '약정여부': g('약정여부'),
            '약정시설수': _num(g('약정시설수')), '약정월정료': _num(g('약정월정료')),
            '해지건수': _num(g('해지건수')), '해지월정료': _num(g('해지월정료')), '해지일자': _date(g('해지일자')),
            '수동재계약': _date(g('수동재계약')), '만기비중': _num(g('만기비중')),
            '업셀링': note('업셀링'), '업셀링금액': _num(g('업셀링금액')),
            'VOC': vocs.get(contract, []),
            # 방문완료 = 이번 분기 안에 방문 (분기 열이 없는 파일은 방문일자 기준)
            '활동상태': '해지징후' if sign == 'Y' else ('방문완료' if in_period else '미방문'),
            'lat': None, 'lng': None, '좌표출처': None,
        }
        hit = _file_coord(r, coord_cols)
        if hit:
            row['lat'], row['lng'], row['좌표출처'] = hit[0], hit[1], '파일'
        rows.append(row)

    # 좌표: 캐시 -> 카카오 (주소 하나당 한 번)
    cache = _load_cache()
    need = sorted({row['설치주소'] for row in rows if row['lat'] is None and row['설치주소']})
    fetched, failed, error = 0, 0, None
    for addr in need:
        if cache.get(addr):  # 못 찾았던 주소(None)는 다음에 다시 시도 -- 주소 정리 규칙이 보강될 수 있음
            continue
        if not kakao_key or error:
            continue
        try:
            hit = geocode_kakao(addr, kakao_key)
        except Exception as e:  # 키 오류·네트워크 -- 한 번만 알리고 나머지는 건너뜀
            error = str(e)
            log(f"카카오 주소검색 실패: {error} -- 지도 좌표 변환을 건너뜁니다.")
            continue
        cache[addr] = [hit[0], hit[1], hit[2]] if hit else None
        fetched += 1
        if not hit:
            failed += 1
    if fetched:
        _save_cache(cache)
        log(f"카카오 주소검색: {fetched}건 변환 (못 찾음 {failed}건), 이 PC에 저장")
    for row in rows:
        hit = cache.get(row['설치주소']) if row['lat'] is None and row['설치주소'] else None
        if hit:
            row['lat'], row['lng'] = hit[0], hit[1]
            row['좌표출처'] = '카카오(동 단위)' if len(hit) > 2 and hit[2] == '동 단위' else '카카오'

    branch_rank = {b: i for i, b in enumerate(BRANCH_ORDER)}
    rows.sort(key=lambda x: (branch_rank.get(x['지사'], len(BRANCH_ORDER)), x['지사'], str(x['관리고객명'] or '')))
    stats = {'파일': sum(1 for x in rows if x['좌표출처'] == '파일'),
             '카카오': sum(1 for x in rows if x['좌표출처'] == '카카오'),
             '동단위': sum(1 for x in rows if x['좌표출처'] == '카카오(동 단위)'),
             '없음': sum(1 for x in rows if x['lat'] is None)}
    return {"rows": rows, "coord_stats": stats, "voc_matched": sum(1 for x in rows if x['VOC']), "period": period,
            "kakao_key_set": bool(kakao_key), "kakao_error": error}
