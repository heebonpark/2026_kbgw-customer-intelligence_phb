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
}
COORD_COL_CANDIDATES = ['위치좌표(위도,경도)', '위치좌표', '좌표']
LAT_CANDIDATES, LNG_CANDIDATES = ['위도', 'lat', 'LAT'], ['경도', 'lng', 'LNG', 'lon']


def _find_col(df, names):
    col = _first_matching_col(df, names)
    if col:
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


def geocode_kakao(address, key):
    """주소 -> (위도, 경도). 전체 주소로 못 찾으면 뒤쪽(건물명·층 등)을 한 단어씩 떼며
    다시 찾고, 그래도 없으면 키워드 검색. 못 찾으면 None. 키 오류는 예외로 올린다."""
    tokens = address.split()
    tries = [' '.join(tokens[:n]) for n in range(len(tokens), max(2, len(tokens) - 3), -1)]
    for q in tries:
        hit = _kakao_get('address', q, key)
        if hit:
            return hit
    return _kakao_get('keyword', address, key)


# ---------------------------------------------------------------- payload

def build_core_payload(core_df, voc_df=None, kakao_key=None, log=print):
    """-> {"rows": [...], "coord_stats": {...}, "voc_matched": n} 또는 None.
    rows 필드 이름은 report.py renderCoreSection / web_app.py buildCorePayload와 같다."""
    if core_df is None or core_df.empty:
        return None
    cols = {k: _find_col(core_df, v) for k, v in CORE_FIELDS.items()}
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
        sign = (str(g('해지징후') or '')).upper()
        contract = _contract(g('계약번호'))
        row = {
            '계약번호': contract, '관리고객명': g('관리고객명'), '관리주체': g('관리주체') or '미지정',
            '본부': g('본부'), '지사': g('지사') or '미지정', '설치주소': g('설치주소'),
            '영업구역': g('영업구역'), '영업구역담당': g('영업구역담당'), '관리고객담당자': g('관리고객담당자'),
            '영업자': g('영업자'), '시설수': _num(g('시설수')), '월정료': _num(g('월정료')),
            '재계약대상시설수': _num(g('재계약대상시설수')), '재계약대상월정료': _num(g('재계약대상월정료')),
            '계약종료일': _date(g('계약종료일')), '방문일자': visit, '3Q방문일자': visit3q,
            '2회방문일자': _date(g('2회방문일자')), '방문대상': g('방문대상'), '방문자': g('방문자'),
            '해지징후': 'Y' if sign == 'Y' else ('N' if sign == 'N' else None),
            '불만요구': g('불만요구'), '요약정리': g('요약정리'), '약정여부': g('약정여부'),
            '해지건수': _num(g('해지건수')), '해지월정료': _num(g('해지월정료')),
            'VOC': vocs.get(contract, []),
            '활동상태': '해지징후' if sign == 'Y' else ('방문완료' if (visit3q or visit) else '미방문'),
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
        if addr in cache:
            continue
        if not kakao_key or error:
            continue
        try:
            hit = geocode_kakao(addr, kakao_key)
        except Exception as e:  # 키 오류·네트워크 -- 한 번만 알리고 나머지는 건너뜀
            error = str(e)
            log(f"카카오 주소검색 실패: {error} -- 지도 좌표 변환을 건너뜁니다.")
            continue
        cache[addr] = list(hit) if hit else None
        fetched += 1
        if not hit:
            failed += 1
    if fetched:
        _save_cache(cache)
        log(f"카카오 주소검색: {fetched}건 변환 (못 찾음 {failed}건), 이 PC에 저장")
    for row in rows:
        if row['lat'] is None and row['설치주소'] and cache.get(row['설치주소']):
            row['lat'], row['lng'] = cache[row['설치주소']]
            row['좌표출처'] = '카카오'

    branch_rank = {b: i for i, b in enumerate(BRANCH_ORDER)}
    rows.sort(key=lambda x: (branch_rank.get(x['지사'], len(BRANCH_ORDER)), x['지사'], str(x['관리고객명'] or '')))
    stats = {'파일': sum(1 for x in rows if x['좌표출처'] == '파일'),
             '카카오': sum(1 for x in rows if x['좌표출처'] == '카카오'),
             '없음': sum(1 for x in rows if x['lat'] is None)}
    return {"rows": rows, "coord_stats": stats, "voc_matched": sum(1 for x in rows if x['VOC']),
            "kakao_key_set": bool(kakao_key), "kakao_error": error}
