import re

import numpy as np
import pandas as pd

from core.matching_config import enabled_conditions, FILE_DISPLAY_COLUMNS

# Canonical 관리본부 names actually present in 관리고객원본/시설현황 (after
# HQ_ALIASES normalization below) -- NOT the old pre-restructuring 11-region
# names. Keeping this in sync with HQ_ALIASES/report.py's JS mirror matters:
# a name that isn't in this list still displays fine (falls into the
# unordered "leftover" bucket), it just won't sort where expected.
HQ_ORDER = ['강북/강원', '강남/서부', '대구/경북', '부산/경남', '충남/충북', '전남/전북']
BRANCH_ORDER = ['중앙', '강북', '서대문', '고양', '의정부', '남양주', '강릉', '원주']

# Real-world exports spell the same HQ multiple ways (with/without "본부",
# merged-region names). Mirrors the HQ_ALIASES map embedded client-side in
# report.py's APP_SCRIPT_TEMPLATE -- keep both in sync.
HQ_ALIASES = {
    '강원본부': '강북/강원', '강북/강원본부': '강북/강원', '강북/강원': '강북/강원',
    '서부본부': '강남/서부', '강남/서부본부': '강남/서부', '강남/서부': '강남/서부',
    '부산/경남본부': '부산/경남', '부산경남본부': '부산/경남', '부산/경남': '부산/경남',
    '전남/전북본부': '전남/전북', '전남전북본부': '전남/전북', '전남/전북': '전남/전북',
    '충남/충북본부': '충남/충북', '충남충북본부': '충남/충북', '충남/충북': '충남/충북',
    '대구/경북본부': '대구/경북', '대구경북본부': '대구/경북', '대구/경북': '대구/경북',
}

OPEN_VOC_STATES = {'미접수', '접수', '처리중', '결재요청'}

# 활동유무 진행 단계 -- 실적 반영 시 더 진행된 상태를 인정 (report.py STATUS_RANK_JS와 같게)
STATUS_RANK = {'미접수': 0, '접수': 1, '처리완료': 2}


def _normalize_status(v):
    """' 처리 완료 ' -> '처리완료' (상태값일 때만 공백 제거, 나머지는 앞뒤 공백만 정리)."""
    if not isinstance(v, str):
        return v
    t = v.replace('\xa0', ' ').strip()
    squeezed = re.sub(r'\s+', '', t)
    return squeezed if squeezed in STATUS_RANK else t


def _status_hits(series):
    return int(series.map(_normalize_status).isin(STATUS_RANK.keys()).sum())


def detect_status_col(df):
    """실적(처리완료/접수/미접수)을 읽을 열.
    1) '활동유무' 열에 상태값이 있으면 무조건 그 열 -- 원래(bba5fc6) 동작 그대로.
       상태값이 더 많은 다른 열(예: 처리상태)이 있어도 바꾸지 않는다.
    2) 없으면 이름이 '활동유무'로 시작하는 열(예: 활동유무(o,x))에 상태값이 있으면 그 열.
    3) 그래도 없으면(예: 상태 열에 상태값, 활동유무(o,x)에는 방문상담/재계약 -- 원래 코드는
       여기서 KeyError) 상태값이 가장 많은 글자 열.
    report.py detectStatusCol()과 같은 규칙."""
    if '활동유무' in df.columns and _status_hits(df['활동유무']) > 0:
        return '활동유무'
    for col in df.columns:
        if str(col).startswith('활동유무') and _status_hits(df[col]) > 0:
            return col
    best, best_hits = None, 0
    for col in df.columns:
        # 글자 열만 (pandas 2는 object, pandas 3은 str 타입)
        if not (df[col].dtype == object or pd.api.types.is_string_dtype(df[col])):
            continue
        hits = int(df[col].map(_normalize_status).isin(STATUS_RANK.keys()).sum())
        prefer = str(col).startswith('활동유무')
        if hits > best_hits or (hits == best_hits and hits > 0 and prefer and not str(best).startswith('활동유무')):
            best, best_hits = col, hits
    if best is None:  # 상태값이 전혀 없으면 이전처럼 이름으로
        best = '활동유무' if '활동유무' in df.columns else next((c for c in df.columns if str(c).startswith('활동유무')), None)
    return best

# 8. 영업구역담당자 -- SP 전용 구역번호 -> 담당자명 표. 총괄DB의 SP 구역
# 컬럼(영업구역정보)과 이 파일의 구역번호를 맞춰 '영업구역담당자'를 채운다.
SP_ZONE_COL_CANDIDATES = ['영업구역정보', '영업구역번호', '영업구역']
ZONE_OWNER_KEY_COL = '구역번호'
ZONE_OWNER_NAME_COL = '담당자명'
ZONE_OWNER_OUTPUT_COL = '영업구역담당자'
# 총괄DB의 SP 담당자 열 (내보내기마다 표기가 조금씩 다름 -> 'SP담당'으로 맞춘다)
SP_OWNER_COL_CANDIDATES = ['SP담당', 'SP_담당', 'SP 담당', 'SP담당자', 'SP_담당자', 'SP 담당자']
# 파일에 구역번호는 있는데 담당자명이 비어 있는 구역 (파일에 아예 없는 구역은
# 집계 단계에서 '미매칭'으로 표시되어 둘이 구분된다).
NO_OWNER_LABEL = '담당자없음'


def _zone_key(v):
    """Normalizes a zone code so '405', 405 and 405.0 (Excel hands numeric
    codes back as floats) all compare equal. Mirrors zoneKey() in report.py."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).replace('\xa0', ' ').strip()
    if s.endswith('.0'):
        s = s[:-2]
    return s or None


# 구역 열 찾기 -- 이름(정확 -> 비슷한 이름) + 값 검사. 구역 값은 G230001 / T001 / Z01 같은
# 코드여야 한다; '만기도래_신규' 같은 글자가 든 열(예: 재계약대상 구분)은 구역 열로 쓰지 않는다.
# report.py findZoneCol()과 같은 규칙.
ZONE_FUZZY = {
    'SP': (['영업구역'], []),
    'SE': (['기술구역'], []),
    'SG': (['출동구역', '구역정보'], ['영업', '기술']),
}
_ZONE_CODE_RE = re.compile(r'^[A-Za-z]{0,4}\d{2,}[A-Za-z0-9-]*$')


def zone_like(series, sample=300):
    """값이 대부분(60% 이상) 구역 코드 형태인지. 값이 하나도 없으면 판단 보류(True)."""
    vals = [v for v in (_zone_key(x) for x in series.head(20000)) if v][:sample]
    if not vals:
        return True
    return sum(1 for v in vals if _ZONE_CODE_RE.match(v)) / len(vals) >= 0.6


def _norm_header(name):
    return re.sub(r'[\s_()\-·]', '', str(name))


def find_zone_col(df, kind, candidates):
    for c in candidates:
        if c in df.columns and zone_like(df[c]):
            return c
    include, exclude = ZONE_FUZZY[kind]
    for c in df.columns:
        n = _norm_header(c)
        if any(f in n for f in include) and not any(x in n for x in exclude) and zone_like(df[c]):
            return c
    return None


def build_zone_owner_map(zone_owner_df):
    """{구역번호: 담당자명}. A zone listed under several names keeps all of
    them joined with '/' (in file order) rather than silently picking one; a
    zone listed with no name at all maps to NO_OWNER_LABEL."""
    if zone_owner_df is None or ZONE_OWNER_KEY_COL not in zone_owner_df.columns \
            or ZONE_OWNER_NAME_COL not in zone_owner_df.columns:
        return {}
    owners = {}
    for zone, name in zip(zone_owner_df[ZONE_OWNER_KEY_COL], zone_owner_df[ZONE_OWNER_NAME_COL]):
        key = _zone_key(zone)
        name = None if name is None or (isinstance(name, float) and pd.isna(name)) else str(name).strip()
        if not key:
            continue
        names = owners.setdefault(key, [])
        if name and name not in names:
            names.append(name)
    return {k: '/'.join(v) or NO_OWNER_LABEL for k, v in owners.items()}


def normalize_hq(val):
    """Trim + alias-canonicalize, falling back to '미상' for display (used by
    해지파이프라인/해지시설내역 -- independent datasets that never go through
    process_and_merge's own _normalize_hq_raw). Kept in sync with that so a
    HQ shows up as one bar, not split between '...본부' and its canonical
    alias -- see HQ_ORDER/HQ_ALIASES above."""
    result = _normalize_hq_raw(val)
    return result if result is not None else "미상"


def normalize_branch(val):
    result = _normalize_branch_raw(val)
    return result if result is not None else "미상"


def _normalize_hq_raw(val):
    """Trim + alias-canonicalize a 관리본부(명) value, preserving None for
    missing input -- unlike normalize_hq() above, which substitutes '미상'
    for display. Used mid-derivation, before the unknown bucket applies."""
    if pd.isna(val) or val == '':
        return None
    s = str(val).strip()
    if s in HQ_ALIASES:
        return HQ_ALIASES[s]
    return re.sub(r'본부$', '', s)


def _normalize_branch_raw(val):
    if pd.isna(val) or val == '':
        return None
    return re.sub(r'지사$', '', str(val).strip())


def _clean_amount_string(series):
    """Strips thousands-separator commas and any non-numeric junk (원, spaces)
    while KEEPING the decimal point. Stripping the point too (the old regex
    was r'[^\d\-]', dropping '.' along with everything else) silently turns
    '150000.0' into '1500000' -- a false 10x inflation on any amount column
    that happens to arrive decimal-formatted (e.g. from an Excel float cell)."""
    return series.astype(str).str.replace(',', '', regex=False).str.replace(r'[^\d.\-]', '', regex=True)


def to_numeric_amount(series):
    return pd.to_numeric(_clean_amount_string(series), errors='coerce').fillna(0)


def to_numeric_amount_raw(series):
    """Like to_numeric_amount but keeps missing values as NaN (no fillna(0))
    so callers can fall through a priority list of amount candidates via
    combine_first() without an absent source masquerading as a real zero."""
    return pd.to_numeric(_clean_amount_string(series), errors='coerce')


def _first_matching_col(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _first_non_null(*series_list):
    """Row-wise first-non-null across aligned Series, in priority order
    (mirrors report.py's embedded JS firstNonNull())."""
    cleaned = []
    for s in series_list:
        if s is None:
            continue
        if s.dtype == object:
            s = s.replace('', np.nan)
        cleaned.append(s)
    if not cleaned:
        return None
    result = cleaned[0]
    for c in cleaned[1:]:
        result = result.combine_first(c)
    return result


def _has_title_row(columns):
    """True when row 0 looks like a single-cell report title (e.g. 'VOC정보
    조회') rather than real column headers -- pandas reads that as column 0's
    name and 'Unnamed: N' for the rest, silently breaking every downstream
    lookup by real column name (계약번호, 관리본부 등이 전부 존재하지 않는
    컬럼이 되어버림). Real headers never look like this."""
    cols = list(columns)
    if len(cols) < 3:
        return False
    unnamed = sum(1 for c in cols[1:] if str(c).startswith('Unnamed:'))
    return unnamed >= len(cols) - 2


def _strip_invisible_whitespace(series):
    """Strips \\xa0 (non-breaking space) and surrounding whitespace from any
    string cells. Excel exports routinely embed \\xa0 in date/time columns
    (e.g. '14:30\\xa0'); pandas' pd.to_datetime() format-guessing tries to
    encode strings with the OS locale codec while inspecting them, and on a
    Windows machine whose locale codec is cp949 that raises UnicodeEncodeError
    -- not just on print(), on the parse itself. Cleaning the string first
    avoids hitting that path at all."""
    if series.dtype != object:
        return series
    return series.apply(lambda v: v.replace('\xa0', ' ').strip() if isinstance(v, str) else v)


def load_data(file_path, is_csv=False):
    if file_path is None: return None
    try:
        if is_csv:
            df = pd.read_csv(file_path, encoding='cp949')
        else:
            df = pd.read_excel(file_path)
        if _has_title_row(df.columns):
            df = (pd.read_csv(file_path, encoding='cp949', header=1) if is_csv
                  else pd.read_excel(file_path, header=1))
        
        # Sanitize column names (remove \xa0 and extra whitespace) to prevent
        # UnicodeEncodeError in Pandas warnings on Windows and fix matching bugs
        df.columns = [str(c).replace('\xa0', ' ').strip() for c in df.columns]
        
        return df
    except Exception as e:
        print(f"Error loading {file_path}: {e}")
        return None


def _merge_simple(merged_df, file_df, conditions, display_cols, suffix):
    """1:1 lookup merge (original/facility) -- keeps only the first matching
    record per key on the right side. Every carried-over display column is
    explicitly suffixed (e.g. '관리본부명_origin') so later derivation is
    unambiguous regardless of name collisions between sources."""
    db_cols = [c['db_col'] for c in conditions]
    file_cols = [c['file_col'] for c in conditions]
    missing_db = [c for c in db_cols if c not in merged_df.columns]
    missing_file = [c for c in file_cols if c not in file_df.columns]
    if missing_db or missing_file:
        return merged_df, []

    cols_to_keep = [c for c in dict.fromkeys(file_cols + display_cols) if c in file_df.columns]
    sub = file_df[cols_to_keep].copy()
    sub = sub.drop_duplicates(subset=file_cols, keep='first')
    rename_map = {c: f'{c}_{suffix}' for c in sub.columns if c not in file_cols}
    sub = sub.rename(columns=rename_map)

    merged_df = pd.merge(merged_df, sub, left_on=db_cols, right_on=file_cols, how='left')
    return merged_df, conditions


def _merge_aggregate(merged_df, file_df, conditions, display_cols, prefix, date_candidates):
    """Count + most-recent-by-date aggregation for 1:N sources (patrol/voc) --
    mirrors report.py's embedded JS applyMatching(..., aggregate=true): every
    db row gets '{prefix}건수' (match count) and '{prefix}_최근일시', plus the
    display columns from whichever matched record is most recent (falling
    back to the last record in file order if no usable date is present)."""
    db_cols = [c['db_col'] for c in conditions]
    file_cols = [c['file_col'] for c in conditions]
    missing_db = [c for c in db_cols if c not in merged_df.columns]
    missing_file = [c for c in file_cols if c not in file_df.columns]
    if missing_db or missing_file:
        return merged_df, []

    date_col = next((c for c in date_candidates if c in file_df.columns), None)
    keep_cols = [c for c in dict.fromkeys(file_cols + display_cols) if c in file_df.columns]
    sub = file_df[keep_cols].copy()

    count_col = f'{prefix}건수'
    latest_col = f'{prefix}_최근일시'

    counts = sub.groupby(file_cols, dropna=False).size().rename(count_col).reset_index()

    if date_col:
        sub['_dt'] = pd.to_datetime(_strip_invisible_whitespace(sub[date_col]), errors='coerce')
        # Stable sort with NaT first: within each group the max-dated row ends
        # up last; a group with no dated rows at all keeps its original file
        # order (stable sort), so its last row wins -- matching report.py's
        # embedded JS applyMatching(aggregate=true) fallback exactly.
        sub = sub.sort_values('_dt', kind='stable', na_position='first')

    picked = sub.drop_duplicates(subset=file_cols, keep='last').copy()
    picked = picked.rename(columns={'_dt': latest_col}) if date_col else picked.assign(**{latest_col: pd.NaT})

    keep_display = [c for c in display_cols if c in picked.columns and c != date_col]
    rename_map = {c: f'{c}_{prefix}' for c in keep_display}
    picked = picked.rename(columns=rename_map)
    picked = picked[file_cols + [latest_col] + [rename_map.get(c, c) for c in keep_display]]
    picked = picked.merge(counts, on=file_cols, how='left')

    merged_df = pd.merge(merged_df, picked, left_on=db_cols, right_on=file_cols, how='left')
    merged_df[count_col] = merged_df[count_col].fillna(0).astype(int)
    return merged_df, conditions


def _compute_open_voc_counts(merged_df, voc_file_df, conditions):
    """Per db row, how many of its matched VOC tickets are still open
    (상태 in OPEN_VOC_STATES) -- mirrors report.py's embedded JS
    countOpenVoc(). Independent of _merge_aggregate's 'most recent' pick
    since this needs a count over *all* matches, not just the latest one."""
    if not conditions or voc_file_df is None or '상태' not in voc_file_df.columns:
        return pd.Series(0, index=merged_df.index)
    db_cols = [c['db_col'] for c in conditions]
    file_cols = [c['file_col'] for c in conditions]
    if any(c not in merged_df.columns for c in db_cols) or any(c not in voc_file_df.columns for c in file_cols):
        return pd.Series(0, index=merged_df.index)

    open_rows = voc_file_df[voc_file_df['상태'].isin(OPEN_VOC_STATES)]
    counts = open_rows.groupby(file_cols, dropna=False).size().rename('_open_cnt').reset_index()

    keys = merged_df[db_cols].reset_index()
    joined = keys.merge(counts, left_on=db_cols, right_on=file_cols, how='left').set_index('index')
    return joined['_open_cnt'].reindex(merged_df.index).fillna(0).astype(int)


def process_and_merge(files_dict, matching_config):
    """Merges the auxiliary files onto 총괄DB and derives every canonical
    business column (관리본부/관리지사/월환산금액/재계약여부/... ) the
    dashboard, table and charts read.

    This mirrors -- and must stay in sync with -- report.py's embedded JS
    rebuildMerged()/applyMatching(), which recomputes the same thing entirely
    client-side when an admin edits the matching config and clicks 적용.
    Keeping both in lockstep means the very first server-rendered view
    already matches what a client-side recompute produces, instead of
    silently showing zeros/blanks until someone opens the admin panel.
    """
    db_df = files_dict.get('db')
    if db_df is None:
        return None, "총괄DB가 없습니다.", {}

    merged_df = db_df.copy()
    match_report = {}

    # 실적 집계는 전부 '활동유무'(처리완료/접수/미접수)를 읽는다. 그 값이 든 열을 값으로
    # 찾아 '활동유무'로 맞춘다 -- 예: 상태 열에 있고 활동유무(o,x)에는 방문상담/재계약인 총괄DB.
    status_col = detect_status_col(merged_df)
    # match_report 값은 모두 [{db_col, file_col}] 목록 (generate_report.py / main.py가 그렇게 읽음)
    match_report['status'] = [{'db_col': status_col, 'file_col': '(실적 기준 열)'}] if status_col else []
    if status_col and status_col != '활동유무':
        if '활동유무' in merged_df.columns:
            merged_df['활동유무_원래열'] = merged_df['활동유무']
        merged_df['활동유무'] = merged_df[status_col]
    if '활동유무' in merged_df.columns:
        merged_df['활동유무'] = merged_df['활동유무'].astype(object).map(_normalize_status)

    for key, suffix in [('original', 'origin'), ('facility', 'fac'), ('cancel', 'cancel'), ('cancelled_facility', 'cancelfac')]:
        file_df = files_dict.get(key)
        conditions = enabled_conditions(matching_config, key) if file_df is not None else []
        if file_df is None or not conditions:
            match_report[key] = []
            continue
        merged_df, used = _merge_simple(merged_df, file_df, conditions, FILE_DISPLAY_COLUMNS.get(key, []), suffix)
        match_report[key] = used

    for key, prefix, date_candidates in [
        ('patrol', 'patrol', ['도착시간', '출발시간']),
        ('voc', 'voc', ['접수일시']),
    ]:
        file_df = files_dict.get(key)
        conditions = enabled_conditions(matching_config, key) if file_df is not None else []
        if file_df is None or not conditions:
            match_report[key] = []
            continue
        merged_df, used = _merge_aggregate(merged_df, file_df, conditions, FILE_DISPLAY_COLUMNS.get(key, []), prefix, date_candidates)
        match_report[key] = used

    def col(name):
        if name in merged_df.columns:
            return merged_df[name]
        return pd.Series([None] * len(merged_df), index=merged_df.index)

    merged_df['만기도래_월'] = col('만기도래 월_origin')
    merged_df['합산월정료'] = to_numeric_amount_raw(col('합산월정료(KTT+KT)_origin').astype(object))
    merged_df['서비스재개시일'] = col('서비스재개시일_fac')
    merged_df['KTT월정료'] = to_numeric_amount_raw(col('KTT월정료_fac').astype(object))
    merged_df['순찰건수'] = merged_df['patrol건수'] if 'patrol건수' in merged_df.columns else 0
    merged_df['최근점검결과'] = col('결과_patrol')
    merged_df['최근특이사항'] = col('특이사항_patrol')
    merged_df['최근점검일'] = (
        pd.to_datetime(merged_df['patrol_최근일시'], errors='coerce').dt.strftime('%Y-%m-%d')
        if 'patrol_최근일시' in merged_df.columns else None
    )
    merged_df['VOC건수'] = merged_df['voc건수'] if 'voc건수' in merged_df.columns else 0
    merged_df['미처리VOC건수'] = _compute_open_voc_counts(merged_df, files_dict.get('voc'), match_report.get('voc') or [])
    merged_df['최근VOC상태'] = col('상태_voc')
    merged_df['최근VOC유형'] = col('VOC유형대_voc')

    hq_src = _first_non_null(col('관리본부명_origin').astype(object), col('관리본부명_fac').astype(object))
    branch_src = _first_non_null(col('관리지사명_origin').astype(object), col('관리지사명_fac').astype(object), col('지사').astype(object))
    merged_df['관리지사'] = branch_src.apply(_normalize_branch_raw)
    hq_norm = hq_src.apply(_normalize_hq_raw)
    merged_df['관리본부'] = [
        h if not pd.isna(h) else ('강북/강원' if b in BRANCH_ORDER else None)
        for h, b in zip(hq_norm, merged_df['관리지사'])
    ]

    amount = _first_non_null(merged_df['합산월정료'], merged_df['KTT월정료'], to_numeric_amount_raw(col('월정료').astype(object)))
    merged_df['월환산금액'] = amount

    merged_df['재계약여부'] = _first_non_null(
        col('재계약여부_origin').astype(object), col('재계약여부_fac').astype(object), col('쟤계약여부_fac').astype(object),
    )
    merged_df['계약상태'] = _first_non_null(
        col('계약상태_origin').astype(object), col('계약상태(중)_fac').astype(object), col('계약상태(대)_fac').astype(object),
    )

    # 8. 영업구역담당자: SP 건만, 영업구역정보 = 구역번호로 담당자명을 붙인다.
    # SP 담당자: 총괄DB의 SP담당 값이 있으면 그 값이 우선, 비어 있으면 8. 영업구역담당자
    # (영업구역정보 = 구역번호). report.py rebuildMerged()와 같은 규칙.
    sp_owner_col = _first_matching_col(merged_df, SP_OWNER_COL_CANDIDATES)
    if sp_owner_col and sp_owner_col != 'SP담당':
        merged_df['SP담당'] = merged_df[sp_owner_col]  # 기존 SP담당 기반 섹션(부진자·발송 리스트·재계약)도 읽도록
    zone_owner_map = build_zone_owner_map(files_dict.get('zone_owner'))
    sp_zone_col = find_zone_col(merged_df, 'SP', SP_ZONE_COL_CANDIDATES)
    use_map = bool(zone_owner_map and sp_zone_col)
    if (use_map or sp_owner_col) and '활동대상구분' in merged_df.columns:
        is_sp = merged_df['활동대상구분'] == 'SP'
        db_owner = merged_df['SP담당'] if sp_owner_col else pd.Series([None] * len(merged_df), index=merged_df.index)
        zones = merged_df[sp_zone_col] if sp_zone_col else pd.Series([None] * len(merged_df), index=merged_df.index)
        owners, sources = [], []
        for sp, own, z in zip(is_sp, db_owner, zones):
            name = None if own is None or (isinstance(own, float) and pd.isna(own)) else str(own).strip()
            if not sp:
                owners.append(None); sources.append(None)
            elif name:
                owners.append(name); sources.append('총괄DB SP담당')
            else:
                mapped = zone_owner_map.get(_zone_key(z)) if use_map else None
                owners.append(mapped); sources.append('8번 영업구역담당자' if mapped else None)
        merged_df[ZONE_OWNER_OUTPUT_COL] = owners
        merged_df['영업구역담당자_출처'] = sources
        match_report['zone_owner'] = ([{'db_col': sp_zone_col, 'file_col': ZONE_OWNER_KEY_COL}] if use_map else []) \
            + ([{'db_col': sp_owner_col, 'file_col': '(총괄DB SP담당 우선)'}] if sp_owner_col else [])
    else:
        match_report['zone_owner'] = []

    # --- 상태값 역반영 (총괄DB 업데이트) 로직 ---
    if 'sp 담당자 상태값' not in merged_df.columns:
        merged_df['sp 담당자 상태값'] = None
    
    # 1. 2번 voc 상태 컬럼에 처리완료, 접수, 미접수 값을 1번 sp 담당자 상태값 반영
    if '최근VOC상태' in merged_df.columns:
        voc_mask = merged_df['최근VOC상태'].isin(['처리완료', '접수', '미접수'])
        merged_df.loc[voc_mask, 'sp 담당자 상태값'] = merged_df.loc[voc_mask, '최근VOC상태']
    
    # 2. 7번 계약상태(중) 컬럼에 일반해지는 1번에 처리완료
    if '계약상태(중)_cancelfac' in merged_df.columns:
        cancel_mask = merged_df['계약상태(중)_cancelfac'] == '일반해지'
        merged_df.loc[cancel_mask, 'sp 담당자 상태값'] = '처리완료'
        
    # 3. 순찰정기점검내역 매칭되는 것은 처리완료로 처리
    if '순찰건수' in merged_df.columns:
        patrol_mask = merged_df['순찰건수'] > 0
        merged_df.loc[patrol_mask, 'sp 담당자 상태값'] = '처리완료'

    # 실적(진척율·구역별·실적현황표)은 총괄DB '활동유무'만 센다 -- 위 역반영 값은
    # 'sp 담당자 상태값' 열에만 남고 실적에는 더하지 않는다 (bba5fc6과 같은 동작).

    return merged_df, "성공적으로 병합되었습니다.", match_report
