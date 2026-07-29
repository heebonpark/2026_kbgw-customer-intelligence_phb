"""
Builds dashboard-ready aggregates (KPI tiles + chart series) from the merged
management-customer dataframe. Every function returns plain dict/list
structures so they can be JSON-dumped straight into the HTML report.

Values are never silently dropped: rows with a missing/NaN category are
grouped into an explicit "미상" (unknown) bucket rather than disappearing,
so chart totals always reconcile with the underlying row count.
"""

import math
import re
from datetime import datetime
from urllib.parse import quote

import pandas as pd

from .handlers import (
    HQ_ORDER, BRANCH_ORDER, normalize_hq, normalize_branch,
    to_numeric_amount, _first_matching_col,
)

UNKNOWN_LABEL = "미상"


def _fmt_int(n):
    if n is None or (isinstance(n, float) and math.isnan(n)):
        return "0"
    return f"{int(n):,}"


def _fmt_compact_won(n):
    """1,234,567 -> '123.5백만원' style compact currency label."""
    if n is None or (isinstance(n, float) and math.isnan(n)):
        return "0원"
    n = float(n)
    if abs(n) >= 1_0000_0000:
        return f"{n / 1_0000_0000:.1f}억원"
    if abs(n) >= 10_000:
        return f"{n / 10_000:.0f}만원"
    return f"{n:,.0f}원"


def _value_counts_ordered(series, order=None, top_n=None):
    """Returns [{label, value}] with NaN grouped into UNKNOWN_LABEL.

    If `order` is given, categories follow that order (extras appended,
    unknown last). Otherwise sorted descending by value (optionally capped
    to top_n, with the remainder folded into '기타').
    """
    s = series.fillna(UNKNOWN_LABEL).astype(str)
    s = s.replace({'nan': UNKNOWN_LABEL, 'NaT': UNKNOWN_LABEL, '': UNKNOWN_LABEL})
    counts = s.value_counts()

    if order:
        items = [{"label": lbl, "value": int(counts.get(lbl, 0))} for lbl in order]
        leftover = counts.drop(index=[l for l in order if l in counts.index], errors='ignore')
        for lbl, val in leftover.items():
            items.append({"label": lbl, "value": int(val)})
        return items

    counts = counts.sort_values(ascending=False)
    if top_n and len(counts) > top_n:
        head = counts.iloc[:top_n]
        rest = int(counts.iloc[top_n:].sum())
        items = [{"label": lbl, "value": int(val)} for lbl, val in head.items()]
        if rest > 0:
            items.append({"label": "기타", "value": rest})
        return items

    return [{"label": lbl, "value": int(val)} for lbl, val in counts.items()]


def _value_counts_known(series, order=None):
    """Like _value_counts_ordered but drops the unknown/NaN bucket entirely and
    returns a coverage note -- for fields only a minority of rows have (e.g.
    cross-referenced pipeline data), so the chart isn't swamped by '미상'."""
    total = len(series)
    known = series.dropna()
    known = known[known.astype(str).str.strip() != '']
    items = _value_counts_ordered(known, order=order) if len(known) else []
    coverage = (
        f"정보 확인된 {len(known):,}건 / 전체 {total:,}건 ({len(known) / total * 100:.1f}%) 기준"
        if total else ""
    )
    return items, coverage


def _sum_by(df, group_col, value_col, order=None):
    if group_col not in df.columns or value_col not in df.columns:
        return []
    g = df.copy()
    g[group_col] = g[group_col].fillna(UNKNOWN_LABEL).astype(str)
    totals = g.groupby(group_col)[value_col].sum()

    if order:
        items = [{"label": lbl, "value": float(totals.get(lbl, 0.0))} for lbl in order]
        leftover = totals.drop(index=[l for l in order if l in totals.index], errors='ignore')
        for lbl, val in leftover.items():
            items.append({"label": lbl, "value": float(val)})
        return items

    totals = totals.sort_values(ascending=False)
    return [{"label": lbl, "value": float(val)} for lbl, val in totals.items()]


def build_kpis(df):
    total = len(df)
    total_amount = df['월환산금액'].sum() if '월환산금액' in df.columns else 0

    recontract_done = 0
    if '재계약여부' in df.columns:
        recontract_done = df['재계약여부'].astype(str).str.contains('재계약').sum()

    unresolved_voc = int(df['미처리VOC건수'].sum()) if '미처리VOC건수' in df.columns else 0

    patrolled_ratio = 0.0
    if '순찰건수' in df.columns and total:
        patrolled_ratio = (df['순찰건수'] > 0).sum() / total * 100

    # 해지위험도/방어진행단계 live only in the independent 해지파이프라인
    # dashboard now (see build_cancel_dashboard) -- 총괄DB 병합 결과에는
    # 해당 컬럼이 없으므로 여기서는 계산하지 않는다.
    return [
        {"label": "총 관리계약", "value": _fmt_int(total), "sub": "건"},
        {"label": "월 정산금액 합계", "value": _fmt_compact_won(total_amount), "sub": f"{_fmt_int(total_amount)}원"},
        {"label": "재계약 완료", "value": _fmt_int(recontract_done), "sub": f"전체의 {recontract_done / total * 100:.1f}%" if total else "0%"},
        {"label": "미처리 VOC", "value": _fmt_int(unresolved_voc), "sub": "건"},
        {"label": "순찰점검 실시율", "value": f"{patrolled_ratio:.1f}%", "sub": "당월 1회 이상"},
    ]


def build_dashboard(merged_df, voc_df=None, patrol_df=None):
    data = {}

    data['kpis'] = build_kpis(merged_df)

    hq_series = merged_df.get('관리본부', merged_df.get('관리본부명', merged_df.get('본부', pd.Series(dtype=object))))
    branch_series = merged_df.get('관리지사', merged_df.get('관리지사명', merged_df.get('지사', pd.Series(dtype=object))))

    data['hq_count_chart'] = {
        "title": "본부별 관리계약 수",
        "unit": "건",
        "items": _value_counts_ordered(hq_series, order=HQ_ORDER),
    }

    data['branch_count_chart'] = {
        "title": "지사별 관리계약 수",
        "unit": "건",
        "items": _value_counts_ordered(branch_series, order=BRANCH_ORDER),
    }

    # Sum by requires the column name in the dataframe
    branch_col_name = '관리지사' if '관리지사' in merged_df.columns else ('관리지사명' if '관리지사명' in merged_df.columns else '지사')
    data['branch_amount_chart'] = {
        "title": "지사별 월 정산금액 합계",
        "unit": "원",
        "items": _sum_by(merged_df, branch_col_name, '월환산금액', order=BRANCH_ORDER) if branch_col_name in merged_df.columns else [],
    }

    data['activity_type_chart'] = {
        "title": "활동대상구분 분포",
        "unit": "건",
        "items": _value_counts_ordered(merged_df.get('활동대상구분', pd.Series(dtype=object))),
    }

    data['activity_status_chart'] = {
        "title": "활동 처리 현황",
        "unit": "건",
        "items": _value_counts_ordered(
            merged_df.get('활동유무', pd.Series(dtype=object)),
            order=['처리완료', '접수', '미접수'],
        ),
    }

    recontract_items, recontract_note = _value_counts_known(merged_df.get('재계약여부', pd.Series(dtype=object)))
    data['recontract_chart'] = {
        "title": "재계약여부 분포",
        "unit": "건",
        "note": recontract_note,
        "items": recontract_items,
    }

    if voc_df is not None and '계약번호' in merged_df.columns:
        scoped_voc = voc_df[voc_df['계약번호'].isin(merged_df['계약번호'])] if '계약번호' in voc_df.columns else voc_df
        type_col = 'VOC유형대' if 'VOC유형대' in scoped_voc.columns else None
        data['voc_type_chart'] = {
            "title": "VOC 유형 분포 (관리계약 기준)",
            "unit": "건",
            "items": _value_counts_ordered(scoped_voc[type_col], top_n=6) if type_col else [],
        }

    if patrol_df is not None and '서비스번호' in merged_df.columns and '고객번호' in patrol_df.columns:
        scoped_patrol = patrol_df[patrol_df['고객번호'].isin(merged_df['서비스번호'])]
        result_col = '결과' if '결과' in scoped_patrol.columns else None
        data['patrol_result_chart'] = {
            "title": "순찰점검 결과 분포 (관리계약 기준)",
            "unit": "건",
            "items": _value_counts_ordered(scoped_patrol[result_col], top_n=6) if result_col else [],
        }

    expiry_col = '만기도래 월' if '만기도래 월' in merged_df.columns else ('만기도래_월' if '만기도래_월' in merged_df.columns else None)
    if expiry_col:
        non_null = merged_df[expiry_col].dropna()
        order = sorted(non_null.unique().tolist())
        data['expiry_chart'] = {
            "title": "만기도래 월 분포",
            "unit": "건",
            "items": _value_counts_ordered(non_null, order=order) if len(order) else [],
        }

    return data


# ---------------------------------------------------------------------------
# 지사 x 활동대상구분(SP/SE/SG) 진척율 매트릭스 -- 총괄DB 원본 컬럼(지사/
# 활동대상구분/활동유무)만으로 계산되는, 매칭 설정과 무관한 지표.
# ---------------------------------------------------------------------------

PROGRESS_TYPES = ['SP', 'SE', 'SG']


def build_progress_matrix(df):
    if df is None or '지사' not in df.columns or '활동대상구분' not in df.columns:
        return None

    work = df.copy()
    work['지사'] = work['지사'].fillna(UNKNOWN_LABEL)
    work['_done'] = work.get('활동유무') == '처리완료'

    present_branches = set(work['지사'].unique())
    branches = [b for b in BRANCH_ORDER if b in present_branches]
    branches += sorted(b for b in present_branches if b not in BRANCH_ORDER)

    def _cell(sub):
        total = len(sub)
        done = int(sub['_done'].sum())
        pct = (done / total * 100) if total else 0.0
        return {"처리완료": done, "미처리": total - done, "계": total, "진척율": pct}

    def _cell_sp(sub):
        """SP is tracked at finer granularity than SE/SG: every row carries an
        explicit 접수/미접수/처리완료 status (no unlogged/NaN rows)."""
        total = len(sub)
        done = int((sub['활동유무'] == '처리완료').sum())
        received = int((sub['활동유무'] == '접수').sum())
        not_received = int((sub['활동유무'] == '미접수').sum())
        pct = (done / total * 100) if total else 0.0
        return {"처리완료": done, "접수": received, "미접수": not_received, "계": total, "진척율": pct}

    branch_rows = []
    for branch in branches:
        sub = work[work['지사'] == branch]
        row = {"지사": branch}
        row['SP'] = _cell_sp(sub[sub['활동대상구분'] == 'SP'])
        for t in ['SE', 'SG']:
            row[t] = _cell(sub[sub['활동대상구분'] == t])
        overall = _cell(sub[sub['활동대상구분'].isin(PROGRESS_TYPES)])
        row['전체'] = overall
        branch_rows.append(row)

    ranked_idx = sorted(range(len(branch_rows)), key=lambda i: -branch_rows[i]['전체']['진척율'])
    for rank, idx in enumerate(ranked_idx, start=1):
        branch_rows[idx]['순위'] = rank

    total_row = {"지사": "본부계"}
    total_row['SP'] = _cell_sp(work[work['활동대상구분'] == 'SP'])
    for t in ['SE', 'SG']:
        total_row[t] = _cell(work[work['활동대상구분'] == t])
    total_row['전체'] = _cell(work[work['활동대상구분'].isin(PROGRESS_TYPES)])
    total_row['순위'] = None

    type_totals = {t: total_row[t]['계'] for t in PROGRESS_TYPES}

    return {"branch_rows": branch_rows, "total_row": total_row, "type_totals": type_totals}


def build_progress_type_charts(matrix):
    """SP/SE/SG 각각을 지사별로 나눠, 진척율 내림차순 막대 리스트로 -- 하나의
    14열짜리 표보다 유형별로 쪼개서 보는 게 한눈에 들어오므로 별도 제공."""
    if matrix is None or not matrix['branch_rows']:
        return None
    charts = {}
    for t in PROGRESS_TYPES:
        rows = [r for r in matrix['branch_rows'] if r[t]['계'] > 0]
        rows = sorted(rows, key=lambda r: -r[t]['진척율'])
        charts[t] = {
            "title": f"{t} 진척율 (지사별)",
            "items": [
                {"label": r['지사'], "value": r[t]['진척율'], "sub": f"{r[t]['처리완료']:,}/{r[t]['계']:,}건"}
                for r in rows
            ],
        }
    return charts


def build_branch_insights(matrix):
    """지사별 분석리포트 요약 -- 전체/유형별 최고·최저 지사를 문장으로 바로
    쓸 수 있는 형태로 요약. build_progress_matrix가 이미 계산한 진척율을
    그대로 재사용하므로 매칭설정/필터가 바뀌면 함께 갱신된다."""
    if matrix is None or not matrix['branch_rows']:
        return None
    rows = matrix['branch_rows']

    def _summarize(r, t):
        cell = r[t]
        return {"지사": r['지사'], "진척율": cell['진척율'], "처리완료": cell['처리완료'], "계": cell['계']}

    best = max(rows, key=lambda r: r['전체']['진척율'])
    worst = min(rows, key=lambda r: r['전체']['진척율'])

    type_worst = {}
    for t in PROGRESS_TYPES:
        eligible = [r for r in rows if r[t]['계'] > 0]
        if eligible:
            type_worst[t] = _summarize(min(eligible, key=lambda r: r[t]['진척율']), t)

    return {
        "avg_pct": matrix['total_row']['전체']['진척율'],
        "best": _summarize(best, '전체'),
        "worst": _summarize(worst, '전체'),
        "type_worst": type_worst,
    }


def build_sp_rep_performance(df, min_count=3):
    """SP 부진자 추가분석 -- 지사 단위보다 세밀하게, 총괄DB의 'SP담당' 컬럼
    기준으로 개인별 SP 진척율을 계산해 평균 미달 담당자를 찾아낸다.
    min_count 미만인 담당자는 표본이 너무 작아(예: 1건 중 0건=0%) 부진자
    판정에서는 제외하되, 전체 리더보드에는 그대로 포함해 존재는 보여준다.
    강북/강원본부 소속 SP 건만 대상으로 한다 ('관리본부' 컬럼이 없으면
    필터를 건너뛴다 -- 애초에 이 리포트 자체가 강북/강원 단일 본부 데이터라
    지금은 사실상 항상 전체가 대상이지만, 다른 본부 데이터가 섞이는 경우를
    대비한 안전장치).
    'SP담당' 컬럼이 없는 파일이면 None (해당 리포트 비활성)."""
    if df is None or 'SP담당' not in df.columns or '활동대상구분' not in df.columns:
        return None
    sp = df[df['활동대상구분'] == 'SP'].copy()
    if '관리본부' in sp.columns:
        sp = sp[sp['관리본부'] == '강북/강원']
    if sp.empty:
        return None
    sp['SP담당'] = sp['SP담당'].fillna('미담당').astype(str).str.strip().replace('', '미담당')
    sp['_done'] = sp.get('활동유무') == '처리완료'
    has_zone = '영업구역정보' in sp.columns

    reps = []
    for owner, g in sp.groupby('SP담당'):
        total = len(g)
        done = int(g['_done'].sum())
        received = int((g['활동유무'] == '접수').sum())
        not_received = int((g['활동유무'] == '미접수').sum())
        pct = (done / total * 100) if total else 0.0
        branch = None
        if '지사' in g.columns and not g['지사'].dropna().empty:
            branch = g['지사'].dropna().mode().iat[0]
        zone = None
        if has_zone and not g['영업구역정보'].dropna().empty:
            zone = g['영업구역정보'].dropna().mode().iat[0]
        reps.append({
            "담당자": owner, "지사": branch, "영업구역": zone, "처리완료": done, "접수": received,
            "미접수": not_received, "계": total, "진척율": pct,
        })

    reps.sort(key=lambda r: r['진척율'])  # 오름차순 -- 부진자가 리스트 맨 앞 (부진자 명단용 기준 순서)

    total_done = sum(r['처리완료'] for r in reps)
    total_count = sum(r['계'] for r in reps)
    avg_pct = (total_done / total_count * 100) if total_count else 0.0

    underperformers = [r for r in reps if r['계'] >= min_count and r['진척율'] < avg_pct]

    # 리더보드 차트 전용 정렬: 100% 달성자(더 볼 필요 없는 완료 건)는 제외하고,
    # 지사(조직 순서) -> 담당자(가나다) -> 영업구역(내림차순) 순으로 재정렬 --
    # "부진자 우선"이 아니라 "조직도 순서로 훑어보기" 용도.
    # Python 정렬은 stable이므로 덜 중요한 키부터 차례로 정렬하면 다중 키
    # 정렬이 된다: 영업구역(내림차순) -> 담당자(오름차순) -> 지사(조직 순서).
    branch_rank = {b: i for i, b in enumerate(BRANCH_ORDER)}
    chart_reps = [r for r in reps if r['진척율'] < 100]
    chart_reps.sort(key=lambda r: r['영업구역'] or '', reverse=True)
    chart_reps.sort(key=lambda r: r['담당자'])
    chart_reps.sort(key=lambda r: branch_rank.get(r['지사'], len(BRANCH_ORDER)))

    return {
        "avg_pct": avg_pct,
        "min_count": min_count,
        "reps": reps,
        "chart_reps": chart_reps,
        "underperformer_count": len(underperformers),
        "underperformers": underperformers[:30],
    }


def kakao_map_link(address):
    """No-API-key Kakao Map deep link -- opens Kakao Map's own address search.
    (An embedded live map would need a Kakao JS API key; shipping one inside
    a widely password-shared static report risks exposing/over-using it, so
    this simple search-link is the safe option -- see project review notes.)"""
    if not address:
        return None
    return "https://map.kakao.com/link/search/" + quote(str(address))


PENDING_STATES = ['미접수', '접수']


def build_sp_pending_contact_list(df, hq_filter='강북/강원'):
    """SP 활동 중 아직 미접수/접수 상태인 건을 SP담당자별로 묶어, 메일/문자에
    바로 붙여넣을 수 있는 텍스트 요약(계약번호/상호/월정료)을 만든다.
    설치주소가 있으면 담당자가 바로 찾아갈 수 있도록 카카오맵 링크도 함께 준다."""
    if df is None or 'SP담당' not in df.columns or '활동대상구분' not in df.columns:
        return None
    sp = df[(df['활동대상구분'] == 'SP') & (df.get('활동유무').isin(PENDING_STATES))].copy()
    if hq_filter and '관리본부' in sp.columns:
        sp = sp[sp['관리본부'] == hq_filter]
    if sp.empty:
        return None
    sp['SP담당'] = sp['SP담당'].fillna('미담당').astype(str).str.strip().replace('', '미담당')

    def _clean(v):
        return None if pd.isna(v) else v

    reps = []
    for owner, g in sp.groupby('SP담당'):
        items = []
        for _, row in g.iterrows():
            contract = _clean(row.get('계약번호'))
            amount = _clean(row.get('월환산금액'))
            address = _clean(row.get('설치주소'))
            items.append({
                "계약번호": int(contract) if isinstance(contract, float) and contract.is_integer() else contract,
                "상호": _clean(row.get('상호')),
                "월정료": float(amount) if amount is not None else None,
                "상태": row.get('활동유무'),
                "설치주소": str(address) if address is not None else None,
                "지도링크": kakao_map_link(address),
            })
        items.sort(key=lambda it: (0 if it['상태'] == '미접수' else 1, str(it['상호'] or '')))

        text_lines = []
        for it in items:
            amount_str = f"{it['월정료']:,.0f}원" if it['월정료'] is not None else '-'
            contract_str = it['계약번호'] if it['계약번호'] is not None else '-'
            text_lines.append(f"[{it['상태']}] {contract_str} / {it['상호'] or '-'} / {amount_str}")
        reps.append({
            "담당자": owner,
            "count": len(items),
            "미접수_count": sum(1 for it in items if it['상태'] == '미접수'),
            "접수_count": sum(1 for it in items if it['상태'] == '접수'),
            "items": items,
            "text": "\n".join(text_lines),
        })
    reps.sort(key=lambda r: -r['count'])

    return {"reps": reps, "total_count": sum(r['count'] for r in reps)}


def _service_restart_year(v):
    """서비스재개시일은 YYYYMMDD 형태의 숫자(예: 20260701.0)로 들어온다."""
    if pd.isna(v):
        return None
    try:
        return int(str(int(float(v)))[:4])
    except (ValueError, TypeError):
        return None


def build_recontract_target_analysis(df, current_year=None):
    """재계약대상(SP) 분석 -- 총괄DB(SP)와 관리고객원본('관리고객리스트_재계약
    여부') 파일이 계약번호로 매칭되어 계약상태/만기도래_월이 채워진 SP 건을
    재계약대상으로 삼는다 (이 매칭 자체는 process_and_merge에서 이미 끝나
    있으므로 여기서는 그 결과 컬럼만 읽는다). 그중 수동재계약 + 서비스재개시일이
    당해년도인 건은 '실적', 나머지는 '집중 재계약 활동 대상'으로 분류한다."""
    if df is None or '활동대상구분' not in df.columns or '계약상태' not in df.columns:
        return None
    if current_year is None:
        current_year = datetime.now().year

    target = df[(df['활동대상구분'] == 'SP') & df['계약상태'].notna()].copy()
    if target.empty:
        return None

    service_year = target['서비스재개시일'].apply(_service_restart_year) if '서비스재개시일' in target.columns else None
    achieved = (target.get('재계약여부') == '수동재계약') & (service_year == current_year)
    target['_구분'] = achieved.map({True: '실적', False: '집중 재계약 활동 대상'})

    total = len(target)
    achieved_count = int(achieved.sum())
    focus_count = total - achieved_count

    kpis = [
        {"label": "재계약대상(SP) 전체", "value": _fmt_int(total), "sub": "건"},
        {"label": "실적 (수동재계약·당해년도)", "value": _fmt_int(achieved_count),
         "sub": f"전체의 {achieved_count / total * 100:.1f}%" if total else "0%"},
        {"label": "집중 재계약 활동 대상", "value": _fmt_int(focus_count),
         "sub": f"전체의 {focus_count / total * 100:.1f}%" if total else "0%"},
    ]

    def _group_stats(col):
        if col not in target.columns:
            return []
        rows = []
        for name, g in target.groupby(target[col].fillna(UNKNOWN_LABEL)):
            done = int((g['_구분'] == '실적').sum())
            tot = len(g)
            rows.append({
                "label": str(name), "achieved": done, "focus": tot - done, "total": tot,
                "pct": (done / tot * 100) if tot else 0.0,
            })
        return rows

    branch_rank = {b: i for i, b in enumerate(BRANCH_ORDER)}
    by_branch = _group_stats('관리지사')
    by_branch.sort(key=lambda r: branch_rank.get(r['label'], len(BRANCH_ORDER)))

    by_owner = _group_stats('SP담당')
    by_owner.sort(key=lambda r: -r['total'])

    def _clean(v):
        return None if pd.isna(v) else v

    def _row(r):
        owner = _clean(r.get('SP담당'))
        return {
            "계약번호": _clean(r.get('계약번호')), "상호": _clean(r.get('상호')), "지사": _clean(r.get('관리지사')),
            "담당자": owner if owner is not None else '미담당', "계약상태": _clean(r.get('계약상태')),
            "만기도래월": _clean(r.get('만기도래_월')), "재계약여부": _clean(r.get('재계약여부')),
            "구분": r['_구분'], "설치주소": _clean(r.get('설치주소')),
        }
    detail_rows = [_row(r) for _, r in target.iterrows()]
    detail_rows.sort(key=lambda r: (
        0 if r['구분'] == '집중 재계약 활동 대상' else 1,
        branch_rank.get(r['지사'], len(BRANCH_ORDER)),
        str(r['담당자']),
    ))

    return {
        "current_year": current_year, "total": total,
        "achieved_count": achieved_count, "focus_count": focus_count,
        "kpis": kpis, "by_branch": by_branch, "by_owner": by_owner, "detail_rows": detail_rows,
    }


TABLE_COLUMNS = [
    ("관리본부", "본부"),
    ("관리지사", "지사"),
    ("활동대상구분", "구분"),
    ("상호", "고객상호"),
    ("계약번호", "계약번호"),
    ("서비스번호", "서비스번호"),
    ("월환산금액", "월정산금액"),
    ("재계약여부", "재계약여부"),
    ("계약상태", "계약상태"),
    ("방어진행단계", "해지방어단계"),
    ("해지위험도", "해지위험도"),
    ("VOC건수", "VOC건수"),
    ("미처리VOC건수", "미처리VOC"),
    ("순찰건수", "순찰건수"),
    ("최근점검일", "최근점검일"),
    ("최근점검결과", "최근점검결과"),
    ("만기도래_월", "만기도래월"),
    ("SP담당", "담당자"),
]


def build_table(df):
    available = [(src, label) for src, label in TABLE_COLUMNS if src in df.columns]
    view = df[[src for src, _ in available]].copy()
    view.columns = [label for _, label in available]

    if '월정산금액' in view.columns:
        view['월정산금액'] = pd.to_numeric(view['월정산금액'], errors='coerce').fillna(0).round(0).astype(int)

    columns = list(view.columns)
    rows = view.to_dict(orient='records')
    for row in rows:
        for k, v in row.items():
            if isinstance(v, float) and math.isnan(v):
                row[k] = None
            elif pd.isna(v):
                row[k] = None
    return columns, rows


# ---------------------------------------------------------------------------
# EDA: 월환산금액 분포 + IQR 기준 이상치 -- 총괄DB 기준 병합 데이터 대상.
# 0원/미상 계약은 "금액이 없는" 상태이지 통계적 이상치가 아니므로 분포/IQR
# 계산에서 제외한다 (그대로 두면 저액 이상치 판정이 0원 계약으로 도배됨).
# ---------------------------------------------------------------------------

def _histogram_items(values, bins=10):
    vmin, vmax = float(values.min()), float(values.max())
    if vmin == vmax:
        return [{"label": _fmt_compact_won(vmin), "value": int(len(values))}]
    width = (vmax - vmin) / bins
    items = []
    for i in range(bins):
        lo, hi = vmin + i * width, vmin + (i + 1) * width
        mask = (values >= lo) & (values <= hi if i == bins - 1 else values < hi)
        items.append({"label": f"{_fmt_compact_won(lo)}~{_fmt_compact_won(hi)}", "value": int(mask.sum())})
    return items


def build_eda_stats(df, amount_col='월환산금액', top_n=30):
    if amount_col not in df.columns:
        return None
    numeric = pd.to_numeric(df[amount_col], errors='coerce')
    positive = numeric[numeric > 0].dropna()
    if len(positive) < 5:
        return None

    q1 = float(positive.quantile(0.25))
    q3 = float(positive.quantile(0.75))
    iqr = q3 - q1
    lower_bound = max(0.0, q1 - 1.5 * iqr)
    upper_bound = q3 + 1.5 * iqr

    stats = {
        "count": int(len(positive)),
        "mean": float(positive.mean()),
        "median": float(positive.median()),
        "std": float(positive.std()) if len(positive) > 1 else 0.0,
        "min": float(positive.min()),
        "max": float(positive.max()),
        "q1": q1,
        "q3": q3,
        "lower_bound": lower_bound,
        "upper_bound": upper_bound,
    }

    dist_chart = {
        "title": "월 정산금액 분포 (구간별 계약 수, 0원 제외)",
        "unit": "건",
        "items": _histogram_items(positive, bins=10),
    }

    high_mask = numeric > upper_bound
    low_mask = (numeric > 0) & (numeric < lower_bound)
    outlier_mask = high_mask | low_mask

    out = df.loc[outlier_mask].copy()
    out['_amount'] = numeric.loc[outlier_mask]
    out['_kind'] = out['_amount'].apply(lambda v: '고액 이상치' if v > upper_bound else '저액 이상치')
    out['_severity'] = out['_amount'].apply(lambda v: v - upper_bound if v > upper_bound else lower_bound - v)

    view_cols = {}
    if '관리지사' in out.columns:
        view_cols['관리지사'] = '지사'
    if '상호' in out.columns:
        view_cols['상호'] = '고객상호'
    if '계약번호' in out.columns:
        view_cols['계약번호'] = '계약번호'
    view_cols['_amount'] = '월정산금액'
    view_cols['_kind'] = '이상치구분'

    out = out.sort_values('_severity', ascending=False)
    view = out[list(view_cols.keys())].rename(columns=view_cols)
    view['월정산금액'] = view['월정산금액'].round(0).astype(int)
    rows = view.head(top_n).to_dict(orient='records')
    for row in rows:
        for k, v in row.items():
            if pd.isna(v):
                row[k] = None

    return {
        "stats": stats,
        "dist_chart": dist_chart,
        "outlier_count": int(outlier_mask.sum()),
        "outlier_columns": list(view.columns),
        "outlier_rows": rows,
    }


# ---------------------------------------------------------------------------
# 해지 파이프라인 -- deliberately independent of the master-DB merge above.
# It is a company-wide, cross-region dataset; joining it onto the (often
# region-scoped) 총괄DB would silently narrow it to whatever contracts
# happen to also be in this run's DB file. It gets its own dashboard.
# ---------------------------------------------------------------------------

def build_cancel_dashboard(cancel_df, hq_filter='강북/강원'):
    """해지 파이프라인은 전사(company-wide) 데이터지만, 이 리포트 자체가
    강북/강원본부 전용이므로 hq_filter로 그 본부 시설만 걸러서 보여준다.
    None을 넘기면 필터 없이 전사 전체를 보여준다."""
    if cancel_df is None or cancel_df.empty:
        return None

    df = cancel_df.copy()
    if '관리본부' in df.columns:
        df['관리본부'] = df['관리본부'].apply(normalize_hq)
    if '관리지사' in df.columns:
        df['관리지사'] = df['관리지사'].apply(normalize_branch)

    if hq_filter and '관리본부' in df.columns:
        df = df[df['관리본부'] == hq_filter]
        if df.empty:
            return None

    total = len(df)
    risk_pct = pd.to_numeric(df['해지위험도'].astype(str).str.replace('%', ''), errors='coerce') if '해지위험도' in df.columns else pd.Series(dtype=float)
    defense_done = int((df['방어진행단계'] == '방어성공').sum()) if '방어진행단계' in df.columns else 0
    defense_failed = int((df['방어진행단계'] == '방어실패').sum()) if '방어진행단계' in df.columns else 0
    high_risk = int((risk_pct >= 80).sum())
    amount = to_numeric_amount(df['KTT월정료(조정)']) if 'KTT월정료(조정)' in df.columns else pd.Series(dtype=float)

    scope_label = f"{hq_filter} 해지파이프라인" if hq_filter else "전사 해지파이프라인"
    kpis = [
        {"label": scope_label, "value": _fmt_int(total), "sub": "건 (독립 데이터)"},
        {"label": "방어 성공", "value": _fmt_int(defense_done), "sub": f"전체의 {defense_done / total * 100:.1f}%" if total else "0%"},
        {"label": "방어 실패", "value": _fmt_int(defense_failed), "sub": f"전체의 {defense_failed / total * 100:.1f}%" if total else "0%"},
        {"label": "고위험(80%+)", "value": _fmt_int(high_risk), "sub": "건"},
        {"label": "관련 월정료 합계", "value": _fmt_compact_won(amount.sum()), "sub": f"{_fmt_int(amount.sum())}원"},
    ]

    charts = {
        'cancel_hq_chart': {
            "title": "본부별 해지파이프라인 건수",
            "unit": "건",
            "items": _value_counts_ordered(df.get('관리본부', pd.Series(dtype=object)), order=HQ_ORDER),
        },
        'cancel_defense_chart': {
            "title": "해지 방어 진행단계 (전사)",
            "unit": "건",
            "items": _value_counts_ordered(
                df.get('방어진행단계', pd.Series(dtype=object)), order=['방어성공', '진행중', '방어실패'],
            ),
        },
        'cancel_risk_chart': {
            "title": "해지위험도 분포",
            "unit": "건",
            "items": _value_counts_ordered(
                df['해지위험도'] if '해지위험도' in df.columns else pd.Series(dtype=object),
                order=['100%', '80%', '60%', '40%', '20%', '0%'],
            ),
        },
        'cancel_issue_chart': {
            "title": "이슈유형 분포",
            "unit": "건",
            "items": _value_counts_ordered(df.get('이슈유형', pd.Series(dtype=object)), top_n=6),
        },
        'cancel_reason_chart': {
            "title": "해지사유 분포",
            "unit": "건",
            "items": _value_counts_ordered(df.get('해지사유', pd.Series(dtype=object)), top_n=6),
        },
        'cancel_recontract_chart': {
            "title": "재계약여부 분포 (해지파이프라인)",
            "unit": "건",
            "items": _value_counts_ordered(df.get('재계약여부', pd.Series(dtype=object))),
        },
    }

    table_cols = [
        ('관리본부', '본부'), ('관리지사', '지사'), ('상호명', '고객상호'), ('계약번호', '계약번호'),
        ('KTT월정료(조정)', '월정료'), ('해지위험도', '해지위험도'), ('방어진행단계', '방어단계'),
        ('이슈유형', '이슈유형'), ('해지사유', '해지사유'), ('담당자', '담당자'), ('해지예정일', '해지예정일'),
    ]
    available = [(src, label) for src, label in table_cols if src in df.columns]
    view = df[[src for src, _ in available]].copy()
    view.columns = [label for _, label in available]
    if '월정료' in view.columns:
        view['월정료'] = to_numeric_amount(view['월정료']).fillna(0).round(0).astype(int)
    rows = view.to_dict(orient='records')
    for row in rows:
        for k, v in row.items():
            if isinstance(v, float) and math.isnan(v):
                row[k] = None
            elif pd.isna(v):
                row[k] = None

    return {"kpis": kpis, "charts": charts, "table_columns": list(view.columns), "table_rows": rows}


# ---------------------------------------------------------------------------
# 확장: 고액 미등록 해지 알림 -- 해지시설내역(이미 해지 처리된 시설 목록)에는
# 있지만 해지파이프라인(추적 대상) 에는 없는, 월정료 임계값 이상 계약을
# 찾아 파이프라인 등록을 독려한다. 두 업로드가 모두 있을 때만 활성화된다.
# ---------------------------------------------------------------------------

CONTRACT_COL_CANDIDATES = ['계약번호']
AMOUNT_COL_CANDIDATES = ['월정료', 'KTT월정료', 'KTT월정료(조정)', '합산월정료(KTT+KT)', '월정료(조정)', '견적월정료']
NAME_COL_CANDIDATES = ['상호', '상호명', '고객명']
HQ_COL_CANDIDATES = ['관리본부명', '관리본부']
BRANCH_COL_CANDIDATES = ['관리지사명', '관리지사']
CONTRACT_STATUS_COL_CANDIDATES = ['계약상태(중)', '계약상태중', '계약상태']
CANCEL_DATE_COL_CANDIDATES = ['해지일자', '해지일', '해지날짜']
SALES_ZONE_COL_CANDIDATES = ['영업구역번호', '영업구역', '영업구역정보']
FIRST_SERVICE_DATE_COL_CANDIDATES = ['계약최초서비스게시일', '계약최초서비스개시일', '최초서비스개시일', '최초서비스게시일']
SALES_REP_COL_CANDIDATES = ['영업자명', '영업사원명', '영업담당자명', '영업담당자']
CONTRACT_START_COL_CANDIDATES = ['계약시작일', '계약개시일']
CONTRACT_END_COL_CANDIDATES = ['계약종료일', '계약만료일']
LINE_TYPE_COL_CANDIDATES = ['회선방식']
MIN_NUMBER_COL_CANDIDATES = ['MIN번호', 'MIN', 'min번호']


def _fmt_numeric_date(v):
    """YYYYMMDD or YYYYMMDDHHMMSS (however Excel handed it to us -- int,
    float with a trailing .0, or plain string) -> 'YYYY-MM-DD'. Only the
    date part matters here even for the 14-digit form, so both collapse to
    the same first-8-digits parse. Falls back to pd.to_datetime for an
    already-parsed Timestamp or a differently-formatted date string. None
    if nothing usable is found."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime('%Y-%m-%d') if pd.notna(v) else None
    s = str(v).strip()
    if not s or s.lower() in ('nan', 'nat', 'none'):
        return None
    digits = re.sub(r'\D', '', s.split('.')[0])
    if len(digits) >= 8:
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    parsed = pd.to_datetime(v, errors='coerce')
    return parsed.strftime('%Y-%m-%d') if pd.notna(parsed) else None


def find_unregistered_high_value_cancellations(cancelled_facility_df, cancel_df, threshold=100_000):
    """
    cancelled_facility_df: '해지시설내역' upload -- facilities/contracts that
    have already been cancelled/terminated.
    cancel_df: '해지 파이프라인' -- the tracking tool contracts are supposed
    to be registered into before/around cancellation.

    Returns a dict describing high-value (>= threshold) cancelled contracts
    that never made it into the pipeline -- a governance nudge, not a merge.
    None when the facility file hasn't been provided (feature not active yet).
    """
    if cancelled_facility_df is None:
        return None

    contract_col = _first_matching_col(cancelled_facility_df, CONTRACT_COL_CANDIDATES)
    amount_col = _first_matching_col(cancelled_facility_df, AMOUNT_COL_CANDIDATES)
    if not contract_col or not amount_col:
        return {"active": False, "reason": "필요한 컬럼(계약번호/월정료)을 찾을 수 없습니다.", "rows": [], "count": 0}

    df = cancelled_facility_df.copy()
    df['_amount'] = to_numeric_amount(df[amount_col])
    high_value = df[df['_amount'] >= threshold].copy()

    registered_ids = set()
    if cancel_df is not None and '계약번호' in cancel_df.columns:
        registered_ids = set(cancel_df['계약번호'].dropna().tolist())

    unregistered = high_value[~high_value[contract_col].isin(registered_ids)].copy()

    name_col = _first_matching_col(unregistered, NAME_COL_CANDIDATES)
    hq_col = _first_matching_col(unregistered, HQ_COL_CANDIDATES)
    branch_col = _first_matching_col(unregistered, BRANCH_COL_CANDIDATES)
    status_col = _first_matching_col(unregistered, CONTRACT_STATUS_COL_CANDIDATES)
    cancel_date_col = _first_matching_col(unregistered, CANCEL_DATE_COL_CANDIDATES)
    zone_col = _first_matching_col(unregistered, SALES_ZONE_COL_CANDIDATES)
    first_service_col = _first_matching_col(unregistered, FIRST_SERVICE_DATE_COL_CANDIDATES)
    rep_col = _first_matching_col(unregistered, SALES_REP_COL_CANDIDATES)
    start_col = _first_matching_col(unregistered, CONTRACT_START_COL_CANDIDATES)
    end_col = _first_matching_col(unregistered, CONTRACT_END_COL_CANDIDATES)
    line_type_col = _first_matching_col(unregistered, LINE_TYPE_COL_CANDIDATES)
    min_col = _first_matching_col(unregistered, MIN_NUMBER_COL_CANDIDATES)

    # YYYYMMDD(HHMMSS) numeric date columns need reformatting before they can
    # just be renamed+selected like the plain passthrough columns below.
    if cancel_date_col:
        unregistered['_해지일자'] = unregistered[cancel_date_col].apply(_fmt_numeric_date)
    if first_service_col:
        unregistered['_계약최초서비스게시일'] = unregistered[first_service_col].apply(_fmt_numeric_date)
    if start_col:
        unregistered['_계약시작일'] = unregistered[start_col].apply(_fmt_numeric_date)
    if end_col:
        unregistered['_계약종료일'] = unregistered[end_col].apply(_fmt_numeric_date)

    display_cols = {contract_col: '계약번호'}
    if name_col:
        display_cols[name_col] = '고객상호'
    if hq_col:
        display_cols[hq_col] = '본부'
    if branch_col:
        display_cols[branch_col] = '지사'
    display_cols['_amount'] = '월정료'
    if status_col:
        display_cols[status_col] = '계약상태(중)'
    if cancel_date_col:
        display_cols['_해지일자'] = '해지일자'
    if zone_col:
        display_cols[zone_col] = '영업구역번호'
    if first_service_col:
        display_cols['_계약최초서비스게시일'] = '계약최초서비스게시일'
    if rep_col:
        display_cols[rep_col] = '영업자명'
    if start_col:
        display_cols['_계약시작일'] = '계약시작일'
    if end_col:
        display_cols['_계약종료일'] = '계약종료일'
    if line_type_col:
        display_cols[line_type_col] = '회선방식'
    if min_col:
        display_cols[min_col] = 'MIN번호'

    view = unregistered[list(display_cols.keys())].rename(columns=display_cols)
    view['월정료'] = view['월정료'].round(0).astype(int)
    if '본부' in view.columns:
        view['본부'] = view['본부'].apply(normalize_hq)
    if '지사' in view.columns:
        view['지사'] = view['지사'].apply(normalize_branch)
    view = view.sort_values('월정료', ascending=False)

    rows = view.to_dict(orient='records')
    for row in rows:
        for k, v in row.items():
            if pd.isna(v):
                row[k] = None

    status_values = sorted(view['계약상태(중)'].dropna().unique().tolist()) if '계약상태(중)' in view.columns else []

    by_branch = []
    if '지사' in view.columns:
        for name, g in view.groupby('지사'):
            by_branch.append({"지사": str(name), "건수": int(len(g)), "월정료합계": int(g['월정료'].sum())})
        by_branch.sort(key=lambda r: -r['월정료합계'])

    return {
        "active": True,
        "threshold": threshold,
        "total_high_value": int(len(high_value)),
        "count": int(len(unregistered)),
        "amount_sum": float(unregistered['_amount'].sum()) if len(unregistered) else 0.0,
        "rows": rows,
        "columns": list(display_cols.values()),
        "status_values": status_values,
        "by_branch": by_branch,
    }
