import html
import json
import math
import random
import string
import calendar
from datetime import datetime

import pandas as pd

from .analytics import (
    build_dashboard, build_table, build_cancel_dashboard, build_eda_stats,
    find_unregistered_high_value_cancellations, build_progress_matrix, PROGRESS_TYPES,
    build_progress_type_charts, build_branch_insights, build_sp_rep_performance,
    build_sp_pending_contact_list, build_recontract_target_analysis, kakao_map_link,
    _fmt_compact_won,
)
from .handlers import HQ_ORDER, BRANCH_ORDER
from .matching_config import (
    MATCHABLE_FILES, FILE_LABELS, DB_KEY_CANDIDATES, FILE_KEY_CANDIDATES,
    FILE_DISPLAY_COLUMNS, load_matching_config,
)

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

# A second, separate password that unlocks the admin-only matching-config
# panel on top of normal viewing access (entering this instead of the viewer
# password grants both). Keeps 매칭설정 restricted the same way the Streamlit
# app restricts it to the 'admin' role -- see app/main.py render_matching_admin().
DEFAULT_ADMIN_PASSWORD = "0303"


def generate_random_password(length=5):
    """Generates a random English letter password (kept for callers that
    explicitly want a one-off random password instead of the fixed default)."""
    letters = string.ascii_letters
    return ''.join(random.choice(letters) for i in range(length))


def get_end_of_month_iso():
    """Returns the end of the current month in ISO format (YYYY-MM-DD)."""
    now = datetime.now()
    last_day = calendar.monthrange(now.year, now.month)[1]
    return f"{now.year}-{now.month:02d}-{last_day:02d}"


def _e(value):
    """HTML-escapes any value for safe interpolation into markup."""
    return html.escape(str(value), quote=True)


def _fmt_value(value, unit):
    if unit == '원':
        return f"{value:,.0f}원"
    return f"{value:,.0f}{unit}"


# Categorical slots (light / dark) -- from the validated reference palette.
SERIES_SLOTS = ['s1', 's2', 's3', 's4', 's5', 's6', 's7', 's8']

# Fixed label -> role bindings so a status color always means the same thing
# and a category never gets recolored when the underlying counts change.
STATUS_ROLE_MAP = {
    '처리완료': 'good', '방어성공': 'good', '자동재계약': 'good',
    '접수': 'warning', '진행중': 'warning', '수동재계약': 'warning',
    '미접수': 'critical', '방어실패': 'critical', '재계약 없음': 'critical',
    '미상': 'muted', '기타': 'muted',
}


# ---------------------------------------------------------------------------
# Chart rendering (plain HTML/CSS bars -- no chart library dependency)
# ---------------------------------------------------------------------------

def render_stat_tiles(kpis):
    tiles = []
    for k in kpis:
        tiles.append(f"""
        <div class="stat-tile">
            <div class="stat-label">{_e(k['label'])}</div>
            <div class="stat-value">{_e(k['value'])}</div>
            <div class="stat-sub">{_e(k['sub'])}</div>
        </div>""")
    return "".join(tiles)


def _color_role_for(label, index, mode):
    if mode == 'status':
        return f"role-{STATUS_ROLE_MAP.get(label, 'muted')}"
    if label in ('미상', '기타'):
        return "role-muted"
    return f"role-{SERIES_SLOTS[index % len(SERIES_SLOTS)]}"


def render_magnitude_chart(chart, chart_id):
    """Single-hue horizontal bar list -- for comparing magnitudes (one series)."""
    items = [it for it in chart['items'] if it['value'] is not None]
    # A chart with at most one non-zero bar carries no comparison -- the KPI
    # tiles already say the number; skip rather than ship a one-bar chart.
    if len([it for it in items if it['value'] > 0]) < 2:
        return ""
    max_val = max((it['value'] for it in items), default=0) or 1
    unit = chart['unit']

    rows = []
    for it in items:
        pct = max(0.0, min(100.0, it['value'] / max_val * 100))
        rows.append(f"""
        <div class="bar-row" tabindex="0" title="{_e(it['label'])}: {_e(_fmt_value(it['value'], unit))}">
            <span class="bar-row-label">{_e(it['label'])}</span>
            <div class="bar-row-track">
                <div class="bar-row-fill role-s1" style="width:{pct:.2f}%"></div>
            </div>
            <span class="bar-row-value">{_e(_fmt_value(it['value'], unit))}</span>
        </div>""")

    return f"""
    <section class="chart-card" id="{chart_id}">
        <h3 class="chart-title">{_e(chart['title'])}</h3>
        <div class="bar-list">{"".join(rows)}</div>
    </section>"""


def render_stacked_chart(chart, chart_id, mode='categorical'):
    """One segmented bar (part-to-whole) + legend -- for a small set of categories."""
    items = [it for it in chart['items'] if it['value'] is not None]
    total = sum(it['value'] for it in items)
    if not items or total == 0:
        return ""

    segments = []
    legend = []
    for i, it in enumerate(items):
        pct = it['value'] / total * 100
        role = _color_role_for(it['label'], i, mode)
        segments.append(
            f'<div class="stack-seg {role}" style="width:{pct:.2f}%" '
            f'tabindex="0" title="{_e(it["label"])}: {_e(_fmt_value(it["value"], chart["unit"]))} ({pct:.1f}%)"></div>'
        )
        legend.append(f"""
        <div class="legend-item">
            <span class="legend-swatch {role}"></span>
            <span class="legend-label">{_e(it['label'])}</span>
            <span class="legend-value">{_e(_fmt_value(it['value'], chart['unit']))} ({pct:.1f}%)</span>
        </div>""")

    note = chart.get('note')
    note_html = f'<p class="chart-note">{_e(note)}</p>' if note else ""

    return f"""
    <section class="chart-card" id="{chart_id}">
        <h3 class="chart-title">{_e(chart['title'])}</h3>
        {note_html}
        <div class="stack-bar">{"".join(segments)}</div>
        <div class="legend-grid">{"".join(legend)}</div>
    </section>"""


MAIN_CHART_SPECS = [
    ('hq_count_chart', 'magnitude'),
    ('branch_count_chart', 'magnitude'),
    ('branch_amount_chart', 'magnitude'),
    ('activity_type_chart', 'categorical'),
    ('activity_status_chart', 'status'),
    ('recontract_chart', 'categorical'),
    ('voc_type_chart', 'magnitude'),
    ('patrol_result_chart', 'magnitude'),
    ('expiry_chart', 'magnitude'),
]

CANCEL_CHART_SPECS = [
    ('cancel_hq_chart', 'magnitude'),
    ('cancel_defense_chart', 'status'),
    ('cancel_risk_chart', 'magnitude'),
    ('cancel_issue_chart', 'magnitude'),
    ('cancel_reason_chart', 'magnitude'),
    ('cancel_recontract_chart', 'categorical'),
]


def render_chart_grid(dashboard, specs):
    parts = []
    for key, mode in specs:
        chart = dashboard.get(key)
        if not chart or not chart.get('items'):
            continue
        if mode == 'magnitude':
            parts.append(render_magnitude_chart(chart, key))
        else:
            parts.append(render_stacked_chart(chart, key, mode=mode))
    return "".join(parts)


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------

def render_table(columns, rows, filterable=True):
    branch_idx = columns.index('지사') if '지사' in columns else None
    type_idx = columns.index('구분') if '구분' in columns else None

    thead = "".join(f"<th>{_e(c)}</th>" for c in columns)

    body_rows = []
    for r in rows:
        attrs = ""
        if filterable and branch_idx is not None:
            attrs += f' data-branch="{_e(r.get("지사") or "")}"'
        if filterable and type_idx is not None:
            attrs += f' data-type="{_e(r.get("구분") or "")}"'
        cells = []
        for c in columns:
            v = r.get(c)
            if v is None:
                cells.append("<td class=\"cell-empty\">-</td>")
            elif c in ('월정산금액', '월정료'):
                cells.append(f'<td class="cell-num">{v:,.0f}</td>')
            else:
                cells.append(f"<td>{_e(v)}</td>")
        body_rows.append(f"<tr{attrs}>{''.join(cells)}</tr>")

    table_html = f"""
    <div class="table-scroll">
        <table id="dataTable">
            <thead><tr>{thead}</tr></thead>
            <tbody>{"".join(body_rows)}</tbody>
        </table>
    </div>"""

    if not filterable:
        return table_html

    branches = sorted({r.get('지사') for r in rows if r.get('지사')})
    types = sorted({r.get('구분') for r in rows if r.get('구분')})
    branch_options = "".join(f'<option value="{_e(b)}">{_e(b)}</option>' for b in branches)
    type_options = "".join(f'<option value="{_e(t)}">{_e(t)}</option>' for t in types)

    toolbar = f"""
    <div class="table-toolbar">
        <input type="text" id="tableSearch" class="filter-input" placeholder="상호/계약번호/담당자 검색">
        <select id="branchFilter" class="filter-select">
            <option value="">전체 지사</option>
            {branch_options}
        </select>
        <select id="typeFilter" class="filter-select">
            <option value="">전체 구분</option>
            {type_options}
        </select>
        <span id="rowCount" class="row-count"></span>
    </div>"""

    return toolbar + table_html


def render_simple_table(columns, rows):
    thead = "".join(f"<th>{_e(c)}</th>" for c in columns)
    body_rows = []
    for r in rows:
        cells = []
        for c in columns:
            v = r.get(c)
            if v is None:
                cells.append("<td class=\"cell-empty\">-</td>")
            elif c in ('월정료', '월정산금액'):
                cells.append(f'<td class="cell-num">{v:,.0f}</td>')
            else:
                cells.append(f"<td>{_e(v)}</td>")
        body_rows.append(f"<tr>{''.join(cells)}</tr>")
    return f"""
    <div class="table-scroll">
        <table>
            <thead><tr>{thead}</tr></thead>
            <tbody>{"".join(body_rows)}</tbody>
        </table>
    </div>"""


# ---------------------------------------------------------------------------
# 지사 x 활동대상구분(SP/SE/SG) 진척율 매트릭스 -- 총괄DB 원본 컬럼만으로
# 계산되는, 매칭 설정과 무관한 지표. 진척율은 색상 강도(sequential blue)로
# 인코딩한다 -- 값이 클수록 진하게, 값 자체는 셀 안에 항상 직접 라벨링.
# ---------------------------------------------------------------------------

def _progress_cell_style(pct):
    """진척율을 상태색(위험/주의/양호)으로 인코딩한다 -- 값이 높을수록 진해
    지는 단색 그라데이션 대신, 부진 지사(낮은 진척율)가 즉시 눈에 띄도록
    빨간 톤을 쓴다. 부진은 강하게(55%), 양호는 옅게(22%) 칠해서 '나쁜 것'
    쪽으로 시선이 먼저 가게 한 것도 의도적이다."""
    pct = max(0.0, min(100.0, pct))
    if pct < 30:
        var_name, opacity, text_role = '--critical', 55, 'progress-text-light'
    elif pct < 55:
        var_name, opacity, text_role = '--warning', 38, 'progress-text-dark'
    else:
        var_name, opacity, text_role = '--good', 22, 'progress-text-dark'
    return f'background: color-mix(in srgb, var({var_name}) {opacity}%, var(--surface-1));', text_role


def render_progress_matrix(matrix):
    if matrix is None:
        return ""

    # SP is tracked with an explicit 접수/미접수 split (no unlogged rows);
    # SE/SG only ever carry 처리완료 vs unlogged, so they get the simpler
    # 처리완료/미처리 pair. 전체 rolls back up to that simpler pair too.
    group_header = "<th rowspan=\"2\">지사</th>"
    group_header += '<th colspan="5" class="progress-group-th">SP</th>'
    for t in ['SE', 'SG']:
        group_header += f'<th colspan="4" class="progress-group-th">{_e(t)}</th>'
    group_header += '<th colspan="5" class="progress-group-th">전체</th>'

    sub_header = "<th>처리완료</th><th>접수</th><th>미접수</th><th>계</th><th>진척율</th>"
    for _ in ['SE', 'SG']:
        sub_header += "<th>처리완료</th><th>미처리</th><th>계</th><th>진척율</th>"
    sub_header += "<th>처리완료</th><th>미처리</th><th>계</th><th>진척율</th><th>순위</th>"

    def render_row(row, is_total=False):
        cells = [f'<td class="progress-branch{" progress-total-label" if is_total else ""}">{_e(row["지사"])}</td>']

        sp = row['SP']
        style, text_role = _progress_cell_style(sp['진척율'])
        cells.append(f'<td class="cell-num">{sp["처리완료"]:,}</td>')
        cells.append(f'<td class="cell-num">{sp["접수"]:,}</td>')
        cells.append(f'<td class="cell-num">{sp["미접수"]:,}</td>')
        cells.append(f'<td class="cell-num">{sp["계"]:,}</td>')
        cells.append(f'<td class="cell-num progress-cell {text_role}" style="{style}">{sp["진척율"]:.1f}%</td>')

        for t in ['SE', 'SG', '전체']:
            cell = row[t]
            style, text_role = _progress_cell_style(cell['진척율'])
            cells.append(f'<td class="cell-num">{cell["처리완료"]:,}</td>')
            cells.append(f'<td class="cell-num">{cell["미처리"]:,}</td>')
            cells.append(f'<td class="cell-num">{cell["계"]:,}</td>')
            cells.append(
                f'<td class="cell-num progress-cell {text_role}" style="{style}">{cell["진척율"]:.1f}%</td>'
            )
        if not is_total:
            cells.append(f'<td class="cell-num progress-rank">{row["순위"]}</td>')
        else:
            cells.append('<td class="cell-num">-</td>')
        row_attrs = ' class="progress-total-row"' if is_total else ''
        return f'<tr{row_attrs}>{"".join(cells)}</tr>'

    body_rows = [render_row(r) for r in matrix['branch_rows']]
    body_rows.append(render_row(matrix['total_row'], is_total=True))

    type_summary = " · ".join(f"{t} {matrix['type_totals'][t]:,}건" for t in PROGRESS_TYPES)

    return f"""
    <p class="section-desc">활동대상구분 기준 이번 달 대상 건수 -- {type_summary}</p>
    <div class="table-scroll progress-table-scroll">
        <table class="progress-table">
            <thead>
                <tr>{group_header}</tr>
                <tr>{sub_header}</tr>
            </thead>
            <tbody>{"".join(body_rows)}</tbody>
        </table>
    </div>"""


PROGRESS_SERIES_ROLE = {'SP': 'role-s3', 'SE': 'role-s2', 'SG': 'role-s1'}


def render_progress_bar_chart(matrix):
    if matrix is None or not matrix['branch_rows']:
        return ""

    legend = "".join(
        f'<div class="legend-item"><span class="legend-swatch {PROGRESS_SERIES_ROLE[t]}"></span>'
        f'<span class="legend-label">{_e(t)}</span></div>'
        for t in PROGRESS_TYPES
    )

    groups = []
    for row in matrix['branch_rows']:
        bars = ""
        for t in PROGRESS_TYPES:
            pct = row[t]['진척율']
            bars += (
                f'<div class="progress-chart-bar {PROGRESS_SERIES_ROLE[t]}" style="height:{max(pct,1):.1f}%" '
                f'tabindex="0" title="{_e(row["지사"])} {_e(t)}: {pct:.1f}%"></div>'
            )
        groups.append(f"""
        <div class="progress-chart-group">
            <div class="progress-chart-bars">{bars}</div>
            <span class="progress-chart-label">{_e(row['지사'])}</span>
        </div>""")

    return f"""
    <div class="legend-grid progress-chart-legend">{legend}</div>
    <div class="progress-chart">{"".join(groups)}</div>"""


def render_progress_type_chart(chart, role, chart_id):
    """One SP/SE/SG chart-card: branches ranked by 진척율 descending -- reads
    like a leaderboard, easier to scan than picking one column out of the
    14-column combined table below."""
    items = chart['items']
    if not items:
        return ""
    rows = []
    for it in items:
        pct = max(0.0, min(100.0, it['value']))
        rows.append(f"""
        <div class="bar-row" tabindex="0" title="{_e(it['label'])}: {it['value']:.1f}% ({_e(it['sub'])})">
            <span class="bar-row-label">{_e(it['label'])}</span>
            <div class="bar-row-track">
                <div class="bar-row-fill {role}" style="width:{pct:.2f}%"></div>
            </div>
            <span class="bar-row-value">{it['value']:.1f}% <span class="chart-note" style="display:inline;margin:0;">({_e(it['sub'])})</span></span>
        </div>""")
    return f"""
    <section class="chart-card" id="{chart_id}">
        <h3 class="chart-title">{_e(chart['title'])}</h3>
        <div class="bar-list">{"".join(rows)}</div>
    </section>"""


def _bar_row_html(it, role):
    pct = max(0.0, min(100.0, it['value']))
    return f"""
    <div class="bar-row" tabindex="0" title="{_e(it['label'])}: {it['value']:.1f}% ({_e(it['sub'])})">
        <span class="bar-row-label">{_e(it['label'])}</span>
        <div class="bar-row-track">
            <div class="bar-row-fill {role}" style="width:{pct:.2f}%"></div>
        </div>
        <span class="bar-row-value">{it['value']:.1f}% <span class="chart-note" style="display:inline;margin:0;">({_e(it['sub'])})</span></span>
    </div>"""


def render_grouped_bar_chart(title, groups, role, chart_id):
    """Like render_progress_type_chart, but items arrive pre-grouped (e.g. by
    지사) with a header row between groups -- shows a 지사 -> 담당자
    hierarchy inside one scrollable chart-card instead of a flat list."""
    groups = [g for g in groups if g['items']]
    if not groups:
        return ""
    parts = []
    for g in groups:
        parts.append(
            f'<div class="bar-group-header"><span>{_e(g["label"])}</span>'
            f'<span class="bar-group-count">{len(g["items"])}명</span></div>'
        )
        parts.extend(_bar_row_html(it, role) for it in g['items'])
    return f"""
    <section class="chart-card" id="{chart_id}">
        <h3 class="chart-title">{_e(title)}</h3>
        <div class="bar-list">{"".join(parts)}</div>
    </section>"""


def render_progress_type_dashboard(charts):
    """SP/SE/SG 담당유형별 대시보드 -- 세 개의 독립된 리더보드 카드로 구성."""
    if charts is None:
        return ""
    parts = [render_progress_type_chart(charts[t], PROGRESS_SERIES_ROLE[t], f'progressType{t}Chart') for t in PROGRESS_TYPES]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    return f'<div class="chart-grid">{"".join(parts)}</div>'


def render_branch_insights(insights, section_id="progressInsight"):
    """지사별 분석리포트 요약 -- 최고/최저(전체) + 유형별 최저 지사를 짧은
    콜아웃 카드로. 숫자 표를 다시 읽지 않아도 핵심을 바로 파악하게 한다."""
    if insights is None:
        return f'<div class="empty-card" id="{section_id}">진척율을 계산할 데이터가 없습니다.</div>'

    def card(label, d, css_cls):
        return f"""
        <div class="callout {css_cls}">
            <span class="insight-label">{_e(label)}</span>
            <span class="insight-value"><strong>{_e(d['지사'])}</strong> -- {d['진척율']:.1f}% ({d['처리완료']:,}/{d['계']:,}건)</span>
        </div>"""

    cards = [
        card("전체 진척율 최고 지사", insights['best'], 'callout-good'),
        card("전체 진척율 최저 지사 (집중관리 필요)", insights['worst'], 'callout-warning'),
    ]
    type_labels = {'SP': 'SP 진척율 최저 지사', 'SE': 'SE 진척율 최저 지사', 'SG': 'SG 진척율 최저 지사'}
    for t in PROGRESS_TYPES:
        if t in insights['type_worst']:
            cards.append(card(type_labels[t], insights['type_worst'][t], 'callout-warning'))

    return f"""
    <p class="section-desc" id="{section_id}Note">전사 평균 진척율 {insights['avg_pct']:.1f}% 기준 요약입니다.</p>
    <div class="insight-grid" id="{section_id}">{"".join(cards)}</div>"""


def render_sp_rep_section(sp_perf, section_id="spRepSection"):
    """SP 부진자 추가분석 -- 지사가 아닌 총괄DB 'SP담당' 컬럼(개인 담당자)
    기준. 표본이 작은 담당자(min_count 미만)를 부진자 판정에서 제외하는
    이유는 build_sp_rep_performance()의 docstring 참고."""
    if sp_perf is None:
        return ""
    if not sp_perf['reps']:
        return f'<div class="empty-card" id="{section_id}">SP 활동 데이터가 없어 담당자별 분석을 표시할 수 없습니다.</div>'

    groups = []
    for r in sp_perf['chart_reps']:
        label = r['지사'] or '미상'
        if not groups or groups[-1]['label'] != label:
            groups.append({"label": label, "items": []})
        groups[-1]['items'].append({
            "label": r['담당자'],
            "value": r['진척율'],
            "sub": f"{r['처리완료']:,}/{r['계']:,}건" + (f" · {r['영업구역']}" if r['영업구역'] else ""),
        })
    if not groups:
        chart_html = '<div class="empty-card">100% 달성자를 제외하면 표시할 담당자가 없습니다 (강북/강원본부 SP 전원 100% 달성).</div>'
    else:
        chart_html = render_grouped_bar_chart(
            "SP 담당자별 진척율 (강북/강원본부, 100% 달성자 제외, 지사·담당자순)",
            groups, PROGRESS_SERIES_ROLE['SP'], f'{section_id}Chart',
        )

    note = (
        f"SP 담당자 전체 평균 진척율 {sp_perf['avg_pct']:.1f}% 기준, "
        f"최소 {sp_perf['min_count']}건 이상 처리한 담당자 중 평균 미달 인원은 "
        f"<strong>{sp_perf['underperformer_count']}명</strong>입니다."
    )

    if not sp_perf['underperformers']:
        table_html = '<div class="empty-card">평균 미달 담당자가 없습니다.</div>'
    else:
        columns = ['담당자', '지사', '영업구역', '처리완료', '접수', '미접수', '계', '진척율']
        rows = [
            {
                '담당자': r['담당자'], '지사': r['지사'] or '-', '영업구역': r['영업구역'] or '-',
                '처리완료': f"{r['처리완료']:,}", '접수': f"{r['접수']:,}",
                '미접수': f"{r['미접수']:,}", '계': f"{r['계']:,}",
                '진척율': f"{r['진척율']:.1f}%",
            }
            for r in sp_perf['underperformers']
        ]
        table_html = render_simple_table(columns, rows)

    return f"""
    <p class="section-desc">{note}</p>
    <div class="chart-grid" id="{section_id}ChartWrap">{chart_html}</div>
    <div class="table-section" id="{section_id}TableWrap">{table_html}</div>"""


def _pending_rep_card_html(rep, idx, section_id):
    text_id = f'{section_id}Text{idx}'
    rows = []
    for it in rep['items']:
        status_cls = 'status-' + it['상태'] if it['상태'] else ''
        amount_str = f"{it['월정료']:,.0f}원" if it['월정료'] is not None else '-'
        map_link = (
            f'<a class="pending-map-link" href="{_e(it["지도링크"])}" target="_blank" rel="noopener">🗺 지도</a>'
            if it.get('지도링크') else '<span class="pending-map-link disabled">🗺 지도</span>'
        )
        rows.append(f"""
        <div class="pending-detail-row">
            <span class="pending-status {status_cls}">{_e(it['상태'])}</span>
            <span>{_e(it['계약번호'] if it['계약번호'] is not None else '-')} · {_e(it['상호'] or '-')}</span>
            <span class="cell-num">{_e(amount_str)}</span>
            {map_link}
        </div>""")

    return f"""
    <div class="pending-rep-card">
        <div class="pending-rep-header">
            <span class="pending-rep-title">{_e(rep['담당자'])}</span>
            <span class="pending-rep-counts">미접수 {rep['미접수_count']:,} · 접수 {rep['접수_count']:,} · 계 {rep['count']:,}건</span>
            <button class="ghost-btn small" type="button" onclick="copyPendingText('{text_id}', this)">📋 복사 (메일/문자용)</button>
        </div>
        <pre class="copy-block" id="{text_id}">{_e(rep['text'])}</pre>
        <div class="pending-detail-list">{"".join(rows)}</div>
    </div>"""


def render_sp_pending_section(pending, section_id="spPendingSection"):
    """미접수/접수 상태인 SP 건을 담당자별로 묶어 메일/문자 발송용 텍스트
    (계약번호/상호/월정료)를 만든다. 설치주소가 있으면 카카오맵 검색 링크도
    붙여 담당자가 바로 찾아갈 수 있게 한다 (지도 API 키 없이 동작 -- 위젯을
    임베드하는 대신 카카오맵 자체 검색으로 넘기는 방식)."""
    if pending is None:
        return f'<div class="empty-card" id="{section_id}">미접수/접수 상태인 SP 건이 없습니다 (강북/강원본부 기준).</div>'

    cards = [_pending_rep_card_html(rep, i, section_id) for i, rep in enumerate(pending['reps'])]
    return f"""
    <p class="section-desc" id="{section_id}Note">
        강북/강원본부 SP 활동 중 미접수/접수 상태인 <strong>{pending['total_count']:,}건</strong>을 담당자별로 정리했습니다.
        각 담당자 카드의 '복사' 버튼을 누르면 계약번호/상호/월정료 목록이 클립보드에 복사되어 메일이나 문자에 바로 붙여넣을 수 있습니다.
    </p>
    <div id="{section_id}Cards">{"".join(cards)}</div>"""


def _recontract_detail_row_html(r):
    badge_cls = 'recontract-badge-achieved' if r['구분'] == '실적' else 'recontract-badge-focus'
    badge_label = '실적' if r['구분'] == '실적' else '집중'
    status_bits = [b for b in [r.get('계약상태'), r.get('만기도래월')] if b and not (isinstance(b, float) and math.isnan(b))]
    map_link_url = kakao_map_link(r.get('설치주소'))
    map_link = (
        f'<a class="pending-map-link" href="{_e(map_link_url)}" target="_blank" rel="noopener">🗺 지도</a>'
        if map_link_url else '<span class="pending-map-link disabled">🗺 지도</span>'
    )
    return f"""
    <div class="pending-detail-row">
        <span class="recontract-badge {badge_cls}">{_e(badge_label)}</span>
        <span>{_e(r['계약번호'] if r['계약번호'] is not None else '-')} · {_e(r['상호'] or '-')} ({_e(' · '.join(str(b) for b in status_bits) or '-')})</span>
        <span class="cell-num">{_e(r.get('재계약여부') or '-')}</span>
        {map_link}
    </div>"""


def render_recontract_section(analysis, section_id="recontractSection"):
    """재계약대상(SP) 분석 -- analytics.py build_recontract_target_analysis()
    결과를 KPI + 지사별/담당별 리더보드 + 담당자별 상세 리스트(지도 링크 포함)
    로 렌더링한다."""
    if analysis is None:
        return f'<div class="empty-card" id="{section_id}">재계약대상(SP)으로 분류할 데이터가 없습니다 (총괄DB와 관리고객원본이 매칭된 SP 건이 없음).</div>'

    stat_html = render_stat_tiles(analysis['kpis'])

    branch_items = [
        {"label": r['label'], "value": r['pct'], "sub": f"{r['achieved']:,}/{r['total']:,}건"}
        for r in analysis['by_branch']
    ]
    branch_chart_html = render_progress_type_chart(
        {"title": "지사별 재계약 실적율", "items": branch_items}, PROGRESS_SERIES_ROLE['SP'], f'{section_id}BranchChart',
    ) if branch_items else '<div class="empty-card">지사 정보가 없습니다.</div>'

    owner_items_sorted = sorted(analysis['by_owner'], key=lambda r: r['pct'])
    owner_items = [
        {"label": r['label'], "value": r['pct'], "sub": f"{r['achieved']:,}/{r['total']:,}건"}
        for r in owner_items_sorted
    ]
    owner_chart_html = render_progress_type_chart(
        {"title": "담당자별 재계약 실적율 (집중 필요 우선)", "items": owner_items}, PROGRESS_SERIES_ROLE['SP'], f'{section_id}OwnerChart',
    ) if owner_items else '<div class="empty-card">담당자 정보가 없습니다.</div>'

    groups = []
    for r in analysis['detail_rows']:
        owner = r['담당자']
        if not groups or groups[-1]['owner'] != owner:
            groups.append({"owner": owner, "rows": []})
        groups[-1]['rows'].append(r)

    if not groups:
        list_html = '<div class="empty-card">표시할 재계약대상 건이 없습니다.</div>'
    else:
        cards = []
        for g in groups:
            achieved_n = sum(1 for r in g['rows'] if r['구분'] == '실적')
            focus_n = len(g['rows']) - achieved_n
            rows_html = "".join(_recontract_detail_row_html(r) for r in g['rows'])
            cards.append(f"""
            <div class="pending-rep-card">
                <div class="pending-rep-header">
                    <span class="pending-rep-title">{_e(g['owner'])}</span>
                    <span class="pending-rep-counts">실적 {achieved_n:,} · 집중대상 {focus_n:,} · 계 {len(g['rows']):,}건</span>
                </div>
                <div class="pending-detail-list">{rows_html}</div>
            </div>""")
        list_html = "".join(cards)

    return f"""
    <p class="section-desc" id="{section_id}Note">
        총괄DB(SP)와 관리고객원본을 계약번호로 매칭해 계약상태·만기도래월이 확인된 <strong>{analysis['total']:,}건</strong>을 재계약대상으로 삼았습니다.
        이 중 수동재계약이면서 서비스재개시일이 {analysis['current_year']}년인 <strong>{analysis['achieved_count']:,}건</strong>은 실적,
        나머지 <strong>{analysis['focus_count']:,}건</strong>은 집중 재계약 활동 대상입니다.
    </p>
    <div class="stat-grid" id="{section_id}Stats">{stat_html}</div>
    <div class="chart-grid" id="{section_id}Charts">{branch_chart_html}{owner_chart_html}</div>
    <div id="{section_id}List">{list_html}</div>"""


# ---------------------------------------------------------------------------
# Global filter bar (본부/지사/담당자) -- scopes the KPI tiles, chart grid,
# progress matrix and detail table below it (never the independent 해지
# 파이프라인 / 넛지 sections). Filtering is applied entirely client-side by
# the JS engine in APP_SCRIPT_TEMPLATE, on top of whatever the matching
# admin panel currently produces -- see 'renderAll()' there.
# ---------------------------------------------------------------------------

def render_global_filter_bar(df):
    if df is None or df.empty:
        return ""

    hq_col = '관리본부' if '관리본부' in df.columns else '본부'
    hq_present = set(df[hq_col].dropna().unique()) if hq_col in df.columns else set()
    hqs = [h for h in HQ_ORDER if h in hq_present] + sorted(h for h in hq_present if h not in HQ_ORDER)

    branch_col = '관리지사' if '관리지사' in df.columns else '지사'
    branch_present = set(df[branch_col].dropna().unique()) if branch_col in df.columns else set()
    branches = [b for b in BRANCH_ORDER if b in branch_present] + sorted(b for b in branch_present if b not in BRANCH_ORDER)

    activity_col = '활동대상구분'
    activity_present = set(df[activity_col].dropna().unique()) if activity_col in df.columns else set()
    ACTIVITY_ORDER = ['SP', 'SE', 'SG']
    activities = [a for a in ACTIVITY_ORDER if a in activity_present] + sorted(a for a in activity_present if a not in ACTIVITY_ORDER)

    owners = sorted(df['SP담당'].dropna().unique().tolist()) if 'SP담당' in df.columns else []

    def pills(dim, values):
        buttons = [f'<button type="button" class="filter-pill active" data-dim="{dim}" data-value="">전체</button>']
        for v in values:
            buttons.append(f'<button type="button" class="filter-pill" data-dim="{dim}" data-value="{_e(v)}">{_e(v)}</button>')
        return "".join(buttons)

    hq_group = f"""
        <div class="filter-group">
            <span class="filter-group-label">본부</span>
            <div class="filter-pill-row" id="hqFilterRow">{pills('hq', hqs)}</div>
        </div>""" if hqs else ""

    branch_group = f"""
        <div class="filter-group">
            <span class="filter-group-label">지사</span>
            <div class="filter-pill-row" id="branchFilterRow">{pills('branch', branches)}</div>
        </div>""" if branches else ""

    activity_group = f"""
        <div class="filter-group">
            <span class="filter-group-label">대상구분</span>
            <div class="filter-pill-row" id="activityFilterRow">{pills('activity', activities)}</div>
        </div>""" if activities else ""

    owner_options = "".join(f'<option value="{_e(o)}">{_e(o)}</option>' for o in owners)
    owner_group = f"""
        <div class="filter-group">
            <span class="filter-group-label">담당자</span>
            <select id="ownerFilterSelect" class="filter-select">
                <option value="">전체 담당자</option>
                {owner_options}
            </select>
        </div>""" if owners else ""

    return f"""
    <div class="global-filter-bar" id="globalFilterBar">
        {hq_group}
        {branch_group}
        {activity_group}
        {owner_group}
        <span class="filter-summary" id="globalFilterSummary"></span>
    </div>"""


# ---------------------------------------------------------------------------
# 해지 파이프라인 -- independent section (own KPIs/charts/table, never
# joined onto the 총괄DB-anchored dashboard above).
# ---------------------------------------------------------------------------

def render_cancel_section(cancel_df, hq_filter='강북/강원'):
    if cancel_df is None or cancel_df.empty:
        return f"""
        <details class="section-collapse" open><summary class="section-title">해지 파이프라인 현황 <span class="badge">독립 데이터 · 강북/강원본부 기준</span></summary>
        <div class="empty-card">해지 파이프라인 파일이 업로드되지 않았습니다. 업로드 시 총괄DB와 무관하게 별도 섹션으로 표시됩니다.</div>
        </details>"""

    dash = build_cancel_dashboard(cancel_df, hq_filter=hq_filter)
    if dash is None:
        return f"""
        <details class="section-collapse" open><summary class="section-title">해지 파이프라인 현황 <span class="badge">독립 데이터 · 강북/강원본부 기준</span></summary>
        <div class="empty-card">해지 파이프라인 데이터 중 강북/강원본부 소속 시설이 없습니다.</div>
        </details>"""

    stat_html = render_stat_tiles(dash['kpis'])
    chart_html = render_chart_grid(dash['charts'], CANCEL_CHART_SPECS)
    table_html = render_simple_table(dash['table_columns'], dash['table_rows'])

    return f"""
    <details class="section-collapse" open><summary class="section-title">해지 파이프라인 현황 <span class="badge">독립 데이터 · 강북/강원본부 기준</span></summary>
    <p class="section-desc">총괄DB와 매칭하지 않고 원본 그대로 집계한, 강북/강원본부 소속 해지 파이프라인 현황입니다.</p>
    <div class="stat-grid">{stat_html}</div>
    <div class="chart-grid">{chart_html}</div>
    <div class="table-section">{table_html}</div>
    </details>"""


# ---------------------------------------------------------------------------
# 확장: 고액 미등록 해지 알림 -- '해지시설내역' 업로드가 있을 때만 활성화.
# ---------------------------------------------------------------------------

def render_nudge_section(cancelled_facility_df, cancel_df, threshold=100_000):
    nudge = find_unregistered_high_value_cancellations(cancelled_facility_df, cancel_df, threshold=threshold)

    if nudge is None:
        return f"""
        <details class="section-collapse" open><summary class="section-title">고액 해지 미등록 알림 <span class="badge badge-soon">확장 예정</span></summary>
        <div class="empty-card">
            '해지시설 내역' 파일을 업로드하면, 월정료 {threshold:,}원 이상인데 해지 파이프라인에
            등록되지 않은 계약을 자동으로 찾아 등록을 독려하는 알림이 여기에 표시됩니다.
        </div>
        </details>"""

    if not nudge.get('active'):
        return f"""
        <details class="section-collapse" open><summary class="section-title">고액 해지 미등록 알림</summary>
        <div class="empty-card">{_e(nudge.get('reason', '컬럼을 확인할 수 없습니다.'))}</div>
        </details>"""

    if nudge['count'] == 0:
        return f"""
        <details class="section-collapse" open><summary class="section-title">고액 해지 미등록 알림</summary>
        <div class="empty-card">
            월정료 {threshold:,}원 이상 해지 건 {nudge['total_high_value']:,}건 모두 해지 파이프라인에 등록되어 있습니다.
        </div>
        </details>"""

    columns = nudge.get('columns') or ['계약번호', '고객상호', '본부', '지사', '월정료']
    table_html = _nudge_table_html(columns, nudge['rows'])
    filter_html = _nudge_status_filter_html(nudge.get('status_values') or [])
    branch_summary_html = _nudge_branch_summary_html(nudge.get('by_branch') or [])
    charts_html = _nudge_charts_html(nudge.get('by_branch') or [], nudge['rows'])

    return f"""
    <details class="section-collapse" open><summary class="section-title">고액 해지 미등록 알림</summary>
    <div class="callout callout-warning">
        월정료 {threshold:,}원 이상 해지 건 {nudge['total_high_value']:,}건 중
        <strong>{nudge['count']:,}건({_fmt_won(nudge['amount_sum'])})이 해지 파이프라인에 미등록</strong> 상태입니다.
        아래 목록을 해지 파이프라인에 등록하도록 담당자에게 독려하세요.
    </div>
    {charts_html}
    {filter_html}
    <div class="stat-grid" id="nudgeBranchSummary">{branch_summary_html}</div>
    <div class="table-section">{table_html}</div>
    </details>"""


def _fmt_won(v):
    return f"{v:,.0f}원"


def _nudge_table_html(columns, rows):
    """Like render_simple_table, but each <tr> also carries data-status/
    data-branch/data-amount so applyNudgeFilter() (vanilla JS, no server
    round-trip) can filter by 계약상태(중) and recompute the 지사별 요약
    tiles above it without needing this section wired into the big
    filter/recompute engine the rest of the report uses."""
    thead = "".join(f"<th>{_e(c)}</th>" for c in columns)
    body_rows = []
    for r in rows:
        status = r.get('계약상태(중)') or ''
        branch = r.get('지사') or ''
        amount = r.get('월정료')
        attrs = f' data-status="{_e(status)}" data-branch="{_e(branch)}" data-amount="{amount if amount is not None else 0}"'
        cells = []
        for c in columns:
            v = r.get(c)
            if v is None:
                cells.append('<td class="cell-empty">-</td>')
            elif c == '월정료':
                cells.append(f'<td class="cell-num">{v:,.0f}</td>')
            else:
                cells.append(f"<td>{_e(v)}</td>")
        body_rows.append(f"<tr{attrs}>{''.join(cells)}</tr>")
    return f"""
    <div class="table-scroll">
        <table id="nudgeTable">
            <thead><tr>{thead}</tr></thead>
            <tbody>{"".join(body_rows)}</tbody>
        </table>
    </div>"""


def _nudge_status_filter_html(status_values):
    if not status_values:
        return ""
    buttons = ['<button class="filter-pill active" type="button" data-value="">전체</button>']
    for v in status_values:
        buttons.append(f'<button class="filter-pill" type="button" data-value="{_e(v)}">{_e(v)}</button>')
    return f'<div class="filter-pill-row" id="nudgeStatusFilterRow">{"".join(buttons)}</div>'


def _nudge_branch_summary_html(by_branch):
    tiles = []
    for r in by_branch:
        tiles.append(f"""
        <div class="stat-tile">
            <div class="stat-label">{_e(r['지사'])}</div>
            <div class="stat-value">{r['건수']:,}건</div>
            <div class="stat-sub">{r['월정료합계']:,}원</div>
        </div>""")
    return "".join(tiles)


def _nudge_charts_html(by_branch, rows, section_id="nudgeSection"):
    """지사별 건수/금액 타일 옆에 실제 차트 두 개를 더해 한눈에 훑을 수
    있게 한다 -- 지사별 미등록 금액(크기 비교) + 계약상태(중) 분포(구성비)."""
    parts = []
    if by_branch:
        branch_chart = {
            "title": "지사별 미등록 월정료 합계",
            "unit": "원",
            "items": [{"label": r['지사'], "value": r['월정료합계']} for r in by_branch],
        }
        chart_html = render_magnitude_chart(branch_chart, f'{section_id}BranchChart')
        if chart_html:
            parts.append(chart_html)

    status_counts = {}
    for r in rows:
        s = r.get('계약상태(중)')
        if s:
            status_counts[s] = status_counts.get(s, 0) + 1
    if status_counts:
        status_items = sorted(status_counts.items(), key=lambda kv: -kv[1])
        status_chart = {
            "title": "계약상태(중) 분포",
            "unit": "건",
            "items": [{"label": k, "value": v} for k, v in status_items],
        }
        chart_html = render_stacked_chart(status_chart, f'{section_id}StatusChart', mode='categorical')
        if chart_html:
            parts.append(chart_html)

    if not parts:
        return ""
    return f'<div class="chart-grid">{"".join(parts)}</div>'


def render_eda_section_body(eda, section_id="edaSection"):
    """The recomputable part of the EDA section (everything but the static
    <h2> title) -- lives inside #edaSectionWrap so renderEdaSectionEl() in
    APP_SCRIPT_TEMPLATE can replace just this on filter/matching-config
    changes, mirroring this function 1:1."""
    if eda is None:
        return f'<div class="empty-card" id="{section_id}">월정산금액 데이터가 충분하지 않아 분포/이상치 분석을 표시할 수 없습니다.</div>'

    s = eda['stats']
    stat_tiles = render_stat_tiles([
        {"label": "분석 대상 계약", "value": f"{s['count']:,}", "sub": "건 (0원 제외)"},
        {"label": "평균", "value": _fmt_compact_won(s['mean']), "sub": f"{s['mean']:,.0f}원"},
        {"label": "중앙값", "value": _fmt_compact_won(s['median']), "sub": f"{s['median']:,.0f}원"},
        {"label": "표준편차", "value": _fmt_compact_won(s['std']), "sub": f"{s['std']:,.0f}원"},
        {"label": "IQR 이상치", "value": f"{eda['outlier_count']:,}", "sub": "건"},
    ])
    dist_chart_html = render_magnitude_chart(eda['dist_chart'], f'{section_id}DistChart')

    bounds_note = (
        f"IQR(사분위범위) 기준: Q1={s['q1']:,.0f}원, Q3={s['q3']:,.0f}원 -- "
        f"{s['upper_bound']:,.0f}원 초과는 고액 이상치, {s['lower_bound']:,.0f}원 미만(0원 제외)은 저액 이상치로 분류합니다."
    )

    if eda['outlier_count'] == 0:
        outlier_html = '<div class="empty-card">IQR 기준 이상치로 분류된 계약이 없습니다.</div>'
    else:
        table_html = render_simple_table(eda['outlier_columns'], eda['outlier_rows'])
        shown = len(eda['outlier_rows'])
        more_note = f" (심각도순 상위 {shown:,}건 표시, 전체 {eda['outlier_count']:,}건 중)" if shown < eda['outlier_count'] else ""
        outlier_html = f'<p class="chart-note">이상치 목록{more_note}</p><div class="table-section">{table_html}</div>'

    return f"""
    <p class="section-desc">{_e(bounds_note)}</p>
    <div class="stat-grid" id="{section_id}">{stat_tiles}</div>
    <div class="chart-grid">{dist_chart_html}</div>
    {outlier_html}"""


# ---------------------------------------------------------------------------
# Client-side matching admin -- embeds a trimmed, JOIN-ready slice of the raw
# files so the checkbox condition builder can re-run the composite-key match
# and redraw the 총괄DB dashboard entirely in the browser, no server needed.
# ---------------------------------------------------------------------------

def _json_safe(value):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime('%Y-%m-%dT%H:%M:%S')
    if hasattr(value, 'item'):
        return value.item()
    return value


def _table_payload(df, columns):
    cols = [c for c in columns if df is not None and c in df.columns]
    if df is None or not cols:
        return {"columns": [], "rows": []}
    sub = df[cols]
    rows = [[_json_safe(v) for v in row] for row in sub.itertuples(index=False, name=None)]
    return {"columns": cols, "rows": rows}


def _prefilter_to_possible_matches(file_df, file_key_cols, db_key_values):
    """Keeps only rows that COULD match some DB row under some candidate key
    (regardless of which condition the admin ends up choosing) -- shrinks a
    51k-row file down to a few thousand before it's embedded in the HTML."""
    if file_df is None:
        return None
    mask = pd.Series(False, index=file_df.index)
    hit_any = False
    for col in file_key_cols:
        if col in file_df.columns:
            mask = mask | file_df[col].isin(db_key_values)
            hit_any = True
    if not hit_any:
        return file_df.iloc[0:0]
    return file_df[mask]


def build_embedded_data(raw_files, matching_config):
    db_df = raw_files.get('db') if raw_files else None
    if db_df is None:
        return None

    db_key_values = set()
    for col in DB_KEY_CANDIDATES:
        if col in db_df.columns:
            db_key_values |= set(db_df[col].dropna().tolist())

    files_payload = {}
    for key in MATCHABLE_FILES:
        fdf = raw_files.get(key)
        cols = list(dict.fromkeys(FILE_KEY_CANDIDATES[key] + FILE_DISPLAY_COLUMNS[key]))
        if fdf is not None:
            fdf = _prefilter_to_possible_matches(fdf, FILE_KEY_CANDIDATES[key], db_key_values)
        files_payload[key] = _table_payload(fdf, cols)

    return {
        "db": _table_payload(db_df, list(db_df.columns)),
        "files": files_payload,
        "matchingConfig": matching_config,
        "fileLabels": FILE_LABELS,
        "dbKeyCandidates": DB_KEY_CANDIDATES,
        "fileKeyCandidates": FILE_KEY_CANDIDATES,
        "displayColumns": FILE_DISPLAY_COLUMNS,
        "hqOrder": HQ_ORDER,
        "branchOrder": BRANCH_ORDER,
    }


def render_admin_panel_shell():
    """Wrapped in #adminOnlyWrap, hidden by default (see CSS + checkPassword()
    in APP_SCRIPT_TEMPLATE) -- only visible after unlocking with the admin
    password (DEFAULT_ADMIN_PASSWORD), not the regular viewer password."""
    return """
    <div id="adminOnlyWrap" style="display:none">
    <details class="admin-details">
        <summary>🛠 관리자: 컬럼 매칭 설정 (다중조건, 브라우저에서 즉시 재계산)</summary>
        <div class="admin-body">
            <p class="section-desc">
                체크된 조건이 2개 이상이면 모두 동시에 일치해야 매칭됩니다 (AND 복합키).
                조건을 바꾸고 <strong>적용</strong>을 누르면 위 대시보드 전체가 이 브라우저 안에서 다시 계산됩니다.
                서버로 아무것도 전송되지 않습니다.
            </p>
            <div id="matchingAdminPanel"></div>
            <div class="admin-toolbar">
                <button id="applyMatchBtn" class="primary-btn" type="button">적용 (재계산)</button>
                <button id="resetMatchBtn" class="ghost-btn" type="button">기본값으로 초기화</button>
                <button id="exportMatchBtn" class="ghost-btn" type="button">설정 내보내기 (JSON)</button>
                <label class="ghost-btn file-btn">
                    설정 가져오기 (JSON)
                    <input type="file" id="importMatchInput" accept="application/json" hidden>
                </label>
                <span id="matchSummary" class="match-summary"></span>
            </div>
            <div class="admin-log">
                <h4 class="admin-log-title">변경 이력 (이 세션 동안)</h4>
                <ul id="changeLog" class="change-log-list"><li class="change-log-empty">아직 변경 내역이 없습니다.</li></ul>
            </div>
        </div>
    </details>
    </div>
    <div id="toastContainer" class="toast-container"></div>"""


# ---------------------------------------------------------------------------
# Full report assembly
# ---------------------------------------------------------------------------

CSS = """
:root {
    color-scheme: light;
    --surface-1:      #fcfcfb;
    --page-plane:     #f9f9f7;
    --text-primary:   #0b0b0b;
    --text-secondary: #52514e;
    --text-muted:     #898781;
    --grid-line:      #e1e0d9;
    --baseline:       #c3c2b7;
    --border:         rgba(11,11,11,0.10);
    --brand:          #2563eb;
    --brand-dark:     #1d4ed8;
    --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --s4: #eda100;
    --s5: #e87ba4; --s6: #008300; --s7: #4a3aa7; --s8: #e34948;
    --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b;
}
@media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) {
        color-scheme: dark;
        --surface-1:      #1a1a19;
        --page-plane:     #0d0d0d;
        --text-primary:   #ffffff;
        --text-secondary: #c3c2b7;
        --text-muted:     #898781;
        --grid-line:      #2c2c2a;
        --baseline:       #383835;
        --border:         rgba(255,255,255,0.10);
        --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500;
        --s5: #d55181; --s6: #008300; --s7: #9085e9; --s8: #e66767;
    }
}
:root[data-theme="dark"] {
    color-scheme: dark;
    --surface-1:      #1a1a19;
    --page-plane:     #0d0d0d;
    --text-primary:   #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted:     #898781;
    --grid-line:      #2c2c2a;
    --baseline:       #383835;
    --border:         rgba(255,255,255,0.10);
    --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500;
    --s5: #d55181; --s6: #008300; --s7: #9085e9; --s8: #e66767;
}

* { box-sizing: border-box; }
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');
body {
    font-family: 'Pretendard', system-ui, -apple-system, sans-serif;
    background-color: var(--page-plane);
    color: var(--text-primary);
    margin: 0;
    padding: 0 0 48px;
}


/* ---- Premium Direct Features ---- */
.fab-container { position: fixed; bottom: 30px; right: 30px; display: flex; flex-direction: column; gap: 12px; z-index: 9999; }
.fab-btn { width: 48px; height: 48px; border-radius: 24px; border: none; background: var(--surface-1); box-shadow: 0 4px 16px rgba(0,0,0,0.15); font-size: 20px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: transform 0.2s, background 0.2s; border: 1px solid var(--border); color: var(--text-primary); }
.fab-btn:hover { transform: scale(1.1); background: var(--surface-2); }
.export-btn { display: inline-flex; align-items: center; gap: 6px; padding: 10px 18px; border-radius: 8px; border: 1px solid var(--grid-line); background: var(--surface-1); color: var(--text-primary); font-weight: 600; font-size: 13px; cursor: pointer; transition: all 0.2s; box-shadow: 0 2px 8px rgba(0,0,0,0.04); margin-bottom: 20px; }
.export-btn:hover { border-color: var(--brand); color: var(--brand); background: var(--page-plane); transform: translateY(-1px); }

/* ---- lock screen ---- */
.lock-screen {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    height: 100vh; background-color: #0f172a; color: white; text-align: center; padding: 24px;
}
.lock-screen input[type="password"] {
    padding: 10px 14px; border-radius: 8px; border: 1px solid #475569; margin-top: 20px; width: 220px;
}
.lock-screen button {
    padding: 10px 20px; background-color: var(--brand); color: white; border: none;
    border-radius: 8px; cursor: pointer; margin-top: 10px; font-weight: 600;
}
.lock-screen button:hover { background-color: var(--brand-dark); }
.lock-screen .error { color: #f87171; margin-top: 10px; min-height: 1.2em; }
#content { display: none; }

/* ---- layout ---- */
.topbar {
    position: sticky; top: 0; z-index: 20; display: flex; align-items: center; justify-content: space-between;
    padding: 16px 32px; background: var(--surface-1); border-bottom: 1px solid var(--border);
}
.topbar h1 { font-size: 18px; margin: 0; font-weight: 700; }
.topbar .meta { color: var(--text-muted); font-size: 12px; margin-top: 2px; }
.theme-toggle {
    border: 1px solid var(--border); background: var(--page-plane); color: var(--text-primary);
    border-radius: 999px; padding: 6px 14px; cursor: pointer; font-size: 13px; font-family: inherit;
}
.container { max-width: 1360px; margin: 0 auto; padding: 24px 32px; }

/* ---- stat tiles ---- */
.stat-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 28px;
}
.stat-tile {
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 14px; padding: 16px 18px;
}
.stat-label { font-size: 13px; color: var(--text-secondary); margin-bottom: 8px; }
.stat-value { font-size: 26px; font-weight: 700; color: var(--text-primary); line-height: 1.15; }
.stat-sub { font-size: 12px; color: var(--text-muted); margin-top: 6px; }

/* ---- chart grid ---- */
.chart-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 16px; margin-bottom: 28px; }
.chart-card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 14px; padding: 20px; }
.chart-title { font-size: 14px; font-weight: 700; margin: 0 0 4px; color: var(--text-primary); }
.chart-note { font-size: 11.5px; color: var(--text-muted); margin: 0 0 14px; }

.bar-list { display: flex; flex-direction: column; gap: 10px; }
.bar-row { display: grid; grid-template-columns: 84px 1fr auto; align-items: center; gap: 10px; border-radius: 6px; }
.bar-row:hover, .bar-row:focus { background: var(--page-plane); outline: none; }
.bar-row-label { font-size: 12px; color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bar-row-track { height: 14px; background: var(--grid-line); border-radius: 0 4px 4px 0; overflow: hidden; }
.bar-row-fill { height: 100%; border-radius: 0 4px 4px 0; }
.bar-row-value { font-size: 12px; color: var(--text-primary); font-variant-numeric: tabular-nums; white-space: nowrap; }
.bar-group-header {
    display: flex; justify-content: space-between; align-items: baseline;
    font-weight: 700; font-size: 12.5px; color: var(--text-primary);
    padding: 10px 2px 4px; margin-top: 8px; border-bottom: 1px solid var(--grid-line);
}
.bar-group-header:first-child { margin-top: 0; }
.bar-group-count { font-weight: 400; font-size: 11px; color: var(--text-muted); }

.stack-bar { display: flex; height: 22px; border-radius: 6px; overflow: hidden; gap: 2px; background: var(--surface-1); }
.stack-seg { height: 100%; min-width: 2px; }
.legend-grid { display: flex; flex-wrap: wrap; gap: 10px 18px; margin-top: 16px; }
.legend-item { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.legend-swatch { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
.legend-label { color: var(--text-secondary); }
.legend-value { color: var(--text-primary); font-variant-numeric: tabular-nums; }

.role-s1 { background: var(--s1); } .role-s2 { background: var(--s2); }
.role-s3 { background: var(--s3); } .role-s4 { background: var(--s4); }
.role-s5 { background: var(--s5); } .role-s6 { background: var(--s6); }
.role-s7 { background: var(--s7); } .role-s8 { background: var(--s8); }
.role-good { background: var(--good); } .role-warning { background: var(--warning); }
.role-serious { background: var(--serious); } .role-critical { background: var(--critical); }
.role-muted { background: var(--baseline); }

/* ---- table ---- */
.table-section { background: var(--surface-1); border: 1px solid var(--border); border-radius: 14px; padding: 20px; margin-bottom: 8px; }
.table-toolbar { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; align-items: center; }
.filter-input, .filter-select {
    padding: 8px 12px; border-radius: 8px; border: 1px solid var(--border); background: var(--page-plane);
    color: var(--text-primary); font-family: inherit; font-size: 13px;
}
.filter-input { flex: 1; min-width: 200px; }
.row-count { margin-left: auto; font-size: 12px; color: var(--text-muted); }
.table-scroll { overflow-x: auto; max-height: 640px; overflow-y: auto; border: 1px solid var(--grid-line); border-radius: 10px; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
thead th {
    position: sticky; top: 0; background: var(--brand); color: white; text-align: left;
    padding: 10px 12px; white-space: nowrap; z-index: 5;
}
tbody td { padding: 8px 12px; border-bottom: 1px solid var(--grid-line); white-space: nowrap; }
tbody tr:hover { background: var(--page-plane); }
.cell-num { text-align: right; font-variant-numeric: tabular-nums; }
.cell-empty { color: var(--text-muted); }



/* ---- EDA Button ---- */
.eda-btn-wrap { text-align: right; margin-bottom: 20px; }
.eda-btn { display: inline-flex; align-items: center; gap: 8px; padding: 12px 24px; background: linear-gradient(135deg, var(--brand-dark), var(--brand)); color: white; text-decoration: none; border-radius: 8px; font-weight: 700; font-size: 14px; box-shadow: 0 4px 12px rgba(37,99,235,0.3); transition: transform 0.2s, box-shadow 0.2s; }
.eda-btn:hover { transform: translateY(-2px); box-shadow: 0 6px 16px rgba(37,99,235,0.4); color: white; }

/* ---- Top 10 Dashboard ---- */
.top10-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 32px; }
.top10-card { background: var(--surface-1); border-radius: 12px; border: 1px solid var(--border); box-shadow: 0 2px 8px rgba(0,0,0,0.04); overflow: hidden; display: flex; flex-direction: column; }
.top10-header { padding: 16px; font-weight: 700; font-size: 15px; border-bottom: 1px solid var(--grid-line); background: var(--page-plane); color: var(--text-primary); display: flex; align-items: center; gap: 8px; }
.top10-list { padding: 0; margin: 0; list-style: none; flex: 1; }
.top10-item { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; border-bottom: 1px solid var(--grid-line); font-size: 13px; transition: background 0.2s; }
.top10-item:last-child { border-bottom: none; }
.top10-item:hover { background: var(--surface-2); }
.top10-rank { font-weight: 800; width: 24px; font-size: 14px; color: var(--text-muted); }
.top10-item:nth-child(1) .top10-rank { color: #d4af37; } /* Gold */
.top10-item:nth-child(2) .top10-rank { color: #c0c0c0; } /* Silver */
.top10-item:nth-child(3) .top10-rank { color: #cd7f32; } /* Bronze */
.top10-name { flex: 1; font-weight: 600; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.top10-value { font-weight: 700; color: var(--brand-primary); }
.top10-sub { font-size: 11px; color: var(--text-muted); margin-left: 8px; }
.top10-empty { padding: 24px; text-align: center; color: var(--text-muted); font-size: 13px; }

/* ---- tree-grid summary ---- */
.tree-table-wrap { overflow-x: auto; margin-bottom: 28px; border-radius: 8px; border: 1px solid var(--border); background: var(--surface-1); }
.tree-table { width: 100%; border-collapse: collapse; text-align: left; }
.tree-table th { background: var(--page-plane); color: var(--text-secondary); font-size: 11.5px; font-weight: 600; padding: 12px 16px; border-bottom: 1px solid var(--grid-line); white-space: nowrap; }
.tree-table td { padding: 10px 16px; font-size: 13px; border-bottom: 1px solid var(--grid-line); white-space: nowrap; }
.tree-row { transition: background-color 0.2s, display 0.2s; }
.tree-row:hover { background-color: var(--surface-2); }
.tree-row.hidden { display: none; }
.tree-level-1 td:first-child { font-weight: 700; color: var(--brand-primary); }
.tree-level-2 td:first-child { font-weight: 600; padding-left: 36px; color: var(--text-primary); }
.tree-level-3 td:first-child { font-weight: 400; padding-left: 64px; color: var(--text-secondary); }
.tree-toggle { display: inline-flex; width: 18px; height: 18px; align-items: center; justify-content: center; cursor: pointer; user-select: none; margin-right: 6px; border-radius: 4px; transition: background 0.2s, transform 0.2s; font-size: 10px; color: var(--text-muted); }
.tree-toggle:hover { background: var(--border); color: var(--text-primary); }
.tree-toggle.expanded { transform: rotate(90deg); }
.tree-toggle.empty { visibility: hidden; }

/* ---- progress matrix (지사 x SP/SE/SG) ---- */
.progress-table-scroll { margin-bottom: 24px; }
.progress-table { font-size: 12px; }
.progress-table th { white-space: nowrap; }
.progress-table .cell-num { text-align: center; }
.progress-group-th { text-align: center; background: var(--brand-dark); }
.progress-branch { font-weight: 600; white-space: nowrap; }
.progress-cell { font-weight: 700; }
.progress-text-light { color: #ffffff; }
.progress-text-dark { color: var(--text-primary); }
.progress-rank { font-weight: 700; color: var(--brand); }
.progress-total-row td { border-top: 2px solid var(--border); font-weight: 700; background: var(--page-plane); }
.progress-total-label { color: var(--text-primary); }

.progress-chart-legend { margin: 0 0 8px; }
.progress-chart {
    display: flex; align-items: flex-end; gap: 20px; height: 200px;
    padding: 10px 8px 0; border-bottom: 1px solid var(--baseline);
}
.progress-chart-group { display: flex; flex-direction: column; align-items: center; gap: 8px; flex: 1; min-width: 0; }
.progress-chart-bars { display: flex; align-items: flex-end; gap: 3px; height: 160px; width: 100%; justify-content: center; }
.progress-chart-bar { width: 14px; border-radius: 4px 4px 0 0; min-height: 2px; }
.progress-chart-label { font-size: 11px; color: var(--text-secondary); white-space: nowrap; }

.section-title { font-size: 16px; font-weight: 700; margin: 40px 0 6px; display: flex; align-items: center; gap: 10px; }
.section-desc { font-size: 12.5px; color: var(--text-muted); margin: 0 0 16px; }

/* ---- collapsible sections (접기/펼치기) ---- */
details.section-collapse, details.subsection-collapse { margin: 0; }
summary.section-title, summary.subsection-title {
    cursor: pointer; list-style: none; user-select: none;
}
summary.section-title::-webkit-details-marker, summary.subsection-title::-webkit-details-marker { display: none; }
summary.section-title::before, summary.subsection-title::before {
    content: '▸'; display: inline-block; font-size: 12px; color: var(--text-muted);
    transition: transform 0.15s ease; margin-right: 8px;
}
details[open] > summary.section-title::before, details[open] > summary.subsection-title::before { transform: rotate(90deg); }
.subsection-title { font-size: 14px; font-weight: 700; margin: 32px 0 4px; color: var(--text-primary); display: flex; align-items: center; }
.badge {
    font-size: 11px; font-weight: 600; padding: 3px 9px; border-radius: 999px;
    background: var(--page-plane); border: 1px solid var(--border); color: var(--text-secondary);
}
.badge-soon { color: var(--warning); border-color: var(--warning); }
.empty-card {
    background: var(--surface-1); border: 1px dashed var(--border); border-radius: 14px;
    padding: 24px; color: var(--text-muted); font-size: 13px; line-height: 1.6; margin-bottom: 28px;
}
.callout {
    border-radius: 12px; padding: 16px 18px; font-size: 13px; line-height: 1.6; margin-bottom: 20px;
    border: 1px solid var(--border);
}
.callout-warning { background: color-mix(in srgb, var(--warning) 12%, var(--surface-1)); border-color: var(--warning); }
.callout-good { background: color-mix(in srgb, var(--good) 12%, var(--surface-1)); border-color: var(--good); }
.callout strong { color: var(--text-primary); }
.insight-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin-bottom: 20px; }
.insight-grid .callout { margin-bottom: 0; }
.insight-label { display: block; font-size: 11.5px; color: var(--text-muted); margin-bottom: 4px; }
.insight-value { font-size: 14px; }

.pending-rep-card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; padding: 18px 20px; margin-bottom: 16px; }
.pending-rep-header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px 14px; margin-bottom: 12px; }
.pending-rep-title { font-weight: 700; font-size: 14px; color: var(--text-primary); }
.pending-rep-counts { font-size: 12px; color: var(--text-muted); }
.copy-block {
    background: var(--page-plane); border: 1px solid var(--grid-line); border-radius: 8px;
    padding: 12px 14px; font-size: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    white-space: pre-wrap; word-break: break-all; line-height: 1.7; max-height: 220px; overflow-y: auto; margin: 0 0 10px;
    color: var(--text-secondary);
}
.pending-detail-list { display: flex; flex-direction: column; gap: 2px; }
.pending-detail-row {
    display: grid; grid-template-columns: 56px 1fr auto auto; align-items: center; gap: 10px;
    padding: 6px 4px; font-size: 12px; border-bottom: 1px solid var(--grid-line);
}
.pending-detail-row:last-child { border-bottom: none; }
.pending-status { font-weight: 600; font-size: 11px; }
.pending-status.status-미접수 { color: var(--critical); }
.pending-status.status-접수 { color: var(--warning); }
.pending-map-link {
    font-size: 11px; color: var(--brand); text-decoration: none; white-space: nowrap;
    padding: 3px 8px; border-radius: 999px; border: 1px solid var(--brand);
}
.pending-map-link:hover { background: var(--brand); color: white; }
.pending-map-link.disabled { color: var(--text-muted); border-color: var(--border); pointer-events: none; opacity: 0.5; }
.recontract-badge { font-size: 11px; font-weight: 700; padding: 3px 8px; border-radius: 999px; white-space: nowrap; }
.recontract-badge-achieved { background: color-mix(in srgb, var(--good) 16%, var(--surface-1)); color: var(--good); }
.recontract-badge-focus { background: color-mix(in srgb, var(--critical) 14%, var(--surface-1)); color: var(--critical); }

/* ---- global filter bar (본부/지사/담당자) ---- */
.global-filter-bar {
    display: flex; flex-wrap: wrap; align-items: center; gap: 18px;
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 14px;
    padding: 14px 20px; margin-bottom: 24px;
}
.filter-group { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.filter-group-label { font-size: 12px; font-weight: 700; color: var(--text-secondary); }
.filter-pill-row { display: flex; flex-wrap: wrap; gap: 6px; }
.filter-pill {
    padding: 6px 13px; border-radius: 999px; border: 1px solid var(--border); background: var(--page-plane);
    color: var(--text-secondary); font-size: 12px; font-family: inherit; cursor: pointer;
}
.filter-pill:hover:not(.active) { background: var(--grid-line); }
.filter-pill.active { background: var(--brand); border-color: var(--brand); color: white; font-weight: 600; }
.filter-summary { margin-left: auto; font-size: 12px; color: var(--text-muted); }

/* ---- admin matching panel ---- */
.admin-details {
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 14px;
    padding: 4px 20px; margin: 28px 0;
}
.admin-details summary {
    cursor: pointer; padding: 14px 0; font-weight: 700; font-size: 14px; list-style: none;
}
.admin-details summary::-webkit-details-marker { display: none; }
.admin-body { padding-bottom: 20px; }
.match-group { margin-bottom: 16px; }
.match-group-title { font-size: 13px; font-weight: 700; margin-bottom: 8px; }
.match-rows { display: flex; flex-direction: column; gap: 8px; margin-bottom: 8px; }
.match-row { display: flex; align-items: center; gap: 8px; }
.match-row select {
    padding: 6px 10px; border-radius: 8px; border: 1px solid var(--border); background: var(--page-plane);
    color: var(--text-primary); font-family: inherit; font-size: 12.5px;
}
.match-eq { color: var(--text-muted); font-size: 12px; }
.admin-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-top: 14px; }
.primary-btn, .ghost-btn {
    padding: 8px 16px; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer;
    font-family: inherit; border: 1px solid var(--border);
}
.primary-btn { background: var(--brand); color: white; border-color: var(--brand); }
.primary-btn:hover { background: var(--brand-dark); }
.ghost-btn { background: var(--page-plane); color: var(--text-primary); }
.ghost-btn.small { padding: 4px 10px; font-size: 12px; }
.file-btn { display: inline-flex; align-items: center; }
.match-summary { font-size: 11.5px; color: var(--text-muted); }

.admin-log { margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--border); }
.admin-log-title { font-size: 12px; font-weight: 700; color: var(--text-secondary); margin: 0 0 8px; }
.change-log-list { list-style: none; margin: 0; padding: 0; max-height: 160px; overflow-y: auto; display: flex; flex-direction: column-reverse; gap: 4px; }
.change-log-list li { font-size: 11.5px; color: var(--text-secondary); padding: 4px 0; border-bottom: 1px dashed var(--grid-line); }
.change-log-list li:first-child { border-bottom: none; }
.change-log-time { color: var(--text-muted); font-variant-numeric: tabular-nums; margin-right: 8px; }
.change-log-empty { color: var(--text-muted); font-style: italic; }

.toast-container {
    position: fixed; bottom: 24px; right: 24px; z-index: 100;
    display: flex; flex-direction: column; gap: 8px; pointer-events: none;
}
.toast {
    background: var(--text-primary); color: var(--page-plane); padding: 12px 18px; border-radius: 10px;
    font-size: 13px; box-shadow: 0 8px 24px rgba(0,0,0,0.25); opacity: 0; transform: translateY(8px);
    transition: opacity 0.2s ease, transform 0.2s ease; max-width: 360px;
}
.toast.show { opacity: 1; transform: translateY(0); }
"""


APP_SCRIPT_TEMPLATE = """
// ===== Lock screen =====
const CORRECT_PWD = "__PASSWORD__";
const ADMIN_PWD = "__ADMIN_PASSWORD__";
const EXPIRY_DATE = new Date("__EXPIRY__T23:59:59");
let isAdminUnlocked = false;

function checkPassword() {
    const now = new Date();
    if (now > EXPIRY_DATE) {
        document.getElementById('errorMsg').innerText = "이 리포트는 사용 기간이 만료되었습니다.";
        return;
    }
    const pwd = document.getElementById('pwd').value;
    if (pwd === CORRECT_PWD || pwd === ADMIN_PWD) {
        isAdminUnlocked = (pwd === ADMIN_PWD);
        document.getElementById('lockScreen').style.display = 'none';
        document.getElementById('content').style.display = 'block';
        const adminWrap = document.getElementById('adminOnlyWrap');
        if (adminWrap) adminWrap.style.display = isAdminUnlocked ? '' : 'none';
    } else {
        document.getElementById('errorMsg').innerText = "비밀번호가 틀렸습니다.";
    }
}
document.getElementById('pwd').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') checkPassword();
});

// ===== Theme toggle =====
function initTheme() {
    const saved = localStorage.getItem('dataintel-theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
}
function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme')
        || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('dataintel-theme', next);
}
initTheme();

// ===== Main table filter/search (server-rendered table) =====
function applyFilters() {
    const searchEl = document.getElementById('tableSearch');
    const branchEl = document.getElementById('branchFilter');
    const typeEl = document.getElementById('typeFilter');
    if (!searchEl) return;
    const q = (searchEl.value || '').toLowerCase();
    const branch = branchEl ? branchEl.value : '';
    const type = typeEl ? typeEl.value : '';
    const rows = document.querySelectorAll('#dataTable tbody tr');
    let visible = 0;
    rows.forEach(row => {
        const matchesBranch = !branch || row.dataset.branch === branch;
        const matchesType = !type || row.dataset.type === type;
        const matchesText = !q || row.textContent.toLowerCase().includes(q);
        const show = matchesBranch && matchesType && matchesText;
        row.style.display = show ? '' : 'none';
        if (show) visible++;
    });
    const countEl = document.getElementById('rowCount');
    if (countEl) countEl.textContent = visible.toLocaleString() + ' / ' + rows.length.toLocaleString() + '건';
}
function attachFilterListeners() {
    ['tableSearch', 'branchFilter', 'typeFilter'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('input', applyFilters);
    });
    applyFilters();
}
document.addEventListener('DOMContentLoaded', attachFilterListeners);

// ===== 고액 해지 미등록 알림 -- 계약상태(중) 필터 + 지사별 요약 =====
function applyNudgeFilter(value) {
    const rows = document.querySelectorAll('#nudgeTable tbody tr');
    const branchTotals = new Map();
    rows.forEach(row => {
        const show = !value || row.dataset.status === value;
        row.style.display = show ? '' : 'none';
        if (show) {
            const branch = row.dataset.branch || '미상';
            const amount = Number(row.dataset.amount) || 0;
            if (!branchTotals.has(branch)) branchTotals.set(branch, { count: 0, amount: 0 });
            const t = branchTotals.get(branch);
            t.count += 1;
            t.amount += amount;
        }
    });
    const summaryEl = document.getElementById('nudgeBranchSummary');
    if (!summaryEl) return;
    summaryEl.innerHTML = '';
    const sorted = Array.from(branchTotals, ([label, t]) => ({ label, count: t.count, amount: t.amount }))
        .sort((a, b) => b.amount - a.amount);
    sorted.forEach(r => {
        const tile = document.createElement('div');
        tile.className = 'stat-tile';
        const labelEl = document.createElement('div');
        labelEl.className = 'stat-label';
        labelEl.textContent = r.label;
        const valueEl = document.createElement('div');
        valueEl.className = 'stat-value';
        valueEl.textContent = r.count.toLocaleString('ko-KR') + '건';
        const subEl = document.createElement('div');
        subEl.className = 'stat-sub';
        subEl.textContent = r.amount.toLocaleString('ko-KR') + '원';
        tile.appendChild(labelEl); tile.appendChild(valueEl); tile.appendChild(subEl);
        summaryEl.appendChild(tile);
    });
}
function wireNudgeFilter() {
    const row = document.getElementById('nudgeStatusFilterRow');
    if (!row) return;
    row.addEventListener('click', (e) => {
        const btn = e.target.closest('.filter-pill');
        if (!btn) return;
        row.querySelectorAll('.filter-pill').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        applyNudgeFilter(btn.dataset.value || '');
    });
    applyNudgeFilter('');
}
document.addEventListener('DOMContentLoaded', wireNudgeFilter);

// ===== Client-side matching + dashboard engine =====
// Mirrors app/core/handlers.py (apply_matching/process_and_merge) and
// app/core/analytics.py (build_dashboard) so the admin panel below can
// re-run the composite-key match entirely in the browser.
(function () {
    const dataEl = document.getElementById('embeddedData');
    if (!dataEl) return; // no raw data embedded -- static report, admin panel not available
    const DATA = JSON.parse(dataEl.textContent);

    function toObjects(table) {
        return table.rows.map(r => {
            const o = {};
            table.columns.forEach((c, i) => { o[c] = r[i]; });
            return o;
        });
    }
    const dbRowsBase = toObjects(DATA.db);
    const fileRowsByKey = {};
    Object.keys(DATA.files).forEach(k => { fileRowsByKey[k] = toObjects(DATA.files[k]); });

    // ---- normalization (mirrors handlers.py) ----
    const HQ_ALIASES = {
        '강원본부': '강북/강원', '강북/강원본부': '강북/강원', '강북/강원': '강북/강원',
        '서부본부': '강남/서부', '강남/서부본부': '강남/서부', '강남/서부': '강남/서부',
        '부산/경남본부': '부산/경남', '부산경남본부': '부산/경남', '부산/경남': '부산/경남',
        '전남/전북본부': '전남/전북', '전남전북본부': '전남/전북', '전남/전북': '전남/전북',
        '충남/충북본부': '충남/충북', '충남충북본부': '충남/충북', '충남/충북': '충남/충북',
        '대구/경북본부': '대구/경북', '대구경북본부': '대구/경북', '대구/경북': '대구/경북',
    };
    function normalizeHQ(v) {
        if (v === null || v === undefined || v === '') return v;
        const s = String(v).trim();
        if (HQ_ALIASES[s]) return HQ_ALIASES[s];
        return s.replace(/본부$/, '');
    }
    function normalizeBranch(v) {
        if (v === null || v === undefined || v === '') return v;
        return String(v).trim().replace(/지사$/, '');
    }
    function toNumericAmount(v) {
        if (v === null || v === undefined || v === '') return null;
        const s = String(v).replace(/,/g, '').replace(/원/g, '').trim();
        const n = parseFloat(s);
        return isNaN(n) ? null : n;
    }
    function firstNonNull(arr) {
        for (const v of arr) { if (v !== undefined && v !== null && v !== '') return v; }
        return null;
    }
    function keyOf(row, cols) { return cols.map(c => row[c]).join('\\u0001'); }

    // ---- matching engine (mirrors handlers.py apply_matching) ----
    function activeConditions(conditions, dbCols, fileCols) {
        return (conditions || []).filter(c => c.enabled && dbCols.includes(c.db_col) && fileCols.includes(c.file_col));
    }

    function applyMatching(dbRows, fileRows, conditions, displayCols, prefix, aggregate, dateCandidates) {
        if (!fileRows || !fileRows.length) return { rows: dbRows, used: [] };
        const dbCols = dbRows.length ? Object.keys(dbRows[0]) : [];
        const fileCols = Object.keys(fileRows[0]);
        const used = activeConditions(conditions, dbCols, fileCols);
        if (!used.length) return { rows: dbRows, used: [] };
        const dbKeyCols = used.map(c => c.db_col);
        const fileKeyCols = used.map(c => c.file_col);

        if (aggregate) {
            const dateCol = (dateCandidates || []).find(c => fileCols.includes(c));
            const groups = new Map();
            for (const r of fileRows) {
                const k = keyOf(r, fileKeyCols);
                if (!groups.has(k)) groups.set(k, []);
                groups.get(k).push(r);
            }
            const aggByKey = new Map();
            for (const [k, grpRows] of groups) {
                let sorted = grpRows;
                let latest = grpRows[grpRows.length - 1];
                let latestDate = null;
                if (dateCol) {
                    const withDates = grpRows.map(r => ({ r, t: Date.parse(r[dateCol]) })).filter(x => !isNaN(x.t));
                    if (withDates.length) {
                        withDates.sort((a, b) => a.t - b.t);
                        latest = withDates[withDates.length - 1].r;
                        latestDate = new Date(withDates[withDates.length - 1].t);
                    }
                }
                const entry = { count: grpRows.length, latestDate };
                for (const col of displayCols) {
                    if (col === dateCol) continue;
                    entry[col] = latest ? latest[col] : null;
                }
                aggByKey.set(k, entry);
            }
            return {
                rows: dbRows.map(dbRow => {
                    const k = keyOf(dbRow, dbKeyCols);
                    const agg = aggByKey.get(k);
                    const out = Object.assign({}, dbRow);
                    out[prefix + '건수'] = agg ? agg.count : 0;
                    out[prefix + '_최근일시'] = agg ? agg.latestDate : null;
                    for (const col of displayCols) {
                        if (col === dateCol) continue;
                        out[col + '_' + prefix] = agg ? agg[col] : null;
                    }
                    return out;
                }),
                used,
            };
        }

        const byKey = new Map();
        for (const r of fileRows) {
            const k = keyOf(r, fileKeyCols);
            if (!byKey.has(k)) byKey.set(k, r);
        }
        return {
            rows: dbRows.map(dbRow => {
                const k = keyOf(dbRow, dbKeyCols);
                const match = byKey.get(k);
                const out = Object.assign({}, dbRow);
                for (const col of displayCols) {
                    out[col + '_' + prefix] = match ? match[col] : null;
                }
                return out;
            }),
            used,
        };
    }

    function countOpenVoc(vocFileRows, used, dbRows) {
        const openStates = new Set(['미접수', '접수', '처리중', '결재요청']);
        if (!used.length) return dbRows.map(() => 0);
        const fileKeyCols = used.map(c => c.file_col);
        const dbKeyCols = used.map(c => c.db_col);
        const counts = new Map();
        for (const r of vocFileRows) {
            if (openStates.has(r['상태'])) {
                const k = keyOf(r, fileKeyCols);
                counts.set(k, (counts.get(k) || 0) + 1);
            }
        }
        return dbRows.map(r => counts.get(keyOf(r, dbKeyCols)) || 0);
    }

    // ---- full re-merge (mirrors handlers.py process_and_merge) ----
    function rebuildMerged(config) {
        let rows = dbRowsBase.map(r => Object.assign({}, r));

        const originRes = applyMatching(rows, fileRowsByKey.original, config.original, DATA.displayColumns.original, 'origin', false);
        rows = originRes.rows;
        const facRes = applyMatching(rows, fileRowsByKey.facility, config.facility, DATA.displayColumns.facility, 'fac', false);
        rows = facRes.rows;
        const patrolRes = applyMatching(rows, fileRowsByKey.patrol, config.patrol, DATA.displayColumns.patrol, 'patrol', true, ['도착시간', '출발시간']);
        rows = patrolRes.rows;
        const vocRes = applyMatching(rows, fileRowsByKey.voc, config.voc, DATA.displayColumns.voc, 'voc', true, ['접수일시']);
        rows = vocRes.rows;

        const openCounts = countOpenVoc(fileRowsByKey.voc, vocRes.used, rows);

        rows = rows.map((r, i) => {
            const out = Object.assign({}, r);
            out['만기도래_월'] = out['만기도래 월_origin'] != null ? out['만기도래 월_origin'] : null;
            out['합산월정료'] = toNumericAmount(out['합산월정료(KTT+KT)_origin']);
            out['서비스재개시일'] = out['서비스재개시일_fac'] != null ? out['서비스재개시일_fac'] : null;
            out['KTT월정료'] = toNumericAmount(out['KTT월정료_fac']);
            out['순찰건수'] = out['patrol건수'] || 0;
            out['최근점검결과'] = out['결과_patrol'] != null ? out['결과_patrol'] : null;
            out['최근특이사항'] = out['특이사항_patrol'] != null ? out['특이사항_patrol'] : null;
            out['최근점검일'] = out['patrol_최근일시'] ? new Date(out['patrol_최근일시']).toISOString().slice(0, 10) : null;
            out['VOC건수'] = out['voc건수'] || 0;
            out['미처리VOC건수'] = openCounts[i] || 0;
            out['최근VOC상태'] = out['상태_voc'] != null ? out['상태_voc'] : null;
            out['최근VOC유형'] = out['VOC유형대_voc'] != null ? out['VOC유형대_voc'] : null;

            const hqSrc = firstNonNull([out['관리본부명_origin'], out['관리본부명_fac']]);
            const branchSrc = firstNonNull([out['관리지사명_origin'], out['관리지사명_fac'], out['지사']]);
            const branch = branchSrc != null ? normalizeBranch(branchSrc) : null;
            let hq = hqSrc != null ? normalizeHQ(hqSrc) : (out['본부'] != null ? normalizeHQ(out['본부']) : null);
            if (hq == null && branch != null && DATA.branchOrder.includes(branch)) hq = '강북/강원';
            out['관리본부'] = hq;
            out['관리지사'] = branch;

            const amount = firstNonNull([out['합산월정료'], out['KTT월정료'], toNumericAmount(out['월정료'])]);
            out['월환산금액'] = amount;

            const recontract = firstNonNull([out['재계약여부_origin'], out['재계약여부_fac'], out['쟤계약여부_fac']]);
            out['재계약여부'] = recontract;

            const status = firstNonNull([out['계약상태_origin'], out['계약상태(중)_fac'], out['계약상태(대)_fac']]);
            out['계약상태'] = status;

            return out;
        });

        const hqRank = new Map(DATA.hqOrder.map((v, i) => [v, i]));
        const branchRank = new Map(DATA.branchOrder.map((v, i) => [v, i]));
        rows.sort((a, b) => {
            const ra = hqRank.has(a['관리본부']) ? hqRank.get(a['관리본부']) : DATA.hqOrder.length;
            const rb = hqRank.has(b['관리본부']) ? hqRank.get(b['관리본부']) : DATA.hqOrder.length;
            if (ra !== rb) return ra - rb;
            const bra = branchRank.has(a['관리지사']) ? branchRank.get(a['관리지사']) : DATA.branchOrder.length;
            const brb = branchRank.has(b['관리지사']) ? branchRank.get(b['관리지사']) : DATA.branchOrder.length;
            return bra - brb;
        });

        return { rows, used: { original: originRes.used, facility: facRes.used, patrol: patrolRes.used, voc: vocRes.used } };
    }

    // ---- analytics (mirrors analytics.py) ----
    const UNKNOWN_LABEL = '미상';
    function fmtInt(n) {
        if (n === null || n === undefined || isNaN(n)) return '0';
        return Math.round(n).toLocaleString('ko-KR');
    }
    function fmtCompactWon(n) {
        if (n === null || n === undefined || isNaN(n)) return '0원';
        n = Number(n);
        if (Math.abs(n) >= 100000000) return (n / 100000000).toFixed(1) + '억원';
        if (Math.abs(n) >= 10000) return Math.round(n / 10000) + '만원';
        return Math.round(n).toLocaleString('ko-KR') + '원';
    }
    function valueCountsOrdered(values, order, topN) {
        const counts = new Map();
        for (let v of values) {
            if (v === null || v === undefined || v === '') v = UNKNOWN_LABEL;
            v = String(v);
            counts.set(v, (counts.get(v) || 0) + 1);
        }
        if (order) {
            const items = order.map(lbl => ({ label: lbl, value: counts.get(lbl) || 0 }));
            const used = new Set(order);
            for (const [lbl, val] of counts) if (!used.has(lbl)) items.push({ label: lbl, value: val });
            return items;
        }
        let arr = Array.from(counts, ([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value);
        if (topN && arr.length > topN) {
            const head = arr.slice(0, topN);
            const rest = arr.slice(topN).reduce((s, it) => s + it.value, 0);
            if (rest > 0) head.push({ label: '기타', value: rest });
            return head;
        }
        return arr;
    }
    function valueCountsKnown(values, total, order) {
        const known = values.filter(v => v !== null && v !== undefined && String(v).trim() !== '');
        const items = known.length ? valueCountsOrdered(known, order) : [];
        const note = total ? ('정보 확인된 ' + known.length.toLocaleString('ko-KR') + '건 / 전체 ' + total.toLocaleString('ko-KR') + '건 (' + (known.length / total * 100).toFixed(1) + '%) 기준') : '';
        return { items, note };
    }
    function sumBy(rows, groupCol, valueCol, order) {
        const totals = new Map();
        for (const r of rows) {
            let g = r[groupCol];
            if (g === null || g === undefined || g === '') g = UNKNOWN_LABEL;
            g = String(g);
            const v = Number(r[valueCol]) || 0;
            totals.set(g, (totals.get(g) || 0) + v);
        }
        if (order) {
            const items = order.map(lbl => ({ label: lbl, value: totals.get(lbl) || 0 }));
            const used = new Set(order);
            for (const [lbl, val] of totals) if (!used.has(lbl)) items.push({ label: lbl, value: val });
            return items;
        }
        return Array.from(totals, ([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value);
    }
    function buildKpis(rows) {
        const total = rows.length;
        const totalAmount = rows.reduce((s, r) => s + (Number(r['월환산금액']) || 0), 0);
        const recontractDone = rows.filter(r => r['재계약여부'] && String(r['재계약여부']).indexOf('재계약') !== -1).length;
        const unresolvedVoc = rows.reduce((s, r) => s + (Number(r['미처리VOC건수']) || 0), 0);
        const patrolledRatio = total ? rows.filter(r => (Number(r['순찰건수']) || 0) > 0).length / total * 100 : 0;
        return [
            { label: '총 관리계약', value: fmtInt(total), sub: '건' },
            { label: '월 정산금액 합계', value: fmtCompactWon(totalAmount), sub: fmtInt(totalAmount) + '원' },
            { label: '재계약 완료', value: fmtInt(recontractDone), sub: total ? ('전체의 ' + (recontractDone / total * 100).toFixed(1) + '%') : '0%' },
            { label: '미처리 VOC', value: fmtInt(unresolvedVoc), sub: '건' },
            { label: '순찰점검 실시율', value: patrolledRatio.toFixed(1) + '%', sub: '당월 1회 이상' },
        ];
    }

    function buildDashboardJS(rows, used) {
        const dashboard = {};
        dashboard.kpis = buildKpis(rows);
        dashboard.hq_count_chart = { title: '본부별 관리계약 수', unit: '건', items: valueCountsOrdered(rows.map(r => r['관리본부']), DATA.hqOrder) };
        dashboard.branch_count_chart = { title: '지사별 관리계약 수', unit: '건', items: valueCountsOrdered(rows.map(r => r['관리지사']), DATA.branchOrder) };
        dashboard.branch_amount_chart = { title: '지사별 월 정산금액 합계', unit: '원', items: sumBy(rows, '관리지사', '월환산금액', DATA.branchOrder) };
        dashboard.activity_type_chart = { title: '활동대상구분 분포', unit: '건', items: valueCountsOrdered(rows.map(r => r['활동대상구분'])) };
        dashboard.activity_status_chart = { title: '활동 처리 현황', unit: '건', items: valueCountsOrdered(rows.map(r => r['활동유무']), ['처리완료', '접수', '미접수']) };
        const rc = valueCountsKnown(rows.map(r => r['재계약여부']), rows.length);
        dashboard.recontract_chart = { title: '재계약여부 분포', unit: '건', note: rc.note, items: rc.items };

        if (used.voc && used.voc.length) {
            const dbKeyCols = used.voc.map(c => c.db_col);
            const fileKeyCols = used.voc.map(c => c.file_col);
            const matchedKeys = new Set(rows.map(r => keyOf(r, dbKeyCols)));
            const scoped = fileRowsByKey.voc.filter(r => matchedKeys.has(keyOf(r, fileKeyCols)));
            dashboard.voc_type_chart = { title: 'VOC 유형 분포 (관리계약 기준)', unit: '건', items: valueCountsOrdered(scoped.map(r => r['VOC유형대']), null, 6) };
        }
        if (used.patrol && used.patrol.length) {
            const dbKeyCols = used.patrol.map(c => c.db_col);
            const fileKeyCols = used.patrol.map(c => c.file_col);
            const matchedKeys = new Set(rows.map(r => keyOf(r, dbKeyCols)));
            const scoped = fileRowsByKey.patrol.filter(r => matchedKeys.has(keyOf(r, fileKeyCols)));
            dashboard.patrol_result_chart = { title: '순찰점검 결과 분포 (관리계약 기준)', unit: '건', items: valueCountsOrdered(scoped.map(r => r['결과']), null, 6) };
        }
        const expiryVals = rows.map(r => r['만기도래_월']).filter(v => v !== null && v !== undefined && v !== '');
        if (expiryVals.length) {
            const order = Array.from(new Set(expiryVals)).sort();
            dashboard.expiry_chart = { title: '만기도래 월 분포', unit: '건', items: valueCountsOrdered(expiryVals, order) };
        }
        return dashboard;
    }

    // ---- EDA: 월환산금액 분포/IQR 이상치 -- mirrors analytics.py build_eda_stats ----
    function histogramItems(values, bins) {
        const vmin = Math.min.apply(null, values), vmax = Math.max.apply(null, values);
        if (vmin === vmax) return [{ label: fmtCompactWon(vmin), value: values.length }];
        const width = (vmax - vmin) / bins;
        const items = [];
        for (let i = 0; i < bins; i++) {
            const lo = vmin + i * width, hi = vmin + (i + 1) * width;
            const count = values.filter(v => v >= lo && (i === bins - 1 ? v <= hi : v < hi)).length;
            items.push({ label: fmtCompactWon(lo) + '~' + fmtCompactWon(hi), value: count });
        }
        return items;
    }
    function quantile(sorted, q) {
        const pos = (sorted.length - 1) * q;
        const base = Math.floor(pos), rest = pos - base;
        return sorted[base + 1] !== undefined ? sorted[base] + rest * (sorted[base + 1] - sorted[base]) : sorted[base];
    }
    function buildEdaStatsJS(rows, amountCol) {
        amountCol = amountCol || '월환산금액';
        const positive = rows.map(r => Number(r[amountCol])).filter(v => !isNaN(v) && v > 0).sort((a, b) => a - b);
        if (positive.length < 5) return null;

        const q1 = quantile(positive, 0.25), q3 = quantile(positive, 0.75);
        const iqr = q3 - q1;
        const lowerBound = Math.max(0, q1 - 1.5 * iqr);
        const upperBound = q3 + 1.5 * iqr;
        const sum = positive.reduce((s, v) => s + v, 0);
        const mean = sum / positive.length;
        const variance = positive.length > 1 ? positive.reduce((s, v) => s + (v - mean) * (v - mean), 0) / (positive.length - 1) : 0;

        const stats = {
            count: positive.length, mean, median: quantile(positive, 0.5), std: Math.sqrt(variance),
            min: positive[0], max: positive[positive.length - 1], q1, q3, lower_bound: lowerBound, upper_bound: upperBound,
        };
        const distChart = { title: '월 정산금액 분포 (구간별 계약 수, 0원 제외)', unit: '건', items: histogramItems(positive, 10) };

        const outliers = rows
            .map(r => ({ r, amount: Number(r[amountCol]) }))
            .filter(x => !isNaN(x.amount) && (x.amount > upperBound || (x.amount > 0 && x.amount < lowerBound)))
            .map(x => ({
                지사: x.r['관리지사'], 고객상호: x.r['상호'], 계약번호: x.r['계약번호'],
                월정산금액: Math.round(x.amount),
                이상치구분: x.amount > upperBound ? '고액 이상치' : '저액 이상치',
                _severity: x.amount > upperBound ? (x.amount - upperBound) : (lowerBound - x.amount),
            }))
            .sort((a, b) => b._severity - a._severity);

        return {
            stats, dist_chart: distChart, outlier_count: outliers.length,
            outlier_columns: ['지사', '고객상호', '계약번호', '월정산금액', '이상치구분'],
            outlier_rows: outliers.slice(0, 30).map(({ _severity, ...rest }) => rest),
        };
    }

    // ---- rendering (mirrors report.py's HTML builders, via DOM APIs) ----
    const SERIES_SLOTS = ['s1', 's2', 's3', 's4', 's5', 's6', 's7', 's8'];
    const STATUS_ROLE_MAP = {
        '처리완료': 'good', '방어성공': 'good', '자동재계약': 'good',
        '접수': 'warning', '진행중': 'warning', '수동재계약': 'warning',
        '미접수': 'critical', '방어실패': 'critical', '재계약 없음': 'critical',
        '미상': 'muted', '기타': 'muted',
    };
    function colorRoleFor(label, index, mode) {
        if (mode === 'status') return 'role-' + (STATUS_ROLE_MAP[label] || 'muted');
        if (label === '미상' || label === '기타') return 'role-muted';
        return 'role-' + SERIES_SLOTS[index % SERIES_SLOTS.length];
    }
    function fmtValue(value, unit) {
        if (unit === '원') return Math.round(value).toLocaleString('ko-KR') + '원';
        return Math.round(value).toLocaleString('ko-KR') + unit;
    }
    function mkEl(tag, className, text) {
        const e = document.createElement(tag);
        if (className) e.className = className;
        if (text !== undefined) e.textContent = text;
        return e;
    }

    function renderMagnitudeChartEl(chart, chartId) {
        const items = chart.items.filter(it => it.value !== null && it.value !== undefined);
        if (items.filter(it => it.value > 0).length < 2) return null;
        const maxVal = Math.max.apply(null, items.map(it => it.value).concat([1])) || 1;
        const section = mkEl('section', 'chart-card');
        section.id = chartId;
        section.appendChild(mkEl('h3', 'chart-title', chart.title));
        const list = mkEl('div', 'bar-list');
        for (const it of items) {
            const pct = Math.max(0, Math.min(100, it.value / maxVal * 100));
            const row = mkEl('div', 'bar-row');
            row.tabIndex = 0;
            row.title = it.label + ': ' + fmtValue(it.value, chart.unit);
            row.appendChild(mkEl('span', 'bar-row-label', it.label));
            const track = mkEl('div', 'bar-row-track');
            const fill = mkEl('div', 'bar-row-fill role-s1');
            fill.style.width = pct.toFixed(2) + '%';
            track.appendChild(fill);
            row.appendChild(track);
            row.appendChild(mkEl('span', 'bar-row-value', fmtValue(it.value, chart.unit)));
            list.appendChild(row);
        }
        section.appendChild(list);
        return section;
    }

    function renderStackedChartEl(chart, chartId, mode) {
        const items = chart.items.filter(it => it.value !== null && it.value !== undefined);
        const total = items.reduce((s, it) => s + it.value, 0);
        if (!items.length || total === 0) return null;
        const section = mkEl('section', 'chart-card');
        section.id = chartId;
        section.appendChild(mkEl('h3', 'chart-title', chart.title));
        if (chart.note) section.appendChild(mkEl('p', 'chart-note', chart.note));
        const bar = mkEl('div', 'stack-bar');
        const legend = mkEl('div', 'legend-grid');
        items.forEach((it, i) => {
            const pct = it.value / total * 100;
            const role = colorRoleFor(it.label, i, mode);
            const seg = mkEl('div', 'stack-seg ' + role);
            seg.style.width = pct.toFixed(2) + '%';
            seg.tabIndex = 0;
            seg.title = it.label + ': ' + fmtValue(it.value, chart.unit) + ' (' + pct.toFixed(1) + '%)';
            bar.appendChild(seg);
            const li = mkEl('div', 'legend-item');
            li.appendChild(mkEl('span', 'legend-swatch ' + role));
            li.appendChild(mkEl('span', 'legend-label', it.label));
            li.appendChild(mkEl('span', 'legend-value', fmtValue(it.value, chart.unit) + ' (' + pct.toFixed(1) + '%)'));
            legend.appendChild(li);
        });
        section.appendChild(bar);
        section.appendChild(legend);
        return section;
    }

    const CHART_SPECS = [
        ['hq_count_chart', 'magnitude'], ['branch_count_chart', 'magnitude'], ['branch_amount_chart', 'magnitude'],
        ['activity_type_chart', 'categorical'], ['activity_status_chart', 'status'], ['recontract_chart', 'categorical'],
        ['voc_type_chart', 'magnitude'], ['patrol_result_chart', 'magnitude'], ['expiry_chart', 'magnitude'],
    ];

    function renderStatTilesEl(containerEl, kpis) {
        containerEl.innerHTML = '';
        for (const k of kpis) {
            const tile = mkEl('div', 'stat-tile');
            tile.appendChild(mkEl('div', 'stat-label', k.label));
            tile.appendChild(mkEl('div', 'stat-value', k.value));
            tile.appendChild(mkEl('div', 'stat-sub', k.sub));
            containerEl.appendChild(tile);
        }
    }
    function renderChartGridEl(containerEl, dashboardData) {
        containerEl.innerHTML = '';
        for (const spec of CHART_SPECS) {
            const key = spec[0], mode = spec[1];
            const chart = dashboardData[key];
            if (!chart || !chart.items || !chart.items.length) continue;
            const node = mode === 'magnitude' ? renderMagnitudeChartEl(chart, key) : renderStackedChartEl(chart, key, mode);
            if (node) containerEl.appendChild(node);
        }
    }

    function renderSimpleTableEl(columns, rows) {
        const wrap = mkEl('div', 'table-scroll');
        const table = document.createElement('table');
        const thead = document.createElement('thead');
        const headRow = document.createElement('tr');
        columns.forEach(c => headRow.appendChild(mkEl('th', null, c)));
        thead.appendChild(headRow);
        table.appendChild(thead);
        const tbody = document.createElement('tbody');
        rows.forEach(r => {
            const tr = document.createElement('tr');
            columns.forEach(c => {
                const v = r[c];
                let td;
                if (v === null || v === undefined) {
                    td = mkEl('td', 'cell-empty', '-');
                } else if (c === '월정료' || c === '월정산금액') {
                    td = mkEl('td', 'cell-num', Math.round(v).toLocaleString('ko-KR'));
                } else {
                    td = mkEl('td', null, String(v));
                }
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        wrap.appendChild(table);
        return wrap;
    }

    function renderEdaSectionEl(containerEl, eda) {
        containerEl.innerHTML = '';
        if (!eda) {
            containerEl.appendChild(mkEl('div', 'empty-card', '월정산금액 데이터가 충분하지 않아 분포/이상치 분석을 표시할 수 없습니다.'));
            return;
        }
        const s = eda.stats;
        const statGrid = mkEl('div', 'stat-grid');
        renderStatTilesEl(statGrid, [
            { label: '분석 대상 계약', value: fmtInt(s.count), sub: '건 (0원 제외)' },
            { label: '평균', value: fmtCompactWon(s.mean), sub: fmtInt(s.mean) + '원' },
            { label: '중앙값', value: fmtCompactWon(s.median), sub: fmtInt(s.median) + '원' },
            { label: '표준편차', value: fmtCompactWon(s.std), sub: fmtInt(s.std) + '원' },
            { label: 'IQR 이상치', value: fmtInt(eda.outlier_count), sub: '건' },
        ]);
        containerEl.appendChild(statGrid);

        const chartGrid = mkEl('div', 'chart-grid');
        const distEl = renderMagnitudeChartEl(eda.dist_chart, 'edaSectionDistChart');
        if (distEl) chartGrid.appendChild(distEl);
        containerEl.appendChild(chartGrid);

        containerEl.appendChild(mkEl('p', 'section-desc',
            'IQR(사분위범위) 기준: Q1=' + fmtInt(s.q1) + '원, Q3=' + fmtInt(s.q3) + '원 -- ' +
            fmtInt(s.upper_bound) + '원 초과는 고액 이상치, ' + fmtInt(s.lower_bound) + '원 미만(0원 제외)은 저액 이상치로 분류합니다.'
        ));

        if (eda.outlier_count === 0) {
            containerEl.appendChild(mkEl('div', 'empty-card', 'IQR 기준 이상치로 분류된 계약이 없습니다.'));
        } else {
            const shown = eda.outlier_rows.length;
            const note = shown < eda.outlier_count ? (' (심각도순 상위 ' + shown.toLocaleString('ko-KR') + '건 표시, 전체 ' + eda.outlier_count.toLocaleString('ko-KR') + '건 중)') : '';
            containerEl.appendChild(mkEl('p', 'chart-note', '이상치 목록' + note));
            const tableSection = mkEl('div', 'table-section');
            tableSection.appendChild(renderSimpleTableEl(eda.outlier_columns, eda.outlier_rows));
            containerEl.appendChild(tableSection);
        }
    }

    const TABLE_COLUMNS = [
        ['관리본부', '본부'], ['관리지사', '지사'], ['활동대상구분', '구분'], ['상호', '고객상호'],
        ['계약번호', '계약번호'], ['서비스번호', '서비스번호'], ['월환산금액', '월정산금액'],
        ['재계약여부', '재계약여부'], ['계약상태', '계약상태'], ['VOC건수', 'VOC건수'],
        ['미처리VOC건수', '미처리VOC'], ['순찰건수', '순찰건수'], ['최근점검일', '최근점검일'],
        ['최근점검결과', '최근점검결과'], ['만기도래_월', '만기도래월'], ['SP담당', '담당자'],
    ];

    function renderTableEl(sectionEl, rows) {
        const columns = TABLE_COLUMNS.map(p => p[1]);
        const dataRows = rows.map(r => {
            const o = {};
            for (const p of TABLE_COLUMNS) o[p[1]] = (r[p[0]] !== undefined ? r[p[0]] : null);
            return o;
        });
        const branches = Array.from(new Set(dataRows.map(r => r['지사']).filter(Boolean))).sort();
        const types = Array.from(new Set(dataRows.map(r => r['구분']).filter(Boolean))).sort();

        sectionEl.innerHTML = '';
        const toolbar = mkEl('div', 'table-toolbar');
        const search = document.createElement('input');
        search.type = 'text'; search.id = 'tableSearch'; search.className = 'filter-input'; search.placeholder = '상호/계약번호/담당자 검색';
        const branchSel = document.createElement('select'); branchSel.id = 'branchFilter'; branchSel.className = 'filter-select';
        branchSel.appendChild(new Option('전체 지사', ''));
        branches.forEach(b => branchSel.appendChild(new Option(b, b)));
        const typeSel = document.createElement('select'); typeSel.id = 'typeFilter'; typeSel.className = 'filter-select';
        typeSel.appendChild(new Option('전체 구분', ''));
        types.forEach(t => typeSel.appendChild(new Option(t, t)));
        const countSpan = mkEl('span', 'row-count'); countSpan.id = 'rowCount';
        toolbar.appendChild(search); toolbar.appendChild(branchSel); toolbar.appendChild(typeSel); toolbar.appendChild(countSpan);

        const scrollDiv = mkEl('div', 'table-scroll');
        const table = document.createElement('table'); table.id = 'dataTable';
        const thead = document.createElement('thead'); const trh = document.createElement('tr');
        columns.forEach(c => trh.appendChild(mkEl('th', '', c)));
        thead.appendChild(trh); table.appendChild(thead);
        const tbody = document.createElement('tbody');
        for (const r of dataRows) {
            const tr = document.createElement('tr');
            if (r['지사']) tr.dataset.branch = r['지사'];
            if (r['구분']) tr.dataset.type = r['구분'];
            for (const c of columns) {
                const v = r[c];
                const td = document.createElement('td');
                if (v === null || v === undefined || v === '') { td.className = 'cell-empty'; td.textContent = '-'; }
                else if (c === '월정산금액') { td.className = 'cell-num'; td.textContent = Math.round(v).toLocaleString('ko-KR'); }
                else { td.textContent = v; }
                tr.appendChild(td);
            }
            tbody.appendChild(tr);
        }
        table.appendChild(tbody);
        scrollDiv.appendChild(table);
        sectionEl.appendChild(toolbar);
        sectionEl.appendChild(scrollDiv);
        attachFilterListeners();
    }



    // ---- Top 10 Expert Insights ----
    function renderTop10El(container, rows) {
        container.innerHTML = '';
        if (!rows || rows.length === 0) return;

        function groupBy(arr, keyFn) {
            return arr.reduce((acc, obj) => {
                const key = keyFn(obj);
                if (!acc[key]) acc[key] = [];
                acc[key].push(obj);
                return acc;
            }, {});
        }

        // 1. Top 10 실적 우수 지사 (진척율 기준)
        const branchGroups = groupBy(rows, r => r['관리지사'] || r['지사'] || '미상');
        const branchStats = Object.keys(branchGroups).map(br => {
            const brRows = branchGroups[br];
            let done=0, target=0;
            brRows.forEach(r => {
                const act = r['활동대상구분'];
                if(act==='SP' || act==='SE' || act==='SG') {
                    target++;
                    if(r['활동유무'] === '처리완료') done++;
                }
            });
            return { name: br, pct: target > 0 ? (done/target)*100 : 0, done, target };
        }).filter(b => b.target > 0);
        
        branchStats.sort((a,b) => b.pct - a.pct || b.done - a.done);
        const top10Best = branchStats.slice(0, 10);

        // 2. Top 10 미처리 지사 (잔여 타겟 건수 기준)
        const branchStatsBad = branchStats.map(b => ({ ...b, remain: b.target - b.done }));
        branchStatsBad.sort((a,b) => b.remain - a.remain || a.pct - b.pct);
        const top10Worst = branchStatsBad.slice(0, 10);

        // 3. Top 10 고액 관리고객 (VIP)
        const clients = rows.map(r => {
            let rev = parseFloat(r['월환산금액']||r['월정산금액']||r['월정료']||0) || 0;
            return { name: r['고객명'] || r['가입자명'] || '미상', rev, branch: r['관리지사'] || r['지사'] || '' };
        });
        clients.sort((a,b) => b.rev - a.rev);
        // unique clients
        const seen = new Set();
        const top10VIP = [];
        for (let c of clients) {
            const key = c.name + '_' + c.branch;
            if(!seen.has(key)) {
                seen.add(key);
                top10VIP.push(c);
                if(top10VIP.length >= 10) break;
            }
        }

        const grid = mkEl('div', 'top10-grid');

        function buildCard(title, icon, items, valFn, subFn) {
            const card = mkEl('div', 'top10-card');
            const hdr = mkEl('div', 'top10-header', `${icon} ${title}`);
            card.appendChild(hdr);
            
            if (items.length === 0) {
                card.appendChild(mkEl('div', 'top10-empty', '데이터가 없습니다.'));
            } else {
                const list = mkEl('ul', 'top10-list');
                items.forEach((item, idx) => {
                    const li = mkEl('li', 'top10-item');
                    li.appendChild(mkEl('div', 'top10-rank', String(idx + 1)));
                    li.appendChild(mkEl('div', 'top10-name', item.name));
                    
                    const valWrap = mkEl('div', 'top10-value', valFn(item));
                    if (subFn) {
                        const sub = mkEl('span', 'top10-sub', subFn(item));
                        valWrap.appendChild(sub);
                    }
                    li.appendChild(valWrap);
                    list.appendChild(li);
                });
                card.appendChild(list);
            }
            return card;
        }

        grid.appendChild(buildCard('Top 10 실적 우수 지사', '🏆', top10Best, 
            item => item.pct.toFixed(1) + '%', 
            item => `(${item.done}/${item.target})`
        ));
        
        grid.appendChild(buildCard('Top 10 집중 관리 지사', '🚨', top10Worst, 
            item => item.remain.toLocaleString('ko-KR') + '건 미처리',
            item => `(${item.pct.toFixed(1)}%)`
        ));
        
        grid.appendChild(buildCard('Top 10 고액 관리고객 (VIP)', '💎', top10VIP, 
            item => item.rev.toLocaleString('ko-KR') + '원',
            item => item.branch
        ));

        const title = mkEl('h2', 'section-title', '전문가 Top 10 인사이트 (필터 연동)');
        container.appendChild(title);
        container.appendChild(grid);
    }

    // ---- tree-grid summary (HQ > Branch > Owner) ----
    function buildTreeData(rows) {
        const tree = [];
        
        function groupBy(arr, keyFn) {
            return arr.reduce((acc, obj) => {
                const key = keyFn(obj);
                if (!acc[key]) acc[key] = [];
                acc[key].push(obj);
                return acc;
            }, {});
        }

        const hqGroups = groupBy(rows, r => r['관리본부'] || r['본부'] || '미상');
        const hqs = Object.keys(hqGroups).sort();

        function sumMetrics(arr) {
            let spDone=0, seDone=0, sgDone=0;
            let spTarget=0, seTarget=0, sgTarget=0;
            let rev=0;
            arr.forEach(r => {
                const act = r['활동대상구분'];
                const done = r['활동유무'] === '처리완료';
                if(act==='SP') { spTarget++; if(done) spDone++; }
                else if(act==='SE') { seTarget++; if(done) seDone++; }
                else if(act==='SG') { sgTarget++; if(done) sgDone++; }
                rev += (parseFloat(r['월환산금액']||r['월정산금액']||r['월정료']||0) || 0);
            });
            const totDone = spDone+seDone+sgDone;
            const totTarget = spTarget+seTarget+sgTarget;
            const pct = totTarget > 0 ? (totDone/totTarget)*100 : 0;
            return {
                count: arr.length,
                rev, spDone, spTarget, seDone, seTarget, sgDone, sgTarget, pct
            };
        }

        hqs.forEach(hq => {
            const hqRows = hqGroups[hq];
            const hqNode = { id: 'hq_'+hq, type: 'hq', name: hq, metrics: sumMetrics(hqRows), children: [] };
            
            const branchGroups = groupBy(hqRows, r => r['관리지사'] || r['지사'] || '미상');
            const branches = Object.keys(branchGroups).sort((a,b)=> {
                const ra = DATA.branchOrder.indexOf(a);
                const rb = DATA.branchOrder.indexOf(b);
                const rankA = ra !== -1 ? ra : 999;
                const rankB = rb !== -1 ? rb : 999;
                return rankA - rankB || a.localeCompare(b);
            });
            
            branches.forEach(br => {
                const brRows = branchGroups[br];
                const brNode = { id: 'br_'+hq+'_'+br, type: 'branch', name: br, metrics: sumMetrics(brRows), children: [] };
                
                const ownerGroups = groupBy(brRows, r => r['SP담당'] || '미담당');
                const owners = Object.keys(ownerGroups).sort();
                
                owners.forEach(ow => {
                    const owRows = ownerGroups[ow];
                    const owNode = { id: 'ow_'+hq+'_'+br+'_'+ow, type: 'owner', name: ow, metrics: sumMetrics(owRows), children: null };
                    brNode.children.push(owNode);
                });
                
                hqNode.children.push(brNode);
            });
            tree.push(hqNode);
        });
        return tree;
    }

    function renderTreeTableEl(container, rows) {
        container.innerHTML = '';
        if (!rows || !rows.length) return;

        const tree = buildTreeData(rows);
        
        const wrap = mkEl('div', 'tree-table-wrap');
        const table = document.createElement('table');
        table.className = 'tree-table';
        
        const thead = document.createElement('thead');
        thead.innerHTML = `<tr>
            <th>구분 (본부/지사/담당자)</th>
            <th class="cell-num">관리 고객 수</th>
            <th class="cell-num">월 정산금액 합계</th>
            <th class="cell-num">SP 처리</th>
            <th class="cell-num">SE 처리</th>
            <th class="cell-num">SG 처리</th>
            <th class="cell-num">진척율</th>
        </tr>`;
        table.appendChild(thead);
        
        const tbody = document.createElement('tbody');
        
        function createRow(node, level, parentId) {
            const tr = document.createElement('tr');
            tr.className = `tree-row tree-level-${level}`;
            if (level > 1) tr.classList.add('hidden');
            tr.dataset.id = node.id;
            tr.dataset.parentId = parentId;
            
            const tdName = document.createElement('td');
            const hasChildren = node.children && node.children.length > 0;
            const toggle = mkEl('span', 'tree-toggle ' + (hasChildren ? '' : 'empty'), '▶');
            
            if (hasChildren) {
                tr.style.cursor = 'pointer';
                tr.title = "클릭하여 하위 항목 열기/닫기";
                tr.addEventListener('click', (e) => {
                    const isExpanded = toggle.classList.contains('expanded');
                    if (isExpanded) {
                        toggle.classList.remove('expanded');
                        // Hide all descendants
                        const descs = tbody.querySelectorAll(`[data-parent-id^="${node.id}"]`);
                        descs.forEach(d => {
                            d.classList.add('hidden');
                            const tgl = d.querySelector('.tree-toggle');
                            if(tgl) tgl.classList.remove('expanded');
                        });
                    } else {
                        toggle.classList.add('expanded');
                        // Show direct children
                        const children = tbody.querySelectorAll(`[data-parent-id="${node.id}"]`);
                        children.forEach(c => c.classList.remove('hidden'));
                    }
                });
            }
            
            tdName.appendChild(toggle);
            tdName.appendChild(document.createTextNode(node.name));
            tr.appendChild(tdName);
            
            const m = node.metrics;
            tr.appendChild(mkEl('td', 'cell-num', m.count.toLocaleString('ko-KR') + '건'));
            tr.appendChild(mkEl('td', 'cell-num', m.rev.toLocaleString('ko-KR') + '원'));
            tr.appendChild(mkEl('td', 'cell-num', `${m.spDone.toLocaleString('ko-KR')}/${m.spTarget.toLocaleString('ko-KR')}`));
            tr.appendChild(mkEl('td', 'cell-num', `${m.seDone.toLocaleString('ko-KR')}/${m.seTarget.toLocaleString('ko-KR')}`));
            tr.appendChild(mkEl('td', 'cell-num', `${m.sgDone.toLocaleString('ko-KR')}/${m.sgTarget.toLocaleString('ko-KR')}`));
            
            const { bg, textRole } = progressCellStyle(m.pct);
            const pctTd = mkEl('td', 'cell-num progress-cell ' + textRole, m.pct.toFixed(1) + '%');
            pctTd.setAttribute('style', bg);
            tr.appendChild(pctTd);
            
            tbody.appendChild(tr);
            
            if (hasChildren) {
                node.children.forEach(child => createRow(child, level + 1, node.id));
            }
        }
        
        tree.forEach(hqNode => createRow(hqNode, 1, 'root'));
        table.appendChild(tbody);
        
        const title = mkEl('h2', 'section-title', '본부/지사/담당자 종합 요약 (Tree-Grid)');
        wrap.appendChild(table);
        container.appendChild(title);
        container.appendChild(wrap);
    }

    // ---- progress matrix (지사 x SP/SE/SG) -- mirrors analytics.py build_progress_matrix / report.py render_progress_matrix ----
    const PROGRESS_TYPES = ['SP', 'SE', 'SG'];
    const PROGRESS_SERIES_ROLE = { SP: 'role-s3', SE: 'role-s2', SG: 'role-s1' };

    function progressCellGeneric(subRows) {
        const total = subRows.length;
        const done = subRows.filter(r => r['활동유무'] === '처리완료').length;
        const pct = total ? done / total * 100 : 0;
        return { 처리완료: done, 미처리: total - done, 계: total, 진척율: pct };
    }
    function progressCellSP(subRows) {
        const total = subRows.length;
        const done = subRows.filter(r => r['활동유무'] === '처리완료').length;
        const received = subRows.filter(r => r['활동유무'] === '접수').length;
        const notReceived = subRows.filter(r => r['활동유무'] === '미접수').length;
        const pct = total ? done / total * 100 : 0;
        return { 처리완료: done, 접수: received, 미접수: notReceived, 계: total, 진척율: pct };
    }
    function buildProgressMatrixJS(rows) {
        if (!rows.length) return null;
        const present = new Set(rows.map(r => r['지사'] || UNKNOWN_LABEL));
        const branches = DATA.branchOrder.filter(b => present.has(b))
            .concat(Array.from(present).filter(b => !DATA.branchOrder.includes(b)).sort());

        const branchRows = branches.map(branch => {
            const sub = rows.filter(r => (r['지사'] || UNKNOWN_LABEL) === branch);
            const row = { 지사: branch };
            row.SP = progressCellSP(sub.filter(r => r['활동대상구분'] === 'SP'));
            row.SE = progressCellGeneric(sub.filter(r => r['활동대상구분'] === 'SE'));
            row.SG = progressCellGeneric(sub.filter(r => r['활동대상구분'] === 'SG'));
            row['전체'] = progressCellGeneric(sub.filter(r => PROGRESS_TYPES.includes(r['활동대상구분'])));
            return row;
        });
        branchRows.map((r, i) => i)
            .sort((a, b) => branchRows[b]['전체'].진척율 - branchRows[a]['전체'].진척율)
            .forEach((idx, rank) => { branchRows[idx]['순위'] = rank + 1; });

        const totalRow = { 지사: '본부계' };
        totalRow.SP = progressCellSP(rows.filter(r => r['활동대상구분'] === 'SP'));
        totalRow.SE = progressCellGeneric(rows.filter(r => r['활동대상구분'] === 'SE'));
        totalRow.SG = progressCellGeneric(rows.filter(r => r['활동대상구분'] === 'SG'));
        totalRow['전체'] = progressCellGeneric(rows.filter(r => PROGRESS_TYPES.includes(r['활동대상구분'])));
        totalRow['순위'] = null;

        return {
            branch_rows: branchRows, total_row: totalRow,
            type_totals: { SP: totalRow.SP.계, SE: totalRow.SE.계, SG: totalRow.SG.계 },
        };
    }

    // ---- SP/SE/SG 담당유형별 대시보드 + 지사별 분석리포트 요약 -- mirrors
    // analytics.py build_progress_type_charts / build_branch_insights ----
    function buildProgressTypeChartsJS(matrix) {
        if (!matrix || !matrix.branch_rows.length) return null;
        const charts = {};
        PROGRESS_TYPES.forEach(t => {
            const rows = matrix.branch_rows.filter(r => r[t].계 > 0).slice().sort((a, b) => b[t].진척율 - a[t].진척율);
            charts[t] = {
                title: t + ' 진척율 (지사별)',
                items: rows.map(r => ({ label: r['지사'], value: r[t].진척율, sub: fmtInt(r[t].처리완료) + '/' + fmtInt(r[t].계) + '건' })),
            };
        });
        return charts;
    }
    function buildBranchInsightsJS(matrix) {
        if (!matrix || !matrix.branch_rows.length) return null;
        const rows = matrix.branch_rows;
        const summarize = (r, t) => ({ 지사: r['지사'], 진척율: r[t].진척율, 처리완료: r[t].처리완료, 계: r[t].계 });
        const best = rows.reduce((a, b) => b['전체'].진척율 > a['전체'].진척율 ? b : a);
        const worst = rows.reduce((a, b) => b['전체'].진척율 < a['전체'].진척율 ? b : a);
        const typeWorst = {};
        PROGRESS_TYPES.forEach(t => {
            const eligible = rows.filter(r => r[t].계 > 0);
            if (eligible.length) typeWorst[t] = summarize(eligible.reduce((a, b) => b[t].진척율 < a[t].진척율 ? b : a), t);
        });
        return { avg_pct: matrix.total_row['전체'].진척율, best: summarize(best, '전체'), worst: summarize(worst, '전체'), type_worst: typeWorst };
    }
    function barRowEl(it, role) {
        const pct = Math.max(0, Math.min(100, it.value));
        const row = mkEl('div', 'bar-row');
        row.tabIndex = 0;
        row.title = it.label + ': ' + it.value.toFixed(1) + '% (' + it.sub + ')';
        row.appendChild(mkEl('span', 'bar-row-label', it.label));
        const track = mkEl('div', 'bar-row-track');
        const fill = mkEl('div', 'bar-row-fill ' + role);
        fill.style.width = pct.toFixed(2) + '%';
        track.appendChild(fill);
        row.appendChild(track);
        const valueEl = mkEl('span', 'bar-row-value', it.value.toFixed(1) + '% ');
        valueEl.appendChild(mkEl('span', 'chart-note', '(' + it.sub + ')'));
        valueEl.lastChild.style.display = 'inline';
        valueEl.lastChild.style.margin = '0';
        row.appendChild(valueEl);
        return row;
    }
    function renderProgressTypeChartEl(chart, role, chartId) {
        const items = chart.items;
        if (!items.length) return null;
        const section = mkEl('section', 'chart-card');
        section.id = chartId;
        section.appendChild(mkEl('h3', 'chart-title', chart.title));
        const list = mkEl('div', 'bar-list');
        items.forEach(it => list.appendChild(barRowEl(it, role)));
        section.appendChild(list);
        return section;
    }
    function renderGroupedBarChartEl(title, groups, role, chartId) {
        groups = groups.filter(g => g.items.length);
        if (!groups.length) return null;
        const section = mkEl('section', 'chart-card');
        section.id = chartId;
        section.appendChild(mkEl('h3', 'chart-title', title));
        const list = mkEl('div', 'bar-list');
        groups.forEach(g => {
            const header = mkEl('div', 'bar-group-header');
            header.appendChild(mkEl('span', null, g.label));
            header.appendChild(mkEl('span', 'bar-group-count', g.items.length + '명'));
            list.appendChild(header);
            g.items.forEach(it => list.appendChild(barRowEl(it, role)));
        });
        section.appendChild(list);
        return section;
    }
    function renderProgressTypeDashboardEl(containerEl, charts) {
        containerEl.innerHTML = '';
        if (!charts) return;
        const grid = mkEl('div', 'chart-grid');
        let any = false;
        PROGRESS_TYPES.forEach(t => {
            const el = renderProgressTypeChartEl(charts[t], PROGRESS_SERIES_ROLE[t], 'progressType' + t + 'Chart');
            if (el) { grid.appendChild(el); any = true; }
        });
        if (any) containerEl.appendChild(grid);
    }
    function renderBranchInsightsEl(containerEl, insights) {
        containerEl.innerHTML = '';
        if (!insights) {
            containerEl.appendChild(mkEl('div', 'empty-card', '진척율을 계산할 데이터가 없습니다.'));
            return;
        }
        containerEl.appendChild(mkEl('p', 'section-desc', '전사 평균 진척율 ' + insights.avg_pct.toFixed(1) + '% 기준 요약입니다.'));
        const grid = mkEl('div', 'insight-grid');
        function card(label, d, cssCls) {
            const box = mkEl('div', 'callout ' + cssCls);
            box.appendChild(mkEl('span', 'insight-label', label));
            const valueEl = mkEl('span', 'insight-value');
            const strong = document.createElement('strong');
            strong.textContent = d.지사;
            valueEl.appendChild(strong);
            valueEl.appendChild(document.createTextNode(' -- ' + d.진척율.toFixed(1) + '% (' + fmtInt(d.처리완료) + '/' + fmtInt(d.계) + '건)'));
            box.appendChild(valueEl);
            return box;
        }
        grid.appendChild(card('전체 진척율 최고 지사', insights.best, 'callout-good'));
        grid.appendChild(card('전체 진척율 최저 지사 (집중관리 필요)', insights.worst, 'callout-warning'));
        const typeLabels = { SP: 'SP 진척율 최저 지사', SE: 'SE 진척율 최저 지사', SG: 'SG 진척율 최저 지사' };
        PROGRESS_TYPES.forEach(t => {
            if (insights.type_worst[t]) grid.appendChild(card(typeLabels[t], insights.type_worst[t], 'callout-warning'));
        });
        containerEl.appendChild(grid);
    }

    // ---- SP 부진자 추가분석 (SP담당 컬럼 기준) -- mirrors analytics.py build_sp_rep_performance ----
    function modeOf(g, col) {
        const counts = new Map();
        g.forEach(r => { const v = r[col]; if (v) counts.set(v, (counts.get(v) || 0) + 1); });
        let best = null, bestCount = -1;
        for (const [v, c] of counts) if (c > bestCount) { best = v; bestCount = c; }
        return best;
    }
    function buildSpRepPerformanceJS(rows, minCount) {
        minCount = minCount || 3;
        let sp = rows.filter(r => r['활동대상구분'] === 'SP');
        if (sp.length && Object.prototype.hasOwnProperty.call(sp[0], '관리본부')) {
            sp = sp.filter(r => r['관리본부'] === '강북/강원');
        }
        if (!sp.length) return null;
        const groups = new Map();
        sp.forEach(r => {
            let owner = r['SP담당'];
            owner = (owner === null || owner === undefined || String(owner).trim() === '') ? '미담당' : String(owner).trim();
            if (!groups.has(owner)) groups.set(owner, []);
            groups.get(owner).push(r);
        });
        const reps = [];
        for (const [owner, g] of groups) {
            const total = g.length;
            const done = g.filter(r => r['활동유무'] === '처리완료').length;
            const received = g.filter(r => r['활동유무'] === '접수').length;
            const notReceived = g.filter(r => r['활동유무'] === '미접수').length;
            const pct = total ? (done / total * 100) : 0;
            reps.push({
                담당자: owner, 지사: modeOf(g, '지사'), 영업구역: modeOf(g, '영업구역정보'),
                처리완료: done, 접수: received, 미접수: notReceived, 계: total, 진척율: pct,
            });
        }
        reps.sort((a, b) => a.진척율 - b.진척율);

        const totalDone = reps.reduce((s, r) => s + r.처리완료, 0);
        const totalCount = reps.reduce((s, r) => s + r.계, 0);
        const avgPct = totalCount ? (totalDone / totalCount * 100) : 0;
        const underperformers = reps.filter(r => r.계 >= minCount && r.진척율 < avgPct);

        // stable multi-pass sort: 영업구역(내림차순) -> 담당자(오름차순) -> 지사(조직 순서)
        const branchRank = new Map(DATA.branchOrder.map((v, i) => [v, i]));
        const chartReps = reps.filter(r => r.진척율 < 100).slice();
        chartReps.sort((a, b) => (b.영업구역 || '').localeCompare(a.영업구역 || ''));
        chartReps.sort((a, b) => a.담당자.localeCompare(b.담당자));
        chartReps.sort((a, b) => (branchRank.has(a.지사) ? branchRank.get(a.지사) : DATA.branchOrder.length) - (branchRank.has(b.지사) ? branchRank.get(b.지사) : DATA.branchOrder.length));

        return {
            avg_pct: avgPct, min_count: minCount, reps, chart_reps: chartReps,
            underperformer_count: underperformers.length, underperformers: underperformers.slice(0, 30),
        };
    }

    function renderSpRepSectionEl(containerEl, spPerf) {
        containerEl.innerHTML = '';
        if (!spPerf || !spPerf.reps.length) {
            containerEl.appendChild(mkEl('div', 'empty-card', 'SP 활동 데이터가 없어 담당자별 분석을 표시할 수 없습니다.'));
            return;
        }
        const note = mkEl('p', 'section-desc');
        note.appendChild(document.createTextNode(
            'SP 담당자 전체 평균 진척율 ' + spPerf.avg_pct.toFixed(1) + '% 기준, 최소 ' + spPerf.min_count + '건 이상 처리한 담당자 중 평균 미달 인원은 '
        ));
        const strong = document.createElement('strong');
        strong.textContent = spPerf.underperformer_count + '명';
        note.appendChild(strong);
        note.appendChild(document.createTextNode('입니다.'));
        containerEl.appendChild(note);

        const chartGroups = [];
        spPerf.chart_reps.forEach(r => {
            const label = r.지사 || '미상';
            if (!chartGroups.length || chartGroups[chartGroups.length - 1].label !== label) {
                chartGroups.push({ label, items: [] });
            }
            chartGroups[chartGroups.length - 1].items.push({
                label: r.담당자, value: r.진척율,
                sub: fmtInt(r.처리완료) + '/' + fmtInt(r.계) + '건' + (r.영업구역 ? (' · ' + r.영업구역) : ''),
            });
        });
        const chartGrid = mkEl('div', 'chart-grid');
        if (!chartGroups.length) {
            chartGrid.appendChild(mkEl('div', 'empty-card', '100% 달성자를 제외하면 표시할 담당자가 없습니다 (강북/강원본부 SP 전원 100% 달성).'));
        } else {
            const chartEl = renderGroupedBarChartEl(
                'SP 담당자별 진척율 (강북/강원본부, 100% 달성자 제외, 지사·담당자순)', chartGroups,
                PROGRESS_SERIES_ROLE.SP, 'spRepSectionChart',
            );
            if (chartEl) chartGrid.appendChild(chartEl);
        }
        containerEl.appendChild(chartGrid);

        const tableWrap = mkEl('div', 'table-section');
        if (!spPerf.underperformers.length) {
            tableWrap.appendChild(mkEl('div', 'empty-card', '평균 미달 담당자가 없습니다.'));
        } else {
            const columns = ['담당자', '지사', '영업구역', '처리완료', '접수', '미접수', '계', '진척율'];
            const tableRows = spPerf.underperformers.map(r => ({
                담당자: r.담당자, 지사: r.지사 || '-', 영업구역: r.영업구역 || '-',
                처리완료: fmtInt(r.처리완료), 접수: fmtInt(r.접수), 미접수: fmtInt(r.미접수), 계: fmtInt(r.계),
                진척율: r.진척율.toFixed(1) + '%',
            }));
            tableWrap.appendChild(renderSimpleTableEl(columns, tableRows));
        }
        containerEl.appendChild(tableWrap);
    }

    // ---- SP 미접수/접수 발송용 리스트 -- mirrors analytics.py build_sp_pending_contact_list ----
    const PENDING_STATES = ['미접수', '접수'];
    function kakaoMapLinkJS(address) {
        if (!address) return null;
        return 'https://map.kakao.com/link/search/' + encodeURIComponent(address);
    }
    function buildSpPendingContactListJS(rows) {
        let sp = rows.filter(r => r['활동대상구분'] === 'SP' && PENDING_STATES.includes(r['활동유무']));
        if (sp.length && Object.prototype.hasOwnProperty.call(sp[0], '관리본부')) {
            sp = sp.filter(r => r['관리본부'] === '강북/강원');
        }
        if (!sp.length) return null;
        const groups = new Map();
        sp.forEach(r => {
            let owner = r['SP담당'];
            owner = (owner === null || owner === undefined || String(owner).trim() === '') ? '미담당' : String(owner).trim();
            if (!groups.has(owner)) groups.set(owner, []);
            groups.get(owner).push(r);
        });
        const reps = [];
        for (const [owner, g] of groups) {
            const items = g.map(row => {
                const amount = row['월환산금액'];
                const address = row['설치주소'] || null;
                return {
                    계약번호: row['계약번호'] != null ? row['계약번호'] : null,
                    상호: row['상호'] != null ? row['상호'] : null,
                    월정료: (amount !== null && amount !== undefined && !isNaN(amount)) ? Number(amount) : null,
                    상태: row['활동유무'],
                    설치주소: address,
                    지도링크: kakaoMapLinkJS(address),
                };
            });
            items.sort((a, b) => {
                const pa = a.상태 === '미접수' ? 0 : 1, pb = b.상태 === '미접수' ? 0 : 1;
                if (pa !== pb) return pa - pb;
                return String(a.상호 || '').localeCompare(String(b.상호 || ''));
            });
            const textLines = items.map(it => {
                const amt = it.월정료 !== null ? fmtInt(it.월정료) + '원' : '-';
                const contract = it.계약번호 !== null ? it.계약번호 : '-';
                return '[' + it.상태 + '] ' + contract + ' / ' + (it.상호 || '-') + ' / ' + amt;
            });
            reps.push({
                담당자: owner, count: items.length,
                미접수_count: items.filter(it => it.상태 === '미접수').length,
                접수_count: items.filter(it => it.상태 === '접수').length,
                items, text: textLines.join('\\n'),
            });
        }
        reps.sort((a, b) => b.count - a.count);
        return { reps, total_count: reps.reduce((s, r) => s + r.count, 0) };
    }

    let pendingCopyIdCounter = 0;
    function pendingRepCardEl(rep, sectionId) {
        const textId = sectionId + 'Text' + (pendingCopyIdCounter++);
        const card = mkEl('div', 'pending-rep-card');
        const header = mkEl('div', 'pending-rep-header');
        header.appendChild(mkEl('span', 'pending-rep-title', rep.담당자));
        header.appendChild(mkEl('span', 'pending-rep-counts',
            '미접수 ' + fmtInt(rep.미접수_count) + ' · 접수 ' + fmtInt(rep.접수_count) + ' · 계 ' + fmtInt(rep.count) + '건'));
        const copyBtn = document.createElement('button');
        copyBtn.className = 'ghost-btn small';
        copyBtn.type = 'button';
        copyBtn.textContent = '📋 복사 (메일/문자용)';
        copyBtn.addEventListener('click', () => copyPendingText(textId, copyBtn));
        header.appendChild(copyBtn);
        card.appendChild(header);

        const pre = document.createElement('pre');
        pre.className = 'copy-block';
        pre.id = textId;
        pre.textContent = rep.text;
        card.appendChild(pre);

        const list = mkEl('div', 'pending-detail-list');
        rep.items.forEach(it => {
            const row = mkEl('div', 'pending-detail-row');
            row.appendChild(mkEl('span', 'pending-status status-' + it.상태, it.상태));
            row.appendChild(mkEl('span', null, (it.계약번호 !== null ? it.계약번호 : '-') + ' · ' + (it.상호 || '-')));
            row.appendChild(mkEl('span', 'cell-num', it.월정료 !== null ? fmtInt(it.월정료) + '원' : '-'));
            if (it.지도링크) {
                const a = document.createElement('a');
                a.className = 'pending-map-link';
                a.href = it.지도링크;
                a.target = '_blank';
                a.rel = 'noopener';
                a.textContent = '🗺 지도';
                row.appendChild(a);
            } else {
                row.appendChild(mkEl('span', 'pending-map-link disabled', '🗺 지도'));
            }
            list.appendChild(row);
        });
        card.appendChild(list);
        return card;
    }
    function renderSpPendingSectionEl(containerEl, pending, sectionId) {
        containerEl.innerHTML = '';
        if (!pending) {
            containerEl.appendChild(mkEl('div', 'empty-card', '미접수/접수 상태인 SP 건이 없습니다 (강북/강원본부 기준).'));
            return;
        }
        const note = mkEl('p', 'section-desc');
        note.appendChild(document.createTextNode('강북/강원본부 SP 활동 중 미접수/접수 상태인 '));
        const strong = document.createElement('strong');
        strong.textContent = fmtInt(pending.total_count) + '건';
        note.appendChild(strong);
        note.appendChild(document.createTextNode("을 담당자별로 정리했습니다. 각 담당자 카드의 '복사' 버튼을 누르면 계약번호/상호/월정료 목록이 클립보드에 복사되어 메일이나 문자에 바로 붙여넣을 수 있습니다."));
        containerEl.appendChild(note);
        const cardsWrap = mkEl('div');
        pending.reps.forEach(rep => cardsWrap.appendChild(pendingRepCardEl(rep, sectionId)));
        containerEl.appendChild(cardsWrap);
    }

    // ---- 재계약대상(SP) -- mirrors analytics.py build_recontract_target_analysis ----
    function serviceRestartYear(v) {
        if (v === null || v === undefined || v === '' || isNaN(v)) return null;
        const s = String(Math.trunc(Number(v)));
        return s.length >= 4 ? parseInt(s.slice(0, 4), 10) : null;
    }
    function buildRecontractAnalysisJS(rows, currentYear) {
        currentYear = currentYear || new Date().getFullYear();
        const target = rows.filter(r => r['활동대상구분'] === 'SP' && r['계약상태'] !== null && r['계약상태'] !== undefined && r['계약상태'] !== '');
        if (!target.length) return null;

        target.forEach(r => {
            const achieved = r['재계약여부'] === '수동재계약' && serviceRestartYear(r['서비스재개시일']) === currentYear;
            r._구분 = achieved ? '실적' : '집중 재계약 활동 대상';
        });

        const total = target.length;
        const achievedCount = target.filter(r => r._구분 === '실적').length;
        const focusCount = total - achievedCount;

        const kpis = [
            { label: '재계약대상(SP) 전체', value: fmtInt(total), sub: '건' },
            { label: '실적 (수동재계약·당해년도)', value: fmtInt(achievedCount), sub: total ? ('전체의 ' + (achievedCount / total * 100).toFixed(1) + '%') : '0%' },
            { label: '집중 재계약 활동 대상', value: fmtInt(focusCount), sub: total ? ('전체의 ' + (focusCount / total * 100).toFixed(1) + '%') : '0%' },
        ];

        function groupStats(col) {
            const groups = new Map();
            target.forEach(r => {
                const key = (r[col] === null || r[col] === undefined || r[col] === '') ? UNKNOWN_LABEL : r[col];
                if (!groups.has(key)) groups.set(key, []);
                groups.get(key).push(r);
            });
            const out = [];
            for (const [label, g] of groups) {
                const done = g.filter(r => r._구분 === '실적').length;
                out.push({ label, achieved: done, focus: g.length - done, total: g.length, pct: g.length ? (done / g.length * 100) : 0 });
            }
            return out;
        }

        const branchRank = new Map(DATA.branchOrder.map((v, i) => [v, i]));
        const byBranch = groupStats('관리지사');
        byBranch.sort((a, b) => (branchRank.has(a.label) ? branchRank.get(a.label) : DATA.branchOrder.length) - (branchRank.has(b.label) ? branchRank.get(b.label) : DATA.branchOrder.length));

        const byOwner = groupStats('SP담당');
        byOwner.sort((a, b) => b.total - a.total);

        const detailRows = target.map(r => ({
            계약번호: r['계약번호'] != null ? r['계약번호'] : null,
            상호: r['상호'] != null ? r['상호'] : null,
            지사: r['관리지사'] != null ? r['관리지사'] : null,
            담당자: r['SP담당'] || '미담당',
            계약상태: r['계약상태'] != null ? r['계약상태'] : null,
            만기도래월: r['만기도래_월'] != null ? r['만기도래_월'] : null,
            재계약여부: r['재계약여부'] != null ? r['재계약여부'] : null,
            구분: r._구분,
            설치주소: r['설치주소'] != null ? r['설치주소'] : null,
        }));
        detailRows.sort((a, b) => {
            const fa = a.구분 === '집중 재계약 활동 대상' ? 0 : 1, fb = b.구분 === '집중 재계약 활동 대상' ? 0 : 1;
            if (fa !== fb) return fa - fb;
            const ra = branchRank.has(a.지사) ? branchRank.get(a.지사) : DATA.branchOrder.length;
            const rb = branchRank.has(b.지사) ? branchRank.get(b.지사) : DATA.branchOrder.length;
            if (ra !== rb) return ra - rb;
            return String(a.담당자).localeCompare(String(b.담당자));
        });

        return { current_year: currentYear, total, achieved_count: achievedCount, focus_count: focusCount, kpis, by_branch: byBranch, by_owner: byOwner, detail_rows: detailRows };
    }

    function recontractDetailRowEl(r) {
        const isAchieved = r.구분 === '실적';
        const row = mkEl('div', 'pending-detail-row');
        row.appendChild(mkEl('span', 'recontract-badge ' + (isAchieved ? 'recontract-badge-achieved' : 'recontract-badge-focus'), isAchieved ? '실적' : '집중'));
        const statusBits = [r.계약상태, r.만기도래월].filter(b => b !== null && b !== undefined && b !== '');
        const mainText = (r.계약번호 !== null ? r.계약번호 : '-') + ' · ' + (r.상호 || '-') + ' (' + (statusBits.length ? statusBits.join(' · ') : '-') + ')';
        row.appendChild(mkEl('span', null, mainText));
        row.appendChild(mkEl('span', 'cell-num', r.재계약여부 || '-'));
        const link = kakaoMapLinkJS(r.설치주소);
        if (link) {
            const a = document.createElement('a');
            a.className = 'pending-map-link'; a.href = link; a.target = '_blank'; a.rel = 'noopener'; a.textContent = '🗺 지도';
            row.appendChild(a);
        } else {
            row.appendChild(mkEl('span', 'pending-map-link disabled', '🗺 지도'));
        }
        return row;
    }

    function renderRecontractSectionEl(containerEl, analysis) {
        containerEl.innerHTML = '';
        if (!analysis) {
            containerEl.appendChild(mkEl('div', 'empty-card', '재계약대상(SP)으로 분류할 데이터가 없습니다.'));
            return;
        }
        const note = mkEl('p', 'section-desc');
        note.appendChild(document.createTextNode(
            '총괄DB(SP)와 관리고객원본을 계약번호로 매칭해 계약상태·만기도래월이 확인된 ' + fmtInt(analysis.total) + '건을 재계약대상으로 삼았습니다. ' +
            '이 중 수동재계약이면서 서비스재개시일이 ' + analysis.current_year + '년인 ' + fmtInt(analysis.achieved_count) + '건은 실적, 나머지 ' + fmtInt(analysis.focus_count) + '건은 집중 재계약 활동 대상입니다.'
        ));
        containerEl.appendChild(note);

        const statGrid = mkEl('div', 'stat-grid');
        renderStatTilesEl(statGrid, analysis.kpis);
        containerEl.appendChild(statGrid);

        const chartGrid = mkEl('div', 'chart-grid');
        const branchItems = analysis.by_branch.map(r => ({ label: r.label, value: r.pct, sub: fmtInt(r.achieved) + '/' + fmtInt(r.total) + '건' }));
        const branchEl = renderProgressTypeChartEl({ title: '지사별 재계약 실적율', items: branchItems }, PROGRESS_SERIES_ROLE.SP, 'recontractBranchChart');
        if (branchEl) chartGrid.appendChild(branchEl);
        const ownerSorted = analysis.by_owner.slice().sort((a, b) => a.pct - b.pct);
        const ownerItems = ownerSorted.map(r => ({ label: r.label, value: r.pct, sub: fmtInt(r.achieved) + '/' + fmtInt(r.total) + '건' }));
        const ownerEl = renderProgressTypeChartEl({ title: '담당자별 재계약 실적율 (집중 필요 우선)', items: ownerItems }, PROGRESS_SERIES_ROLE.SP, 'recontractOwnerChart');
        if (ownerEl) chartGrid.appendChild(ownerEl);
        containerEl.appendChild(chartGrid);

        const listWrap = mkEl('div');
        const groups = [];
        analysis.detail_rows.forEach(r => {
            if (!groups.length || groups[groups.length - 1].owner !== r.담당자) groups.push({ owner: r.담당자, rows: [] });
            groups[groups.length - 1].rows.push(r);
        });
        if (!groups.length) {
            listWrap.appendChild(mkEl('div', 'empty-card', '표시할 재계약대상 건이 없습니다.'));
        } else {
            groups.forEach(g => {
                const achievedN = g.rows.filter(r => r.구분 === '실적').length;
                const card = mkEl('div', 'pending-rep-card');
                const header = mkEl('div', 'pending-rep-header');
                header.appendChild(mkEl('span', 'pending-rep-title', g.owner));
                header.appendChild(mkEl('span', 'pending-rep-counts', '실적 ' + fmtInt(achievedN) + ' · 집중대상 ' + fmtInt(g.rows.length - achievedN) + ' · 계 ' + fmtInt(g.rows.length) + '건'));
                card.appendChild(header);
                const list = mkEl('div', 'pending-detail-list');
                g.rows.forEach(r => list.appendChild(recontractDetailRowEl(r)));
                card.appendChild(list);
                listWrap.appendChild(card);
            });
        }
        containerEl.appendChild(listWrap);
    }

    function progressCellStyle(pct) {
        pct = Math.max(0, Math.min(100, pct));
        let varName, opacity, textRole;
        if (pct < 30) { varName = '--critical'; opacity = 55; textRole = 'progress-text-light'; }
        else if (pct < 55) { varName = '--warning'; opacity = 38; textRole = 'progress-text-dark'; }
        else { varName = '--good'; opacity = 22; textRole = 'progress-text-dark'; }
        return {
            bg: 'background: color-mix(in srgb, var(' + varName + ') ' + opacity + '%, var(--surface-1));',
            textRole: textRole,
        };
    }
    function progressNumTd(value) {
        return mkEl('td', 'cell-num', value.toLocaleString('ko-KR'));
    }
    function progressPctTd(cell) {
        const { bg, textRole } = progressCellStyle(cell.진척율);
        const cellEl = mkEl('td', 'cell-num progress-cell ' + textRole, cell.진척율.toFixed(1) + '%');
        cellEl.setAttribute('style', bg);
        return cellEl;
    }
    function progressRowEl(row, isTotal) {
        const tr = document.createElement('tr');
        if (isTotal) tr.className = 'progress-total-row';
        tr.appendChild(mkEl('td', 'progress-branch' + (isTotal ? ' progress-total-label' : ''), row['지사']));

        const sp = row.SP;
        tr.appendChild(progressNumTd(sp.처리완료));
        tr.appendChild(progressNumTd(sp.접수));
        tr.appendChild(progressNumTd(sp.미접수));
        tr.appendChild(progressNumTd(sp.계));
        tr.appendChild(progressPctTd(sp));

        ['SE', 'SG', '전체'].forEach(t => {
            const cell = row[t];
            tr.appendChild(progressNumTd(cell.처리완료));
            tr.appendChild(progressNumTd(cell.미처리));
            tr.appendChild(progressNumTd(cell.계));
            tr.appendChild(progressPctTd(cell));
        });
        tr.appendChild(mkEl('td', 'cell-num' + (isTotal ? '' : ' progress-rank'), isTotal ? '-' : String(row['순위'])));
        return tr;
    }
    function groupTh(text, colspan) {
        const th = mkEl('th', 'progress-group-th', text);
        th.colSpan = colspan;
        return th;
    }
    function renderProgressSectionEl(containerEl, matrix) {
        containerEl.innerHTML = '';
        if (!matrix) return;

        const typeSummary = PROGRESS_TYPES.map(t => t + ' ' + matrix.type_totals[t].toLocaleString('ko-KR') + '건').join(' · ');
        containerEl.appendChild(mkEl('p', 'section-desc', '활동대상구분 기준 이번 달 대상 건수 -- ' + typeSummary));

        const scrollDiv = mkEl('div', 'table-scroll progress-table-scroll');
        const table = document.createElement('table'); table.className = 'progress-table';
        const thead = document.createElement('thead');
        const groupTr = document.createElement('tr');
        const branchTh = mkEl('th', '', '지사'); branchTh.rowSpan = 2;
        groupTr.appendChild(branchTh);
        groupTr.appendChild(groupTh('SP', 5));
        groupTr.appendChild(groupTh('SE', 4));
        groupTr.appendChild(groupTh('SG', 4));
        groupTr.appendChild(groupTh('전체', 5));
        const subTr = document.createElement('tr');
        ['처리완료', '접수', '미접수', '계', '진척율'].forEach(t => subTr.appendChild(mkEl('th', '', t)));
        for (let i = 0; i < 2; i++) ['처리완료', '미처리', '계', '진척율'].forEach(t => subTr.appendChild(mkEl('th', '', t)));
        ['처리완료', '미처리', '계', '진척율', '순위'].forEach(t => subTr.appendChild(mkEl('th', '', t)));
        thead.appendChild(groupTr); thead.appendChild(subTr);
        table.appendChild(thead);

        const tbody = document.createElement('tbody');
        matrix.branch_rows.forEach(r => tbody.appendChild(progressRowEl(r, false)));
        tbody.appendChild(progressRowEl(matrix.total_row, true));
        table.appendChild(tbody);
        scrollDiv.appendChild(table);
        containerEl.appendChild(scrollDiv);

        const legend = mkEl('div', 'legend-grid progress-chart-legend');
        PROGRESS_TYPES.forEach(t => {
            const item = mkEl('div', 'legend-item');
            item.appendChild(mkEl('span', 'legend-swatch ' + PROGRESS_SERIES_ROLE[t]));
            item.appendChild(mkEl('span', 'legend-label', t));
            legend.appendChild(item);
        });
        containerEl.appendChild(legend);

        const chart = mkEl('div', 'progress-chart');
        matrix.branch_rows.forEach(row => {
            const group = mkEl('div', 'progress-chart-group');
            const bars = mkEl('div', 'progress-chart-bars');
            PROGRESS_TYPES.forEach(t => {
                const pct = row[t].진척율;
                const bar = mkEl('div', 'progress-chart-bar ' + PROGRESS_SERIES_ROLE[t]);
                bar.style.height = Math.max(pct, 1).toFixed(1) + '%';
                bar.tabIndex = 0;
                bar.title = row['지사'] + ' ' + t + ': ' + pct.toFixed(1) + '%';
                bars.appendChild(bar);
            });
            group.appendChild(bars);
            group.appendChild(mkEl('span', 'progress-chart-label', row['지사']));
            chart.appendChild(group);
        });
        containerEl.appendChild(chart);
    }

    // ---- global filter (본부/지사/담당자) -- scopes stat/chart/progress/table ----
    const globalFilter = { hq: '', branch: '', activity: '', owner: '' };

    function applyGlobalFilter(rows) {
        return rows.filter(r =>
            (!globalFilter.hq || r['관리본부'] === globalFilter.hq || r['본부'] === globalFilter.hq) &&
            (!globalFilter.branch || r['관리지사'] === globalFilter.branch || r['지사'] === globalFilter.branch) &&
            (!globalFilter.activity || r['활동대상구분'] === globalFilter.activity) &&
            (!globalFilter.owner || r['SP담당'] === globalFilter.owner)
        );
    }
    function updateFilterSummary(totalCount, filteredCount) {
        const el = document.getElementById('globalFilterSummary');
        if (!el) return;
        const parts = [];
        if (globalFilter.hq) parts.push('본부: ' + globalFilter.hq);
        if (globalFilter.branch) parts.push('지사: ' + globalFilter.branch);
        if (globalFilter.activity) parts.push('구분: ' + globalFilter.activity);
        if (globalFilter.owner) parts.push('담당자: ' + globalFilter.owner);
        el.textContent = (parts.length ? parts.join(' · ') + ' · ' : '') + filteredCount.toLocaleString('ko-KR') + ' / ' + totalCount.toLocaleString('ko-KR') + '건';
    }

    // ---- toast + change log (admin action feedback) ----
    function showToast(message) {
        const container = document.getElementById('toastContainer');
        if (!container) return;
        const toast = mkEl('div', 'toast', message);
        container.appendChild(toast);
        requestAnimationFrame(() => toast.classList.add('show'));
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 250);
        }, 3200);
    }

    const changeLogEntries = [];
    function renderChangeLog() {
        const list = document.getElementById('changeLog');
        if (!list) return;
        list.innerHTML = '';
        if (!changeLogEntries.length) {
            list.appendChild(mkEl('li', 'change-log-empty', '아직 변경 내역이 없습니다.'));
            return;
        }
        changeLogEntries.forEach(entry => {
            const li = document.createElement('li');
            li.appendChild(mkEl('span', 'change-log-time', entry.time));
            li.appendChild(document.createTextNode(entry.message));
            list.appendChild(li);
        });
    }
    function logChange(message) {
        const time = new Date().toLocaleTimeString('ko-KR', { hour12: false });
        changeLogEntries.push({ time, message });
        if (changeLogEntries.length > 30) changeLogEntries.shift();
        renderChangeLog();
    }

    // ---- admin panel wiring ----
    let currentConfig = JSON.parse(JSON.stringify(DATA.matchingConfig));
    try {
        const saved = localStorage.getItem('dataintel-matching-config');
        if (saved) currentConfig = JSON.parse(saved);
    } catch (e) { /* ignore */ }

    function renderConditionRows(wrapEl, key) {
        wrapEl.innerHTML = '';
        const conditions = currentConfig[key] || (currentConfig[key] = []);
        conditions.forEach((cond, i) => {
            const row = mkEl('div', 'match-row');
            const chk = document.createElement('input');
            chk.type = 'checkbox'; chk.checked = !!cond.enabled;
            chk.addEventListener('change', () => { cond.enabled = chk.checked; });
            const dbSel = document.createElement('select');
            DATA.dbKeyCandidates.forEach(c => dbSel.appendChild(new Option(c, c)));
            dbSel.value = cond.db_col;
            dbSel.addEventListener('change', () => { cond.db_col = dbSel.value; });
            const eq = mkEl('span', 'match-eq', '=');
            const fileSel = document.createElement('select');
            (DATA.fileKeyCandidates[key] || []).forEach(c => fileSel.appendChild(new Option(c, c)));
            fileSel.value = cond.file_col;
            fileSel.addEventListener('change', () => { cond.file_col = fileSel.value; });
            const delBtn = mkEl('button', 'ghost-btn small', '삭제');
            delBtn.type = 'button';
            delBtn.addEventListener('click', () => { conditions.splice(i, 1); renderConditionRows(wrapEl, key); });
            row.appendChild(chk); row.appendChild(dbSel); row.appendChild(eq); row.appendChild(fileSel); row.appendChild(delBtn);
            wrapEl.appendChild(row);
        });
    }

    function renderAdminPanel() {
        const container = document.getElementById('matchingAdminPanel');
        if (!container) return;
        container.innerHTML = '';
        Object.keys(DATA.fileLabels).forEach(key => {
            const group = mkEl('div', 'match-group');
            group.appendChild(mkEl('div', 'match-group-title', DATA.fileLabels[key]));
            const rowsWrap = mkEl('div', 'match-rows');
            renderConditionRows(rowsWrap, key);
            group.appendChild(rowsWrap);
            const addBtn = mkEl('button', 'ghost-btn small', '+ 조건 추가');
            addBtn.type = 'button';
            addBtn.addEventListener('click', () => {
                (currentConfig[key] = currentConfig[key] || []).push({ db_col: DATA.dbKeyCandidates[0], file_col: DATA.fileKeyCandidates[key][0], enabled: true });
                renderConditionRows(rowsWrap, key);
            });
            group.appendChild(addBtn);
            container.appendChild(group);
        });
    }

    function summarizeUsed(used) {
        return Object.keys(used).map(k => {
            const label = DATA.fileLabels[k] || k;
            if (used[k] && used[k].length) {
                return label + ': ' + used[k].map(c => c.db_col + '=' + c.file_col).join(' AND ');
            }
            return label + ': 미적용';
        }).join(' · ');
    }

    // ---- top-level render pipeline ----
    let latestMergedRows = [];
    let latestUsed = {};

    function recomputeMerge() {
        const { rows, used } = rebuildMerged(currentConfig);
        latestMergedRows = rows;
        latestUsed = used;
    }

    function renderAll() {
        const filtered = applyGlobalFilter(latestMergedRows);
        const dashboardData = buildDashboardJS(filtered, latestUsed);
        const progressMatrix = buildProgressMatrixJS(filtered);

        const statGrid = document.getElementById('statGrid');
        const chartGrid = document.getElementById('chartGrid');
        

        const top10Section = document.getElementById('top10Section');
        if (top10Section) renderTop10El(top10Section, filtered);

        const treeSummarySection = document.getElementById('treeSummarySection');
        if (treeSummarySection) renderTreeTableEl(treeSummarySection, filtered);

        const progressSection = document.getElementById('progressSection');
        const tableSection = document.getElementById('tableSection');
        if (statGrid) renderStatTilesEl(statGrid, dashboardData.kpis);
        if (chartGrid) renderChartGridEl(chartGrid, dashboardData);
        if (progressSection) renderProgressSectionEl(progressSection, progressMatrix);
        if (tableSection) renderTableEl(tableSection, filtered);

        const progressInsightWrap = document.getElementById('progressInsightWrap');
        if (progressInsightWrap) renderBranchInsightsEl(progressInsightWrap, buildBranchInsightsJS(progressMatrix));
        const progressTypeWrap = document.getElementById('progressTypeWrap');
        if (progressTypeWrap) renderProgressTypeDashboardEl(progressTypeWrap, buildProgressTypeChartsJS(progressMatrix));

        const spRepSectionWrap = document.getElementById('spRepSectionWrap');
        if (spRepSectionWrap) renderSpRepSectionEl(spRepSectionWrap, buildSpRepPerformanceJS(filtered));

        const spPendingSectionWrap = document.getElementById('spPendingSectionWrap');
        if (spPendingSectionWrap) renderSpPendingSectionEl(spPendingSectionWrap, buildSpPendingContactListJS(filtered), 'spPendingSection');

        const recontractSectionWrap = document.getElementById('recontractSectionWrap');
        if (recontractSectionWrap) renderRecontractSectionEl(recontractSectionWrap, buildRecontractAnalysisJS(filtered));

        const edaSectionWrap = document.getElementById('edaSectionWrap');
        if (edaSectionWrap) renderEdaSectionEl(edaSectionWrap, buildEdaStatsJS(filtered));

        const summaryEl = document.getElementById('matchSummary');
        if (summaryEl) summaryEl.textContent = summarizeUsed(latestUsed);
        updateFilterSummary(latestMergedRows.length, filtered.length);

        const metaEl = document.getElementById('reportMeta');
        if (metaEl) {
            metaEl.textContent = '관리계약 ' + filtered.length.toLocaleString('ko-KR') + ' / ' + latestMergedRows.length.toLocaleString('ko-KR') + '건 (브라우저에서 재계산됨)';
        }
    }

    const FILTER_DIM_LABEL = { hq: '본부', branch: '지사', owner: '담당자' };

    function wireFilterPillRow(rowId, dim) {
        const row = document.getElementById(rowId);
        if (!row) return;
        row.addEventListener('click', (e) => {
            const btn = e.target.closest('.filter-pill');
            if (!btn) return;
            row.querySelectorAll('.filter-pill').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            globalFilter[dim] = btn.dataset.value || '';
            renderAll();
            const filtered = applyGlobalFilter(latestMergedRows);
            logChange((btn.dataset.value ? FILTER_DIM_LABEL[dim] + ' 필터: ' + btn.dataset.value : FILTER_DIM_LABEL[dim] + ' 필터 해제') + ' (' + filtered.length.toLocaleString('ko-KR') + '/' + latestMergedRows.length.toLocaleString('ko-KR') + '건)');
        });
    }

    // ---- CSV export (필터링된 데이터 엑셀 다운로드) ----
    function downloadCSV() {
        const rows = applyGlobalFilter(latestMergedRows);
        if (!rows.length) { showToast('다운로드할 데이터가 없습니다'); return; }
        const headers = Object.keys(rows[0]);
        let csv = '﻿' + headers.join(',') + '\\r\\n';
        rows.forEach(row => {
            csv += headers.map(h => {
                let v = row[h];
                if (v === null || v === undefined) v = '';
                return '"' + String(v).replace(/"/g, '""') + '"';
            }).join(',') + '\\r\\n';
        });
        const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = 'Data_Intel_PRO_Filtered_Data.csv'; a.click();
        URL.revokeObjectURL(url);
    }

    // ---- 복사 (메일/문자용) -- SP 미접수/접수 발송 리스트 ----
    function copyPendingText(elId, btnEl) {
        const el = document.getElementById(elId);
        if (!el) return;
        const text = el.textContent;
        const done = () => {
            showToast('클립보드에 복사되었습니다');
            if (btnEl) {
                const original = btnEl.textContent;
                btnEl.textContent = '✅ 복사됨';
                setTimeout(() => { btnEl.textContent = original; }, 1500);
            }
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
        } else {
            fallbackCopy(text, done);
        }
    }
    window.copyPendingText = copyPendingText; // called from inline onclick= in server-rendered HTML
    function fallbackCopy(text, done) {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); done(); } catch (e) { showToast('복사에 실패했습니다 -- 직접 선택해 복사해주세요'); }
        document.body.removeChild(ta);
    }

    document.addEventListener('DOMContentLoaded', () => {
        renderAdminPanel();
        recomputeMerge(); // silent -- reproduces the server-rendered numbers so filters work immediately
        renderChangeLog();

        const btnTheme = document.getElementById('btnTheme');
        if (btnTheme) btnTheme.addEventListener('click', toggleTheme);
        const btnTop = document.getElementById('btnTop');
        if (btnTop) btnTop.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
        const btnExportCSV = document.getElementById('btnExportCSV');
        if (btnExportCSV) btnExportCSV.addEventListener('click', downloadCSV);

        wireFilterPillRow('hqFilterRow', 'hq');
        wireFilterPillRow('branchFilterRow', 'branch');
        wireFilterPillRow('activityFilterRow', 'activity');
        const ownerSelect = document.getElementById('ownerFilterSelect');
        if (ownerSelect) ownerSelect.addEventListener('change', () => {
            globalFilter.owner = ownerSelect.value;
            renderAll();
            const filtered = applyGlobalFilter(latestMergedRows);
            logChange((ownerSelect.value ? '담당자 필터: ' + ownerSelect.value : '담당자 필터 해제') + ' (' + filtered.length.toLocaleString('ko-KR') + '/' + latestMergedRows.length.toLocaleString('ko-KR') + '건)');
        });

        const applyBtn = document.getElementById('applyMatchBtn');
        const resetBtn = document.getElementById('resetMatchBtn');
        const exportBtn = document.getElementById('exportMatchBtn');
        const importInput = document.getElementById('importMatchInput');
        if (applyBtn) applyBtn.addEventListener('click', () => {
            recomputeMerge();
            renderAll();
            try { localStorage.setItem('dataintel-matching-config', JSON.stringify(currentConfig)); } catch (e) { /* ignore */ }
            showToast('매칭 설정이 적용되었습니다 -- ' + latestMergedRows.length.toLocaleString('ko-KR') + '건 재계산됨');
            logChange('매칭 설정 적용: ' + summarizeUsed(latestUsed));
        });
        if (resetBtn) resetBtn.addEventListener('click', () => {
            currentConfig = JSON.parse(JSON.stringify(DATA.matchingConfig));
            renderAdminPanel();
            recomputeMerge();
            renderAll();
            showToast('매칭 설정이 기본값으로 초기화되었습니다');
            logChange('매칭 설정 초기화: ' + summarizeUsed(latestUsed));
        });
        if (exportBtn) exportBtn.addEventListener('click', () => {
            const blob = new Blob([JSON.stringify(currentConfig, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url; a.download = 'matching_config.json'; a.click();
            URL.revokeObjectURL(url);
            showToast('매칭 설정을 matching_config.json으로 내보냈습니다');
            logChange('설정 내보내기: matching_config.json');
        });
        if (importInput) importInput.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = () => {
                try {
                    currentConfig = JSON.parse(reader.result);
                    renderAdminPanel();
                    recomputeMerge();
                    renderAll();
                    showToast('설정 파일을 불러와 적용했습니다: ' + file.name);
                    logChange('설정 가져오기: ' + file.name);
                } catch (err) { alert('설정 파일을 읽을 수 없습니다.'); }
            };
            reader.readAsText(file);
        });
    });
})();
"""


def generate_html_report(df, voc_df=None, patrol_df=None, cancel_df=None,
                          cancelled_facility_df=None, raw_files=None, matching_config=None,
                          password=None, admin_password=None, expiry_date=None):
    """Generates the password-protected HTML dashboard report.

    df: already-merged 총괄DB dataframe (server-rendered initial dashboard).
    voc_df / patrol_df: raw detail files, used to scope the VOC/순찰 charts.
    cancel_df: 해지 파이프라인 -- rendered as an independent section, never
        joined onto `df`.
    cancelled_facility_df: optional '해지시설내역' upload -- powers the
        고액 미등록 알림 section when present.
    raw_files: optional dict of PRE-merge raw dataframes {'db','original',
        'facility','patrol','voc'} -- when given, an admin matching panel is
        embedded so column matching can be reconfigured and the whole
        dashboard re-computed client-side, in the browser.
    matching_config: the config actually used to produce `df` -- becomes the
        admin panel's starting state. Defaults to the persisted config.
    password: fixed by default (DEFAULT_REPORT_PASSWORD) so the report can be
        rebuilt without re-sharing a new password every time; pass an
        explicit value to rotate it, or generate_random_password() for a
        one-off random one.
    admin_password: separate, higher-privilege password (DEFAULT_ADMIN_PASSWORD
        by default). Entering `password` unlocks viewing only; entering this
        additionally unlocks the 관리자: 컬럼 매칭 설정 panel -- mirrors the
        Streamlit app's admin/user role split (see app/main.py).
    """
    if password is None:
        import random
        password = str(random.randint(1000, 9999))
    if admin_password is None:
        admin_password = DEFAULT_ADMIN_PASSWORD
    if expiry_date is None:
        expiry_date = get_end_of_month_iso()

    dashboard = build_dashboard(df, voc_df=voc_df, patrol_df=patrol_df)
    columns, rows = build_table(df)

    stat_html = render_stat_tiles(dashboard['kpis'])
    chart_grid_html = render_chart_grid(dashboard, MAIN_CHART_SPECS)
    table_html = render_table(columns, rows)

    progress_matrix = build_progress_matrix(df)
    progress_table_html = render_progress_matrix(progress_matrix)
    progress_chart_html = render_progress_bar_chart(progress_matrix)
    progress_type_dashboard_html = render_progress_type_dashboard(build_progress_type_charts(progress_matrix))
    branch_insights_html = render_branch_insights(build_branch_insights(progress_matrix))
    sp_rep_section_html = render_sp_rep_section(build_sp_rep_performance(df))
    sp_pending_section_html = render_sp_pending_section(build_sp_pending_contact_list(df))
    recontract_section_html = render_recontract_section(build_recontract_target_analysis(df))

    eda_stats = build_eda_stats(df)
    eda_section_html = render_eda_section_body(eda_stats)

    cancel_section_html = render_cancel_section(cancel_df)
    nudge_section_html = render_nudge_section(cancelled_facility_df, cancel_df)

    admin_panel_html = ""
    filter_bar_html = ""
    embedded_script = ""
    if raw_files is not None:
        if matching_config is None:
            matching_config = load_matching_config()
        embedded = build_embedded_data(raw_files, matching_config)
        if embedded is not None:
            admin_panel_html = render_admin_panel_shell()
            filter_bar_html = render_global_filter_bar(df)
            embedded_json = json.dumps(embedded, ensure_ascii=False).replace('</script>', '<\\/script>')
            embedded_script = f'<script type="application/json" id="embeddedData">{embedded_json}</script>'

    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M')

    script = (
        APP_SCRIPT_TEMPLATE
        .replace('__PASSWORD__', password)
        .replace('__ADMIN_PASSWORD__', admin_password)
        .replace('__EXPIRY__', expiry_date)
    )

    html_out = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Data Intel PRO Report</title>
<style>{CSS}</style>
</head>
<body>

<div class="fab-container">
    <button id="btnTheme" class="fab-btn" title="다크 모드 변경">🌙</button>
    <button id="btnTop" class="fab-btn" title="맨 위로 가기">⬆️</button>
</div>

<div id="lockScreen" class="lock-screen">
    <h2>Data Intel PRO 보안 리포트</h2>
    <p>만료일: {_e(expiry_date)}</p>
    <input type="password" id="pwd" placeholder="비밀번호 입력" autocomplete="off">
    <button onclick="checkPassword()">확인</button>
    <div id="errorMsg" class="error"></div>
</div>

<div id="content">
    <div class="topbar">
        <div>
            <h1>Data Intel PRO 관리고객 대시보드</h1>
            <div class="meta" id="reportMeta">생성일시 {_e(generated_at)} · 관리계약 {len(rows):,}건 · 만료일 {_e(expiry_date)}</div>
        </div>
        <button class="theme-toggle" onclick="toggleTheme()">🌓 테마 전환</button>
    </div>
    <div class="container">
        {admin_panel_html}
        {filter_bar_html}

        <div class="eda-btn-wrap">
            <a href="Data_Intel_PRO_EDA.html" target="_blank" class="eda-btn">🚀 딥 다이브 EDA 분석기 열기 (별도 창)</a>
        </div>

        <details class="section-collapse">
        <summary class="section-title">🔄 재계약대상(SP)</summary>
        <div id="recontractSectionWrap">
            {recontract_section_html}
        </div>
        </details>

        <details class="section-collapse" open>
        <summary class="section-title">총괄DB 기준 대시보드</summary>
        <div class="stat-grid" id="statGrid">{stat_html}</div>
        <div class="chart-grid" id="chartGrid">{chart_grid_html}</div>

        <div id="top10Section"></div>

        <div id="treeSummarySection"></div>
        </details>

        <details class="section-collapse" open>
        <summary class="section-title">지사별 활동 진척율 (SP/SE/SG)</summary>
        <div id="progressInsightWrap">
            {branch_insights_html}
        </div>
        <div id="progressTypeWrap">
            {progress_type_dashboard_html}
        </div>
        <div class="table-section" id="progressSection">
            {progress_table_html}
            {progress_chart_html}
        </div>
        </details>

        <details class="subsection-collapse" open>
        <summary class="subsection-title">SP 부진자 추가분석 (담당자 기준)</summary>
        <div id="spRepSectionWrap">
            {sp_rep_section_html}
        </div>
        </details>

        <details class="subsection-collapse" open>
        <summary class="subsection-title">SP 미접수/접수 발송용 리스트 (담당자별)</summary>
        <div id="spPendingSectionWrap">
            {sp_pending_section_html}
        </div>
        </details>

        <details class="section-collapse" open>
        <summary class="section-title">데이터 분포/이상치 분석 (EDA, 월정산금액 기준)</summary>
        <div id="edaSectionWrap">
            {eda_section_html}
        </div>
        </details>

        <details class="section-collapse" open>
        <summary class="section-title">관리고객 상세 (필터/검색 가능)</summary>
        <button id="btnExportCSV" class="export-btn" title="현재 조건으로 필터링된 모든 데이터를 엑셀(CSV)로 다운로드합니다.">📥 필터링된 데이터 엑셀(CSV) 다운로드</button>
        <div class="table-section" id="tableSection">
            {table_html}
        </div>
        </details>

        {cancel_section_html}
        {nudge_section_html}
    </div>
</div>

{embedded_script}
<script>{script}</script>
</body>
</html>"""

    return html_out, password, expiry_date, admin_password
