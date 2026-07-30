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

    return merged_df, "성공적으로 병합되었습니다.", match_report
