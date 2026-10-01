import html
import json
import re
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
    build_zone_activity, ZONE_LABELS, ZONE_SP_MODES, ZONE_SP_MODE_ORDER, _fmt_compact_won,
)
from .handlers import HQ_ORDER, BRANCH_ORDER, build_zone_owner_map
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


# 핵심 요약 표(지사 x SP/SE/SG/전체): 그룹별 색 헤더 + 그룹 경계선 + 순위 하위 지사 음영.
# report.py JS의 renderProgressSectionEl과 같은 마크업을 만든다.
PROGRESS_GROUPS = ['SP', 'SE', 'SG', '전체']
PROGRESS_GROUP_CLASS = {'SP': 'pg-SP', 'SE': 'pg-SE', 'SG': 'pg-SG', '전체': 'pg-ALL'}
PROGRESS_GROUP_COLS = {
    'SP': ['처리완료', '접수', '미접수', '계', '진척율'],
    'SE': ['처리완료', '미처리', '계', '진척율'],
    'SG': ['처리완료', '미처리', '계', '진척율'],
    '전체': ['처리완료', '미처리', '계', '진척율', '순위'],
}


def progress_bottom_count(n):
    """순위 하위로 강조할 지사 수 -- 8개 지사면 4개, 적으면 절반 (1개뿐이면 없음)."""
    return min(4, n // 2)


def render_progress_matrix(matrix):
    if matrix is None:
        return ""

    # SP is tracked with an explicit 접수/미접수 split (no unlogged rows);
    # SE/SG only ever carry 처리완료 vs unlogged, so they get the simpler
    # 처리완료/미처리 pair. 전체 rolls back up to that simpler pair too.
    group_header = '<th rowspan="2" class="pg-branch-th">지사</th>' + "".join(
        f'<th colspan="{len(PROGRESS_GROUP_COLS[g])}" class="pg-g {PROGRESS_GROUP_CLASS[g]} grp-start">{_e(g)}</th>'
        for g in PROGRESS_GROUPS
    )
    sub_header = "".join(
        f'<th class="pg-sub {PROGRESS_GROUP_CLASS[g]}{" grp-start" if i == 0 else ""}">{_e(c)}</th>'
        for g in PROGRESS_GROUPS for i, c in enumerate(PROGRESS_GROUP_COLS[g])
    )
    n = len(matrix['branch_rows'])
    k = progress_bottom_count(n)

    def render_row(row, is_total=False):
        bottom = (not is_total) and k > 0 and row['순위'] > n - k
        cells = [f'<td class="progress-branch{" progress-total-label" if is_total else ""}">'
                 f'{_e(row["지사"])}</td>']
        for g in PROGRESS_GROUPS:
            cell = row[g]
            for i, c in enumerate(col for col in PROGRESS_GROUP_COLS[g] if col != '순위'):
                cls = 'cell-num' + (' grp-start' if i == 0 else '')
                if c == '진척율':
                    style, text_role = _progress_cell_style(cell[c])
                    cells.append(f'<td class="{cls} progress-cell {text_role}" style="{style}">{cell[c]:.1f}%</td>')
                else:
                    cells.append(f'<td class="{cls}">{cell[c]:,}</td>')
        if is_total:
            cells.append('<td class="cell-num">-</td>')
        elif bottom:
            cells.append(f'<td class="cell-num progress-rank pg-rank-bottom" title="순위 하위 {k}개">▼ {row["순위"]}</td>')
        else:
            cells.append(f'<td class="cell-num progress-rank">{row["순위"]}</td>')
        row_cls = 'progress-total-row' if is_total else ('pg-bottom' if bottom else '')
        row_attr = f' class="{row_cls}"' if row_cls else ''
        return f'<tr{row_attr}>{"".join(cells)}</tr>'

    body_rows = [render_row(r) for r in matrix['branch_rows']]
    body_rows.append(render_row(matrix['total_row'], is_total=True))

    type_summary = " · ".join(f"{t} {matrix['type_totals'][t]:,}건" for t in PROGRESS_TYPES)
    note = (f'<p class="pg-note"><span class="pg-note-swatch"></span>▼ 순위 하위 {k}개 지사 (연한 붉은 음영) · '
            f'진척율 칸 색: 30% 미만 위험 · 55% 미만 주의 · 그 이상 양호</p>') if k else ""

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
    </div>
    {note}"""


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


# 구역별 활동유무 -- SP는 처리완료/접수/미접수, SE/SG는 처리완료/미처리로
# 나눠 누적 막대로 그린다. 막대 길이는 가장 큰 구역의 대상 건수 대비라서
# 구역 간 물량 차이도 함께 보인다.
ZONE_SEGMENTS = {
    'SP': [('처리완료', 'role-good'), ('접수', 'role-warning'), ('미접수', 'role-critical')],
    'SE': [('처리완료', 'role-good'), ('미처리', 'role-critical')],
    'SG': [('처리완료', 'role-good'), ('미처리', 'role-critical')],
}
ZONE_SEGMENT_LABELS = {'접수': '미처리(접수)'}


def _zone_table_columns(t):
    if t == 'SP':
        return ['대상', '처리완료', '접수', '미접수', '처리율']
    return ['대상', '처리완료', '미처리', '처리율']


def _zone_card_html(t, data):
    rows = data['rows']
    total = data['total']
    max_total = max((r['대상'] for r in rows), default=0) or 1
    segments = ZONE_SEGMENTS[t]

    legend = "".join(
        f'<div class="legend-item"><span class="legend-swatch {role}"></span>'
        f'<span class="legend-label">{_e(ZONE_SEGMENT_LABELS.get(key, key))}</span></div>'
        for key, role in segments
    )

    bar_rows = []
    for r in rows:
        segs = ""
        for key, role in segments:
            if not r[key]:
                continue
            pct = r[key] / max_total * 100
            text = f"{r[key]:,}"
            segs += (f'<div class="zone-seg {role}" style="width:{pct:.2f}%">'
                     f'<span class="zl{min(len(text), 5)}">{text}</span></div>')
        tip = " · ".join(f"{ZONE_SEGMENT_LABELS.get(k, k)} {r[k]:,}" for k, _ in segments)
        bar_rows.append(f"""
        <div class="zone-row" tabindex="0" title="{_e(r['라벨'])}: 대상 {r['대상']:,}건 · {_e(tip)} · 처리율 {r['처리율']:.1f}%">
            <span class="bar-row-label">{_e(r['라벨'])}</span>
            <div class="zone-track">{segs}</div>
            <span class="bar-row-value">{r['처리율']:.1f}% <span class="zone-sub">({r['처리완료']:,}/{r['대상']:,}건)</span></span>
        </div>""")

    mode = data.get('mode')
    if mode:
        _, group_title, head_label = ZONE_SP_MODES[mode]
        count_label = f"{len(rows):,}" + {'owner': '명', 'zone': '개 구역'}.get(mode, '개 그룹')
    else:
        group_title, head_label = f"{ZONE_LABELS[t]}별", f"지사_{ZONE_LABELS[t]}"
        count_label = f"{len(rows):,}개 구역"
    mode_pills = ""
    if mode:
        mode_pills = '<div class="filter-pill-row zone-mode-row">' + "".join(
            f'<button type="button" class="filter-pill{" active" if m == mode else ""}" data-zone-mode="{m}">'
            f'{_e(ZONE_SP_MODES[m][2])}</button>'
            for m in ZONE_SP_MODE_ORDER
        ) + '</div>'

    columns = _zone_table_columns(t)
    head = f"<th>{_e(head_label)}</th>" + "".join(
        f"<th>{_e(ZONE_SEGMENT_LABELS.get(c, c))}</th>" for c in columns
    )

    def table_row(label, r, is_total=False):
        cells = [f'<td class="progress-branch{" progress-total-label" if is_total else ""}">{_e(label)}</td>']
        for c in columns:
            if c == '처리율':
                style, text_role = _progress_cell_style(r[c])
                cells.append(f'<td class="cell-num progress-cell {text_role}" style="{style}">{r[c]:.1f}%</td>')
            else:
                cells.append(f'<td class="cell-num">{r[c]:,}</td>')
        attrs = ' class="progress-total-row"' if is_total else ''
        return f'<tr{attrs}>{"".join(cells)}</tr>'

    body = "".join(table_row(r['라벨'], r) for r in rows) + table_row('계', total, is_total=True)

    summary = f"대상 {total['대상']:,}건 · 처리완료 {total['처리완료']:,}건 · "
    if t == 'SP':
        summary += f"미처리(접수) {total['접수']:,}건 · 미접수 {total['미접수']:,}건 · "
    else:
        summary += f"미처리 {total['미처리']:,}건 · "
    summary += f"처리율 {total['처리율']:.1f}%"

    return f"""
    <section class="chart-card zone-card" id="zoneActivity{t}" data-mode="{_e(mode or 'zone')}">
        <h3 class="chart-title">{_e(t)} {_e(group_title)} 활동유무 <span class="zone-sub">({_e(data['zone_col'])} 기준, {count_label}, 지사순)</span></h3>
        {mode_pills}
        <p class="chart-note">{_e(summary)}</p>
        <div class="legend-grid zone-legend">{legend}</div>
        <div class="zone-list">{"".join(bar_rows)}</div>
        <details class="zone-table-toggle">
            <summary>{_e(group_title)} 건수 표 보기</summary>
            <div class="table-scroll"><table class="progress-table">
                <thead><tr>{head}</tr></thead>
                <tbody>{body}</tbody>
            </table></div>
        </details>
    </section>"""


def render_zone_activity_section(zone_data):
    if not zone_data:
        return ('<div class="empty-card">구역별로 집계할 데이터가 없습니다 '
                '(영업구역정보/기술구역정보/구역정보 컬럼을 찾지 못했습니다).</div>')
    return "".join(_zone_card_html(t, zone_data[t]) for t in PROGRESS_TYPES if t in zone_data)


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
        "zoneOwnerMap": build_zone_owner_map(raw_files.get('zone_owner')),
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

.zone-card { margin-bottom: 16px; }
.zone-sub { font-size: 11px; font-weight: 400; color: var(--text-muted); }
.zone-legend { margin: 0 0 12px; }
.zone-list { display: flex; flex-direction: column; gap: 6px; max-height: 560px; overflow-y: auto; padding-right: 4px; }
.zone-row { display: grid; grid-template-columns: 130px 1fr 150px; align-items: center; gap: 10px; border-radius: 6px; }
.zone-row:hover, .zone-row:focus { background: var(--page-plane); outline: none; }
.zone-track { display: flex; gap: 2px; height: 18px; background: var(--grid-line); border-radius: 4px; overflow: hidden; }
.zone-seg { height: 100%; display: flex; align-items: center; justify-content: center; font-size: 10.5px; font-weight: 700; color: #fff; overflow: hidden; white-space: nowrap; font-variant-numeric: tabular-nums; }
.zone-seg:last-child { border-radius: 0 4px 4px 0; }
/* 조각 안 숫자는 들어갈 때만 표시 -- 잘린 숫자 대신 범례/툴팁/표가 값을 전달한다.
   .zlN = 글자 수 N (쉼표 포함), 기준 = 글자당 약 7px + 좌우 여백 */
.zone-seg { container-type: inline-size; }
@container (max-width: 17px) { .zl1 { display: none; } }
@container (max-width: 24px) { .zl2 { display: none; } }
@container (max-width: 31px) { .zl3 { display: none; } }
@container (max-width: 38px) { .zl4 { display: none; } }
@container (max-width: 46px) { .zl5 { display: none; } }
/* ---- 구역별 고급 시각화 ---- */
.core-filter-bar { margin-bottom: 14px; }
.core-headline { display: flex; flex-direction: column; gap: 3px; margin-bottom: 14px; }
.core-headline-main { font-size: 15px; font-weight: 700; color: var(--text-primary); }
.core-headline-sub { font-size: 12.5px; color: var(--text-secondary); }
.core-meter { height: 6px; background: var(--grid-line); border-radius: 3px; margin-top: 8px; overflow: hidden; }
.core-meter > span { display: block; height: 100%; background: var(--good); border-radius: 3px; }
.core-block-title { font-size: 14px; font-weight: 700; margin: 26px 0 10px; display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; color: var(--text-primary); }
.core-block-title small { font-weight: 400; font-size: 12px; color: var(--text-muted); }
#coreSectionWrap .stat-grid { margin-bottom: 0; }
.core-action-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 270px), 1fr)); gap: 12px; }
.core-action { padding: 14px 12px 10px; border-top: 3px solid var(--baseline); }
.core-action.is-critical { border-top-color: var(--critical); }
.core-action.is-serious { border-top-color: var(--serious); }
.core-action.is-warning { border-top-color: var(--warning); }
.core-action-head { display: flex; align-items: baseline; gap: 8px; font-size: 13px; font-weight: 700; margin: 0 4px 8px; }
.core-action-count { margin-left: auto; font-size: 18px; font-weight: 800; font-variant-numeric: tabular-nums; }
.core-action-list { display: flex; flex-direction: column; gap: 2px; max-height: 218px; overflow-y: auto; }
.core-action-item { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 1px 8px; width: 100%; text-align: left; padding: 6px 8px; border: 0; border-radius: 8px;
                    background: none; color: inherit; font: inherit; cursor: pointer; }
.core-action-item:hover, .core-action-item:focus-visible { background: var(--page-plane); outline: none; }
.core-action-name { font-size: 12.5px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.core-action-tag { font-size: 11.5px; color: var(--text-secondary); font-variant-numeric: tabular-nums; white-space: nowrap; }
.core-action-sub { grid-column: 1 / -1; font-size: 11.5px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.core-action-empty { font-size: 12px; color: var(--text-muted); padding: 6px 8px; }
.core-charts { margin-bottom: 0; grid-template-columns: repeat(auto-fit, minmax(min(100%, 400px), 1fr)); }
.core-charts .chart-note { margin-bottom: 10px; }
.core-chart-legend { margin: 0 0 10px; }
.core-row { grid-template-columns: 62px 1fr 104px; }
.core-row.wide { grid-template-columns: 168px 1fr 44px; }
.core-row[data-core-key], .core-row[data-core-q] { cursor: pointer; }
.core-row.is-active { background: color-mix(in srgb, var(--brand) 12%, transparent); outline: 1px solid var(--brand); }
/* 중립(미방문·여유 있는 계약): 연한 --baseline은 다크 모드에서 배경과 구분이 안 돼 한 단계 진한 회색 */
.role-none { background: var(--text-muted); }
.core-cols { display: flex; align-items: flex-end; gap: 6px; height: 186px; }
.core-col { flex: 1; min-width: 0; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; height: 100%; border-radius: 6px; }
.core-col:hover, .core-col:focus { background: var(--page-plane); outline: none; }
.core-col-val { font-size: 11.5px; font-weight: 700; color: var(--text-primary); font-variant-numeric: tabular-nums; margin-bottom: 3px; }
.core-col-bar { width: 62%; max-width: 46px; border-radius: 4px 4px 0 0; min-height: 2px; }
.core-col-label { width: 100%; min-height: 40px; text-align: center; font-size: 11px; color: var(--text-secondary); border-top: 1px solid var(--baseline); padding-top: 5px;
                  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.core-col-sub { display: block; font-size: 10.5px; color: var(--text-muted); }
.core-map-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 18px; margin: 0 0 8px; }
.core-legend { margin: 0; align-items: center; }
.core-size-note { color: var(--text-muted); font-size: 12px; }
.core-coord-note { margin-left: auto; color: var(--text-muted); font-size: 12px; }
.core-sign-key { border-radius: 50%; background: var(--surface-1); border: 3px solid var(--critical); box-sizing: border-box; }
.core-map { height: 520px; border: 1px solid var(--border); border-radius: 14px; overflow: hidden; margin-bottom: 4px; background: var(--page-plane); }
.core-map-empty { height: auto; padding: 18px 20px; font-size: 13px; color: var(--text-secondary); }
.core-pop { font-size: 12px; line-height: 1.45; min-width: 220px; }
.core-pop-title { font-weight: 800; font-size: 13.5px; margin-bottom: 4px; }
.core-pop-note { margin: 4px 0; padding: 5px 7px; background: #f6f6f3; border-radius: 6px; color: #333; }
.core-pop-addr { color: #666; margin: 2px 0 6px; }
.core-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin: 0 0 10px; }
.core-toolbar .filter-input { max-width: 360px; }
.core-table-scroll { max-height: 640px; }
#coreTable td { vertical-align: top; }
#coreTable th[data-core-sort] { cursor: pointer; user-select: none; }
#coreTable tbody tr[data-core-row] { cursor: pointer; }
#coreTable tbody tr[data-core-row]:hover td { background: var(--page-plane); }
#coreTable tr.core-active td { background: color-mix(in srgb, var(--brand) 12%, var(--surface-1)); }
#coreTable tr.core-sign td { background: color-mix(in srgb, var(--critical) 7%, var(--surface-1)); }
#coreTable td.core-note { max-width: 380px; min-width: 220px; white-space: normal; font-size: 12px; color: var(--text-secondary); }
#coreTable td.core-novisit { color: var(--text-muted); }
#coreTable tr.core-detail td { background: var(--page-plane); white-space: normal; padding: 14px 16px; }
.core-state { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; font-weight: 600; white-space: nowrap; }
.core-state::before { content: ''; width: 8px; height: 8px; border-radius: 50%; background: var(--baseline); }
.core-state.s-방문완료::before { background: var(--good); }
.core-state.s-해지징후::before { background: var(--critical); }
.core-dday { margin-left: 6px; font-size: 11px; color: var(--text-muted); font-variant-numeric: tabular-nums; }
.core-dday.soon { color: var(--critical); font-weight: 700; }
.core-detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 260px), 1fr)); gap: 18px; }
.core-detail-title { font-size: 12px; font-weight: 700; color: var(--text-secondary); margin-bottom: 8px; }
.core-hist { border-left: 2px solid var(--baseline); padding: 0 0 10px 10px; margin-left: 3px; font-size: 12px; }
.core-hist-head { font-weight: 600; color: var(--text-primary); }
.core-hist-body { color: var(--text-secondary); margin-top: 2px; line-height: 1.5; }
.core-kv { display: grid; grid-template-columns: 92px minmax(0, 1fr); gap: 4px 10px; font-size: 12px; margin: 0 0 10px; }
.core-kv dt { color: var(--text-muted); }
.core-kv dd { margin: 0; color: var(--text-primary); }
.cust-map-bar { margin-bottom: 10px; gap: 12px 18px; }
.cust-map-toggle { font-weight: 700; padding: 8px 16px; border-color: var(--brand); color: var(--brand); background: var(--surface-1); }
.cust-map-toggle.active { color: white; }
.cust-map { height: 600px; }
.core-map-tall { height: min(680px, 72vh); min-height: 420px; }
#coreSectionWrap { scroll-margin-top: calc(var(--stick-top, 64px) + 58px); }
.cust-map[hidden] { display: none; }
.cust-cluster-wrap { background: none; border: 0; }
/* 지도 공통: 지도 칸 + 도구 줄 + 안내 + 가까운 곳 + 로드뷰 (DataIntelMapLib) */
.dim-map { position: relative; }
.dim-map-canvas { position: absolute; inset: 0; }
.dim-map-bar { position: absolute; top: 10px; left: 10px; right: 56px; z-index: 1000; display: flex; flex-wrap: wrap; gap: 6px; pointer-events: none; }
.dim-map-tool { pointer-events: auto; border: 1px solid rgba(0,0,0,0.18); background: #fff; color: #222; font: inherit; font-size: 12px; font-weight: 600;
                padding: 6px 10px; border-radius: 8px; cursor: pointer; box-shadow: 0 1px 4px rgba(0,0,0,0.25); white-space: nowrap; }
.dim-map-tool:hover { background: #f1f5f9; }
.dim-map-tool.on { background: #2563eb; color: #fff; border-color: #2563eb; }
.dim-map-tool:disabled { opacity: 0.6; cursor: progress; }
.dim-map-msg { position: absolute; left: 50%; bottom: 18px; transform: translateX(-50%); z-index: 1001; background: rgba(17,24,39,0.92); color: #fff;
               font-size: 12.5px; padding: 8px 14px; border-radius: 999px; max-width: calc(100% - 40px); text-align: center; }
.dim-map-msg[hidden] { display: none; }
.dim-map-near { position: absolute; left: 10px; bottom: 34px; z-index: 1000; width: 240px; max-width: calc(100% - 20px); background: #fff; color: #222;
                border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.3); padding: 8px; font-size: 12px; }
.dim-map-near-head { position: relative; font-weight: 700; padding: 2px 22px 6px 4px; }
.dim-map-near-item { display: flex; justify-content: space-between; gap: 8px; width: 100%; border: 0; background: none; font: inherit; color: inherit;
                     text-align: left; padding: 5px 4px; border-radius: 6px; cursor: pointer; }
.dim-map-near-item:hover, .dim-map-near-item:focus-visible { background: #f1f5f9; outline: none; }
.dim-map-near-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dim-map-near-dist { color: #1d4ed8; font-weight: 700; white-space: nowrap; font-variant-numeric: tabular-nums; }
.dim-route-wrap { background: none; border: 0; }
/* 순번: 마커 위쪽에 얹는다 (마커 색이 보이게). 구간 거리: 점선 가운데 */
.dim-route-no { display: inline-flex; align-items: center; justify-content: center; min-width: 20px; height: 20px; padding: 0 5px; box-sizing: border-box; border-radius: 10px;
                background: #1d4ed8; color: #fff; font-size: 11.5px; font-weight: 800; font-variant-numeric: tabular-nums; border: 2px solid #fff; box-shadow: 0 1px 4px rgba(0,0,0,0.45); }
.dim-route-pin { transform: translate(-50%, -165%); pointer-events: none; }
.dim-route-leg { display: inline-block; transform: translate(-50%, -50%); background: #fff; color: #1d4ed8; font-size: 11px; font-weight: 700; padding: 1px 6px; border-radius: 999px;
                 border: 1px solid #1d4ed8; white-space: nowrap; font-variant-numeric: tabular-nums; box-shadow: 0 1px 3px rgba(0,0,0,0.25); pointer-events: none; }
.dim-route-panel { width: 280px; }
.dim-route-pick { display: flex; align-items: center; gap: 5px; padding: 0 4px 6px; color: #555; font-size: 11.5px; }
.dim-route-n { border: 1px solid #cbd5e1; background: #fff; color: #222; font: inherit; font-size: 11.5px; padding: 2px 8px; border-radius: 999px; cursor: pointer; }
.dim-route-n.on { background: #1d4ed8; border-color: #1d4ed8; color: #fff; font-weight: 700; }
.dim-route-list { max-height: 200px; overflow-y: auto; }
.dim-route-list .dim-map-near-item { justify-content: flex-start; align-items: center; }
.dim-route-list .dim-route-no { border: 0; box-shadow: none; flex: none; }
.dim-route-list .dim-map-near-name { flex: 1; }
.dim-route-foot { padding: 6px 4px 0; color: #777; font-size: 11px; }
.dim-pop-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 8px; }
.dim-pop-actions button.pending-map-link { background: #fff; font-family: inherit; cursor: pointer; }
.dim-pop-actions button.pending-map-link:hover { background: var(--brand); }
.dim-pop-dist { font-size: 11.5px; color: #1d4ed8; font-weight: 700; margin-right: 2px; }
.dim-me { width: 16px; height: 16px; border-radius: 50%; background: #2563eb; border: 3px solid #fff; box-shadow: 0 0 0 2px rgba(37,99,235,0.45), 0 1px 4px rgba(0,0,0,0.4); }
.dim-rv { position: absolute; top: 0; right: 0; bottom: 0; width: 50%; z-index: 1002; background: #111; display: flex; flex-direction: column; }
.dim-rv[hidden] { display: none; }
.dim-rv-head { display: flex; align-items: center; gap: 8px; padding: 6px 8px; background: #fff; color: #222; font-size: 12.5px; font-weight: 700; }
.dim-rv-head .dim-map-tool { box-shadow: none; }
.dim-rv-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dim-rv-view { flex: 1; min-height: 0; }
.dim-rv-open .dim-map-canvas { right: 50%; }
.dim-rv-open .dim-map-bar { right: calc(50% + 56px); }
.dim-rv-dot { width: 18px; height: 18px; border-radius: 50%; background: #f97316; border: 3px solid #fff; box-shadow: 0 1px 5px rgba(0,0,0,0.5); }
.dim-map-full { position: fixed !important; inset: 0; z-index: 10000; height: auto !important; min-height: 0 !important; border-radius: 0 !important; margin: 0 !important; }
.dim-map-lock, .dim-map-lock body { overflow: hidden; }
@media (max-width: 700px) {
    .dim-rv { top: 50%; width: 100%; }
    .dim-rv-open .dim-map-canvas { right: 0; bottom: 50%; }
    .dim-rv-open .dim-map-bar { right: 56px; }
    .dim-map-tool { padding: 7px 9px; }
}
/* 카카오맵용 마커·풍선 (Leaflet은 자체 스타일) */
.dim-kmarker { border-radius: 50%; box-sizing: border-box; cursor: pointer; box-shadow: 0 1px 3px rgba(0,0,0,0.45); opacity: 0.94; }
.dim-kpop { cursor: default; }
.dim-kpop-box { position: relative; min-width: 250px; max-width: 340px; background: #fff; color: #222; border-radius: 12px; padding: 13px 16px 12px;
                box-shadow: 0 3px 14px rgba(0,0,0,0.4); font-size: 12px; text-align: left; white-space: normal; }
.dim-kpop-box::after { content: ''; position: absolute; left: 50%; bottom: -8px; margin-left: -8px; border: 8px solid transparent; border-top-color: #fff; border-bottom: 0; }
.dim-kpop-close { position: absolute; top: 4px; right: 6px; border: 0; background: none; font-size: 18px; line-height: 1; color: #777; cursor: pointer; padding: 4px; }
.dim-kpop-box .core-pop-title { padding-right: 18px; }
.cust-cluster { width: 100%; height: 100%; border-radius: 50%; display: flex; align-items: center; justify-content: center; box-shadow: 0 1px 5px rgba(0,0,0,0.4); }
.cust-cluster span { width: calc(100% - 12px); height: calc(100% - 12px); border-radius: 50%; background: #fff; color: #111; display: flex; align-items: center; justify-content: center;
                     font-size: 12px; font-weight: 800; font-variant-numeric: tabular-nums; }
.cust-pop { min-width: 250px; max-height: 330px; overflow-y: auto; padding-right: 4px; }
.cust-pop-item { padding: 0 0 8px; margin: 0 0 8px; border-bottom: 1px solid #e5e5e0; }
.cust-pop-name { font-weight: 700; font-size: 12.5px; margin-bottom: 2px; }
.cust-pop-all summary { cursor: pointer; color: #2563eb; font-size: 11.5px; margin-top: 4px; }
.cust-pop-all .core-kv { margin: 6px 0 0; grid-template-columns: 104px minmax(0, 1fr); font-size: 11.5px; }
.cust-pop .core-kv dt { color: #777; } .cust-pop .core-kv dd { color: #111; word-break: break-all; }
.cust-pop .core-state { color: #111; }
.core-state.s-처리완료::before { background: var(--good); }
.core-state.s-접수::before { background: var(--warning); }
.core-state.s-미접수::before { background: var(--critical); }
.core-state.s-미처리::before { background: var(--text-muted); }
/* 코어고객만 보는 화면 (총괄DB 없이 9번만 올린 웹 / 코어고객만 공유) */
body.core-only .dash-sec:not(#secCore), body.core-only #dashNav, body.core-only .dash-admin, body.core-only .dash-anchor,
body.core-only .global-filter-bar:not(.core-filter-bar) { display: none !important; }
body.core-only #secCore > summary { display: none; }
.core-foot { margin: 12px 0 0; }
.core-page-note { font-size: 12px; color: var(--text-muted); margin: 0 0 14px; }
@media (max-width: 640px) { .core-row.wide { grid-template-columns: 110px 1fr 40px; } .core-map { height: 380px; } }
.perf-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 380px), 1fr)); gap: 16px; align-items: start; margin-bottom: 16px; }
.perf-card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
.perf-band { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 14px; font-weight: 700; font-size: 14.5px;
             color: var(--text-primary); background: color-mix(in srgb, var(--brand) 16%, var(--surface-1)); }
.perf-copy { font-size: 11.5px; font-weight: 600; padding: 4px 9px; border-radius: 6px; border: 1px solid var(--border); background: var(--surface-1); color: var(--text-primary); cursor: pointer; white-space: nowrap; }
.perf-copy:disabled { opacity: .5; cursor: default; }
.table-scroll.perf-scroll { max-height: none; overflow-x: auto; overflow-y: visible; }  /* 스크롤 없이 한 번에 (.table-scroll의 640px 제한 해제) */
.perf-toolbar { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 12px; }
.perf-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.perf-table th { position: sticky; top: 0; z-index: 1; background: var(--brand-dark); color: #fff; font-weight: 700; padding: 8px 6px; text-align: center; white-space: nowrap; }
.perf-table th.perf-th-alert { color: #ffb4b4; }
.perf-table td { padding: 5px 8px; border-bottom: 1px solid var(--grid-line); border-left: 1px solid var(--grid-line); text-align: center; font-variant-numeric: tabular-nums; }
.perf-table td.perf-branch { font-weight: 700; color: var(--text-primary); background: var(--surface-1); vertical-align: middle; }
.perf-table td.perf-name { white-space: nowrap; }
.perf-table td.perf-alert { color: var(--critical); }
.perf-table tr.perf-low td:not(.perf-branch) { background: color-mix(in srgb, var(--warning) 38%, var(--surface-1)); }
.perf-table tr.perf-sub td { background: color-mix(in srgb, var(--baseline) 30%, var(--surface-1)); font-weight: 700; }
.perf-table tr.perf-total td { background: color-mix(in srgb, var(--brand) 12%, var(--surface-1)); font-weight: 800; border-top: 2px solid var(--baseline); }
.perf-table td.perf-warn { color: var(--critical); }

/* ---- 섹션 메뉴 (상단 고정) ---- */
.dash-nav { position: sticky; top: var(--stick-top, 64px); z-index: 19; background: color-mix(in srgb, var(--surface-1) 94%, transparent);
            backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px); border-bottom: 1px solid var(--border); }
.dash-nav[hidden] { display: none; }
.dash-nav-inner { max-width: 1360px; margin: 0 auto; padding: 6px 32px; display: flex; align-items: center; gap: 12px; }
.dash-nav-links { display: flex; gap: 4px; overflow-x: auto; scrollbar-width: none; flex: 1; min-width: 0; }
.dash-nav-links::-webkit-scrollbar { display: none; }
.dash-nav-links a { flex: none; padding: 7px 12px; border-radius: 8px; font-size: 13px; font-weight: 600; color: var(--text-secondary); text-decoration: none; white-space: nowrap; }
.dash-nav-links a:hover { background: var(--page-plane); color: var(--text-primary); }
.dash-nav-links a.active { background: color-mix(in srgb, var(--brand) 14%, var(--surface-1)); color: var(--brand); }
.dash-nav-filter { flex: none; font-size: 12px; color: var(--text-muted); white-space: nowrap; font-variant-numeric: tabular-nums; }
.dash-sec, .dash-anchor { scroll-margin-top: calc(var(--stick-top, 64px) + 58px); }
.sec-hint { font-size: 12px; font-weight: 400; color: var(--text-muted); margin-left: 2px; }
.dash-admin { margin-top: 40px; }
#overviewTiles:empty { display: none; }
@media (max-width: 640px) {
    .dash-nav-inner { padding: 6px 12px; }
    .dash-nav-filter, .sec-hint { display: none; }
}
.dv-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin-bottom: 16px; }
.dv-tile { background: var(--surface-1); border: 1px solid var(--border); border-radius: 14px; padding: 16px 18px; }
.dv-tile-lead { border-color: color-mix(in srgb, var(--brand) 45%, var(--border)); }
.dv-tile-label { font-size: 12.5px; color: var(--text-secondary); font-weight: 600; }
.dv-tile-value { font-size: 30px; font-weight: 700; color: var(--text-primary); margin: 4px 0 8px; letter-spacing: -0.5px; }
.dv-tile-lead .dv-tile-value { font-size: 36px; }
.dv-tile-status { display: flex; align-items: center; gap: 5px; font-size: 12px; color: var(--text-secondary); margin-top: 8px; }
.dv-tile-sub { font-size: 11.5px; color: var(--text-muted); margin-top: 4px; font-variant-numeric: tabular-nums; }
.dv-status-icon { font-size: 10px; }
.dv-status-critical { color: var(--critical); } .dv-status-warning { color: var(--warning); } .dv-status-good { color: var(--good); }
.dv-meter { height: 8px; border-radius: 4px; overflow: hidden; }
.dv-meter-critical { background: color-mix(in srgb, var(--critical) 16%, var(--surface-1)); }
.dv-meter-warning { background: color-mix(in srgb, var(--warning) 20%, var(--surface-1)); }
.dv-meter-good { background: color-mix(in srgb, var(--good) 16%, var(--surface-1)); }
.dv-meter-fill { height: 100%; border-radius: 0 4px 4px 0; }
.dv-grid-2 { grid-template-columns: repeat(auto-fit, minmax(min(100%, 440px), 1fr)); margin-bottom: 16px; }
.dv-card { display: flex; flex-direction: column; }
.dv-rank { list-style: none; margin: 4px 0 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.dv-rank-row { display: grid; grid-template-columns: 20px minmax(0, 1fr) 90px 52px 58px; align-items: center; gap: 8px; padding: 5px 6px; border-radius: 6px; cursor: pointer; font-size: 12px; }
.dv-rank-row:hover, .dv-rank-row:focus { background: var(--page-plane); outline: none; }
.dv-rank-no { color: var(--text-muted); font-weight: 700; text-align: right; font-variant-numeric: tabular-nums; }
.dv-rank-name { color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dv-type-badge { display: inline-block; font-size: 10px; font-weight: 700; color: var(--text-secondary); border: 1px solid var(--border); border-radius: 4px; padding: 0 4px; margin-right: 6px; }
.dv-rank-value { text-align: right; font-weight: 700; color: var(--text-primary); font-variant-numeric: tabular-nums; }
.dv-rank-sub { text-align: right; color: var(--text-muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.dv-scatter-wrap { width: 100%; }
.dv-scatter { width: 100%; height: auto; display: block; overflow: visible; }
.dv-grid { stroke: var(--grid-line); stroke-width: 1; }
.dv-axis { stroke: var(--baseline); stroke-width: 1; }
.dv-tick { fill: var(--text-muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.dv-axis-title { fill: var(--text-muted); font-size: 11px; }
.dv-ref { stroke: var(--text-muted); stroke-width: 1; opacity: 0.6; }
.dv-ref-label { fill: var(--text-secondary); font-size: 11px; font-weight: 600; paint-order: stroke; stroke: var(--surface-1); stroke-width: 3px; }
.dv-pt { cursor: pointer; outline: none; }
.dv-hit { fill: transparent; }
.dv-dot { fill: var(--s1); stroke: var(--surface-1); stroke-width: 2; }
.dv-pt-flag .dv-dot { fill: var(--critical); }
.dv-pt:hover .dv-dot, .dv-pt:focus .dv-dot { r: 7; }
.dv-pt-active .dv-dot { stroke: var(--text-primary); }
.dv-pt-label { fill: var(--text-primary); font-size: 11.5px; font-weight: 600; paint-order: stroke; stroke: var(--surface-1); stroke-width: 3px; }
.dv-dot-key { width: 10px; height: 10px; border-radius: 50%; background: var(--s1); display: inline-block; }
.dv-dot-key-flag { background: var(--critical); }
.dv-tip { position: fixed; z-index: 9999; display: none; pointer-events: none; max-width: 320px; background: var(--surface-1); color: var(--text-primary);
          border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; font-size: 12px; box-shadow: 0 6px 20px rgba(0,0,0,0.18); }
.dv-tip-head { font-weight: 700; margin-bottom: 2px; }
.dv-tip-line { color: var(--text-secondary); font-variant-numeric: tabular-nums; }
@media (max-width: 640px) { .dv-rank-row { grid-template-columns: 18px minmax(0, 1fr) 60px 48px; } .dv-rank-sub { display: none; } }
.zone-seg.role-warning { color: #1f2937; }
.zone-card[data-mode="zone_owner"] .zone-row { grid-template-columns: 190px 1fr 150px; }
.zone-mode-row { margin: 8px 0 6px; align-items: center; }
.zone-owner-pick { display: inline-flex; align-items: center; gap: 6px; margin-left: 8px; font-size: 12.5px; font-weight: 600; color: var(--text-secondary); }
.zone-owner-pick select { max-width: 240px; }
.zone-owner-chip { font-size: 12px; color: var(--text-secondary); background: color-mix(in srgb, var(--brand) 10%, var(--surface-1)); border: 1px solid color-mix(in srgb, var(--brand) 35%, var(--border)); border-radius: 8px; padding: 6px 10px; margin: 2px 0 8px; }
.zone-status-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; margin: 4px 0 14px; }
.zone-status-label { font-size: 12.5px; font-weight: 700; color: var(--text-secondary); }
.filter-pill.ghost-pill { background: transparent; }
.zone-row[data-zone-focus] { cursor: pointer; }
.zone-row-active { background: color-mix(in srgb, var(--brand) 12%, transparent); outline: 1px solid var(--brand); }
.zone-facility-head { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; }
.zone-facility-head .chart-title { margin: 0; margin-right: auto; }
.zone-facility-scroll { max-height: 520px; overflow-y: auto; }
.zone-table-toggle { margin-top: 14px; }
.zone-table-toggle > summary { cursor: pointer; font-size: 12.5px; font-weight: 600; color: var(--text-secondary); margin-bottom: 8px; }
@media (max-width: 640px) { .zone-row { grid-template-columns: 104px 1fr 110px; gap: 6px; } }

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
/* 핵심 요약 표: 그룹별 색 헤더 (SP=s3, SE=s2, SG=s1 -- 차트 범례와 같은 색), 그룹 경계선, 하위 순위 음영 */
.table-scroll.progress-table-scroll { max-height: none; }
.progress-table { border-collapse: separate; border-spacing: 0; }
.progress-table thead th { position: static; text-align: center; padding: 7px 6px; color: var(--text-primary); border-bottom: 1px solid var(--grid-line); }
.progress-table th.pg-branch-th { background: var(--text-primary); color: var(--surface-1); font-size: 13px; font-weight: 800; min-width: 64px; }
.progress-table th.pg-g { font-size: 14px; font-weight: 800; letter-spacing: 0.5px; border-top: 4px solid; padding: 8px 6px; }
.progress-table th.pg-sub { font-size: 11.5px; font-weight: 700; color: var(--text-secondary); }
.progress-table th.pg-g.pg-SP { background: color-mix(in srgb, var(--s3) 24%, var(--surface-1)); border-top-color: var(--s3); }
.progress-table th.pg-g.pg-SE { background: color-mix(in srgb, var(--s2) 22%, var(--surface-1)); border-top-color: var(--s2); }
.progress-table th.pg-g.pg-SG { background: color-mix(in srgb, var(--s1) 22%, var(--surface-1)); border-top-color: var(--s1); }
.progress-table th.pg-g.pg-ALL { background: color-mix(in srgb, var(--text-primary) 12%, var(--surface-1)); border-top-color: var(--text-primary); }
.progress-table th.pg-sub.pg-SP { background: color-mix(in srgb, var(--s3) 9%, var(--surface-1)); }
.progress-table th.pg-sub.pg-SE { background: color-mix(in srgb, var(--s2) 8%, var(--surface-1)); }
.progress-table th.pg-sub.pg-SG { background: color-mix(in srgb, var(--s1) 8%, var(--surface-1)); }
.progress-table th.pg-sub.pg-ALL { background: color-mix(in srgb, var(--text-primary) 5%, var(--surface-1)); }
.progress-table td { border-bottom: 1px solid var(--grid-line); padding: 7px 6px; }
.progress-table .grp-start { border-left: 2px solid color-mix(in srgb, var(--text-primary) 35%, var(--surface-1)); }
.progress-table td.progress-branch { font-size: 13px; text-align: center; }
.progress-table tr.pg-bottom td:not(.progress-cell) { background: color-mix(in srgb, var(--critical) 7%, var(--surface-1)); }
.progress-table td.pg-rank-bottom { color: var(--critical); }
.pg-note { display: flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--text-muted); margin: 8px 0 18px; }
.pg-note-swatch { width: 12px; height: 12px; border-radius: 3px; background: color-mix(in srgb, var(--critical) 12%, var(--surface-1)); border: 1px solid color-mix(in srgb, var(--critical) 35%, var(--surface-1)); display: inline-block; }
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
.pending-status.status-처리완료 { color: var(--good); }
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
const pwdInput = document.getElementById('pwd');  // absent on the web uploader page (web_app.py)
if (pwdInput) pwdInput.addEventListener('keydown', function(e) {
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

// ===== 섹션 메뉴: 상단 고정 높이 맞춤 / 클릭 이동(접힌 섹션은 펼침) / 현재 섹션 강조 =====
function initDashNav() {
    const nav = document.getElementById('dashNav');
    if (!nav) return;
    const topbar = document.querySelector('.topbar');
    const setTop = () => document.documentElement.style.setProperty('--stick-top', (topbar ? topbar.offsetHeight : 0) + 'px');
    setTop();
    if (topbar && window.ResizeObserver) new ResizeObserver(setTop).observe(topbar);
    const links = Array.from(nav.querySelectorAll('a[data-sec]'));
    nav.addEventListener('click', (e) => {
        const a = e.target.closest('a[data-sec]');
        if (!a) return;
        e.preventDefault();
        const sec = document.getElementById(a.dataset.sec);
        if (!sec) return;
        if (sec.tagName === 'DETAILS') sec.open = true;
        setTop();
        sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
        links.forEach(l => l.classList.toggle('active', l === a));
    });
    links.forEach(l => { const s = document.getElementById(l.dataset.sec); if (!s || s.hidden) l.hidden = true; });
    if ('IntersectionObserver' in window) {
        const io = new IntersectionObserver(entries => {
            entries.forEach(en => { if (en.isIntersecting) links.forEach(l => l.classList.toggle('active', l.dataset.sec === en.target.id)); });
        }, { rootMargin: '-40% 0px -55% 0px' });
        links.forEach(l => { const s = document.getElementById(l.dataset.sec); if (s) io.observe(s); });
    }
}
document.addEventListener('DOMContentLoaded', initDashNav);

// ===== 지도 -- 코어고객 지도와 '지도로 보기'가 같이 쓴다 =====
// 배경 지도는 두 가지: 카카오맵(리포트에 JavaScript 키가 실려 있고, 카카오에 등록한 도메인에서 열었을 때)
// 또는 Leaflet + Esri 거리지도(키 없음 / PC에서 파일로 직접 열었을 때 / 카카오 로딩 실패). 화면 쪽 코드는
// open()이 돌려주는 같은 모양의 객체(setMarkers / fit / focus / resize / destroy)만 쓴다.
// 지도 위 도구(현위치·전체 보기·크게 보기, 카카오맵이면 스카이뷰·교통·로드뷰)와 풍선의 로드뷰/길찾기도 여기서 붙인다.
const DataIntelMapLib = (function () {
    const BASE = 'https://cdnjs.cloudflare.com/ajax/libs/';
    const DEFAULT_CENTER = [37.6, 127.2];
    const pending = {};
    let lastHere = null;  // 한 번 잡은 현위치 -- 조건을 바꿔 지도를 새로 그려도 표시와 '가까운 곳'을 유지한다
    const lastRoute = { on: false, n: 10 };  // 방문 루트 켜짐 / 몇 곳까지 -- 지도를 새로 그려도 유지
    const config = (function () { const e = document.getElementById('mapConfig'); if (!e) return {}; try { return JSON.parse(e.textContent) || {}; } catch (err) { return {}; } })();
    const mk = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = text; return e; };
    function css(href) {
        if (document.querySelector('link[href="' + href + '"]')) return;
        const l = document.createElement('link'); l.rel = 'stylesheet'; l.href = href; document.head.appendChild(l);
    }
    function script(src) {
        return new Promise((resolve, reject) => {
            const s = document.createElement('script'); s.src = src; s.onload = () => resolve(); s.onerror = () => reject(new Error(src));
            document.head.appendChild(s);
        });
    }
    function once(name, start) {  // 실패하면 다음에 다시 시도할 수 있게 비운다
        if (!pending[name]) pending[name] = start().catch(e => { pending[name] = null; throw e; });
        return pending[name];
    }
    const leaflet = () => once('leaflet', () => { css(BASE + 'leaflet/1.9.4/leaflet.css'); return window.L ? Promise.resolve() : script(BASE + 'leaflet/1.9.4/leaflet.js'); });
    const cluster = () => once('cluster', () => leaflet().then(() => {
        css(BASE + 'leaflet.markercluster/1.5.3/MarkerCluster.css');
        return script(BASE + 'leaflet.markercluster/1.5.3/leaflet.markercluster.js');
    }));
    // 카카오맵 SDK: 등록 안 된 도메인·파일(file://)에서는 스크립트가 거절된다 -> 실패로 처리해 Leaflet으로 넘어간다
    const kakao = () => once('kakao', () => new Promise((resolve, reject) => {
        // 시간 제한은 SDK 스크립트를 받은 뒤부터 잰다 -- 큰 리포트를 계산하느라 화면이 잠시 멈춘 사이(느린 PC)
        // 제한이 먼저 끝나 기본 지도로 넘어가 버리지 않게. 스크립트 자체가 거절되면(도메인 미등록) 바로 실패한다.
        const ready = () => {
            setTimeout(() => reject(new Error('kakao timeout')), 20000);  // 이미 끝났으면 아무 일도 없다
            try { window.kakao.maps.load(() => resolve()); } catch (e) { reject(e); }
        };
        if (window.kakao && window.kakao.maps) { ready(); return; }
        script('https://dapi.kakao.com/v2/maps/sdk.js?appkey=' + encodeURIComponent(config.kakaoJsKey) + '&autoload=false&libraries=clusterer')
            .then(ready, () => reject(new Error('kakao sdk')));
    }));

    // 묶음(숫자 원): 테두리 = 묶인 항목의 비율(ringOf가 CSS 배경을 만든다), 가운데 숫자 = 건수
    function sumItems(items) {
        const counts = {};
        let rows = 0;
        items.forEach(it => { Object.keys(it.counts || {}).forEach(k => { counts[k] = (counts[k] || 0) + it.counts[k]; }); rows += it.rows || 1; });
        return { counts, rows, size: rows >= 300 ? 54 : rows >= 50 ? 46 : 38 };
    }
    function clusterEl(sum, ringOf) {
        const d = mk('div', 'cust-cluster');
        d.style.background = (ringOf && ringOf(sum.counts)) || '#898781';
        d.style.width = sum.size + 'px'; d.style.height = sum.size + 'px';
        d.appendChild(mk('span', null, sum.rows.toLocaleString('ko-KR')));
        return d;
    }

    // ---- 두 지도가 같이 쓰는 부품 ----
    // holder 안: 지도 칸(.dim-map-canvas) + 도구 줄 + 안내 문구 + (필요할 때) 가까운 곳 목록·로드뷰
    function shell(holder) {
        holder.textContent = '';
        holder.classList.add('dim-map');
        const ui = { holder, canvas: mk('div', 'dim-map-canvas'), bar: mk('div', 'dim-map-bar'), msg: mk('div', 'dim-map-msg'), near: null, timer: null };
        ui.msg.hidden = true;
        ui.msg.setAttribute('role', 'status');
        holder.appendChild(ui.canvas); holder.appendChild(ui.bar); holder.appendChild(ui.msg);
        return ui;
    }
    function say(ui, text) {
        ui.msg.textContent = text; ui.msg.hidden = false;
        clearTimeout(ui.timer);
        ui.timer = setTimeout(() => { ui.msg.hidden = true; }, 4200);
    }
    function tool(ui, label, title, onClick) {
        const b = mk('button', 'dim-map-tool', label);
        b.type = 'button'; b.title = title; b.setAttribute('aria-label', title);
        b.addEventListener('click', e => { e.stopPropagation(); onClick(b); });
        ui.bar.appendChild(b);
        return b;
    }
    const press = (b, on) => { b.classList.toggle('on', on); b.setAttribute('aria-pressed', on ? 'true' : 'false'); };
    // 두 좌표 사이 거리(km)
    function km(a, b, c, d) {
        const R = 6371, rad = x => x * Math.PI / 180, p = rad(c - a), q = rad(d - b);
        const h = Math.sin(p / 2) * Math.sin(p / 2) + Math.cos(rad(a)) * Math.cos(rad(c)) * Math.sin(q / 2) * Math.sin(q / 2);
        return 2 * R * Math.asin(Math.sqrt(h));
    }
    const distText = d => d < 1 ? Math.round(d * 1000) + 'm' : (d < 10 ? d.toFixed(1) : Math.round(d)) + 'km';
    function locate() {
        return new Promise((resolve, reject) => {
            if (!navigator.geolocation) { reject('이 브라우저는 현위치를 지원하지 않습니다'); return; }
            navigator.geolocation.getCurrentPosition(
                p => resolve({ lat: p.coords.latitude, lng: p.coords.longitude, acc: p.coords.accuracy || 0 }),
                e => reject(e.code === 1 ? '위치 권한이 꺼져 있습니다 -- 브라우저 주소창의 자물쇠에서 위치를 허용하세요' : '현위치를 찾지 못했습니다 (GPS·네트워크 확인)'),
                { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 });
        });
    }
    // 현위치에서 가까운 순 5곳 -- 누르면 그 마커로 간다
    function nearPanel(ui, items, here, focus) {
        if (ui.near) ui.near.remove();
        const ranked = items.map((it, i) => ({ it, i, d: km(here.lat, here.lng, it.lat, it.lng) })).sort((a, b) => a.d - b.d).slice(0, 5);
        if (!ranked.length) return;
        const box = mk('div', 'dim-map-near');
        const head = mk('div', 'dim-map-near-head', '현위치에서 가까운 곳');
        const x = mk('button', 'dim-kpop-close', '×'); x.type = 'button'; x.setAttribute('aria-label', '닫기');
        x.addEventListener('click', () => { box.remove(); ui.near = null; });
        head.appendChild(x); box.appendChild(head);
        ranked.forEach(r => {
            const b = mk('button', 'dim-map-near-item'); b.type = 'button';
            b.appendChild(mk('span', 'dim-map-near-name', r.it.name || r.it.tip || '(이름 없음)'));
            b.appendChild(mk('span', 'dim-map-near-dist', distText(r.d)));
            b.addEventListener('click', () => focus(r.i));
            box.appendChild(b);
        });
        ui.holder.appendChild(box);
        ui.near = box;
    }
    // 방문 루트: 현위치에서 가장 가까운 곳 -> 거기서 가장 가까운 곳 ... 순서로 n곳 (직선거리 기준)
    function routeStops(items, here, n) {
        const left = items.map((it, i) => ({ it, i }));
        const out = [];
        let cur = here, total = 0;
        while (left.length && out.length < n) {
            let best = 0, bd = Infinity;
            left.forEach((x, k) => { const d = km(cur.lat, cur.lng, x.it.lat, x.it.lng); if (d < bd) { bd = d; best = k; } });
            const x = left.splice(best, 1)[0];
            total += bd;
            out.push({ i: x.i, it: x.it, leg: bd, cum: total, from: cur });
            cur = x.it;
        }
        return out;
    }
    const routeNoEl = n => mk('div', 'dim-route-no dim-route-pin', String(n));
    const routeLegEl = d => mk('div', 'dim-route-leg', distText(d));
    // 방문 순서 목록: 번호 · 이름 · 앞 지점에서의 거리. 몇 곳까지 볼지 고를 수 있다.
    function routePanel(ui, stops, total, focus, onCount, onClose) {
        if (ui.near) ui.near.remove();
        const box = mk('div', 'dim-map-near dim-route-panel');
        const head = mk('div', 'dim-map-near-head', '방문 루트 · 총 ' + (stops.length ? distText(stops[stops.length - 1].cum) : '0m'));
        const x = mk('button', 'dim-kpop-close', '×'); x.type = 'button'; x.setAttribute('aria-label', '방문 루트 끄기');
        x.addEventListener('click', onClose);
        head.appendChild(x); box.appendChild(head);
        const pick = mk('div', 'dim-route-pick');
        pick.appendChild(mk('span', null, '가까운 순'));
        const shown = Math.min(lastRoute.n, total);
        [5, 10, 20].filter(n => n < total).concat([total]).forEach(n => {
            const b = mk('button', 'dim-route-n' + (shown === n ? ' on' : ''), n === total ? '전체 ' + total + '곳' : n + '곳'); b.type = 'button';
            b.addEventListener('click', () => onCount(n));
            pick.appendChild(b);
        });
        box.appendChild(pick);
        const list = mk('div', 'dim-route-list');
        stops.forEach((s, k) => {
            const b = mk('button', 'dim-map-near-item'); b.type = 'button';
            b.appendChild(mk('span', 'dim-route-no', String(k + 1)));
            b.appendChild(mk('span', 'dim-map-near-name', s.it.name || s.it.tip || '(이름 없음)'));
            b.appendChild(mk('span', 'dim-map-near-dist', '+' + distText(s.leg)));
            b.addEventListener('click', () => focus(s.i));
            list.appendChild(b);
        });
        box.appendChild(list);
        box.appendChild(mk('div', 'dim-route-foot', '직선거리 기준 · 현위치에서 가까운 곳부터 차례로'));
        ui.holder.appendChild(box);
        ui.near = box;
    }
    // 풍선 내용 = 화면 쪽이 만든 내용 + (현위치를 잡았으면 거리) + 로드뷰 / 길찾기
    function popupContent(it, here, onRoadview) {
        const box = it.popup();
        const row = mk('div', 'dim-pop-actions');
        if (here) row.appendChild(mk('span', 'dim-pop-dist', '현위치에서 ' + distText(km(here.lat, here.lng, it.lat, it.lng))));
        if (onRoadview) {
            const b = mk('button', 'pending-map-link', '👁 로드뷰'); b.type = 'button';
            b.addEventListener('click', e => { e.stopPropagation(); onRoadview(it); });
            row.appendChild(b);
        } else {
            const a = mk('a', 'pending-map-link', '👁 로드뷰'); a.href = 'https://map.kakao.com/link/roadview/' + it.lat + ',' + it.lng; a.target = '_blank'; a.rel = 'noopener';
            row.appendChild(a);
        }
        const to = mk('a', 'pending-map-link', '🚗 길찾기');
        to.href = 'https://map.kakao.com/link/to/' + encodeURIComponent((it.name || '목적지').split(',').join(' ')) + ',' + it.lat + ',' + it.lng; to.target = '_blank'; to.rel = 'noopener';
        row.appendChild(to);
        box.appendChild(row);
        return box;
    }
    // 크게 보기: 화면 전체를 덮는다 (전체화면 API가 없는 아이폰에서도 되게 CSS로). Esc로 닫힘.
    function bigTool(ui, onChange) {
        const b = tool(ui, '⛶ 크게', '지도를 화면 가득 크게 보기 (Esc로 닫기)', () => set(!ui.holder.classList.contains('dim-map-full')));
        function set(on) {
            ui.holder.classList.toggle('dim-map-full', on);
            document.documentElement.classList.toggle('dim-map-lock', on);
            press(b, on); b.textContent = on ? '✕ 닫기' : '⛶ 크게';
            onChange(on);
        }
        document.addEventListener('keydown', e => { if (e.key === 'Escape' && ui.holder.classList.contains('dim-map-full') && document.body.contains(ui.holder)) set(false); });
        return { close: () => { if (ui.holder.classList.contains('dim-map-full')) set(false); } };
    }
    const isTouch = () => ('ontouchstart' in window) || (navigator.maxTouchPoints || 0) > 0;

    // ---- Leaflet + Esri (키 없이, 파일로 열어도 보인다) ----
    function leafletMap(holder, opts, note) {
        return (opts.cluster ? cluster().catch(() => leaflet()) : leaflet()).then(() => {
            const ui = shell(holder);
            const map = L.map(ui.canvas, { scrollWheelZoom: false, preferCanvas: !!opts.cluster, zoomControl: false });
            L.control.zoom({ position: 'topright' }).addTo(map);
            L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, attribution: 'Tiles &copy; Esri' }).addTo(map);
            const clustered = !!opts.cluster && typeof L.markerClusterGroup === 'function';
            let ringOf = null, handles = [], items = [], waiting = null, lastZoom = 14, here = null, me = null;
            function showMe(p, move) {
                here = lastHere = p;
                if (me) me.forEach(x => x.remove());
                me = [L.circle([p.lat, p.lng], { radius: Math.min(p.acc, 2000), weight: 1, color: '#2563eb', fillColor: '#2563eb', fillOpacity: 0.12, interactive: false }).addTo(map),
                      L.circleMarker([p.lat, p.lng], { radius: 7, weight: 3, color: '#fff', fillColor: '#2563eb', fillOpacity: 1, interactive: false }).addTo(map)];
                if (move) map.setView([p.lat, p.lng], Math.max(map.getZoom(), 12));
                refreshNear(false);
            }
            // 현위치 기준 목록: 방문 루트가 켜져 있으면 루트(점선 + 순번 + 구간 거리), 아니면 가까운 5곳
            let routeLayer = null;
            function clearRoute() { if (routeLayer) { routeLayer.remove(); routeLayer = null; } }
            function refreshNear(fit) {
                clearRoute();
                if (!here) return;
                if (!lastRoute.on) { nearPanel(ui, items, here, api.focus); return; }
                const stops = routeStops(items, here, lastRoute.n);
                const path = [[here.lat, here.lng]].concat(stops.map(s => [s.it.lat, s.it.lng]));
                routeLayer = L.layerGroup().addTo(map);
                L.polyline(path, { color: '#1d4ed8', weight: 4, opacity: 0.95, dashArray: '7 9', lineCap: 'round', interactive: false }).addTo(routeLayer);
                stops.forEach((s, k) => {
                    L.marker([(s.from.lat + s.it.lat) / 2, (s.from.lng + s.it.lng) / 2], { icon: L.divIcon({ html: routeLegEl(s.leg), className: 'dim-route-wrap', iconSize: [0, 0] }), interactive: false, keyboard: false }).addTo(routeLayer);
                    L.marker([s.it.lat, s.it.lng], { icon: L.divIcon({ html: routeNoEl(k + 1), className: 'dim-route-wrap', iconSize: [0, 0] }), interactive: false, keyboard: false, zIndexOffset: 500 }).addTo(routeLayer);
                });
                routePanel(ui, stops, items.length, api.focus, n => { lastRoute.n = n; refreshNear(true); }, () => setRoute(false));
                if (fit && stops.length && holder.offsetWidth) map.fitBounds(L.latLngBounds(path), { padding: [48, 48], maxZoom: 16 });
            }
            function setRoute(on) {
                lastRoute.on = on;
                press(routeBtn, on);
                if (!on) { clearRoute(); if (ui.near) { ui.near.remove(); ui.near = null; } if (here) nearPanel(ui, items, here, api.focus); return; }
                if (here) { refreshNear(true); return; }
                routeBtn.disabled = true;  // 현위치가 먼저 필요하다
                locate().then(p => { showMe(p, false); refreshNear(true); }, m => { say(ui, m); lastRoute.on = false; press(routeBtn, false); }).then(() => { routeBtn.disabled = false; });
            }
            const layer = clustered ? L.markerClusterGroup({ maxClusterRadius: 46, showCoverageOnHover: false, disableClusteringAtZoom: 15,
                iconCreateFunction: c => {
                    const sum = sumItems(c.getAllChildMarkers().map(m => m.options.item));
                    return L.divIcon({ html: clusterEl(sum, ringOf), className: 'cust-cluster-wrap', iconSize: [sum.size, sum.size] });
                } }) : L.layerGroup();
            layer.addTo(map);
            map.setView(DEFAULT_CENTER, 8);
            const api = {
                kind: 'leaflet', note: note || '',
                setMarkers(list, o) {
                    ringOf = (o && o.ringOf) || null;
                    items = list;
                    layer.clearLayers();
                    handles = list.map(it => {
                        const m = L.circleMarker([it.lat, it.lng], { radius: it.r, weight: it.ringW || 2, color: it.ring, fillColor: it.fill, fillOpacity: 0.9, item: it });
                        m.bindPopup(() => popupContent(it, here, null), { maxWidth: 340 });
                        if (it.tip) m.bindTooltip(it.tip, { direction: 'top', offset: [0, -6] });
                        if (it.onClick) m.on('click', it.onClick);
                        return m;
                    });
                    if (clustered) layer.addLayers(handles); else handles.forEach(m => m.addTo(layer));
                    refreshNear(false);
                },
                fit(maxZoom) {
                    lastZoom = maxZoom;
                    if (!handles.length) { map.setView(DEFAULT_CENTER, 8); return; }
                    if (!holder.offsetWidth) { waiting = maxZoom; return; }  // 잠금 화면 뒤(크기 0): 보이게 되면 맞춘다
                    waiting = null;
                    map.invalidateSize();
                    map.fitBounds(L.latLngBounds(handles.map(m => m.getLatLng())), { padding: [28, 28], maxZoom });
                },
                focus(i, minZoom) {
                    const m = handles[i];
                    if (!m) return;
                    if (clustered && layer.zoomToShowLayer) { layer.zoomToShowLayer(m, () => m.openPopup()); return; }
                    map.setView(m.getLatLng(), Math.max(map.getZoom(), minZoom || 13));
                    m.openPopup();
                },
                resize() { map.invalidateSize(); },
                destroy() { big.close(); try { map.remove(); } catch (e) { /* 이미 치워짐 */ } },
            };
            tool(ui, '📍 현위치', '내 위치로 이동하고 가까운 곳을 보여줍니다', b => {
                b.disabled = true;
                locate().then(p => showMe(p, true), m => say(ui, m)).then(() => { b.disabled = false; });
            });
            const routeBtn = tool(ui, '🚩 방문 루트', '현위치에서 가까운 순으로 방문 순서를 정해 점선과 거리로 보여줍니다', () => setRoute(!lastRoute.on));
            press(routeBtn, lastRoute.on);
            tool(ui, '🧭 전체', '표시된 곳이 모두 보이게 맞춥니다', () => api.fit(lastZoom));
            const big = bigTool(ui, on => { if (on) map.scrollWheelZoom.enable(); else map.scrollWheelZoom.disable(); setTimeout(() => map.invalidateSize(), 60); });
            L.DomEvent.disableClickPropagation(ui.bar);
            if (lastHere) showMe(lastHere, false);
            if (window.ResizeObserver) new ResizeObserver(() => { if (!holder.offsetWidth) return; map.invalidateSize(); if (waiting != null) api.fit(waiting); }).observe(holder);
            return api;
        });
    }

    // ---- 카카오맵 ----
    // 확대 단계가 반대다: 카카오 level 1 = 가장 가까움. Leaflet zoom z ~ 카카오 level (20 - z).
    function kakaoMap(holder, opts) {
        const K = window.kakao.maps;
        const ui = shell(holder);
        const map = new K.Map(ui.canvas, { center: new K.LatLng(DEFAULT_CENTER[0], DEFAULT_CENTER[1]), level: 11 });
        // 마우스 휠은 페이지 스크롤과 겹치니 '크게 보기'에서만 확대에 쓴다. 손가락 확대(터치 기기)는 항상 켠다.
        map.setZoomable(isTouch());
        map.addControl(new K.ZoomControl(), K.ControlPosition.RIGHT);
        let ringOf = null, handles = [], items = [], waiting = null, lastZoom = 14, popup = null, here = null, me = null;
        let rvMode = false, rvPanel = null, rv = null, rvDot = null;
        const closePopup = () => { if (popup) { popup.setMap(null); popup = null; } };
        function showMe(p, move) {
            here = lastHere = p;
            const pos = new K.LatLng(p.lat, p.lng);
            if (me) me.forEach(x => x.setMap(null));
            me = [new K.Circle({ center: pos, radius: Math.min(p.acc, 2000), strokeWeight: 1, strokeColor: '#2563eb', strokeOpacity: 0.6, fillColor: '#2563eb', fillOpacity: 0.12 }),
                  new K.CustomOverlay({ position: pos, content: mk('div', 'dim-me'), xAnchor: 0.5, yAnchor: 0.5, zIndex: 45 })];
            me.forEach(x => x.setMap(map));
            if (move) { if (map.getLevel() > 8) map.setLevel(8); map.setCenter(pos); }
            refreshNear(false);
        }
        // 현위치 기준 목록: 방문 루트가 켜져 있으면 루트(점선 + 순번 + 구간 거리), 아니면 가까운 5곳
        let routeObjs = [];
        function clearRoute() { routeObjs.forEach(o => o.setMap(null)); routeObjs = []; }
        function refreshNear(fit) {
            clearRoute();
            if (!here) return;
            if (!lastRoute.on) { nearPanel(ui, items, here, api.focus); return; }
            const stops = routeStops(items, here, lastRoute.n);
            const path = [new K.LatLng(here.lat, here.lng)].concat(stops.map(s => new K.LatLng(s.it.lat, s.it.lng)));
            routeObjs.push(new K.Polyline({ path, strokeWeight: 5, strokeColor: '#1d4ed8', strokeOpacity: 0.95, strokeStyle: 'shortdash' }));
            stops.forEach((s, k) => {
                routeObjs.push(new K.CustomOverlay({ position: new K.LatLng((s.from.lat + s.it.lat) / 2, (s.from.lng + s.it.lng) / 2), content: routeLegEl(s.leg), xAnchor: 0, yAnchor: 0, zIndex: 20 }));
                routeObjs.push(new K.CustomOverlay({ position: new K.LatLng(s.it.lat, s.it.lng), content: routeNoEl(k + 1), xAnchor: 0, yAnchor: 0, zIndex: 30 }));
            });
            routeObjs.forEach(o => o.setMap(map));
            routePanel(ui, stops, items.length, api.focus, n => { lastRoute.n = n; refreshNear(true); }, () => setRoute(false));
            if (fit && stops.length && holder.offsetWidth) {
                const b = new K.LatLngBounds();
                path.forEach(pt => b.extend(pt));
                map.setBounds(b, 60, 48, 48, 48);
                if (map.getLevel() < 3) map.setLevel(3);
            }
        }
        function setRoute(on) {
            lastRoute.on = on;
            press(routeBtn, on);
            closePopup();
            if (!on) { clearRoute(); if (ui.near) { ui.near.remove(); ui.near = null; } if (here) nearPanel(ui, items, here, api.focus); return; }
            if (here) { refreshNear(true); return; }
            routeBtn.disabled = true;  // 현위치가 먼저 필요하다
            locate().then(p => { showMe(p, false); refreshNear(true); }, m => { say(ui, m); lastRoute.on = false; press(routeBtn, false); }).then(() => { routeBtn.disabled = false; });
        }
        let clusterer = null;
        if (opts.cluster && K.MarkerClusterer) {
            try {
                clusterer = new K.MarkerClusterer({ map, averageCenter: true, minLevel: 6, gridSize: 60, disableClickZoom: true });
                K.event.addListener(clusterer, 'clustered', clusters => clusters.forEach(c => {
                    const el = clusterEl(sumItems(c.getMarkers().map(m => m.dimItem).filter(Boolean)), ringOf);
                    el.style.cursor = 'pointer';
                    el.addEventListener('click', () => { closePopup(); map.setLevel(Math.max(1, map.getLevel() - 2), { anchor: c.getCenter() }); });
                    c.getClusterMarker().setContent(el);
                }));
            } catch (e) { clusterer = null; }
        }

        // 로드뷰: 지도 옆(좁은 화면은 아래)에 연다. 보는 위치는 지도에 주황 점으로 따라온다.
        function closeRoadview() {
            if (rvPanel) rvPanel.hidden = true;
            if (rvDot) rvDot.setMap(null);
            holder.classList.remove('dim-rv-open');
            map.relayout();
        }
        function openRoadview(pos, label) {
            if (!K.Roadview || !K.RoadviewClient) { say(ui, '로드뷰를 불러오지 못했습니다'); return; }
            new K.RoadviewClient().getNearestPanoId(pos, 80, id => {
                if (id === null) { say(ui, '이 위치 근처(80m)에는 로드뷰가 없습니다'); return; }
                if (!rvPanel) {
                    rvPanel = mk('div', 'dim-rv');
                    const head = mk('div', 'dim-rv-head');
                    head.appendChild(mk('span', 'dim-rv-title'));
                    const x = mk('button', 'dim-map-tool', '✕ 로드뷰 닫기'); x.type = 'button';
                    x.addEventListener('click', closeRoadview);
                    head.appendChild(x);
                    rvPanel.appendChild(head); rvPanel.appendChild(mk('div', 'dim-rv-view'));
                    holder.appendChild(rvPanel);
                }
                rvPanel.hidden = false;
                rvPanel.querySelector('.dim-rv-title').textContent = label || '로드뷰';
                holder.classList.add('dim-rv-open');
                map.relayout();
                if (!rv) {
                    rv = new K.Roadview(rvPanel.querySelector('.dim-rv-view'));
                    K.event.addListener(rv, 'position_changed', () => { if (rvDot) rvDot.setPosition(rv.getPosition()); });
                } else rv.relayout();
                rv.setPanoId(id, pos);
                if (!rvDot) rvDot = new K.CustomOverlay({ content: mk('div', 'dim-rv-dot'), xAnchor: 0.5, yAnchor: 0.5, zIndex: 40 });
                rvDot.setPosition(pos); rvDot.setMap(map);
                map.setCenter(pos);
            });
        }
        K.event.addListener(map, 'click', e => {
            closePopup();
            if (rvMode && e && e.latLng) openRoadview(e.latLng, '로드뷰');
        });

        function openPopup(h) {
            closePopup();
            const it = h.dimItem;
            const wrap = mk('div', 'dim-kpop');
            wrap.style.paddingBottom = (it.r + 10) + 'px';
            const box = mk('div', 'dim-kpop-box');
            const x = mk('button', 'dim-kpop-close', '×'); x.type = 'button'; x.setAttribute('aria-label', '닫기');
            x.addEventListener('click', closePopup);
            box.appendChild(x);
            box.appendChild(popupContent(it, here, () => { closePopup(); openRoadview(h.getPosition(), it.name || '로드뷰'); }));
            wrap.appendChild(box);
            popup = new K.CustomOverlay({ position: h.getPosition(), content: wrap, xAnchor: 0.5, yAnchor: 1, zIndex: 50, clickable: true });
            popup.setMap(map);
            try {  // 풍선이 지도 안에 들어오도록 마커를 화면 아래쪽으로
                const proj = map.getProjection(), pt = proj.containerPointFromCoords(h.getPosition());
                pt.y -= Math.round(ui.canvas.offsetHeight * 0.22);
                map.setCenter(proj.coordsFromContainerPoint(pt));
            } catch (e) { map.setCenter(h.getPosition()); }
        }
        const api = {
            kind: 'kakao', note: '',
            setMarkers(list, o) {
                ringOf = (o && o.ringOf) || null;
                items = list;
                closePopup();
                if (clusterer) clusterer.clear(); else handles.forEach(h => h.setMap(null));
                handles = list.map(it => {
                    const d = mk('div', 'dim-kmarker');
                    d.style.width = d.style.height = (it.r * 2) + 'px';
                    d.style.background = it.fill;
                    d.style.border = (it.ringW || 2) + 'px solid ' + it.ring;
                    if (it.tip) d.title = it.tip;
                    const h = new K.CustomOverlay({ position: new K.LatLng(it.lat, it.lng), content: d, xAnchor: 0.5, yAnchor: 0.5, zIndex: 3, clickable: true });
                    h.dimItem = it;
                    d.addEventListener('click', () => { if (it.onClick) it.onClick(); openPopup(h); });
                    return h;
                });
                refreshNear(false);
                if (clusterer) {
                    try { clusterer.addMarkers(handles); return; } catch (e) { clusterer = null; }  // 묶음이 안 되면 그냥 다 찍는다
                }
                handles.forEach(h => h.setMap(map));
            },
            fit(maxZoom) {
                lastZoom = maxZoom;
                if (!handles.length) { map.setCenter(new K.LatLng(DEFAULT_CENTER[0], DEFAULT_CENTER[1])); map.setLevel(11); return; }
                if (!holder.offsetWidth) { waiting = maxZoom; return; }
                waiting = null;
                map.relayout();
                const b = new K.LatLngBounds();
                handles.forEach(h => b.extend(h.getPosition()));
                map.setBounds(b, 28, 28, 28, 28);
                const closest = 20 - maxZoom;
                if (map.getLevel() < closest) map.setLevel(closest);
            },
            focus(i, minZoom) {
                const h = handles[i];
                if (!h) return;
                const level = 20 - (minZoom || 13);
                if (map.getLevel() > level) map.setLevel(clusterer ? Math.min(level, 5) : level, { anchor: h.getPosition() });
                map.setCenter(h.getPosition());
                openPopup(h);
            },
            resize() { map.relayout(); if (rv && rvPanel && !rvPanel.hidden) rv.relayout(); },
            destroy() { big.close(); closePopup(); clearRoute(); if (clusterer) { try { clusterer.clear(); } catch (e) { /* ignore */ } } handles.forEach(h => h.setMap(null)); holder.textContent = ''; holder.classList.remove('dim-rv-open'); },
        };

        tool(ui, '📍 현위치', '내 위치로 이동하고 가까운 곳을 보여줍니다', b => {
            b.disabled = true;
            locate().then(p => showMe(p, true), m => say(ui, m)).then(() => { b.disabled = false; });
        });
        const routeBtn = tool(ui, '🚩 방문 루트', '현위치에서 가까운 순으로 방문 순서를 정해 점선과 거리로 보여줍니다', () => setRoute(!lastRoute.on));
        press(routeBtn, lastRoute.on);
        tool(ui, '🧭 전체', '표시된 곳이 모두 보이게 맞춥니다', () => { closePopup(); api.fit(lastZoom); });
        tool(ui, '🛰 스카이뷰', '항공사진으로 보기', b => {
            const on = !b.classList.contains('on');
            map.setMapTypeId(on ? K.MapTypeId.HYBRID : K.MapTypeId.ROADMAP);
            press(b, on);
        });
        tool(ui, '🚦 교통', '실시간 교통정보 표시', b => {
            const on = !b.classList.contains('on');
            if (on) map.addOverlayMapTypeId(K.MapTypeId.TRAFFIC); else map.removeOverlayMapTypeId(K.MapTypeId.TRAFFIC);
            press(b, on);
        });
        tool(ui, '👁 로드뷰', '로드뷰가 있는 길(파란 선)을 표시 -- 길이나 마커 풍선의 로드뷰를 누르면 거리 사진이 열립니다', b => {
            rvMode = !rvMode;
            if (rvMode) { map.addOverlayMapTypeId(K.MapTypeId.ROADVIEW); say(ui, '파란 길을 누르면 그 위치의 로드뷰가 열립니다'); }
            else { map.removeOverlayMapTypeId(K.MapTypeId.ROADVIEW); closeRoadview(); }
            press(b, rvMode);
        });
        const big = bigTool(ui, on => { map.setZoomable(on || isTouch()); setTimeout(() => api.resize(), 60); });
        if (lastHere) showMe(lastHere, false);
        if (window.ResizeObserver) new ResizeObserver(() => { if (!holder.offsetWidth) return; api.resize(); if (waiting != null) api.fit(waiting); }).observe(holder);
        return api;
    }

    // holder에 지도를 연다 -> Promise<지도 객체>. opts: { cluster: 가까운 마커를 숫자 원으로 묶을지 }
    function open(holder, opts) {
        opts = opts || {};
        // 기본 지도로 넘어갈 때는 이유를 지도 옆에 적는다 (어디서 막혔는지 화면만 보고 알 수 있게)
        if (!config.kakaoJsKey) return leafletMap(holder, opts, document.getElementById('mapConfig') ? '' : '기본 지도 (리포트에 카카오 JavaScript 키 없음)');
        if (location.protocol === 'file:') return leafletMap(holder, opts, '기본 지도 (파일로 열림 -- 카카오맵은 공유 링크나 GUI 미리보기 주소에서 표시)');
        return kakao().then(() => kakaoMap(holder, opts))
            .catch(e => {
                const why = String((e && e.message) || e);
                const reason = why.indexOf('dapi.kakao.com') >= 0 || why === 'kakao sdk' ? '카카오가 이 주소를 거절: ' + location.origin + ' -- 카카오 콘솔 사이트 도메인 등록 확인'
                    : why === 'kakao timeout' ? '카카오 응답 지연' : '오류: ' + why.slice(0, 80);
                return leafletMap(holder, opts, '기본 지도 (' + reason + ')');
            });
    }
    return { open };
})();

// ===== 9. 코어고객 활동관리 -- 독립 섹션 (총괄DB 필터와 무관). core_customers.py build_core_payload의 rows를 그린다 =====
// 화면 순서: 필터 -> 한 줄 요약 -> 지표 -> 우선 조치 대상 -> 차트 -> 지도 -> 활동내역 표(행을 누르면 방문이력·VOC·계약정보)
(function () {
    const BRANCHES = ['중앙', '강북', '서대문', '고양', '의정부', '남양주', '강릉', '원주'];
    const STATES = ['방문완료', '미방문', '해지징후'];
    const STATE_ROLE = { 방문완료: 'role-good', 미방문: 'role-none', 해지징후: 'role-critical' };
    const STATE_VAR = { 방문완료: '--good', 미방문: '--text-muted', 해지징후: '--critical' };
    const VOC_STATES = ['처리완료', '접수', '미접수'];
    const VOC_ROLE = { 처리완료: 'role-good', 접수: 'role-warning', 미접수: 'role-critical' };
    const END_BUCKETS = [['종료일 지남', 'role-critical'], ['3개월 내', 'role-serious'], ['6개월 내', 'role-warning'], ['1년 내', 'role-none'], ['1년 이후', 'role-none'], ['미상', 'role-none']];
    const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = text; return e; };
    const int = n => Math.round(n || 0).toLocaleString('ko-KR');
    const won = n => n == null ? '-' : (n >= 100000000 ? (n / 100000000).toFixed(1) + '억원' : n >= 10000 ? Math.round(n / 10000).toLocaleString('ko-KR') + '만원' : Math.round(n).toLocaleString('ko-KR') + '원');
    const pct = (a, b) => b ? Math.round(a / b * 100) : 0;
    const kakaoLink = a => a ? 'https://map.kakao.com/link/search/' + encodeURIComponent(a) : null;
    const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    const fresh = () => ({ 관리주체: '', 지사: '', 활동상태: '', 담당자: '', 영업구역: '', q: '', color: '활동상태', sort: null, dir: 1, open: null });
    let state = fresh();
    let view = '현황';  // '현황'(요약·조치·차트·지도·표) / '지도'(지도 크게 + 표) -- 필터를 바꿔도 유지
    let payload = null, map = null, mapToken = null, markerIndex = new Map(), period = '';

    // ---- 날짜 ----
    const today0 = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d; };
    const daysTo = d => { if (!d) return null; const t = new Date(d + 'T00:00:00'); return isNaN(t) ? null : Math.round((t - today0()) / 86400000); };
    const ddayText = n => n == null ? '' : (n < 0 ? Math.abs(n) + '일 지남' : 'D-' + n);
    const endBucket = r => { const n = daysTo(r.계약종료일); return n == null ? '미상' : n < 0 ? '종료일 지남' : n <= 90 ? '3개월 내' : n <= 180 ? '6개월 내' : n <= 365 ? '1년 내' : '1년 이후'; };
    const lastVisit = r => r.최근방문 || r['2회방문일자'] || r['3Q방문일자'] || r.방문일자 || null;
    // 이번 분기 안에 방문했는가 (분기 열이 없는 파일은 방문일자 기준) -- 해지징후 고객도 방문은 한 것
    const visited = r => period ? !!(r['3Q방문일자'] || r['2회방문일자']) : !!r.방문일자;
    const owner = r => r.관리고객담당자 || r.영업구역담당 || '담당 미지정';
    const pendingVoc = r => (r.VOC || []).filter(v => v.상태 === '접수' || v.상태 === '미접수');

    const visible = () => payload.rows.filter(r =>
        (!state.관리주체 || r.관리주체 === state.관리주체) && (!state.지사 || r.지사 === state.지사) && (!state.활동상태 || r.활동상태 === state.활동상태)
        && (!state.담당자 || owner(r) === state.담당자) && (!state.영업구역 || r.영업구역 === state.영업구역));
    const branchRank = b => { const i = BRANCHES.indexOf(b); return i < 0 ? BRANCHES.length : i; };
    // 선택 목록(담당자·영업구역): 고른 관리주체·지사 안에서, 지사 순(중앙, 강북, ...) -> 이름 순, '지사_이름'으로 표시
    function selectGroup(label, key, f, allText) {
        const scope = payload.rows.filter(r => (!state.관리주체 || r.관리주체 === state.관리주체) && (!state.지사 || r.지사 === state.지사));
        const first = new Map();
        scope.forEach(r => { const k = f(r); if (k && (!first.has(k) || branchRank(r.지사) < branchRank(first.get(k)))) first.set(k, r.지사); });
        const opts = Array.from(first).sort((a, b) => branchRank(a[1]) - branchRank(b[1]) || String(a[0]).localeCompare(String(b[0])));
        if (state[key] && !first.has(state[key])) state[key] = '';
        const g = el('div', 'filter-group');
        g.appendChild(el('span', 'filter-group-label', label));
        const sel = el('select', 'filter-select');
        sel.dataset.coreSelect = key; sel.setAttribute('aria-label', label);
        sel.appendChild(new Option(allText + ' (' + opts.length + ')', ''));
        opts.forEach(([k, b]) => sel.appendChild(new Option(b + '_' + k, k)));
        sel.value = state[key];
        g.appendChild(sel);
        return g;
    }
    const branchList = rows => BRANCHES.filter(b => rows.some(r => r.지사 === b)).concat(Array.from(new Set(rows.map(r => r.지사))).filter(b => !BRANCHES.includes(b)));

    // ---- 공용 hover 툴팁: data-core-tip="제목|줄1|줄2" ----
    let tipEl = null;
    function showTip(target, x, y) {
        if (!tipEl || !document.body.contains(tipEl)) { tipEl = el('div', 'dv-tip'); tipEl.setAttribute('role', 'tooltip'); document.body.appendChild(tipEl); }
        tipEl.textContent = '';
        target.dataset.coreTip.split('|').forEach((line, i) => tipEl.appendChild(el('div', i ? 'dv-tip-line' : 'dv-tip-head', line)));
        tipEl.style.display = 'block';
        const pad = 14, w = tipEl.offsetWidth, h = tipEl.offsetHeight;
        let left = x + pad, top = y + pad;
        if (left + w > window.innerWidth - 8) left = x - w - pad;
        if (top + h > window.innerHeight - 8) top = y - h - pad;
        tipEl.style.left = Math.max(8, left) + 'px'; tipEl.style.top = Math.max(8, top) + 'px';
    }
    const hideTip = () => { if (tipEl) tipEl.style.display = 'none'; };
    document.addEventListener('mousemove', e => {
        const t = e.target.closest ? e.target.closest('[data-core-tip]') : null;
        if (t) showTip(t, e.clientX, e.clientY); else hideTip();
    });
    document.addEventListener('focusin', e => {
        const t = e.target.closest ? e.target.closest('[data-core-tip]') : null;
        if (t) { const b = t.getBoundingClientRect(); showTip(t, b.left + b.width / 2, b.bottom); } else hideTip();
    });

    // ---- 작은 부품 ----
    function pills(label, key, values, noAll) {
        const g = el('div', 'filter-group');
        g.appendChild(el('span', 'filter-group-label', label));
        const row = el('div', 'filter-pill-row');
        (noAll ? values : [''].concat(values)).forEach(v => {
            const b = el('button', 'filter-pill' + (state[key] === v ? ' active' : ''), v === '' ? '전체' : v);
            b.type = 'button'; b.dataset.coreKey = key; b.dataset.coreValue = v;
            row.appendChild(b);
        });
        g.appendChild(row);
        return g;
    }
    function tile(label, value, sub, meter) {
        const t = el('div', 'stat-tile');
        t.appendChild(el('div', 'stat-label', label));
        t.appendChild(el('div', 'stat-value', value));
        if (meter != null) { const m = el('div', 'core-meter'); const f = el('span'); f.style.width = Math.max(0, Math.min(100, meter)) + '%'; m.appendChild(f); t.appendChild(m); }
        t.appendChild(el('div', 'stat-sub', sub));
        return t;
    }
    function legend(items) {
        const g = el('div', 'legend-grid core-chart-legend');
        items.forEach(([name, role]) => {
            const it = el('div', 'legend-item');
            it.appendChild(el('span', 'legend-swatch ' + role));
            it.appendChild(el('span', 'legend-label', name));
            g.appendChild(it);
        });
        return g;
    }
    function card(title, note) {
        const c = el('div', 'chart-card');
        c.appendChild(el('h3', 'chart-title', title));
        c.appendChild(el('p', 'chart-note', note || ''));
        return c;
    }
    // 가로 누적 막대 한 줄. parts: [{name, n, role, text}] (text 없으면 건수), scale: 가장 긴 줄의 합
    function stackRow(label, parts, scale, valueText, tipLines, cls) {
        const row = el('div', 'zone-row core-row' + (cls ? ' ' + cls : ''));
        row.tabIndex = 0;
        row.dataset.coreTip = [label].concat(tipLines).join('|');
        const lab = el('span', 'bar-row-label', label); lab.title = label;
        row.appendChild(lab);
        const track = el('div', 'zone-track');
        parts.forEach(p => {
            if (!p.n) return;
            const seg = el('div', 'zone-seg ' + p.role);
            const text = p.text === undefined ? int(p.n) : p.text;
            if (text) seg.appendChild(el('span', 'zl' + Math.min(String(text).length, 5), text));
            seg.style.width = (p.n / (scale || 1) * 100).toFixed(2) + '%';
            track.appendChild(seg);
        });
        row.appendChild(track);
        row.appendChild(el('span', 'bar-row-value', valueText));
        return row;
    }
    // 세로 막대. items: [{label, value, role, sub, tip}]
    function columns(items) {
        const wrap = el('div', 'core-cols');
        const max = Math.max(1, ...items.map(i => i.value));
        items.forEach(i => {
            const col = el('div', 'core-col');
            col.tabIndex = 0;
            col.dataset.coreTip = i.tip;
            col.appendChild(el('span', 'core-col-val', i.value ? int(i.value) : ''));
            const bar = el('div', 'core-col-bar ' + i.role);
            bar.style.height = (i.value / max * 118).toFixed(1) + 'px';
            col.appendChild(bar);
            const lab = el('span', 'core-col-label', i.label);
            if (i.sub) lab.appendChild(el('span', 'core-col-sub', i.sub));
            col.appendChild(lab);
            wrap.appendChild(col);
        });
        return wrap;
    }
    const countBy = (rows, f) => { const m = new Map(); rows.forEach(r => { const k = f(r); m.set(k, (m.get(k) || 0) + 1); }); return m; };
    const stateParts = rows => STATES.map(s => ({ name: s, n: rows.filter(r => r.활동상태 === s).length, role: STATE_ROLE[s] }));

    // ---- 한 줄 요약 ----
    function headline(rows) {
        const n = rows.length, v = rows.filter(visited).length;
        const un = rows.filter(r => !visited(r)), sign = rows.filter(r => r.해지징후 === 'Y');
        const box = el('div', 'callout core-headline ' + (un.length || sign.length ? 'callout-warning' : 'callout-good'));
        box.appendChild(el('div', 'core-headline-main', '코어고객 ' + int(n) + '곳 중 ' + int(v) + '곳(' + pct(v, n) + '%) ' + (period ? period + ' ' : '') + '방문 완료 · 미방문 ' + un.length + '곳 · 해지징후 ' + sign.length + '곳'));
        const notes = [];
        if (un.length) {
            const by = Array.from(countBy(un, r => r.지사)).sort((a, b) => b[1] - a[1]);
            notes.push('미방문이 많은 지사: ' + by.slice(0, 3).map(([b, c]) => b + ' ' + c + '곳').join(', '));
        }
        const soon = rows.filter(r => { const d = daysTo(r.계약종료일); return d != null && d <= 90; });
        if (soon.length) notes.push('계약종료 3개월 이내(지남 포함) ' + soon.length + '곳 · 월정료 ' + won(soon.reduce((s, r) => s + (r.월정료 || 0), 0)));
        const pend = rows.reduce((s, r) => s + pendingVoc(r).length, 0);
        if (pend) notes.push('미처리 VOC ' + pend + '건');
        if (!notes.length) notes.push('미방문·계약종료 임박·미처리 VOC가 없습니다.');
        notes.forEach(t => box.appendChild(el('div', 'core-headline-sub', t)));
        return box;
    }

    // ---- 우선 조치 대상 ----
    function actionCard(title, tone, items, empty) {
        const c = el('div', 'chart-card core-action is-' + tone);
        const head = el('div', 'core-action-head');
        head.appendChild(el('span', null, title));
        head.appendChild(el('span', 'core-action-count', items.length + '곳'));
        c.appendChild(head);
        const list = el('div', 'core-action-list');
        if (!items.length) list.appendChild(el('div', 'core-action-empty', empty));
        items.forEach(it => {
            const b = el('button', 'core-action-item');
            b.type = 'button'; b.dataset.coreFocus = it.r._i;
            b.appendChild(el('span', 'core-action-name', it.r.관리고객명 || '-'));
            b.appendChild(el('span', 'core-action-tag', it.tag || ''));
            b.appendChild(el('span', 'core-action-sub', it.r.지사 + ' · ' + owner(it.r) + (it.sub ? ' · ' + it.sub : '')));
            list.appendChild(b);
        });
        c.appendChild(list);
        return c;
    }
    function actions(rows) {
        const g = el('div', 'core-action-grid');
        const byEnd = (a, b) => (daysTo(a.계약종료일) == null ? 99999 : daysTo(a.계약종료일)) - (daysTo(b.계약종료일) == null ? 99999 : daysTo(b.계약종료일));
        g.appendChild(actionCard('해지징후 고객', 'critical',
            rows.filter(r => r.해지징후 === 'Y').map(r => ({ r, tag: won(r.월정료), sub: r.불만요구 || '' })), '해지징후로 표시된 고객이 없습니다.'));
        g.appendChild(actionCard((period ? period + ' ' : '') + '미방문 고객', 'serious',
            rows.filter(r => !visited(r)).sort(byEnd).map(r => ({ r, tag: r.계약종료일 ? '종료 ' + ddayText(daysTo(r.계약종료일)) : '', sub: r.방문일자 ? '이전 방문 ' + r.방문일자 : '방문 기록 없음' })),
            '미방문 고객이 없습니다.'));
        g.appendChild(actionCard('계약종료 3개월 이내', 'warning',
            rows.filter(r => { const d = daysTo(r.계약종료일); return d != null && d <= 90; }).sort(byEnd)
                .map(r => ({ r, tag: ddayText(daysTo(r.계약종료일)), sub: r.계약종료일 + ' · ' + won(r.월정료) + (r.수동재계약 ? ' · 수동재계약 ' + r.수동재계약 : '') })),
            '3개월 안에 종료되는 계약이 없습니다.'));
        g.appendChild(actionCard('VOC 미처리 (접수·미접수)', 'warning',
            rows.filter(r => pendingVoc(r).length).map(r => ({ r, tag: pendingVoc(r).length + '건', sub: pendingVoc(r).map(v => (v.상태 || '') + ' ' + (v.유형 || '')).join(', ') })),
            payload.rows.some(r => r.VOC && r.VOC.length) ? '미처리 VOC가 없습니다.' : 'VOC 매칭 파일(9-1)이 없습니다.'));
        return g;
    }

    // ---- 차트 ----
    function charts(rows) {
        const g = el('div', 'chart-grid core-charts');
        const P = period ? period + ' ' : '';

        // 1) 지사별 방문 현황 (누르면 그 지사만)
        const c1 = card('지사별 ' + P + '방문 현황', '막대 = 고객 수 · 오른쪽 = 방문 고객/전체 (방문율) · 줄을 누르면 그 지사만 봅니다');
        c1.appendChild(legend(STATES.map(s => [s, STATE_ROLE[s]])));
        const groups = branchList(rows).map(b => ({ b, rows: rows.filter(r => r.지사 === b) }));
        const maxN = Math.max(1, ...groups.map(x => x.rows.length));
        const l1 = el('div', 'zone-list');
        groups.forEach(x => {
            const parts = stateParts(x.rows), v = x.rows.filter(visited).length;
            const row = stackRow(x.b, parts, maxN, v + '/' + x.rows.length + ' · ' + pct(v, x.rows.length) + '%',
                parts.map(p => p.name + ' ' + p.n + '곳').concat(['방문율 ' + pct(v, x.rows.length) + '%']), state.지사 === x.b ? 'is-active' : '');
            row.dataset.coreKey = '지사'; row.dataset.coreValue = x.b;
            l1.appendChild(row);
        });
        c1.appendChild(l1);
        g.appendChild(c1);

        // 2) 월별 방문 추이 -- 방문 회차(이전/분기 1회/2회) 날짜를 모두 센다
        const months = new Map();
        rows.forEach(r => [r.방문일자, r['3Q방문일자'], r['2회방문일자']].forEach(d => { if (d && d.length >= 7) months.set(d.slice(0, 7), (months.get(d.slice(0, 7)) || 0) + 1); }));
        const c2 = card('월별 방문 추이', '방문일자(이전·분기 1회·2회)를 월별로 센 건수 · 최근 12개월');
        const keys = Array.from(months.keys()).sort();
        if (keys.length) {
            const span = [];
            let [y, m] = keys[keys.length - 1].split('-').map(Number);
            for (let i = 0; i < 12; i++) { span.unshift(y + '-' + String(m).padStart(2, '0')); m--; if (m === 0) { m = 12; y--; } }
            const from = span.findIndex(k => months.has(k));
            c2.appendChild(columns(span.slice(Math.max(0, from)).map(k => ({
                label: k.slice(2, 4) + '.' + k.slice(5), value: months.get(k) || 0, role: 'role-s1', tip: k + '|방문 ' + (months.get(k) || 0) + '건' }))));
        } else c2.appendChild(el('div', 'core-action-empty', '방문일자 기록이 없습니다.'));
        g.appendChild(c2);

        // 3) 계약종료 시기
        const c3 = card('계약종료 시기', '오늘 기준 남은 기간별 고객 수 · 아래 금액 = 월정료 합계');
        c3.appendChild(columns(END_BUCKETS.filter(([name]) => name !== '미상' || rows.some(r => endBucket(r) === '미상')).map(([name, role]) => {
            const rs = rows.filter(r => endBucket(r) === name), fee = rs.reduce((s, r) => s + (r.월정료 || 0), 0);
            return { label: name, value: rs.length, role, sub: rs.length ? won(fee) : '', tip: name + '|' + rs.length + '곳|월정료 ' + won(fee) + '|' + (period || '분기') + ' 미방문 ' + rs.filter(r => !visited(r)).length + '곳' };
        })));
        g.appendChild(c3);

        // 4) 지사별 월정료 (재계약대상 / 그 외)
        const c4 = card('지사별 월정료', '막대 = 월정료 합계 · 재계약대상 금액을 따로 표시');
        c4.appendChild(legend([['재계약대상', 'role-s2'], ['그 외', 'role-s1']]));
        const fees = groups.map(x => {
            const total = x.rows.reduce((s, r) => s + (r.월정료 || 0), 0);
            const re = Math.min(total, x.rows.reduce((s, r) => s + (r.재계약대상월정료 || 0), 0));
            return { b: x.b, total, re };
        });
        const maxFee = Math.max(1, ...fees.map(f => f.total));
        const l4 = el('div', 'zone-list');
        fees.forEach(f => l4.appendChild(stackRow(f.b, [{ n: f.re, role: 'role-s2', text: '' }, { n: f.total - f.re, role: 'role-s1', text: '' }], maxFee, won(f.total),
            ['월정료 ' + won(f.total), '재계약대상 ' + won(f.re) + ' (' + pct(f.re, f.total) + '%)'])));
        c4.appendChild(l4);
        g.appendChild(c4);

        // 5) VOC 유형별 처리 현황
        const c5 = card('VOC 유형별 처리 현황', '9-1 VOC매칭 · 건수가 많은 유형 순');
        const vocs = rows.flatMap(r => r.VOC || []);
        if (vocs.length) {
            c5.appendChild(legend(VOC_STATES.map(s => [s, VOC_ROLE[s]])));
            let kinds = Array.from(countBy(vocs, v => v.유형 || '유형 없음')).sort((a, b) => b[1] - a[1]);
            const top = kinds.slice(0, 7).map(k => k[0]);
            const label = v => top.includes(v.유형 || '유형 없음') ? (v.유형 || '유형 없음') : '기타';
            const names = kinds.length > 7 ? top.concat(['기타']) : top;
            const maxV = Math.max(1, ...names.map(nm => vocs.filter(v => label(v) === nm).length));
            const l5 = el('div', 'zone-list');
            names.forEach(nm => {
                const vs = vocs.filter(v => label(v) === nm);
                const parts = VOC_STATES.map(s => ({ name: s, n: vs.filter(v => v.상태 === s).length, role: VOC_ROLE[s] }));
                const etc = vs.length - parts.reduce((s, p) => s + p.n, 0);
                if (etc) parts.push({ name: '기타 상태', n: etc, role: 'role-muted' });
                l5.appendChild(stackRow(nm, parts, maxV, vs.length + '건', parts.filter(p => p.n).map(p => p.name + ' ' + p.n + '건'), 'wide'));
            });
            c5.appendChild(l5);
        } else c5.appendChild(el('div', 'core-action-empty', '매칭된 VOC가 없습니다 (9-1 파일을 함께 올리면 표시됩니다).'));
        g.appendChild(c5);

        // 6) 담당자별 방문 현황 -- 미방문·해지징후가 많은 순 (누르면 표에서 그 담당자만)
        const c6 = card('담당자별 ' + P + '방문 현황', '미방문·해지징후가 많은 순 상위 10명 · 줄을 누르면 아래 표에서 그 담당자만 봅니다');
        c6.appendChild(legend(STATES.map(s => [s, STATE_ROLE[s]])));
        const owners = Array.from(new Set(rows.map(owner))).map(o => ({ o, rows: rows.filter(r => owner(r) === o) }))
            .map(x => Object.assign(x, { bad: x.rows.filter(r => r.활동상태 !== '방문완료').length }))
            .sort((a, b) => b.bad - a.bad || b.rows.length - a.rows.length || String(a.o).localeCompare(String(b.o))).slice(0, 10);
        const maxO = Math.max(1, ...owners.map(x => x.rows.length));
        const l6 = el('div', 'zone-list');
        owners.forEach(x => {
            const parts = stateParts(x.rows), v = x.rows.filter(visited).length;
            const row = stackRow(x.o, parts, maxO, v + '/' + x.rows.length, [x.rows[0].지사].concat(parts.map(p => p.name + ' ' + p.n + '곳')), state.q === x.o ? 'is-active' : '');
            row.dataset.coreQ = x.o;
            l6.appendChild(row);
        });
        c6.appendChild(l6);
        g.appendChild(c6);
        return g;
    }

    // ---- 지도 ----
    const POP_SKIP = ['_i', 'lat', 'lng', '좌표출처', '설치주소', '불만요구', '방문일자', '3Q방문일자', '2회방문일자'];
    const POP_LABEL = { 관리고객명: '관리고객 명', 영업구역담당: '영업구역 담당', 최근방문: '최근 방문', 만기비중: '만기도래 비중', 재계약대상시설수: '재계약대상 시설수', 재계약대상월정료: '재계약대상 월정료' };
    const POP_MONEY = ['월정료', '재계약대상월정료', '약정월정료', '해지월정료', '업셀링금액'];
    function popupEl(r) {
        const box = el('div', 'core-pop cust-pop');
        box.appendChild(el('div', 'core-pop-title', r.관리고객명 || '-'));
        const lines = [
            r.지사 + ' · ' + r.관리주체 + ' · 담당 ' + owner(r),
            '상태: ' + r.활동상태 + ' · 최근 방문 ' + (lastVisit(r) || '없음'),
            '월정료 ' + won(r.월정료) + (r.계약종료일 ? ' · 계약종료 ' + r.계약종료일 + ' (' + ddayText(daysTo(r.계약종료일)) + ')' : ''),
            [r.계약번호 ? '계약 ' + r.계약번호 : '', r.영업구역 ? '영업구역 ' + r.영업구역 + (r.영업구역담당 ? ' (' + r.영업구역담당 + ')' : '') : '', r.시설수 != null ? '시설 ' + int(r.시설수) + '개' : ''].filter(Boolean).join(' · '),
        ].filter(Boolean);
        if (r.VOC && r.VOC.length) lines.push('VOC ' + r.VOC.length + '건: ' + r.VOC.map(v => (v.상태 || '-') + ' ' + (v.유형 || '')).join(', '));
        lines.forEach(t => box.appendChild(el('div', 'core-pop-line', t)));
        if (r.불만요구) box.appendChild(el('div', 'core-pop-note', r.불만요구));
        // 전체 컬럼 -- 값이 있는 항목만
        const det = document.createElement('details');
        det.className = 'cust-pop-all';
        det.appendChild(el('summary', null, '전체 컬럼 보기'));
        const dl = el('dl', 'core-kv');
        Object.keys(r).forEach(k => {
            const v = r[k];
            if (v === null || v === undefined || v === '' || typeof v === 'object' || POP_SKIP.includes(k)) return;
            kv(dl, POP_LABEL[k] || k, typeof v === 'number' && POP_MONEY.includes(k) ? Math.round(v).toLocaleString('ko-KR') + '원' : String(v));
        });
        (r.방문이력 || []).forEach(h => kv(dl, '방문 ' + h.회차, [h.일자, h.징후 ? '징후 ' + h.징후 : null, h.내용].filter(Boolean).join(' · ')));
        det.appendChild(dl);
        box.appendChild(det);
        box.appendChild(el('div', 'core-pop-line core-pop-addr', r.설치주소 || ''));
        const link = kakaoLink(r.설치주소);
        if (link) { const a = el('a', 'pending-map-link', '🗺 카카오맵'); a.href = link; a.target = '_blank'; a.rel = 'noopener'; box.appendChild(a); }
        return box;
    }
    function mapLegend() {
        const box = document.getElementById('coreMapLegend');
        if (!box) return;
        box.textContent = '';
        const dot = (name, v, ring) => {
            const it = el('div', 'legend-item'); const sw = el('span', 'legend-swatch' + (ring ? ' core-sign-key' : ''));
            if (!ring) { sw.style.background = cssVar(v); sw.style.borderRadius = '50%'; }
            it.appendChild(sw); it.appendChild(el('span', 'legend-label', name)); box.appendChild(it);
        };
        if (state.color === '활동상태') STATES.forEach(s => dot(s, STATE_VAR[s]));
        else { dot('본부장', '--s7'); dot('지사장', '--s1'); dot('해지징후 (빨간 테두리)', null, true); }
        box.appendChild(el('span', 'legend-label core-size-note', '원 크기 = 월정료'));
    }
    function drawMap(rows) {
        const holder = document.getElementById('coreMap');
        if (!holder) return;
        mapLegend();
        const pts = rows.filter(r => r.lat != null && r.lng != null);
        if (!payload.rows.some(r => r.lat != null)) {
            holder.className = 'core-map core-map-empty';
            holder.textContent = '지도 좌표가 없습니다 -- 데스크톱 GUI의 "카카오 REST 키" 칸에 키를 넣고 리포트를 만들면 설치주소가 지도에 표시됩니다. (표에서 행을 열면 카카오맵 바로가기가 있습니다)';
            return;
        }
        const paint = (M, fit) => {
            const owners = { 본부장: cssVar('--s7') || '#4a3aa7', 지사장: cssVar('--s1') || '#2a78d6' };
            const ring = cssVar('--surface-1') || '#fff', danger = cssVar('--critical') || '#d03b3b';
            const maxFee = Math.max(1, ...pts.map(r => r.월정료 || 0));
            const byState = state.color === '활동상태';
            markerIndex = new Map();
            M.setMarkers(pts.map((r, i) => {
                const sign = r.해지징후 === 'Y';
                markerIndex.set(r._i, i);
                return { lat: r.lat, lng: r.lng, r: Math.round(6 + 7 * Math.sqrt((r.월정료 || 0) / maxFee)),
                    fill: byState ? (cssVar(STATE_VAR[r.활동상태]) || '#888') : (owners[r.관리주체] || cssVar('--baseline') || '#888'),
                    ring: !byState && sign ? danger : ring, ringW: !byState && sign ? 3 : 2,
                    name: r.관리고객명 || '', tip: (r.관리고객명 || '') + ' · ' + r.활동상태, popup: () => popupEl(r), onClick: () => openRow(r._i, 'map') };
            }));
            if (fit) M.fit(14);
        };
        if (map) { paint(map, false); return; }   // 색상 기준·테마만 바뀜: 마커만 다시
        const token = {};
        mapToken = token;
        DataIntelMapLib.open(holder, { cluster: false }).then(M => {
            if (mapToken !== token) { M.destroy(); return; }  // 그 사이 화면이 다시 그려짐
            map = M;
            const note = document.getElementById('coreMapNote');
            if (note && M.note) note.textContent += ' · ' + M.note;
            paint(M, true);
        }, () => { holder.textContent = '지도 모듈을 불러오지 못했습니다 (인터넷 연결 확인).'; });
    }

    // ---- 활동내역 표 ----
    const COLS = [
        ['지사', r => BRANCHES.indexOf(r.지사) < 0 ? 99 : BRANCHES.indexOf(r.지사)], ['관리주체', r => r.관리주체], ['관리고객 명', r => r.관리고객명 || ''],
        ['담당', r => owner(r)], ['상태', r => STATES.indexOf(r.활동상태)], ['최근 방문', r => lastVisit(r) || ''],
        ['계약종료', r => r.계약종료일 || '9999'], ['월정료', r => r.월정료 || 0], ['VOC', r => (r.VOC || []).length], ['최근 메모 (불만·요구·영업기회)', null],
    ];
    function tableRows() {
        const q = state.q.trim().toLowerCase();
        let rows = visible();
        if (q) rows = rows.filter(r => [r.관리고객명, r.관리고객담당자, r.영업구역담당, r.영업자, r.설치주소, r.불만요구, r.요약정리, r.계약번호, r.지사].join(' ').toLowerCase().includes(q));
        if (state.sort != null && COLS[state.sort][1]) {
            const f = COLS[state.sort][1];
            rows = rows.slice().sort((a, b) => { const x = f(a), y = f(b); return (x < y ? -1 : x > y ? 1 : 0) * state.dir; });
        }
        return rows;
    }
    function kv(dl, k, v) { if (v === null || v === undefined || v === '') return; dl.appendChild(el('dt', null, k)); dl.appendChild(el('dd', null, v)); }
    function detailEl(r) {
        const grid = el('div', 'core-detail-grid');
        const a = el('div');
        a.appendChild(el('div', 'core-detail-title', '방문 이력'));
        const hist = r.방문이력 || [];
        if (!hist.length) a.appendChild(el('div', 'core-action-empty', '방문 기록이 없습니다.'));
        hist.forEach(h => {
            const item = el('div', 'core-hist');
            item.appendChild(el('div', 'core-hist-head', [h.회차, h.일자 || '날짜 미기재', h.징후 ? '해지징후 ' + h.징후 : null, [h.대상, h.방문자].filter(Boolean).join(' / ') || null].filter(Boolean).join(' · ')));
            if (h.내용) item.appendChild(el('div', 'core-hist-body', h.내용));
            a.appendChild(item);
        });
        grid.appendChild(a);
        const b = el('div');
        b.appendChild(el('div', 'core-detail-title', 'VOC ' + (r.VOC || []).length + '건'));
        if (!(r.VOC || []).length) b.appendChild(el('div', 'core-action-empty', '매칭된 VOC가 없습니다.'));
        (r.VOC || []).forEach(v => {
            const item = el('div', 'core-hist');
            item.appendChild(el('div', 'core-hist-head', [v.상태, v.유형, v.접수일, v.처리자].filter(Boolean).join(' · ')));
            if (v.처리내용) item.appendChild(el('div', 'core-hist-body', v.처리내용));
            b.appendChild(item);
        });
        grid.appendChild(b);
        const c = el('div');
        c.appendChild(el('div', 'core-detail-title', '계약 정보'));
        const dl = el('dl', 'core-kv');
        kv(dl, '계약번호', r.계약번호);
        kv(dl, '계약종료일', r.계약종료일 ? r.계약종료일 + ' (' + ddayText(daysTo(r.계약종료일)) + ')' : null);
        kv(dl, '수동재계약', r.수동재계약);
        kv(dl, '시설 · 월정료', (r.시설수 != null ? int(r.시설수) + '개 · ' : '') + won(r.월정료));
        if (r.재계약대상월정료) kv(dl, '재계약대상', (r.재계약대상시설수 != null ? int(r.재계약대상시설수) + '개 · ' : '') + won(r.재계약대상월정료));
        if (r.약정여부) kv(dl, '약정', r.약정여부 + (r.약정시설수 ? ' · ' + int(r.약정시설수) + '개 · ' + won(r.약정월정료) : ''));
        if (r.해지건수) kv(dl, '해지', int(r.해지건수) + '건 · ' + won(r.해지월정료) + (r.해지일자 ? ' · ' + r.해지일자 : ''));
        if (r.업셀링) kv(dl, '업셀링', r.업셀링 + (r.업셀링금액 ? ' · ' + won(r.업셀링금액) : ''));
        kv(dl, '영업자', r.영업자);
        kv(dl, '영업구역', [r.영업구역, r.영업구역담당].filter(Boolean).join(' · '));
        kv(dl, '요약정리', r.요약정리);
        kv(dl, '설치주소', r.설치주소);
        c.appendChild(dl);
        const link = kakaoLink(r.설치주소);
        if (link) { const m = el('a', 'pending-map-link', '🗺 카카오맵에서 보기'); m.href = link; m.target = '_blank'; m.rel = 'noopener'; c.appendChild(m); }
        grid.appendChild(c);
        return grid;
    }
    function fillTable() {
        const t = document.getElementById('coreTable');
        if (!t) return;
        const rows = tableRows();
        const head = t.querySelector('thead tr');
        head.textContent = '';
        COLS.forEach(([name, f], i) => {
            const th = el('th', null, name + (state.sort === i ? (state.dir > 0 ? ' ▲' : ' ▼') : ''));
            if (f) { th.dataset.coreSort = i; th.title = '눌러서 정렬'; }
            head.appendChild(th);
        });
        const tb = t.querySelector('tbody');
        tb.textContent = '';
        rows.forEach(r => {
            const tr = el('tr'); tr.dataset.coreRow = r._i;
            if (r.해지징후 === 'Y') tr.className = 'core-sign';
            if (state.open === r._i) tr.classList.add('core-active');
            [r.지사, r.관리주체, r.관리고객명 || '-', owner(r)].forEach(c => tr.appendChild(el('td', null, c)));
            const st = el('td'); st.appendChild(el('span', 'core-state s-' + r.활동상태, r.활동상태)); tr.appendChild(st);
            tr.appendChild(el('td', lastVisit(r) ? null : 'core-novisit', lastVisit(r) || '기록 없음'));
            const end = el('td', null, r.계약종료일 || '-');
            const dd = daysTo(r.계약종료일);
            if (dd != null && dd <= 365) end.appendChild(el('span', 'core-dday' + (dd <= 90 ? ' soon' : ''), ddayText(dd)));
            tr.appendChild(end);
            tr.appendChild(el('td', 'cell-num', won(r.월정료)));
            const pend = pendingVoc(r).length;
            tr.appendChild(el('td', null, (r.VOC || []).length ? r.VOC.length + '건' + (pend ? ' (미처리 ' + pend + ')' : '') : '-'));
            tr.appendChild(el('td', 'core-note', r.불만요구 || '-'));
            tb.appendChild(tr);
            if (state.open === r._i) {
                const dr = el('tr', 'core-detail'); const td = el('td'); td.colSpan = COLS.length;
                td.appendChild(detailEl(r)); dr.appendChild(td); tb.appendChild(dr);
            }
        });
        if (!rows.length) { const tr = el('tr', 'core-detail'); const td = el('td', null, '조건에 맞는 고객이 없습니다.'); td.colSpan = COLS.length; tr.appendChild(td); tb.appendChild(tr); }
        const cnt = document.getElementById('coreTableCount');
        if (cnt) cnt.textContent = rows.length.toLocaleString('ko-KR') + ' / ' + payload.rows.length.toLocaleString('ko-KR') + '곳';
    }
    // 행 열기 -- src: 'table'(표에서 누름: 다시 누르면 접힘) / 'list'(우선 조치 대상: 표로 이동) / 'map'(지도 마커: 화면은 그대로, 표 안에서만 이동)
    function openRow(i, src) {
        const r = payload.rows.find(x => x._i === i);
        if (!r) return;
        state.open = (src === 'table' && state.open === i) ? null : i;
        if (!tableRows().includes(r)) {
            state.q = '';
            if (!visible().includes(r)) { state.관리주체 = ''; state.지사 = ''; state.활동상태 = ''; render(); }
            else { const s = document.getElementById('coreSearch'); if (s) s.value = ''; fillTable(); }
        } else fillTable();
        const tr = document.querySelector('#coreTable tr[data-core-row="' + i + '"]');
        if (tr && src === 'list') tr.scrollIntoView({ block: 'center', behavior: 'smooth' });
        if (tr && src === 'map') { const box = tr.closest('.table-scroll'); if (box) box.scrollTop = Math.max(0, tr.offsetTop - 44); }
        if (map && markerIndex.has(i) && src !== 'map' && state.open === i) map.focus(markerIndex.get(i), 13);
    }
    function downloadCsv() {
        const head = ['지사', '관리주체', '관리고객명', '계약번호', '담당', '활동상태', '최근방문', '해지징후', '계약종료일', '월정료', '재계약대상월정료', 'VOC건수', '미처리VOC', '최근메모', '설치주소'];
        const q = v => '"' + String(v === null || v === undefined ? '' : v).split('"').join('""') + '"';
        const lines = [head.map(q).join(',')].concat(tableRows().map(r => [r.지사, r.관리주체, r.관리고객명, r.계약번호, owner(r), r.활동상태, lastVisit(r), r.해지징후, r.계약종료일,
            r.월정료, r.재계약대상월정료, (r.VOC || []).length, pendingVoc(r).length, r.불만요구, r.설치주소].map(q).join(',')));
        const blob = new Blob([String.fromCharCode(0xFEFF) + lines.join(String.fromCharCode(13, 10))], { type: 'text/csv;charset=utf-8' });
        const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = '코어고객_활동내역.csv'; a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    }

    function render() {
        const wrap = document.getElementById('coreSectionWrap');
        if (!wrap || !payload) return;
        const all = payload.rows;
        if (map) map.destroy();
        map = null; mapToken = null;  // 지도 컨테이너를 새로 만드므로 지도도 새로
        wrap.textContent = '';
        const bar = el('div', 'global-filter-bar core-filter-bar');
        const mapView = view === '지도';
        const vb = el('button', 'filter-pill cust-map-toggle' + (mapView ? ' active' : ''), mapView ? '📊 현황으로 보기' : '🗺️ 지도로 보기');
        vb.type = 'button'; vb.dataset.coreView = mapView ? '현황' : '지도'; vb.setAttribute('aria-pressed', mapView ? 'true' : 'false');
        bar.appendChild(vb);
        bar.appendChild(pills('관리주체', '관리주체', Array.from(new Set(all.map(r => r.관리주체))).sort()));
        bar.appendChild(pills('지사', '지사', branchList(all)));
        bar.appendChild(pills('활동', '활동상태', STATES.filter(s => all.some(r => r.활동상태 === s))));
        bar.appendChild(selectGroup('담당자', '담당자', owner, '전체 담당자'));
        bar.appendChild(selectGroup('영업구역', '영업구역', r => r.영업구역, '전체 영업구역'));
        const rows = visible();  // selectGroup이 범위 밖 선택을 풀어 준 뒤에 계산
        bar.appendChild(el('span', 'filter-summary', rows.length.toLocaleString('ko-KR') + ' / ' + all.length.toLocaleString('ko-KR') + '곳'));
        wrap.appendChild(bar);
        wrap.appendChild(headline(rows));

        const sum = k => rows.reduce((n, r) => n + (r[k] || 0), 0);
        const vocs = rows.flatMap(r => r.VOC || []);
        const v = rows.filter(visited).length;
        const soon = rows.filter(r => { const d = daysTo(r.계약종료일); return d != null && d <= 90; });
        const grid = el('div', 'stat-grid');
        grid.appendChild(tile('코어고객', int(rows.length) + '곳', '본부장 ' + rows.filter(r => r.관리주체 === '본부장').length + ' · 지사장 ' + rows.filter(r => r.관리주체 === '지사장').length + ' · 시설 ' + int(sum('시설수')) + '개'));
        grid.appendChild(tile((period ? period + ' ' : '') + '방문율', pct(v, rows.length) + '%', '방문 ' + v + '곳 · 미방문 ' + (rows.length - v) + '곳', pct(v, rows.length)));
        grid.appendChild(tile('해지징후', rows.filter(r => r.해지징후 === 'Y').length + '곳', '불만·요구 메모 ' + rows.filter(r => r.불만요구).length + '곳'));
        grid.appendChild(tile('월정료 합계', won(sum('월정료')), '재계약대상 ' + won(sum('재계약대상월정료')) + ' (' + pct(sum('재계약대상월정료'), sum('월정료')) + '%)'));
        grid.appendChild(tile('계약종료 3개월 이내', soon.length + '곳', '월정료 ' + won(soon.reduce((s, r) => s + (r.월정료 || 0), 0))));
        grid.appendChild(tile('VOC (매칭)', vocs.length + '건', VOC_STATES.map(s => s + ' ' + vocs.filter(x => x.상태 === s).length).join(' · ')));
        if (!mapView) {  // 지도로 보기에서는 지도를 바로 위에 크게 -- 지표·조치·차트는 접는다
            wrap.appendChild(grid);
            const t1 = el('h3', 'core-block-title', '우선 조치 대상'); t1.appendChild(el('small', null, '고객을 누르면 아래 표에서 방문 이력·VOC·계약 정보를 펼칩니다'));
            wrap.appendChild(t1);
            wrap.appendChild(actions(rows));
            const t2 = el('h3', 'core-block-title', '현황 분석'); t2.appendChild(el('small', null, '막대에 마우스를 올리면 자세한 수치가 보입니다'));
            wrap.appendChild(t2);
            wrap.appendChild(charts(rows));
        }

        const t3 = el('h3', 'core-block-title', '설치주소 지도');
        t3.appendChild(el('small', null, '마커를 누르면 고객 정보와 전체 컬럼 내역이 보입니다 · 위에서 관리주체·지사·담당자·영업구역을 고르면 지도도 바뀝니다'));
        wrap.appendChild(t3);
        const cs = payload.coord_stats || {};
        const mh = el('div', 'core-map-head');
        mh.appendChild(pills('색상 기준', 'color', ['활동상태', '관리주체'], true));
        const lg = el('div', 'legend-grid core-legend'); lg.id = 'coreMapLegend'; mh.appendChild(lg);
        const cn = mh.appendChild(el('span', 'core-coord-note')); cn.id = 'coreMapNote';
        cn.textContent = ('지도 표시 ' + rows.filter(r => r.lat != null).length + '/' + rows.length + '곳'
            + (cs.동단위 ? ' · 동 단위 위치 ' + cs.동단위 + '곳' : '')
            + (cs.없음 ? ' · 좌표 없음 ' + cs.없음 + '곳' + (payload.coord_note ? ' (' + payload.coord_note + ')'
                : payload.kakao_error ? ' (' + payload.kakao_error + ')' : (payload.kakao_key_set === false ? ' (카카오 키 미설정)' : '')) : ''));
        wrap.appendChild(mh);
        const mapBox = el('div', 'core-map' + (mapView ? ' core-map-tall' : '')); mapBox.id = 'coreMap';
        wrap.appendChild(mapBox);

        const t4 = el('h3', 'core-block-title', '활동내역'); t4.appendChild(el('small', null, '행을 누르면 방문 이력·VOC·계약 정보가 펼쳐집니다 (지도에도 표시) · 제목을 누르면 정렬'));
        wrap.appendChild(t4);
        const tools = el('div', 'core-toolbar');
        const search = el('input', 'filter-input'); search.type = 'search'; search.id = 'coreSearch'; search.placeholder = '고객명 · 담당자 · 주소 · 메모 검색'; search.value = state.q;
        search.setAttribute('aria-label', '코어고객 검색');
        tools.appendChild(search);
        const cnt = el('span', 'row-count'); cnt.id = 'coreTableCount'; tools.appendChild(cnt);
        const csv = el('button', 'filter-pill', '📥 엑셀(CSV) 저장'); csv.type = 'button'; csv.id = 'coreCsvBtn'; tools.appendChild(csv);
        wrap.appendChild(tools);
        const scroll = el('div', 'table-scroll core-table-scroll');
        const t = el('table'); t.id = 'coreTable';
        const thead = el('thead'); thead.appendChild(el('tr')); t.appendChild(thead); t.appendChild(el('tbody'));
        scroll.appendChild(t); wrap.appendChild(scroll);
        wrap.appendChild(el('p', 'section-desc core-foot', '기준 -- 방문완료: ' + (period ? period + ' 안에' : '') + ' 방문일자가 있는 고객 · 해지징후: 방문 기록 중 한 번이라도 Y · 계약종료 D-day: 이 리포트를 여는 날 기준'));
        fillTable();
        drawMap(rows);
    }

    document.addEventListener('click', (e) => {
        if (!payload || !e.target.closest) return;
        if (e.target.closest('#coreCsvBtn')) { downloadCsv(); return; }
        const vbtn = e.target.closest('[data-core-view]');
        if (vbtn) {
            view = vbtn.dataset.coreView;
            render();
            const top = document.getElementById('coreSectionWrap');
            if (top) top.scrollIntoView({ block: 'start', behavior: 'smooth' });
            return;
        }
        const focus = e.target.closest('[data-core-focus]');
        if (focus) { openRow(Number(focus.dataset.coreFocus), 'list'); return; }
        const sort = e.target.closest('#coreTable th[data-core-sort]');
        if (sort) { const i = Number(sort.dataset.coreSort); state.dir = state.sort === i ? -state.dir : 1; state.sort = i; fillTable(); return; }
        const tr = e.target.closest('#coreTable tr[data-core-row]');
        if (tr && !e.target.closest('a')) { openRow(Number(tr.dataset.coreRow), 'table'); return; }
        const qrow = e.target.closest('[data-core-q]');
        if (qrow) {
            state.q = state.q === qrow.dataset.coreQ ? '' : qrow.dataset.coreQ;
            const s = document.getElementById('coreSearch'); if (s) { s.value = state.q; s.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
            document.querySelectorAll('[data-core-q]').forEach(x => x.classList.toggle('is-active', x.dataset.coreQ === state.q));
            fillTable(); return;
        }
        const b = e.target.closest('[data-core-key][data-core-value]');
        if (!b) return;
        const k = b.dataset.coreKey, v = b.dataset.coreValue;
        if (k === 'color') {
            state.color = v;
            b.parentElement.querySelectorAll('.filter-pill').forEach(x => x.classList.toggle('active', x === b));
            drawMap(visible()); return;
        }
        state[k] = (state[k] === v) ? '' : v;  // 같은 것을 다시 누르면 해제
        state.open = null;
        render();
    });
    document.addEventListener('input', (e) => {
        if (e.target && e.target.id === 'coreSearch') { state.q = e.target.value; fillTable(); }
    });
    document.addEventListener('change', (e) => {
        const sel = e.target && e.target.closest ? e.target.closest('#coreSectionWrap [data-core-select]') : null;
        if (!sel || !payload) return;
        state[sel.dataset.coreSelect] = sel.value; state.open = null;
        render();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' || !e.target.closest) return;
        const row = e.target.closest('.core-row[data-core-key], .core-row[data-core-q]');
        if (row) row.click();
    });
    // 테마를 바꾸면 지도 마커 색도 다시 (막대는 CSS 변수라 자동)
    new MutationObserver(() => { if (payload && map) drawMap(visible()); }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

    // GUI 리포트는 내장 JSON(#coreData)으로, 웹 업로드는 window.DataIntelCore(payload)로 들어온다
    window.DataIntelCore = function (p) {
        payload = p && p.rows && p.rows.length ? p : null;
        if (payload) payload.rows.forEach((r, i) => { r._i = i; });
        period = payload && payload.period ? payload.period : '';
        state = fresh();
        map = null;
        const sec = document.getElementById('secCore');
        const link = document.querySelector('#dashNav a[data-sec="secCore"]');
        if (sec) sec.hidden = !payload;
        if (link) link.hidden = !payload;
        render();
    };
    document.addEventListener('DOMContentLoaded', () => {
        const d = document.getElementById('coreData');
        if (d) { try { window.DataIntelCore(JSON.parse(d.textContent)); } catch (e) { console.error(e); } }
    });
})();


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
    // let (not const): the web uploader (web_app.py) swaps in freshly parsed files via loadDataset()
    let dbRowsBase = toObjects(DATA.db);
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
    // Mirrors handlers.py _zone_key: '405', 405 and 405.0 all compare equal.
    function zoneKey(v) {
        if (v === null || v === undefined) return null;
        let s = String(v).replace(/\\u00a0/g, ' ').trim();
        if (s.endsWith('.0')) s = s.slice(0, -2);
        return s || null;
    }
    let zoneOwnerMap = DATA.zoneOwnerMap || {};
    const SP_ZONE_COL_CANDIDATES = ['영업구역정보', '영업구역번호', '영업구역'];
    const SP_OWNER_COL_CANDIDATES = ['SP담당', 'SP_담당', 'SP 담당', 'SP담당자', 'SP_담당자', 'SP 담당자'];  // handlers.py와 같게
    // 구역 열 찾기 (handlers.py find_zone_col과 같게): 이름(정확 -> 비슷한 이름) + 값이 구역 코드 형태인지
    const ZONE_FUZZY = { SP: [['영업구역'], []], SE: [['기술구역'], []], SG: [['출동구역', '구역정보'], ['영업', '기술']] };
    const ZONE_CODE_RE = /^[A-Za-z]{0,4}\\d{2,}[A-Za-z0-9-]*$/;
    function zoneLikeCol(rows, col) {
        const vals = [];
        for (let i = 0; i < rows.length && i < 20000 && vals.length < 300; i++) { const v = zoneKey(rows[i][col]); if (v) vals.push(v); }
        if (!vals.length) return true;
        return vals.filter(v => ZONE_CODE_RE.test(v)).length / vals.length >= 0.6;
    }
    function findZoneCol(rows, cols, kind, candidates) {
        const exact = candidates.find(c => cols.includes(c) && zoneLikeCol(rows, c));
        if (exact) return exact;
        const [inc, exc] = ZONE_FUZZY[kind];
        const norm = n => String(n).replace(/[\\s_()\\-·]/g, '');
        return cols.find(c => { const n = norm(c); return inc.some(f => n.includes(f)) && !exc.some(x => n.includes(x)) && zoneLikeCol(rows, c); }) || null;
    }

    const STATUS_RANK_JS = { 미접수: 0, 접수: 1, 처리완료: 2 };  // handlers.py STATUS_RANK
    const statusRank = v => (typeof v === 'string' && v in STATUS_RANK_JS ? STATUS_RANK_JS[v] : -1);
    let statusColUsed = null;
    function normalizeStatus(v) {  // handlers.py _normalize_status
        if (typeof v !== 'string') return v === undefined ? null : v;
        const t = v.replace(/\\u00a0/g, ' ').trim();
        const squeezed = t.replace(/\\s+/g, '');
        return squeezed in STATUS_RANK_JS ? squeezed : t;
    }
    function detectStatusCol(rows, cols) {  // handlers.py detect_status_col: 활동유무 우선 -> 활동유무* -> 상태값 가장 많은 열
        const hitsOf = c => rows.reduce((n, r) => n + (typeof r[c] === 'string' && normalizeStatus(r[c]) in STATUS_RANK_JS ? 1 : 0), 0);
        if (cols.includes('활동유무') && hitsOf('활동유무') > 0) return '활동유무';
        const named = cols.find(c => c.startsWith('활동유무') && hitsOf(c) > 0);
        if (named) return named;
        let best = null, bestHits = 0;
        cols.forEach(c => {
            const hits = hitsOf(c);
            const prefer = c.startsWith('활동유무');
            if (hits > bestHits || (hits === bestHits && hits > 0 && prefer && !(best || '').startsWith('활동유무'))) { best = c; bestHits = hits; }
        });
        if (!best) best = cols.includes('활동유무') ? '활동유무' : (cols.find(c => c.startsWith('활동유무')) || null);
        return best;
    }

    function rebuildMerged(config) {
        let rows = dbRowsBase.map(r => Object.assign({}, r));
        const dbColSet = new Set(dbRowsBase.length ? Object.keys(dbRowsBase[0]) : []);
        // 처리완료/접수/미접수가 실제로 든 열을 값으로 찾아 '활동유무'로 맞춘다 (handlers.py detect_status_col과 같게)
        statusColUsed = detectStatusCol(dbRowsBase, Array.from(dbColSet));
        if (statusColUsed && statusColUsed !== '활동유무') {
            rows.forEach(r => { if ('활동유무' in r) r['활동유무_원래열'] = r['활동유무']; r['활동유무'] = r[statusColUsed]; });
        }
        rows.forEach(r => { r['활동유무'] = normalizeStatus(r['활동유무']); });
        const spZoneCol = findZoneCol(dbRowsBase, Array.from(dbColSet), 'SP', SP_ZONE_COL_CANDIDATES);
        const useOwnerMap = Object.keys(zoneOwnerMap).length > 0 && !!spZoneCol;
        const spOwnerCol = SP_OWNER_COL_CANDIDATES.find(c => dbColSet.has(c)) || null;
        if (spOwnerCol && spOwnerCol !== 'SP담당') rows.forEach(r => { r['SP담당'] = r[spOwnerCol]; });

        const originRes = applyMatching(rows, fileRowsByKey.original, config.original, DATA.displayColumns.original, 'origin', false);
        rows = originRes.rows;
        const facRes = applyMatching(rows, fileRowsByKey.facility, config.facility, DATA.displayColumns.facility, 'fac', false);
        rows = facRes.rows;
        // 7. 해지시설내역 -- '일반해지'면 처리완료로 역반영 (handlers.py와 같게)
        const cancelFacRes = applyMatching(rows, fileRowsByKey.cancelled_facility, config.cancelled_facility, DATA.displayColumns.cancelled_facility || [], 'cancelfac', false);
        rows = cancelFacRes.rows;
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

            // 상태값 역반영 (handlers.py process_and_merge와 같게): VOC 상태 -> 해지시설 '일반해지'는
            // 처리완료 -> 순찰 매칭은 처리완료. 'sp 담당자 상태값' 열에만 두고 실적(활동유무)에는 더하지 않는다.
            let writeback = null;
            if (['처리완료', '접수', '미접수'].includes(out['최근VOC상태'])) writeback = out['최근VOC상태'];
            if (out['계약상태(중)_cancelfac'] === '일반해지') writeback = '처리완료';
            if ((out['순찰건수'] || 0) > 0) writeback = '처리완료';
            out['sp 담당자 상태값'] = writeback;

            // 8. 영업구역담당자 -- SP 건만 영업구역정보 = 구역번호 (handlers.py process_and_merge와 같게)
            // SP 담당자: 총괄DB SP담당 값 우선, 비어 있으면 8번 영업구역담당자 (handlers.py와 같게)
            if (useOwnerMap || spOwnerCol) {
                out['영업구역담당자'] = null; out['영업구역담당자_출처'] = null;
                if (out['활동대상구분'] === 'SP') {
                    const own = spOwnerCol && out['SP담당'] != null ? String(out['SP담당']).trim() : '';
                    if (own) { out['영업구역담당자'] = own; out['영업구역담당자_출처'] = '총괄DB SP담당'; }
                    else if (useOwnerMap) {
                        const mapped = zoneOwnerMap[zoneKey(out[spZoneCol])] || null;
                        out['영업구역담당자'] = mapped; out['영업구역담당자_출처'] = mapped ? '8번 영업구역담당자' : null;
                    }
                }
            }

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

        return { rows, used: { original: originRes.used, facility: facRes.used, cancelled_facility: cancelFacRes.used, patrol: patrolRes.used, voc: vocRes.used } };
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

    // ---- 구역별 활동유무 -- mirrors analytics.py build_zone_activity / report.py render_zone_activity_section ----
    const ZONE_COL_CANDIDATES = {
        SP: SP_ZONE_COL_CANDIDATES,
        SE: ['기술구역정보', '기술구역번호', '기술구역'],
        SG: ['구역정보', '구역'],
    };
    const ZONE_LABELS = { SP: '영업구역', SE: '기술구역', SG: '구역' };
    const ZONE_SEGMENTS = {
        SP: [['처리완료', 'role-good'], ['접수', 'role-warning'], ['미접수', 'role-critical']],
        SE: [['처리완료', 'role-good'], ['미처리', 'role-critical']],
        SG: [['처리완료', 'role-good'], ['미처리', 'role-critical']],
    };
    const ZONE_SEGMENT_LABELS = { 접수: '미처리(접수)' };
    const zoneSegLabel = k => ZONE_SEGMENT_LABELS[k] || k;

    function zoneCounts(sub) {
        const total = sub.length;
        const done = sub.filter(r => r['활동유무'] === '처리완료').length;
        const received = sub.filter(r => r['활동유무'] === '접수').length;
        const notReceived = sub.filter(r => r['활동유무'] === '미접수').length;
        return { 대상: total, 처리완료: done, 접수: received, 미접수: notReceived, 미처리: total - done, 처리율: total ? done / total * 100 : 0 };
    }
    // SP는 8. 영업구역담당자가 매칭되면 담당자 기준으로도 묶을 수 있다 -- analytics.py ZONE_SP_MODES와 같게.
    const ZONE_SP_MODES = {
        zone_owner: [['지사', '구역', '담당자'], '영업구역·담당자별', '지사_영업구역_담당자'],
        owner: [['지사', '담당자'], '담당자별', '지사_담당자'],
        zone: [['지사', '구역'], '영업구역별', '지사_영업구역'],
    };
    const ZONE_SP_MODE_ORDER = ['zone_owner', 'owner', 'zone'];
    let zoneSpMode = 'zone_owner';

    function buildZoneActivityJS(rows, spMode) {
        if (!rows.length) return null;
        const cols = new Set();
        rows.forEach(r => Object.keys(r).forEach(k => cols.add(k)));
        const hasOwner = cols.has('영업구역담당자');
        const clean = (v, missing) => (v === null || v === undefined || String(v).trim() === '') ? missing : String(v).trim();
        const branchRank = b => { const i = DATA.branchOrder.indexOf(b); return i < 0 ? DATA.branchOrder.length : i; };
        const cmp = (x, y) => x < y ? -1 : x > y ? 1 : 0;
        const result = {};
        PROGRESS_TYPES.forEach(t => {
            const zoneCol = findZoneCol(rows, Array.from(cols), t, ZONE_COL_CANDIDATES[t]);
            const sub = rows.filter(r => r['활동대상구분'] === t);
            if (!zoneCol || !sub.length) return;
            let mode = null, keys = ['지사', '구역'];
            if (t === 'SP' && hasOwner) {
                mode = ZONE_SP_MODES[spMode] ? spMode : 'zone_owner';
                keys = ZONE_SP_MODES[mode][0];
            }
            const groups = new Map();
            sub.forEach(r => {
                const parts = {
                    지사: clean(r['지사'], UNKNOWN_LABEL),
                    구역: zoneKey(r[zoneCol]) || UNKNOWN_LABEL,
                    담당자: clean(r['영업구역담당자'], '미매칭'),
                };
                const vals = keys.map(k => parts[k]);
                const label = vals.join('_');
                if (!groups.has(label)) groups.set(label, { vals, rows: [] });
                groups.get(label).rows.push(r);
            });
            const zoneRows = Array.from(groups, ([label, g]) => {
                const row = { 라벨: label, _rows: g.rows };
                keys.forEach((k, i) => { row[k] = g.vals[i]; });
                return Object.assign(row, zoneCounts(g.rows));
            });
            zoneRows.sort((a, b) => {
                const d = branchRank(a.지사) - branchRank(b.지사);
                if (d) return d;
                for (const k of keys) { const c = cmp(a[k], b[k]); if (c) return c; }
                return 0;
            });
            result[t] = { zone_col: zoneCol, mode, rows: zoneRows, total: zoneCounts(sub) };
        });
        return Object.keys(result).length ? result : null;
    }

    // ---- 활동유무 상태 필터(복수 선택) + 시설 조회 ----
    // 처리완료 / 접수 / 그 외(미접수·미입력)의 세 갈래. SE/SG의 '미처리'는 접수+미접수.
    const ZONE_STATUS_OPTIONS = [['미접수', '미접수'], ['접수', '미처리(접수)'], ['처리완료', '처리완료']];
    const zoneStatusSel = new Set(ZONE_STATUS_OPTIONS.map(o => o[0]));
    let zoneFocus = null; // 't|라벨' -- 막대를 클릭하면 그 구역의 시설만 조회
    const ZONE_FACILITY_LIMIT = 300;
    function zoneStatusOf(r) {
        const v = r['활동유무'];
        return v === '처리완료' || v === '접수' ? v : '미접수';
    }
    // SP 담당자 선택 ('지사|담당자', ''=전체). SP 건만 거르고 SE/SG는 그대로 둔다.
    let zoneOwnerSel = '';
    const ownerClean = (v, missing) => (v === null || v === undefined || String(v).trim() === '') ? missing : String(v).trim();
    const spOwnerKey = r => ownerClean(r['지사'], UNKNOWN_LABEL) + '|' + ownerClean(r['영업구역담당자'], '미매칭');
    function spOwnerOptions(rows) {
        // [{지사, 담당자, key, n}] -- 지사 조직 순서, 그 안에서 담당자 가나다
        const counts = new Map();
        rows.forEach(r => { if (r['활동대상구분'] === 'SP') { const k = spOwnerKey(r); counts.set(k, (counts.get(k) || 0) + 1); } });
        const branchRank = b => { const i = DATA.branchOrder.indexOf(b); return i < 0 ? DATA.branchOrder.length : i; };
        return Array.from(counts, ([key, n]) => { const [지사, 담당자] = key.split('|'); return { 지사, 담당자, key, n }; })
            .sort((a, b) => (branchRank(a.지사) - branchRank(b.지사)) || (a.지사 < b.지사 ? -1 : a.지사 > b.지사 ? 1 : 0) || (a.담당자 < b.담당자 ? -1 : a.담당자 > b.담당자 ? 1 : 0));
    }
    const zoneAllStatuses = () => zoneStatusSel.size === ZONE_STATUS_OPTIONS.length;
    const zonePass = r => zoneStatusSel.has(zoneStatusOf(r));
    function zoneCardEl(t, data) {
        const rows = data.rows, total = data.total;
        const segments = ZONE_SEGMENTS[t];
        const card = mkEl('section', 'chart-card zone-card');
        card.id = 'zoneActivity' + t;
        const mode = data.mode;
        card.dataset.mode = mode || 'zone';
        let groupTitle, headLabel, countLabel;
        if (mode) {
            groupTitle = ZONE_SP_MODES[mode][1];
            headLabel = ZONE_SP_MODES[mode][2];
            countLabel = fmtInt(rows.length) + ({ owner: '명', zone: '개 구역' }[mode] || '개 그룹');
        } else {
            groupTitle = ZONE_LABELS[t] + '별';
            headLabel = '지사_' + ZONE_LABELS[t];
            countLabel = fmtInt(rows.length) + '개 구역';
        }

        const title = mkEl('h3', 'chart-title', t + ' ' + groupTitle + ' 활동유무 ');
        title.appendChild(mkEl('span', 'zone-sub', '(' + data.zone_col + ' 기준, ' + countLabel + ', 지사순)'));
        card.appendChild(title);

        if (mode) {
            const pills = mkEl('div', 'filter-pill-row zone-mode-row');
            ZONE_SP_MODE_ORDER.forEach(m => {
                const btn = mkEl('button', 'filter-pill' + (m === mode ? ' active' : ''), ZONE_SP_MODES[m][2]);
                btn.type = 'button';
                btn.dataset.zoneMode = m;
                pills.appendChild(btn);
            });
            if (data.ownerOptions && data.ownerOptions.length) {
                const lab = mkEl('label', 'zone-owner-pick');
                lab.appendChild(mkEl('span', null, '담당자'));
                const sel = mkEl('select', 'filter-select');
                sel.dataset.zoneOwner = '1';
                sel.appendChild(new Option('전체 담당자 (' + fmtInt(data.ownerOptions.length) + '명)', ''));
                let group = null;
                data.ownerOptions.forEach(o => {
                    if (!group || group.label !== o.지사) { group = document.createElement('optgroup'); group.label = o.지사; sel.appendChild(group); }
                    group.appendChild(new Option(o.담당자 + ' (' + fmtInt(o.n) + '건)', o.key));
                });
                sel.value = zoneOwnerSel;
                lab.appendChild(sel);
                pills.appendChild(lab);
            }
            card.appendChild(pills);
            if (zoneOwnerSel) {
                const [b, o] = zoneOwnerSel.split('|');
                const chip = mkEl('div', 'zone-owner-chip');
                chip.appendChild(document.createTextNode('선택한 담당자: '));
                chip.appendChild(mkEl('strong', null, b + ' · ' + o));
                chip.appendChild(document.createTextNode(' -- SP 막대·표·요약·TOP 10·시설 조회에 적용 (SE/SG는 그대로)'));
                card.appendChild(chip);
            }
        }

        let summary = '대상 ' + fmtInt(total.대상) + '건 · 처리완료 ' + fmtInt(total.처리완료) + '건 · ';
        summary += t === 'SP'
            ? '미처리(접수) ' + fmtInt(total.접수) + '건 · 미접수 ' + fmtInt(total.미접수) + '건 · '
            : '미처리 ' + fmtInt(total.미처리) + '건 · ';
        summary += '처리율 ' + total.처리율.toFixed(1) + '%';
        card.appendChild(mkEl('p', 'chart-note', summary));

        const legend = mkEl('div', 'legend-grid zone-legend');
        segments.forEach(([key, role]) => {
            const item = mkEl('div', 'legend-item');
            item.appendChild(mkEl('span', 'legend-swatch ' + role));
            item.appendChild(mkEl('span', 'legend-label', zoneSegLabel(key)));
            legend.appendChild(item);
        });
        card.appendChild(legend);

        const list = mkEl('div', 'zone-list');
        const allStatuses = zoneAllStatuses();
        const visible = rows.map(r => Object.assign({}, r, { _sel: allStatuses ? r : zoneCounts(r._rows.filter(zonePass)) }))
            .filter(r => r._sel.대상 > 0);
        const maxSel = Math.max(1, ...visible.map(r => r._sel.대상));
        if (!visible.length) list.appendChild(mkEl('div', 'empty-card', '선택한 활동유무에 해당하는 시설이 없습니다.'));
        visible.forEach(r => {
            const focusKey = t + '|' + r.라벨;
            const row = mkEl('div', 'zone-row' + (zoneFocus === focusKey ? ' zone-row-active' : ''));
            row.dataset.zoneFocus = focusKey;
            row.tabIndex = 0;
            const tip = segments.map(([k]) => zoneSegLabel(k) + ' ' + fmtInt(r[k])).join(' · ');
            row.dataset.tip = [r.라벨, '대상 ' + fmtInt(r.대상) + '건 · 처리율 ' + r.처리율.toFixed(1) + '%', tip].join('||');
            row.appendChild(mkEl('span', 'bar-row-label', r.라벨));
            const track = mkEl('div', 'zone-track');
            segments.forEach(([key, role]) => {
                const n = r._sel[key];
                if (!n) return;
                const pct = n / maxSel * 100;
                const seg = mkEl('div', 'zone-seg ' + role);
                const label = fmtInt(n);
                // 숫자는 조각 너비에 들어갈 때만 보인다 (CSS container query, .zl1~.zl5)
                seg.appendChild(mkEl('span', 'zl' + Math.min(label.length, 5), label));
                seg.style.width = pct.toFixed(2) + '%';
                track.appendChild(seg);
            });
            row.appendChild(track);
            const value = mkEl('span', 'bar-row-value', r.처리율.toFixed(1) + '% ');
            value.appendChild(mkEl('span', 'zone-sub', '(' + fmtInt(r.처리완료) + '/' + fmtInt(r.대상) + '건)'));
            row.appendChild(value);
            list.appendChild(row);
        });
        card.appendChild(list);

        const columns = t === 'SP' ? ['대상', '처리완료', '접수', '미접수', '처리율'] : ['대상', '처리완료', '미처리', '처리율'];
        const details = mkEl('details', 'zone-table-toggle');
        details.appendChild(mkEl('summary', null, groupTitle + ' 건수 표 보기'));
        const wrap = mkEl('div', 'table-scroll');
        const table = mkEl('table', 'progress-table');
        const thead = document.createElement('thead');
        const headRow = document.createElement('tr');
        headRow.appendChild(mkEl('th', null, headLabel));
        columns.forEach(c => headRow.appendChild(mkEl('th', null, zoneSegLabel(c))));
        thead.appendChild(headRow);
        table.appendChild(thead);
        const tbody = document.createElement('tbody');
        function tableRow(label, r, isTotal) {
            const tr = document.createElement('tr');
            if (isTotal) tr.className = 'progress-total-row';
            tr.appendChild(mkEl('td', 'progress-branch' + (isTotal ? ' progress-total-label' : ''), label));
            columns.forEach(c => {
                if (c === '처리율') {
                    const { bg, textRole } = progressCellStyle(r[c]);
                    const td = mkEl('td', 'cell-num progress-cell ' + textRole, r[c].toFixed(1) + '%');
                    td.setAttribute('style', bg);
                    tr.appendChild(td);
                } else {
                    tr.appendChild(mkEl('td', 'cell-num', fmtInt(r[c])));
                }
            });
            tbody.appendChild(tr);
        }
        rows.forEach(r => tableRow(r.라벨, r, false));
        tableRow('계', total, true);
        table.appendChild(tbody);
        wrap.appendChild(table);
        details.appendChild(wrap);
        card.appendChild(details);
        return card;
    }
    // ---- 고급 시각화: 요약 타일 / 집중관리 구역 TOP 10 / SP 담당자 분포 ----
    // 처리율 상태색 기준은 진척율 표(progressCellStyle)와 같다: <30 위험, <55 주의, 그 이상 양호.
    function rateRole(pct) { return pct < 30 ? 'critical' : pct < 55 ? 'warning' : 'good'; }
    const RATE_ICON = { critical: '▼', warning: '■', good: '▲' };
    const RATE_WORD = { critical: '위험', warning: '주의', good: '양호' };
    const ZONE_FOCUS_MIN = 5; // TOP 10 표본 하한 -- 1건 중 0건(0%) 같은 구역이 목록을 채우지 않게

    function zoneMeterEl(pct) {
        const role = rateRole(pct);
        const track = mkEl('div', 'dv-meter dv-meter-' + role);
        const fill = mkEl('div', 'dv-meter-fill role-' + role);
        fill.style.width = Math.max(0, Math.min(100, pct)).toFixed(1) + '%';
        track.appendChild(fill);
        return track;
    }
    function zoneKpiTilesEl(zoneData) {
        const grid = mkEl('div', 'dv-tiles');
        const types = PROGRESS_TYPES.filter(t => zoneData[t]);
        const all = types.reduce((a, t) => {
            const x = zoneData[t].total;
            a.대상 += x.대상; a.처리완료 += x.처리완료; a.접수 += x.접수; a.미접수 += x.미접수;
            return a;
        }, { 대상: 0, 처리완료: 0, 접수: 0, 미접수: 0 });
        all.미처리 = all.대상 - all.처리완료;
        all.처리율 = all.대상 ? all.처리완료 / all.대상 * 100 : 0;
        [['전체', all]].concat(types.map(t => [t + ' ' + ZONE_LABELS[t], zoneData[t].total])).forEach(([label, x], i) => {
            const role = rateRole(x.처리율);
            const tile = mkEl('div', 'dv-tile' + (i === 0 ? ' dv-tile-lead' : ''));
            tile.appendChild(mkEl('div', 'dv-tile-label', label + ' 처리율'));
            const v = mkEl('div', 'dv-tile-value', x.처리율.toFixed(1) + '%');
            tile.appendChild(v);
            tile.appendChild(zoneMeterEl(x.처리율));
            const st = mkEl('div', 'dv-tile-status');
            st.appendChild(mkEl('span', 'dv-status-icon dv-status-' + role, RATE_ICON[role]));
            st.appendChild(document.createTextNode(RATE_WORD[role]));
            tile.appendChild(st);
            tile.appendChild(mkEl('div', 'dv-tile-sub',
                '처리완료 ' + fmtInt(x.처리완료) + ' / 대상 ' + fmtInt(x.대상) + '건 · 미처리 ' + fmtInt(x.미처리) + '건'));
            grid.appendChild(tile);
        });
        return grid;
    }
    function zoneFocusTop10El(zoneData) {
        const card = mkEl('section', 'chart-card dv-card');
        card.appendChild(mkEl('h3', 'chart-title', '집중관리 구역 TOP 10'));
        card.appendChild(mkEl('p', 'chart-note', '대상 ' + ZONE_FOCUS_MIN + '건 이상 구역 중 처리율 낮은 순 (같으면 미처리 많은 순) · 클릭하면 아래 시설 조회'));
        const cand = [];
        PROGRESS_TYPES.forEach(t => {
            if (!zoneData[t]) return;
            zoneData[t].rows.forEach(r => { if (r.대상 >= ZONE_FOCUS_MIN) cand.push({ t, r }); });
        });
        cand.sort((a, b) => (a.r.처리율 - b.r.처리율) || (b.r.미처리 - a.r.미처리));
        if (!cand.length) {
            card.appendChild(mkEl('div', 'empty-card', '대상 ' + ZONE_FOCUS_MIN + '건 이상인 구역이 없습니다.'));
            return card;
        }
        const list = mkEl('ol', 'dv-rank');
        cand.slice(0, 10).forEach(({ t, r }, i) => {
            const li = mkEl('li', 'dv-rank-row');
            const key = t + '|' + r.라벨;
            if (zoneFocus === key) li.classList.add('zone-row-active');
            li.dataset.zoneFocus = key;
            li.tabIndex = 0;
            li.dataset.tip = [t + ' ' + r.라벨, '처리율 ' + r.처리율.toFixed(1) + '% (' + fmtInt(r.처리완료) + '/' + fmtInt(r.대상) + '건)', '미처리 ' + fmtInt(r.미처리) + '건'].join('||');
            li.appendChild(mkEl('span', 'dv-rank-no', String(i + 1)));
            const name = mkEl('span', 'dv-rank-name');
            name.appendChild(mkEl('span', 'dv-type-badge', t));
            name.appendChild(document.createTextNode(r.라벨));
            li.appendChild(name);
            li.appendChild(zoneMeterEl(r.처리율));
            li.appendChild(mkEl('span', 'dv-rank-value', r.처리율.toFixed(1) + '%'));
            li.appendChild(mkEl('span', 'dv-rank-sub', '미처리 ' + fmtInt(r.미처리)));
            list.appendChild(li);
        });
        card.appendChild(list);
        return card;
    }
    function niceStep(max, target) {
        const raw = max / target;
        const mag = Math.pow(10, Math.floor(Math.log10(raw)));
        const n = raw / mag;
        return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag;
    }
    function ownerScatterEl(spData) {
        const byOwner = spData && spData.mode;
        const card = mkEl('section', 'chart-card dv-card');
        card.appendChild(mkEl('h3', 'chart-title', byOwner ? 'SP 담당자 분포 -- 담당 물량 vs 처리율' : 'SP 영업구역 분포 -- 대상 건수 vs 처리율'));
        const pts = spData ? spData.rows.filter(r => r.대상 > 0) : [];
        if (pts.length < 2) {
            card.appendChild(mkEl('div', 'empty-card', '분포를 그릴 SP 데이터가 부족합니다.'));
            return card;
        }
        const avgRate = spData.total.처리율;
        const avgLoad = spData.total.대상 / pts.length;
        const flagged = r => r.처리율 < avgRate && r.대상 >= avgLoad;
        card.appendChild(mkEl('p', 'chart-note', '점 하나 = ' + (byOwner ? '지사_담당자' : '지사_구역') + ' · 평균 처리율 ' + avgRate.toFixed(1) + '%, 평균 물량 ' + avgLoad.toFixed(1) + '건 · 점을 클릭하면 그 시설 조회'));

        const legend = mkEl('div', 'legend-grid zone-legend');
        [['dv-dot-key', (byOwner ? '담당자' : '구역')], ['dv-dot-key dv-dot-key-flag', '집중관리 (처리율 평균 미만 · 물량 평균 이상)']].forEach(([cls, text]) => {
            const it = mkEl('div', 'legend-item');
            it.appendChild(mkEl('span', cls));
            it.appendChild(mkEl('span', 'legend-label', text));
            legend.appendChild(it);
        });
        card.appendChild(legend);

        const W = 720, H = 300, m = { l: 44, r: 16, t: 12, b: 34 };
        const pw = W - m.l - m.r, ph = H - m.t - m.b;
        const maxX = Math.max(...pts.map(r => r.대상));
        const stepX = niceStep(maxX, 5);
        const topX = Math.ceil(maxX / stepX) * stepX || 1;
        const x = v => m.l + v / topX * pw;
        const y = v => m.t + (1 - v / 100) * ph;
        const NS = 'http://www.w3.org/2000/svg';
        const el = (tag, attrs, text) => {
            const e = document.createElementNS(NS, tag);
            Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
            if (text !== undefined) e.textContent = text;
            return e;
        };
        const svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'dv-scatter', role: 'img',
            'aria-label': 'SP ' + (byOwner ? '담당자' : '구역') + '별 대상 건수와 처리율 산점도' });
        [0, 25, 50, 75, 100].forEach(v => {
            svg.appendChild(el('line', { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), class: 'dv-grid' }));
            svg.appendChild(el('text', { x: m.l - 8, y: y(v) + 4, class: 'dv-tick', 'text-anchor': 'end' }, v + '%'));
        });
        for (let v = 0; v <= topX + 1e-9; v += stepX) {
            svg.appendChild(el('text', { x: x(v), y: H - m.b + 18, class: 'dv-tick', 'text-anchor': 'middle' }, fmtInt(v)));
        }
        svg.appendChild(el('line', { x1: m.l, x2: W - m.r, y1: y(0), y2: y(0), class: 'dv-axis' }));
        svg.appendChild(el('text', { x: W - m.r, y: H - 2, class: 'dv-axis-title', 'text-anchor': 'end' }, '대상 건수 →'));
        // 평균 기준선 (hairline) + 라벨
        svg.appendChild(el('line', { x1: m.l, x2: W - m.r, y1: y(avgRate), y2: y(avgRate), class: 'dv-ref' }));
        svg.appendChild(el('text', { x: m.l + 6, y: y(avgRate) - 5, class: 'dv-ref-label' }, '평균 처리율 ' + avgRate.toFixed(1) + '%'));
        svg.appendChild(el('line', { x1: x(avgLoad), x2: x(avgLoad), y1: m.t, y2: y(0), class: 'dv-ref' }));
        svg.appendChild(el('text', { x: x(avgLoad) + 5, y: m.t + 10, class: 'dv-ref-label' }, '평균 물량 ' + avgLoad.toFixed(1) + '건'));

        // 집중관리 점은 위에 그린다 (겹칠 때 가려지지 않게). 라벨은 가장 급한 3명만.
        const ordered = pts.slice().sort((a, b) => flagged(a) - flagged(b));
        const labelSet = new Set(pts.filter(flagged).sort((a, b) => (a.처리율 - b.처리율) || (b.대상 - a.대상)).slice(0, 3).map(r => r.라벨));
        ordered.forEach(r => {
            const key = 'SP|' + r.라벨;
            const picked = zoneOwnerSel && byOwner && r.라벨 === zoneOwnerSel.replace('|', '_');
            const g = el('g', { class: 'dv-pt' + (flagged(r) ? ' dv-pt-flag' : '') + (zoneFocus === key || picked ? ' dv-pt-active' : ''), tabindex: 0 });
            g.dataset.zoneFocus = key;
            g.dataset.zoneFocusMode = byOwner ? 'owner' : 'zone';
            g.dataset.tip = [r.라벨, '대상 ' + fmtInt(r.대상) + '건 · 처리율 ' + r.처리율.toFixed(1) + '%', '처리완료 ' + fmtInt(r.처리완료) + ' · 미처리(접수) ' + fmtInt(r.접수) + ' · 미접수 ' + fmtInt(r.미접수)].join('||');
            g.appendChild(el('circle', { cx: x(r.대상), cy: y(r.처리율), r: 12, class: 'dv-hit' }));
            g.appendChild(el('circle', { cx: x(r.대상), cy: y(r.처리율), r: 5, class: 'dv-dot' }));
            svg.appendChild(g);
            if (labelSet.has(r.라벨)) {
                const nearRight = x(r.대상) > W - 160;
                svg.appendChild(el('text', { x: x(r.대상) + (nearRight ? -9 : 9), y: y(r.처리율) + 4, class: 'dv-pt-label',
                    'text-anchor': nearRight ? 'end' : 'start' }, r.라벨.split('_').slice(1).join('_')));
            }
        });
        const wrap = mkEl('div', 'dv-scatter-wrap');
        wrap.appendChild(svg);
        card.appendChild(wrap);
        return card;
    }

    // 공용 hover 툴팁 -- data-tip="줄1||줄2||줄3"
    let dvTipEl = null;
    function dvTip() {
        if (!dvTipEl || !document.body.contains(dvTipEl)) {
            dvTipEl = mkEl('div', 'dv-tip');
            dvTipEl.setAttribute('role', 'tooltip');
            document.body.appendChild(dvTipEl);
        }
        return dvTipEl;
    }
    function showDvTip(target, clientX, clientY) {
        const tip = dvTip();
        tip.innerHTML = '';
        target.dataset.tip.split('||').forEach((line, i) => tip.appendChild(mkEl('div', i ? 'dv-tip-line' : 'dv-tip-head', line)));
        tip.style.display = 'block';
        const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
        let left = clientX + pad, top = clientY + pad;
        if (left + w > window.innerWidth - 8) left = clientX - w - pad;
        if (top + h > window.innerHeight - 8) top = clientY - h - pad;
        tip.style.left = Math.max(8, left) + 'px';
        tip.style.top = Math.max(8, top) + 'px';
    }
    function hideDvTip() { if (dvTipEl) dvTipEl.style.display = 'none'; }

    function zoneStatusPillsEl() {
        const wrap = mkEl('div', 'zone-status-bar');
        wrap.appendChild(mkEl('span', 'zone-status-label', '활동유무 (복수 선택)'));
        const row = mkEl('div', 'filter-pill-row');
        ZONE_STATUS_OPTIONS.forEach(([value, label]) => {
            const btn = mkEl('button', 'filter-pill' + (zoneStatusSel.has(value) ? ' active' : ''), label);
            btn.type = 'button';
            btn.dataset.zoneStatus = value;
            row.appendChild(btn);
        });
        const allBtn = mkEl('button', 'filter-pill ghost-pill', '전체');
        allBtn.type = 'button';
        allBtn.dataset.zoneStatus = '*';
        row.appendChild(allBtn);
        wrap.appendChild(row);
        return wrap;
    }
    function zoneFacilityListEl(zoneData) {
        const box = mkEl('div', 'table-section zone-facility');
        const types = PROGRESS_TYPES.filter(t => zoneData[t] && (!zoneOwnerSel || t === 'SP'));
        let groups = [];
        types.forEach(t => zoneData[t].rows.forEach(r => groups.push({ t, zoneCol: zoneData[t].zone_col, r })));
        if (zoneFocus) groups = groups.filter(g => g.t + '|' + g.r.라벨 === zoneFocus);
        const items = [];
        groups.forEach(g => g.r._rows.forEach(row => { if (zonePass(row)) items.push({ t: g.t, zoneCol: g.zoneCol, row }); }));

        const statusRank = { 미접수: 0, 접수: 1, 처리완료: 2 };
        const branchRank = b => { const i = DATA.branchOrder.indexOf(b); return i < 0 ? DATA.branchOrder.length : i; };
        const zoneOf = it => zoneKey(it.row[it.zoneCol]) || UNKNOWN_LABEL;
        items.sort((a, b) => (branchRank(a.row['지사']) - branchRank(b.row['지사']))
            || String(a.row['지사'] || '').localeCompare(String(b.row['지사'] || ''))
            || PROGRESS_TYPES.indexOf(a.t) - PROGRESS_TYPES.indexOf(b.t)
            || zoneOf(a).localeCompare(zoneOf(b))
            || statusRank[zoneStatusOf(a.row)] - statusRank[zoneStatusOf(b.row)]);

        const head = mkEl('div', 'zone-facility-head');
        const selLabels = ZONE_STATUS_OPTIONS.filter(o => zoneStatusSel.has(o[0])).map(o => o[1]).join(', ') || '없음';
        const title = mkEl('h3', 'chart-title', '시설 조회 ');
        title.appendChild(mkEl('span', 'zone-sub', '(' + selLabels + ' · ' + fmtInt(items.length) + '건)'));
        head.appendChild(title);
        if (zoneFocus) {
            const chip = mkEl('button', 'filter-pill active zone-focus-chip', zoneFocus.replace('|', ' ') + '  ✕');
            chip.type = 'button';
            chip.dataset.zoneFocusClear = '1';
            head.appendChild(chip);
        }
        const csvBtn = mkEl('button', 'ghost-btn small', '📥 조회 결과 CSV');
        csvBtn.type = 'button';
        csvBtn.dataset.zoneCsv = '1';
        csvBtn.disabled = !items.length;
        head.appendChild(csvBtn);
        box.appendChild(head);
        box.appendChild(mkEl('p', 'chart-note', zoneFocus
            ? '선택한 구역의 시설만 표시합니다. ✕를 누르면 전체 구역으로 돌아갑니다.'
            : '위 막대의 구역을 클릭하면 그 구역 시설만 조회됩니다.'));

        const columns = ['구분', '지사', '구역', '담당자', '상호', '계약번호', '활동유무', '설치주소'];
        const toRecord = it => ({
            구분: it.t, 지사: it.row['지사'] || UNKNOWN_LABEL, 구역: zoneOf(it),
            담당자: it.t === 'SP' ? (it.row['영업구역담당자'] || '') : '',
            상호: it.row['상호'] || '', 계약번호: it.row['계약번호'] != null ? String(zoneKey(it.row['계약번호'])) : '',
            활동유무: it.row['활동유무'] || '미입력', 설치주소: it.row['설치주소'] || '',
        });
        zoneFacilityRecords = items.map(toRecord);

        if (!items.length) {
            box.appendChild(mkEl('div', 'empty-card', '조건에 맞는 시설이 없습니다.'));
            return box;
        }
        const wrap = mkEl('div', 'table-scroll zone-facility-scroll');
        const table = document.createElement('table');
        const thead = document.createElement('thead');
        const hr = document.createElement('tr');
        columns.concat(['지도']).forEach(c => hr.appendChild(mkEl('th', null, c)));
        thead.appendChild(hr);
        table.appendChild(thead);
        const tbody = document.createElement('tbody');
        zoneFacilityRecords.slice(0, ZONE_FACILITY_LIMIT).forEach(rec => {
            const tr = document.createElement('tr');
            columns.forEach(c => {
                if (c === '활동유무') {
                    const td = document.createElement('td');
                    td.appendChild(mkEl('span', 'pending-status status-' + zoneStatusOf({ 활동유무: rec[c] }), rec[c]));
                    tr.appendChild(td);
                } else {
                    tr.appendChild(mkEl('td', rec[c] ? null : 'cell-empty', rec[c] || '-'));
                }
            });
            const mapTd = document.createElement('td');
            const url = kakaoMapLinkJS(rec.설치주소);
            if (url) {
                const aEl = mkEl('a', 'pending-map-link', '🗺 지도');
                aEl.href = url; aEl.target = '_blank'; aEl.rel = 'noopener';
                mapTd.appendChild(aEl);
            } else {
                mapTd.appendChild(mkEl('span', 'pending-map-link disabled', '🗺 지도'));
            }
            tr.appendChild(mapTd);
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        wrap.appendChild(table);
        box.appendChild(wrap);
        if (items.length > ZONE_FACILITY_LIMIT) {
            box.appendChild(mkEl('p', 'chart-note', '상위 ' + fmtInt(ZONE_FACILITY_LIMIT) + '건만 표시합니다 -- 전체 ' + fmtInt(items.length) + '건은 CSV로 받으세요.'));
        }
        return box;
    }
    let zoneFacilityRecords = [];
    function downloadZoneFacilityCSV() {
        if (!zoneFacilityRecords.length) return;
        const eol = String.fromCharCode(13, 10);
        const headers = Object.keys(zoneFacilityRecords[0]);
        const esc = v => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
        const csv = String.fromCharCode(0xFEFF) + headers.join(',') + eol
            + zoneFacilityRecords.map(r => headers.map(h => esc(r[h])).join(',')).join(eol) + eol;
        const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8;' }));
        const a = document.createElement('a');
        a.href = url; a.download = '구역별_시설조회.csv'; a.click();
        URL.revokeObjectURL(url);
    }
    function renderZoneActivityEl(containerEl, zoneData, ownerData) {
        // Keep whichever 건수 표 toggles the viewer had open across a re-render
        const openTables = new Set(Array.from(containerEl.querySelectorAll('.zone-card')).filter(c => {
            const d = c.querySelector('details.zone-table-toggle');
            return d && d.open;
        }).map(c => c.id));
        containerEl.innerHTML = '';
        if (!zoneData) {
            containerEl.appendChild(mkEl('div', 'empty-card', '구역별로 집계할 데이터가 없습니다 (영업구역정보/기술구역정보/구역정보 컬럼을 찾지 못했습니다).'));
            return;
        }
        if (zoneFocus && !PROGRESS_TYPES.some(t => zoneData[t] && zoneData[t].rows.some(r => t + '|' + r.라벨 === zoneFocus))) {
            zoneFocus = null; // 필터/보기 기준이 바뀌어 그 구역이 사라졌으면 해제
        }
        containerEl.appendChild(zoneStatusPillsEl());
        const dvGrid = mkEl('div', 'chart-grid dv-grid-2');
        dvGrid.appendChild(zoneFocusTop10El(zoneData));
        dvGrid.appendChild(ownerScatterEl(ownerData && ownerData.SP ? ownerData.SP : zoneData.SP));
        containerEl.appendChild(dvGrid);
        PROGRESS_TYPES.forEach(t => {
            if (!zoneData[t]) return;
            const card = zoneCardEl(t, zoneData[t]);
            if (openTables.has(card.id)) card.querySelector('details.zone-table-toggle').open = true;
            containerEl.appendChild(card);
        });
        containerEl.appendChild(zoneFacilityListEl(zoneData));
    }
    // ---- 구역별 실적현황 (보고서 양식: 영업사원 / 기술사원 / 출동사원) ----
    // SP는 8. 영업구역담당자로 붙인 담당자별(없으면 영업구역별), SE는 기술구역정보별, SG는 구역정보별.
    // 달성율 = 처리완료 / 대상. 소계 달성율 90% 미만은 빨간색, 담당자 달성율 30% 미만 줄은 노란 배경.
    const PERF_WARN_PCT = 90, PERF_LOW_PCT = 30;
    const pct0 = (done, total) => total ? Math.round(done / total * 100) + '%' : '-';
    function perfGroups(zd) {
        // zd.rows는 이미 지사 순서로 정렬됨 -- 지사별로 묶고 소계를 붙인다
        const groups = [];
        zd.rows.forEach(r => {
            if (!groups.length || groups[groups.length - 1].지사 !== r.지사) groups.push({ 지사: r.지사, rows: [] });
            groups[groups.length - 1].rows.push(r);
        });
        groups.forEach(g => {
            g.sub = g.rows.reduce((a, r) => ({ 대상: a.대상 + r.대상, 처리완료: a.처리완료 + r.처리완료, 접수: a.접수 + r.접수, 미접수: a.미접수 + r.미접수 }),
                { 대상: 0, 처리완료: 0, 접수: 0, 미접수: 0 });
        });
        return groups;
    }
    function perfTableCard(title, cols, zd, nameOf, cellsOf, lowRow) {
        const card = mkEl('section', 'perf-card');
        const band = mkEl('div', 'perf-band');
        band.appendChild(mkEl('span', null, title));
        const copyBtn = mkEl('button', 'perf-copy', '📋 엑셀용 복사');
        copyBtn.type = 'button';
        band.appendChild(copyBtn);
        card.appendChild(band);
        if (!zd || !zd.rows.length) {
            card.appendChild(mkEl('div', 'empty-card', '집계할 데이터가 없습니다.'));
            copyBtn.disabled = true;
            return card;
        }
        const tsv = [cols.join('\\t')];
        const wrap = mkEl('div', 'table-scroll perf-scroll');
        const table = mkEl('table', 'perf-table');
        const thead = document.createElement('thead');
        const hr = document.createElement('tr');
        cols.forEach(c => hr.appendChild(mkEl('th', c === '미접수' ? 'perf-th-alert' : null, c)));
        thead.appendChild(hr);
        table.appendChild(thead);
        const tbody = document.createElement('tbody');
        const addCells = (tr, cells) => cells.forEach(([text, cls]) => tr.appendChild(mkEl('td', cls || 'cell-num', text)));
        perfGroups(zd).forEach(g => {
            g.rows.forEach((r, i) => {
                const tr = document.createElement('tr');
                if (lowRow && lowRow(r)) tr.className = 'perf-low';
                if (i === 0) {
                    const td = mkEl('td', 'perf-branch', g.지사);
                    td.rowSpan = g.rows.length + 1;
                    tr.appendChild(td);
                }
                tr.appendChild(mkEl('td', 'perf-name', nameOf(r)));
                const cells = cellsOf(r);
                addCells(tr, cells);
                tbody.appendChild(tr);
                tsv.push([g.지사, nameOf(r)].concat(cells.map(c => c[0])).join('\\t'));
            });
            const sub = document.createElement('tr');
            sub.className = 'perf-sub';
            sub.appendChild(mkEl('td', 'perf-name', '소계'));
            const cells = cellsOf(g.sub, true);
            addCells(sub, cells);
            tbody.appendChild(sub);
            tsv.push([g.지사, '소계'].concat(cells.map(c => c[0])).join('\\t'));
        });
        const totalRow = document.createElement('tr');
        totalRow.className = 'perf-total';
        const t0 = mkEl('td', 'perf-branch', '합계');
        t0.colSpan = 2;
        totalRow.appendChild(t0);
        const totalCells = cellsOf(zd.total, true);
        addCells(totalRow, totalCells);
        tbody.appendChild(totalRow);
        tsv.push(['합계', ''].concat(totalCells.map(c => c[0])).join('\\t'));
        table.appendChild(tbody);
        wrap.appendChild(table);
        card.appendChild(wrap);
        copyBtn.dataset.perfCopy = tsv.join('\\n');
        return card;
    }
    function rateCell(done, total, isSub) {
        const warn = isSub && total && done / total * 100 < PERF_WARN_PCT;
        return [pct0(done, total), 'cell-num perf-rate' + (warn ? ' perf-warn' : '')];
    }
    const blankZero = n => n ? fmtInt(n) : '';
    function renderPerfReport() {
        const wrap = document.getElementById('perfReportWrap');
        if (!wrap) return;
        wrap.innerHTML = '';
        const rows = applyGlobalFilter(latestMergedRows);
        const byOwner = buildZoneActivityJS(rows, 'owner');
        const byZone = buildZoneActivityJS(rows, 'zone');
        if (!byZone) {
            wrap.appendChild(mkEl('div', 'empty-card', '구역별로 집계할 데이터가 없습니다.'));
            return;
        }
        const spData = byOwner && byOwner.SP && byOwner.SP.mode ? byOwner.SP : byZone.SP;
        const spByOwner = !!(spData && spData.mode);
        wrap.appendChild(mkEl('p', 'section-desc',
            '달성율 = 처리완료 ÷ 대상 · 소계 달성율 ' + PERF_WARN_PCT + '% 미만 빨간색 · 담당자 달성율 ' + PERF_LOW_PCT + '% 미만 노란 줄'
            + (spByOwner ? '' : ' · 8. 영업구역담당자 파일이 없어 영업사원 표는 영업구역별로 표시')));
        const bar = mkEl('div', 'perf-toolbar');
        [['view', '📄 HTML 새 창으로 보기 (인쇄용)'], ['save', '💾 HTML 파일로 저장']].forEach(([mode, label]) => {
            const b = mkEl('button', 'ghost-btn small', label);
            b.type = 'button';
            b.dataset.perfHtml = mode;
            bar.appendChild(b);
        });
        wrap.appendChild(bar);
        const grid = mkEl('div', 'perf-grid');
        grid.appendChild(perfTableCard('영업사원 실적현황 (SP)', ['지사', spByOwner ? '담당자' : '영업구역', '미접수', '접수', '처리완료', '합계', '달성율'],
            spData, r => spByOwner ? r.담당자 : r.구역,
            (r, isSub) => [[blankZero(r.미접수), 'cell-num perf-alert'], [blankZero(r.접수), 'cell-num perf-alert'],
                [blankZero(r.처리완료)], [fmtInt(r.대상)], rateCell(r.처리완료, r.대상, isSub)],
            r => r.대상 && r.처리완료 / r.대상 * 100 < PERF_LOW_PCT));
        [['SE', '기술사원 실적현황 (SE · 구역별)'], ['SG', '출동사원 실적현황 (SG · 구역별)']].forEach(([t, title]) => {
            grid.appendChild(perfTableCard(title, ['지사', '구역', '대상', '실적', '달성율'], byZone[t], r => r.구역,
                (r, isSub) => [[fmtInt(r.대상)], [fmtInt(r.처리완료)], rateCell(r.처리완료, r.대상, isSub)]));
        });
        wrap.appendChild(grid);
    }
    // 실적현황표 3개를 한 장짜리 HTML로 (스크롤 없이, A4 가로 인쇄용 스타일 포함)
    // 한 화면 캡처용 배치: 긴 표(보통 영업사원)는 지사 묶음 단위로 끊어 여러 열로 나눈다 --
    // 표 높이와 전체 폭이 지금 창 비율에 맞도록 열 수를 고른다 (지사 병합칸·소계는 끊지 않음).
    function perfCaptureLayout(grid) {
        const ROW_H = 16, HEAD_H = 60, GAP = 12;
        const availW = Math.max(900, window.innerWidth - 40), availH = Math.max(480, window.innerHeight - 70);
        const tables = Array.from(grid.querySelectorAll('.perf-card')).map(card => {
            const groups = [];
            let total = null;
            card.querySelectorAll('tbody tr').forEach(tr => {
                if (tr.classList.contains('perf-total')) { total = tr; return; }
                if (tr.querySelector('td.perf-branch') || !groups.length) groups.push([]);
                groups[groups.length - 1].push(tr);
            });
            const cols = card.querySelectorAll('thead th').length;
            return { card, groups, total, width: cols > 5 ? 345 : 245 };
        });
        const pack = (groups, limit) => {
            const chunks = [];
            let cur = [], n = 0;
            groups.forEach(g => {
                if (cur.length && n + g.length > limit) { chunks.push(cur); cur = []; n = 0; }
                cur = cur.concat(g); n += g.length;
            });
            if (cur.length) chunks.push(cur);
            return chunks;
        };
        const withRows = tables.filter(t => t.groups.length);
        const maxGroup = Math.max(1, ...withRows.map(t => Math.max(...t.groups.map(g => g.length))));
        const maxRows = Math.max(1, ...withRows.map(t => t.groups.reduce((n, g) => n + g.length, 0)));
        let best = null;
        for (let limit = maxGroup; limit <= maxRows; limit++) {
            let width = 0, count = 0;
            withRows.forEach(t => { const k = pack(t.groups, limit).length; width += k * t.width; count += k; });
            width += GAP * Math.max(0, count - 1);
            const scale = Math.min(1, availH / (HEAD_H + (limit + 1) * ROW_H), availW / width);
            if (!best || scale >= best.scale - 0.001) best = { limit, scale, count };  // 같은 배율이면 덜 쪼갠(열이 적은) 배치
        }
        const out = document.createElement('div');
        out.className = 'perf-grid';
        let columns = 0;
        tables.forEach(t => {
            let chunks = t.groups.length ? pack(t.groups, best.limit) : [[]];
            if (chunks.length > 1) {
                // 같은 열 수로 나누되 높이가 고르게: 가장 작은 한도부터 올려 같은 개수가 되는 첫 한도
                const rowsTotal = t.groups.reduce((n, g) => n + g.length, 0);
                for (let lim = Math.ceil(rowsTotal / chunks.length); lim <= best.limit; lim++) {
                    const c = pack(t.groups, lim);
                    if (c.length <= chunks.length) { chunks = c; break; }
                }
            }
            chunks.forEach((rows, i) => {
                const card = t.card.cloneNode(false);
                const band = t.card.querySelector('.perf-band');
                if (band) {
                    const b = band.cloneNode(true);
                    b.querySelectorAll('.perf-copy').forEach(x => x.remove());
                    if (chunks.length > 1) { const title = b.querySelector('span'); if (title) title.textContent += ' · ' + (i + 1) + '/' + chunks.length; }
                    card.appendChild(b);
                }
                const srcTable = t.card.querySelector('table');
                if (srcTable) {
                    const wrap = document.createElement('div');
                    wrap.className = 'table-scroll perf-scroll';
                    const table = srcTable.cloneNode(false);
                    table.appendChild(srcTable.querySelector('thead').cloneNode(true));
                    const tbody = document.createElement('tbody');
                    rows.forEach(tr => tbody.appendChild(tr.cloneNode(true)));
                    if (i === chunks.length - 1 && t.total) tbody.appendChild(t.total.cloneNode(true));
                    table.appendChild(tbody);
                    wrap.appendChild(table);
                    card.appendChild(wrap);
                } else {
                    Array.from(t.card.children).slice(1).forEach(c => card.appendChild(c.cloneNode(true)));
                }
                out.appendChild(card);
                columns++;
            });
        });
        out.style.gridTemplateColumns = 'repeat(' + columns + ', max-content)';
        return out;
    }
    function perfStandaloneHtml() {
        const grid = document.querySelector('#perfReportWrap .perf-grid');
        if (!grid) return null;
        const layout = perfCaptureLayout(grid);
        const css = Array.from(document.querySelectorAll('style')).map(st => st.textContent).join('\\n');
        const escHtml = t => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        const filter = (document.getElementById('navFilterSummary') || {}).textContent || '';
        const stamp = new Date().toLocaleString('ko-KR');
        // 한 화면 캡처용: 표는 내용 폭만큼(가로로 늘어나지 않게) 나란히, 행 높이 압축,
        // 열면 창 크기에 맞춰 전체를 축소(zoom) -- '원래 크기'로 되돌릴 수 있다.
        const extra = [
            'html,body{background:#fff;margin:0;}',
            'body{padding:10px 14px;}',
            '.cap-head{display:flex;align-items:baseline;gap:12px;margin:0 0 8px;flex-wrap:wrap;}',
            '.cap-head h1{font-size:16px;margin:0;color:#0b0b0b;}',
            '.cap-head p{margin:0;color:#52514e;font-size:11.5px;}',
            '.cap-tools{margin-left:auto;display:flex;gap:6px;}',
            '.cap-tools button{font-size:11.5px;padding:3px 10px;border:1px solid #c3c2b7;border-radius:6px;background:#fff;color:#0b0b0b;cursor:pointer;}',
            '.cap-tools button.on{background:#2563eb;color:#fff;border-color:#2563eb;}',
            '#capArea{width:max-content;}',
            '.perf-grid{display:grid;align-items:start;gap:12px;margin:0;}',
            '.perf-card{border-radius:8px;}',
            '.perf-band{padding:4px 10px;font-size:12.5px;}',
            '.table-scroll.perf-scroll{overflow:visible;border:none;border-radius:0;}',
            '.perf-table{width:auto;font-size:11px;line-height:1.2;}',
            '.perf-table th{position:static;padding:3px 8px;font-size:11px;}',
            '.perf-table td{padding:1px 8px;height:15px;}',
            '.perf-table td.perf-name{min-width:62px;}',
            '.perf-table td.perf-branch{min-width:44px;}',
            '@media print{@page{size:A4 landscape;margin:6mm}.cap-tools{display:none}body{padding:0}}'
        ].join('');
        // 맞춤: zoom을 준 뒤 실제 크기를 재서 넘치면 다시 줄이기를 몇 번 반복 (글자 반올림 오차 보정)
        const fitScript = '<script>(function(){var a=document.getElementById("capArea"),mode="fit";'
            + 'function mark(){document.querySelectorAll("[data-cap]").forEach(function(b){b.classList.toggle("on",b.getAttribute("data-cap")===mode);});}'
            + 'function apply(){var s=1;a.style.zoom="1";if(mode==="fit"){for(var i=0;i<5;i++){var r=a.getBoundingClientRect();'
            + 'var f=Math.min((window.innerHeight-r.top-8)/r.height,(window.innerWidth-r.left-14)/r.width);'
            + 'if(f>=0.999&&(s>=1||i>0))break;s=Math.max(0.3,Math.min(1,s*f));a.style.zoom=String(s);}}mark();}'
            + 'document.addEventListener("click",function(e){var b=e.target.closest("[data-cap]");if(!b)return;'
            + 'if(b.getAttribute("data-cap")==="print"){window.print();return;}mode=b.getAttribute("data-cap");apply();});'
            + 'window.addEventListener("resize",apply);window.addEventListener("load",apply);apply();})();<\\/script>';
        return '<!DOCTYPE html><html lang="ko" data-theme="light"><head><meta charset="UTF-8">'
            + '<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>실적현황표</title>'
            + '<style>' + css + '</style><style>' + extra + '</style></head><body>'
            + '<div class="cap-head"><h1>유지고객 이탈방지 실적현황</h1><p>' + escHtml(stamp) + ' 기준 · ' + escHtml(filter) + '</p>'
            + '<div class="cap-tools"><button type="button" data-cap="fit">한 화면에 맞춤</button>'
            + '<button type="button" data-cap="full">원래 크기</button><button type="button" data-cap="print">🖨 인쇄</button></div></div>'
            + '<div id="capArea">' + layout.outerHTML + '</div>' + fitScript + '</body></html>';
    }
    function perfHtmlExport(mode) {
        const html = perfStandaloneHtml();
        if (!html) return;
        const url = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }));
        if (mode === 'view' && window.open(url, '_blank')) { setTimeout(() => URL.revokeObjectURL(url), 60000); return; }
        const d = new Date(), p = x => String(x).padStart(2, '0');
        const a = document.createElement('a');
        a.href = url; a.download = '실적현황표_' + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + '.html';
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 5000);
    }
    function copyPerfTable(btn) {
        const text = btn.dataset.perfCopy;
        const done = () => { const t = btn.textContent; btn.textContent = '✅ 복사됨 -- 엑셀에 붙여넣기'; setTimeout(() => { btn.textContent = t; }, 1600); };
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
        else fallbackCopy(text, done);
    }

    function rerenderZoneActivity() {
        const wrap = document.getElementById('zoneActivityWrap');
        if (!wrap) return;
        const allRows = applyGlobalFilter(latestMergedRows);
        const hasOwner = allRows.some(r => r['영업구역담당자'] !== undefined && r['영업구역담당자'] !== null);
        const options = hasOwner ? spOwnerOptions(allRows) : [];
        if (zoneOwnerSel && !options.some(o => o.key === zoneOwnerSel)) zoneOwnerSel = '';  // 필터가 바뀌어 사라진 담당자
        const rows = zoneOwnerSel ? allRows.filter(r => r['활동대상구분'] !== 'SP' || spOwnerKey(r) === zoneOwnerSel) : allRows;
        const zoneData = buildZoneActivityJS(rows, zoneSpMode);
        if (zoneData && zoneData.SP) zoneData.SP.ownerOptions = options;
        const ownerData = buildZoneActivityJS(allRows, 'owner');  // 산점도는 비교용으로 전체 유지
        renderZoneActivityEl(wrap, zoneData, ownerData);
        const tiles = document.getElementById('overviewTiles');
        if (tiles) {
            tiles.innerHTML = '';
            const overall = zoneOwnerSel ? buildZoneActivityJS(allRows, 'zone') : zoneData;
            if (overall) tiles.appendChild(zoneKpiTilesEl(overall));
            // 어떤 열의 어떤 값으로 셌는지 -- 실적이 이상하면 이 줄로 바로 확인
            const dist = new Map();
            allRows.forEach(r => { const v = r['활동유무']; const k = (v === null || v === undefined || v === '') ? '빈칸' : String(v); dist.set(k, (dist.get(k) || 0) + 1); });
            const order = ['처리완료', '접수', '미접수'];
            const known = order.filter(k => dist.has(k)).map(k => k + ' ' + fmtInt(dist.get(k)));
            const others = Array.from(dist).filter(([k]) => !order.includes(k)).sort((a, b) => b[1] - a[1]);
            const otherText = others.length ? ' · 그 외 ' + others.slice(0, 4).map(([k, n]) => k + ' ' + fmtInt(n)).join(', ') + (others.length > 4 ? ' …' : '') : '';
            tiles.appendChild(mkEl('p', 'section-desc status-diag',
                '상태 기준 열: ' + (statusColUsed || '(찾지 못함)')
                + (DATA.db && DATA.db.renamed && DATA.db.renamed[statusColUsed] ? ' (파일의 ' + DATA.db.renamed[statusColUsed] + ' 열)' : '') + ' -- ' + (known.length ? known.join(' · ') : '처리완료/접수/미접수 값 없음') + otherText));
            const spRows = allRows.filter(r => r['활동대상구분'] === 'SP');
            if (spRows.some(r => '영업구역담당자_출처' in r)) {
                const fromDb = spRows.filter(r => r['영업구역담당자_출처'] === '총괄DB SP담당').length;
                const fromMap = spRows.filter(r => r['영업구역담당자_출처'] === '8번 영업구역담당자').length;
                tiles.appendChild(mkEl('p', 'section-desc', 'SP 담당자: 총괄DB SP담당 우선 ' + fmtInt(fromDb) + '건 · 8번 영업구역담당자 ' + fmtInt(fromMap)
                    + '건 · 담당자 없음(미매칭) ' + fmtInt(spRows.length - fromDb - fromMap) + '건'));
            }
            tiles.appendChild(mkEl('p', 'section-desc', '실적 기준: 총괄DB 활동유무만 (2번 VOC · 3번 순찰 · 7번 해지 결과는 실적에 더하지 않고 상세 데이터의 sp 담당자 상태값 열에 표시)'));
        }
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
    // 핵심 요약 표 -- report.py render_progress_matrix와 같은 마크업 (그룹 색 헤더 / 그룹 경계선 / 하위 순위 음영)
    const PG_GROUPS = ['SP', 'SE', 'SG', '전체'];
    const PG_CLASS = { SP: 'pg-SP', SE: 'pg-SE', SG: 'pg-SG', '전체': 'pg-ALL' };
    const PG_COLS = {
        SP: ['처리완료', '접수', '미접수', '계', '진척율'],
        SE: ['처리완료', '미처리', '계', '진척율'],
        SG: ['처리완료', '미처리', '계', '진척율'],
        '전체': ['처리완료', '미처리', '계', '진척율', '순위'],
    };
    const pgBottomCount = n => Math.min(4, Math.floor(n / 2));
    function progressRowEl(row, isTotal, n, k) {
        const bottom = !isTotal && k > 0 && row['순위'] > n - k;
        const tr = document.createElement('tr');
        if (isTotal) tr.className = 'progress-total-row'; else if (bottom) tr.className = 'pg-bottom';
        tr.appendChild(mkEl('td', 'progress-branch' + (isTotal ? ' progress-total-label' : ''), row['지사']));
        PG_GROUPS.forEach(g => {
            const cell = row[g];
            PG_COLS[g].filter(c => c !== '순위').forEach((c, i) => {
                const td = c === '진척율' ? progressPctTd(cell) : progressNumTd(cell[c]);
                if (i === 0) td.classList.add('grp-start');
                tr.appendChild(td);
            });
        });
        if (isTotal) tr.appendChild(mkEl('td', 'cell-num', '-'));
        else if (bottom) {
            const td = mkEl('td', 'cell-num progress-rank pg-rank-bottom', '▼ ' + row['순위']);
            td.title = '순위 하위 ' + k + '개';
            tr.appendChild(td);
        } else tr.appendChild(mkEl('td', 'cell-num progress-rank', String(row['순위'])));
        return tr;
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
        const branchTh = mkEl('th', 'pg-branch-th', '지사'); branchTh.rowSpan = 2;
        groupTr.appendChild(branchTh);
        const subTr = document.createElement('tr');
        PG_GROUPS.forEach(g => {
            const th = mkEl('th', 'pg-g ' + PG_CLASS[g] + ' grp-start', g);
            th.colSpan = PG_COLS[g].length;
            groupTr.appendChild(th);
            PG_COLS[g].forEach((c, i) => subTr.appendChild(mkEl('th', 'pg-sub ' + PG_CLASS[g] + (i === 0 ? ' grp-start' : ''), c)));
        });
        thead.appendChild(groupTr); thead.appendChild(subTr);
        table.appendChild(thead);

        const n = matrix.branch_rows.length, k = pgBottomCount(n);
        const tbody = document.createElement('tbody');
        matrix.branch_rows.forEach(r => tbody.appendChild(progressRowEl(r, false, n, k)));
        tbody.appendChild(progressRowEl(matrix.total_row, true, n, k));
        table.appendChild(tbody);
        scrollDiv.appendChild(table);
        containerEl.appendChild(scrollDiv);
        if (k) {
            const note = mkEl('p', 'pg-note');
            note.appendChild(mkEl('span', 'pg-note-swatch'));
            note.appendChild(document.createTextNode('▼ 순위 하위 ' + k + '개 지사 (연한 붉은 음영) · 진척율 칸 색: 30% 미만 위험 · 55% 미만 주의 · 그 이상 양호'));
            containerEl.appendChild(note);
        }

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
        const el = document.getElementById('globalFilterSummary') || document.createElement('span');
        const parts = [];
        if (globalFilter.hq) parts.push('본부: ' + globalFilter.hq);
        if (globalFilter.branch) parts.push('지사: ' + globalFilter.branch);
        if (globalFilter.activity) parts.push('구분: ' + globalFilter.activity);
        if (globalFilter.owner) parts.push('담당자: ' + globalFilter.owner);
        el.textContent = (parts.length ? parts.join(' · ') + ' · ' : '') + filteredCount.toLocaleString('ko-KR') + ' / ' + totalCount.toLocaleString('ko-KR') + '건';
        const navEl = document.getElementById('navFilterSummary');
        if (navEl) navEl.textContent = (parts.length ? '🔍 ' + parts.join(' · ') + ' · ' : '전체 · ') + filteredCount.toLocaleString('ko-KR') + '건';
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

    // ---- 지도로 보기: 설치주소 좌표에 관리계약을 찍는다 ----
    // 좌표는 리포트 생성 때 카카오로 변환해 실은 #geoData({주소: [위도, 경도(, 1=동 단위)]}) 또는 파일의 좌표 열.
    // 위 필터(본부/지사/대상구분/담당자)를 그대로 따르고, 여기서 지사·대상·상태·담당자·구역을 더 좁힌다.
    const geoEl = document.getElementById('geoData');
    const GEO = (function () { if (!geoEl) return null; try { return JSON.parse(geoEl.textContent); } catch (e) { return null; } })();
    const MAP_STATUS = ['미접수', '접수', '미처리', '처리완료'];  // 급한 순 -- 한 주소에 계약이 여럿이면 가장 급한 상태의 색
    const MAP_STATUS_VAR = { 미접수: '--critical', 접수: '--warning', 미처리: '--text-muted', 처리완료: '--good' };
    const MAP_TYPE_VAR = { SP: '--s3', SE: '--s2', SG: '--s1' };
    const MAP_HIDE_COLS = ['활동유무_원래열', '영업구역담당자_출처'];
    const NBSP = String.fromCharCode(160);
    const custMap = { open: false, 지사: '', 대상: '', 상태: '', 관리주체: '', 담당자: '', 구역: '', color: '활동유무', map: null, fitKey: null, loading: false, latest: null, note: '' };
    const mapCssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    const mapText = v => (v === null || v === undefined) ? '' : String(v).split(NBSP).join(' ').trim();
    const mapStatus = r => { const v = r['활동유무']; return (v === '미접수' || v === '접수' || v === '처리완료') ? v : '미처리'; };
    const mapBranch = r => mapText(r['지사']) || mapText(r['관리지사']) || UNKNOWN_LABEL;
    const mapOwner = r => mapText(r['영업구역담당자']) || mapText(r['SP담당']);
    const mapName = r => mapText(r['상호']) || mapText(r['관리고객명']) || mapText(r['고객명']) || '(상호 없음)';
    const mapWon = v => { const n = Number(v); return (v === null || v === undefined || v === '' || isNaN(n)) ? '' : Math.round(n).toLocaleString('ko-KR') + '원'; };
    function rowCoord(r) {
        const c = r['위치좌표(위도,경도)'] || r['위치좌표'];
        if (typeof c === 'string' && c.includes(',')) { const p = c.split(',').map(Number); if (p[0] && p[1]) return [p[0], p[1]]; }
        if (r['위도'] && r['경도']) { const a = Number(r['위도']), b = Number(r['경도']); if (a && b) return [a, b]; }
        if (GEO) { const g = GEO[mapText(r['설치주소'])]; if (g) return g; }
        return null;
    }
    let mapZoneCols = {};
    const mapZone = r => { const c = mapZoneCols[r['활동대상구분']]; return c ? (zoneKey(r[c]) || '') : ''; };
    const branchRankOf = b => { const i = DATA.branchOrder.indexOf(b); return i < 0 ? DATA.branchOrder.length : i; };

    function mapPills(label, key, values, noAll) {
        const g = mkEl('div', 'filter-group');
        g.appendChild(mkEl('span', 'filter-group-label', label));
        const row = mkEl('div', 'filter-pill-row');
        (noAll ? values : [''].concat(values)).forEach(v => {
            const b = mkEl('button', 'filter-pill' + (custMap[key] === v ? ' active' : ''), v === '' ? '전체' : v);
            b.type = 'button'; b.dataset.mapKey = key; b.dataset.mapValue = v;
            row.appendChild(b);
        });
        g.appendChild(row);
        return g;
    }
    function mapSelect(label, key, options, allText) {  // options: [[value, text]]
        const g = mkEl('div', 'filter-group');
        g.appendChild(mkEl('span', 'filter-group-label', label));
        const sel = mkEl('select', 'filter-select');
        sel.dataset.mapSelect = key;
        sel.setAttribute('aria-label', label);
        sel.appendChild(new Option(allText, ''));
        options.forEach(([v, t]) => sel.appendChild(new Option(t, v)));
        sel.value = custMap[key];
        g.appendChild(sel);
        return g;
    }
    function mapPopupEl(group) {
        const box = mkEl('div', 'core-pop cust-pop');
        const rows = group.rows;
        if (rows.length > 1) box.appendChild(mkEl('div', 'core-pop-title', '이 주소의 관리계약 ' + rows.length + '건'));
        rows.slice(0, 12).forEach(r => {
            const item = mkEl('div', 'cust-pop-item');
            item.appendChild(mkEl('div', rows.length > 1 ? 'cust-pop-name' : 'core-pop-title', mapName(r)));
            const st = mkEl('div', 'core-pop-line');
            st.appendChild(mkEl('span', 'core-state s-' + mapStatus(r), mapStatus(r)));
            st.appendChild(document.createTextNode(' · ' + [mapText(r['활동대상구분']), mapText(r['계약번호']) ? '계약 ' + mapText(r['계약번호']) : ''].filter(Boolean).join(' · ')));
            item.appendChild(st);
            const zone = mapZone(r), own = mapOwner(r), fee = mapWon(r['월정료']);
            item.appendChild(mkEl('div', 'core-pop-line', [mapBranch(r), zone ? '구역 ' + zone : '', own ? '담당 ' + own : '', fee ? '월정료 ' + fee : ''].filter(Boolean).join(' · ')));
            if (mapText(r['세부활동내역'])) item.appendChild(mkEl('div', 'core-pop-note', mapText(r['세부활동내역'])));
            // 전체 컬럼 -- 값이 있는 열만
            const det = document.createElement('details');
            det.className = 'cust-pop-all';
            det.appendChild(mkEl('summary', null, '전체 컬럼 보기'));
            const dl = mkEl('dl', 'core-kv');
            Object.keys(r).forEach(k => {
                const v = mapText(r[k]);
                if (!v || k.charAt(0) === '_' || MAP_HIDE_COLS.includes(k)) return;
                dl.appendChild(mkEl('dt', null, k)); dl.appendChild(mkEl('dd', null, v));
            });
            det.appendChild(dl);
            item.appendChild(det);
            box.appendChild(item);
        });
        if (rows.length > 12) box.appendChild(mkEl('div', 'core-pop-line', '외 ' + (rows.length - 12) + '건 -- 아래 상세 데이터에서 주소로 검색하세요'));
        const addr = mapText(rows[0]['설치주소']);
        box.appendChild(mkEl('div', 'core-pop-line core-pop-addr', addr + (group.approx ? ' (동 단위 위치)' : '')));
        if (addr) {
            const a = mkEl('a', 'pending-map-link', '🗺 카카오맵');
            a.href = 'https://map.kakao.com/link/search/' + encodeURIComponent(addr); a.target = '_blank'; a.rel = 'noopener';
            box.appendChild(a);
        }
        return box;
    }
    function drawCustMap(groups, fitKey) {
        const holder = document.getElementById('custMapBox');
        if (!holder || !custMap.open) return;
        custMap.latest = [groups, fitKey];
        const paint = M => {
            const [gs, key] = custMap.latest;
            const byStatus = custMap.color === '활동유무';
            const keys = byStatus ? MAP_STATUS.slice().reverse() : PROGRESS_TYPES;
            const vars = byStatus ? MAP_STATUS_VAR : MAP_TYPE_VAR;
            const ring = mapCssVar('--surface-1') || '#fff';
            // 묶음 테두리: 묶인 계약의 상태(또는 대상구분) 비율
            const ringOf = counts => {
                const total = keys.reduce((n, k) => n + (counts[k] || 0), 0);
                if (!total) return null;
                let at = 0;
                const stops = [];
                keys.forEach(k => {
                    if (!counts[k]) return;
                    const from = at; at += counts[k] / total * 100;
                    stops.push(mapCssVar(vars[k]) + ' ' + from.toFixed(1) + '% ' + at.toFixed(1) + '%');
                });
                return 'conic-gradient(' + stops.join(',') + ')';
            };
            M.setMarkers(gs.map(g => ({
                lat: g.lat, lng: g.lng, r: Math.round(6 + Math.min(7, 2 * Math.sqrt(g.rows.length - 1))), ring,
                fill: (byStatus ? mapCssVar(MAP_STATUS_VAR[g.status]) : (mapCssVar(MAP_TYPE_VAR[g.type]) || mapCssVar('--text-muted'))) || '#888',
                name: mapName(g.rows[0]) + (g.rows.length > 1 ? ' 외 ' + (g.rows.length - 1) + '건' : ''),
                tip: mapName(g.rows[0]) + (g.rows.length > 1 ? ' 외 ' + (g.rows.length - 1) + '건' : '') + ' · ' + g.status,
                popup: () => mapPopupEl(g), counts: byStatus ? g.statusCounts : g.typeCounts, rows: g.rows.length,
            })), { ringOf });
            if (custMap.fitKey !== key) { M.fit(15); custMap.fitKey = key; }
        };
        if (custMap.map) { paint(custMap.map); return; }
        if (custMap.loading) return;  // 열리는 중 -- 끝나면 가장 마지막 조건(custMap.latest)으로 그린다
        custMap.loading = true;
        DataIntelMapLib.open(holder, { cluster: true }).then(M => {
            custMap.loading = false;
            custMap.map = M;
            custMap.fitKey = null;
            custMap.note = M.note || '';
            const note = document.getElementById('custMapNote');
            if (note && custMap.note) note.textContent += ' · ' + custMap.note;
            if (custMap.open) paint(M);
        }, () => { custMap.loading = false; holder.textContent = '지도 모듈을 불러오지 못했습니다 (인터넷 연결 확인).'; });
    }
    function renderCustomerMap() {
        const wrap = document.getElementById('custMapWrap');
        const sec = document.getElementById('secMap');
        if (!wrap || !sec) return;
        const all = latestMergedRows;
        const anyCoord = all.some(r => rowCoord(r));
        const show = GEO !== null || anyCoord;  // 웹 업로드처럼 좌표를 얻을 길이 없으면 섹션을 숨긴다
        sec.hidden = !show;
        const navLink = document.querySelector('#dashNav a[data-sec="secMap"]');
        if (navLink) navLink.hidden = !show;
        if (!show) return;
        if (!wrap.firstChild) {
            const c = mkEl('div'); c.id = 'custMapControls'; wrap.appendChild(c);
            const b = mkEl('div', 'core-map cust-map'); b.id = 'custMapBox'; b.hidden = true; wrap.appendChild(b);
        }
        const controls = document.getElementById('custMapControls'), box = document.getElementById('custMapBox');
        controls.textContent = '';
        if (!anyCoord) {
            const why = (geoEl && geoEl.dataset.note) || '좌표 없음';
            controls.appendChild(mkEl('div', 'empty-card', '지도 좌표가 없습니다 (' + why + ') -- 데스크톱 GUI의 "카카오 REST 키" 칸에 키를 넣고 리포트를 다시 만들면 설치주소가 지도에 표시됩니다.'));
            box.hidden = true;
            return;
        }
        const base = applyGlobalFilter(all);
        const cols = new Set();
        base.forEach(r => Object.keys(r).forEach(k => cols.add(k)));
        mapZoneCols = {};
        PROGRESS_TYPES.forEach(t => { mapZoneCols[t] = findZoneCol(base, Array.from(cols), t, ZONE_COL_CANDIDATES[t]); });
        const uniq = (rows, f) => Array.from(new Set(rows.map(f).filter(Boolean)));
        const branches = uniq(base, mapBranch).sort((a, b) => branchRankOf(a) - branchRankOf(b) || String(a).localeCompare(String(b)));
        const types = PROGRESS_TYPES.filter(t => base.some(r => r['활동대상구분'] === t));
        const managers = cols.has('관리주체') ? uniq(base, r => mapText(r['관리주체'])).sort() : [];
        // 고른 값이 위 필터 때문에 사라졌으면 풀어 준다
        if (custMap.지사 && !branches.includes(custMap.지사)) custMap.지사 = '';
        if (custMap.대상 && !types.includes(custMap.대상)) custMap.대상 = '';
        if (custMap.관리주체 && !managers.includes(custMap.관리주체)) custMap.관리주체 = '';
        const scoped = base.filter(r => (!custMap.지사 || mapBranch(r) === custMap.지사) && (!custMap.대상 || r['활동대상구분'] === custMap.대상)
            && (!custMap.관리주체 || mapText(r['관리주체']) === custMap.관리주체));
        // 담당자·구역 목록은 고른 지사·대상 안에서, 지사 순 -> 이름 순
        const firstBranch = (rows, f) => { const m = new Map(); rows.forEach(r => { const k = f(r); if (k && (!m.has(k) || branchRankOf(mapBranch(r)) < branchRankOf(m.get(k)))) m.set(k, mapBranch(r)); }); return m; };
        const byBranchThen = m => Array.from(m).sort((a, b) => branchRankOf(a[1]) - branchRankOf(b[1]) || String(a[0]).localeCompare(String(b[0]))).map(([k, b]) => [k, b + '_' + k]);
        const owners = byBranchThen(firstBranch(scoped, mapOwner));
        const zones = byBranchThen(firstBranch(scoped.filter(r => !custMap.담당자 || mapOwner(r) === custMap.담당자), mapZone));
        if (custMap.담당자 && !owners.some(o => o[0] === custMap.담당자)) custMap.담당자 = '';
        if (custMap.구역 && !zones.some(z => z[0] === custMap.구역)) custMap.구역 = '';
        const rows = scoped.filter(r => (!custMap.상태 || mapStatus(r) === custMap.상태) && (!custMap.담당자 || mapOwner(r) === custMap.담당자) && (!custMap.구역 || mapZone(r) === custMap.구역));

        // 같은 좌표(같은 주소)의 계약은 마커 하나로
        const groups = new Map();
        let noCoord = 0;
        rows.forEach(r => {
            const c = rowCoord(r);
            if (!c) { noCoord++; return; }
            const k = c[0] + ',' + c[1];
            if (!groups.has(k)) groups.set(k, { lat: c[0], lng: c[1], approx: c[2] === 1, rows: [] });
            groups.get(k).rows.push(r);
        });
        const list = Array.from(groups.values());
        list.forEach(g => {
            g.status = MAP_STATUS.find(s => g.rows.some(r => mapStatus(r) === s));
            g.type = PROGRESS_TYPES.find(t => g.rows.some(r => r['활동대상구분'] === t)) || '';
            g.statusCounts = {}; g.typeCounts = {};
            g.rows.forEach(r => { const st = mapStatus(r), ty = r['활동대상구분']; g.statusCounts[st] = (g.statusCounts[st] || 0) + 1; g.typeCounts[ty] = (g.typeCounts[ty] || 0) + 1; });
        });

        const bar = mkEl('div', 'global-filter-bar cust-map-bar');
        const toggle = mkEl('button', 'filter-pill cust-map-toggle' + (custMap.open ? ' active' : ''), custMap.open ? '🗺️ 지도 접기' : '🗺️ 지도로 보기');
        toggle.type = 'button'; toggle.dataset.mapToggle = '1';
        toggle.setAttribute('aria-pressed', custMap.open ? 'true' : 'false');
        bar.appendChild(toggle);
        if (branches.length > 1) bar.appendChild(mapPills('지사', '지사', branches));
        if (types.length > 1) bar.appendChild(mapPills('대상', '대상', types));
        bar.appendChild(mapPills('상태', '상태', MAP_STATUS.filter(s => scoped.some(r => mapStatus(r) === s)).reverse()));
        if (managers.length) bar.appendChild(mapPills('관리주체', '관리주체', managers));
        if (owners.length) bar.appendChild(mapSelect('담당자', '담당자', owners, '전체 담당자 (' + owners.length + '명)'));
        if (zones.length) bar.appendChild(mapSelect('구역', '구역', zones, '전체 구역 (' + zones.length + '개)'));
        controls.appendChild(bar);

        const info = mkEl('div', 'core-map-head');
        if (custMap.open) {
            info.appendChild(mapPills('색상 기준', 'color', ['활동유무', '대상구분'], true));
            const lg = mkEl('div', 'legend-grid core-legend');
            const items = custMap.color === '활동유무' ? MAP_STATUS.slice().reverse().map(s => [s, MAP_STATUS_VAR[s]]) : PROGRESS_TYPES.map(t => [t, MAP_TYPE_VAR[t]]);
            items.forEach(([name, v]) => {
                const it = mkEl('div', 'legend-item'); const sw = mkEl('span', 'legend-swatch'); sw.style.background = mapCssVar(v); sw.style.borderRadius = '50%';
                it.appendChild(sw); it.appendChild(mkEl('span', 'legend-label', name)); lg.appendChild(it);
            });
            lg.appendChild(mkEl('span', 'legend-label core-size-note', '숫자 원 = 가까운 계약 묶음 (숫자 = 계약 수, 테두리 = 비율 · 누르면 확대) · 마커를 누르면 컬럼 내역'));
            info.appendChild(lg);
        }
        const approx = list.filter(g => g.approx).length;
        const cnote = mkEl('span', 'core-coord-note', '관리계약 ' + fmtInt(rows.length) + '건 · 지도 표시 ' + fmtInt(rows.length - noCoord) + '건 (주소 ' + fmtInt(list.length) + '곳)'
            + (approx ? ' · 동 단위 위치 ' + approx + '곳' : '') + (noCoord ? ' · 좌표 없음 ' + fmtInt(noCoord) + '건' : '') + (custMap.open && custMap.note ? ' · ' + custMap.note : ''));
        cnote.id = 'custMapNote';
        info.appendChild(cnote);
        controls.appendChild(info);

        box.hidden = !custMap.open;
        if (custMap.open) drawCustMap(list, JSON.stringify([globalFilter, custMap.지사, custMap.대상, custMap.상태, custMap.관리주체, custMap.담당자, custMap.구역]));
    }
    document.addEventListener('click', (e) => {
        if (!e.target.closest || !e.target.closest('#custMapWrap')) return;
        if (e.target.closest('[data-map-toggle]')) { custMap.open = !custMap.open; renderCustomerMap(); return; }
        const b = e.target.closest('[data-map-key]');
        if (!b) return;
        const k = b.dataset.mapKey, v = b.dataset.mapValue;
        custMap[k] = (k !== 'color' && custMap[k] === v) ? '' : v;  // 같은 것을 다시 누르면 해제
        if (k !== 'color' && !custMap.open) custMap.open = true;     // 조건을 고르면 지도도 연다
        renderCustomerMap();
    });
    document.addEventListener('change', (e) => {
        const sel = e.target && e.target.closest ? e.target.closest('#custMapWrap [data-map-select]') : null;
        if (!sel) return;
        custMap[sel.dataset.mapSelect] = sel.value;
        custMap.open = true;
        renderCustomerMap();
    });
    new MutationObserver(() => { if (custMap.open && custMap.map) renderCustomerMap(); }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

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

        const zoneActivityWrap = document.getElementById('zoneActivityWrap');
        if (zoneActivityWrap) rerenderZoneActivity();
        renderCustomerMap();
        renderPerfReport();

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

    function wireOwnerSelect() {
        const ownerSelect = document.getElementById('ownerFilterSelect');
        if (ownerSelect) ownerSelect.addEventListener('change', () => {
            globalFilter.owner = ownerSelect.value;
            renderAll();
            const filtered = applyGlobalFilter(latestMergedRows);
            logChange((ownerSelect.value ? '담당자 필터: ' + ownerSelect.value : '담당자 필터 해제') + ' (' + filtered.length.toLocaleString('ko-KR') + '/' + latestMergedRows.length.toLocaleString('ko-KR') + '건)');
        });
    }

    // ---- 웹 업로드용: 필터 바를 병합 결과로 다시 만든다 (report.py render_global_filter_bar와 같은 구성) ----
    function buildGlobalFilterBarEl(containerEl, rows) {
        containerEl.innerHTML = '';
        const ordered = (values, order) => {
            const present = new Set(values.filter(v => v !== null && v !== undefined && v !== ''));
            return order.filter(v => present.has(v)).concat(Array.from(present).filter(v => !order.includes(v)).sort());
        };
        const hqs = ordered(rows.map(r => r['관리본부'] != null ? r['관리본부'] : r['본부']), DATA.hqOrder);
        const branches = ordered(rows.map(r => r['관리지사'] != null ? r['관리지사'] : r['지사']), DATA.branchOrder);
        const activities = ordered(rows.map(r => r['활동대상구분']), ['SP', 'SE', 'SG']);
        const owners = ordered(rows.map(r => r['SP담당']), []);
        const bar = mkEl('div', 'global-filter-bar');
        bar.id = 'globalFilterBar';
        function group(label, id, dim, values) {
            if (!values.length) return;
            const g = mkEl('div', 'filter-group');
            g.appendChild(mkEl('span', 'filter-group-label', label));
            const row = mkEl('div', 'filter-pill-row');
            row.id = id;
            [''].concat(values).forEach(v => {
                const b = mkEl('button', 'filter-pill' + (v === '' ? ' active' : ''), v === '' ? '전체' : String(v));
                b.type = 'button'; b.dataset.dim = dim; b.dataset.value = v;
                row.appendChild(b);
            });
            g.appendChild(row);
            bar.appendChild(g);
        }
        group('본부', 'hqFilterRow', 'hq', hqs);
        group('지사', 'branchFilterRow', 'branch', branches);
        group('대상구분', 'activityFilterRow', 'activity', activities);
        if (owners.length) {
            const g = mkEl('div', 'filter-group');
            g.appendChild(mkEl('span', 'filter-group-label', '담당자'));
            const sel = mkEl('select', 'filter-select');
            sel.id = 'ownerFilterSelect';
            sel.appendChild(new Option('전체 담당자', ''));
            owners.forEach(o => sel.appendChild(new Option(o, o)));
            g.appendChild(sel);
            bar.appendChild(g);
        }
        const summary = mkEl('span', 'filter-summary');
        summary.id = 'globalFilterSummary';
        bar.appendChild(summary);
        containerEl.appendChild(bar);
        wireFilterPillRow('hqFilterRow', 'hq');
        wireFilterPillRow('branchFilterRow', 'branch');
        wireFilterPillRow('activityFilterRow', 'activity');
        wireOwnerSelect();
    }

    // 웹 업로드 페이지(web_app.py)가 브라우저에서 읽은 파일을 넘기는 입구.
    // payload: { db: {columns, rows}, files: {key: {columns, rows}}, zoneOwnerMap: {...} }
    window.DataIntelLoad = function (payload) {
        DATA.db = payload.db;
        Object.keys(fileRowsByKey).forEach(k => { delete fileRowsByKey[k]; });
        DATA.files = payload.files || {};
        Object.keys(DATA.files).forEach(k => { fileRowsByKey[k] = toObjects(DATA.files[k]); });
        dbRowsBase = toObjects(DATA.db);
        zoneOwnerMap = payload.zoneOwnerMap || {};
        Object.keys(globalFilter).forEach(k => { globalFilter[k] = ''; });
        zoneFocus = null;
        zoneOwnerSel = '';
        recomputeMerge();
        const barWrap = document.getElementById('globalFilterBarWrap');
        if (barWrap) buildGlobalFilterBarEl(barWrap, latestMergedRows);
        renderAll();
        logChange('파일 불러오기: 관리계약 ' + latestMergedRows.length.toLocaleString('ko-KR') + '건');
        return { rows: latestMergedRows.length, used: latestUsed };
    };

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
        if (latestMergedRows.length) updateFilterSummary(latestMergedRows.length, latestMergedRows.length);
        renderChangeLog();

        const btnTheme = document.getElementById('btnTheme');
        if (btnTheme) btnTheme.addEventListener('click', toggleTheme);
        const btnTop = document.getElementById('btnTop');
        if (btnTop) btnTop.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
        const btnExportCSV = document.getElementById('btnExportCSV');
        if (btnExportCSV) btnExportCSV.addEventListener('click', downloadCSV);

        // 구역별 활동 현황: SP 보기 기준 전환 / 활동유무 복수 선택 / 구역 클릭 시설 조회 / CSV
        const zoneActivityWrap = document.getElementById('zoneActivityWrap');
        if (zoneActivityWrap) {
            zoneActivityWrap.addEventListener('click', (e) => {
                const modeBtn = e.target.closest('[data-zone-mode]');
                const statusBtn = e.target.closest('[data-zone-status]');
                const focusRow = e.target.closest('[data-zone-focus]');
                if (modeBtn) {
                    if (modeBtn.dataset.zoneMode === zoneSpMode) return;
                    zoneSpMode = modeBtn.dataset.zoneMode;
                    zoneFocus = null;
                } else if (statusBtn) {
                    const v = statusBtn.dataset.zoneStatus;
                    if (v === '*') ZONE_STATUS_OPTIONS.forEach(o => zoneStatusSel.add(o[0]));
                    else if (zoneStatusSel.has(v)) zoneStatusSel.delete(v);
                    else zoneStatusSel.add(v);
                } else if (focusRow) {
                    const nextMode = focusRow.dataset.zoneFocusMode;
                    if (nextMode && nextMode !== zoneSpMode && ZONE_SP_MODES[nextMode]) {
                        zoneSpMode = nextMode;  // 산점도 점: 그 점과 같은 기준(담당자별)으로 전환 후 조회
                        zoneFocus = focusRow.dataset.zoneFocus;
                    } else {
                        zoneFocus = zoneFocus === focusRow.dataset.zoneFocus ? null : focusRow.dataset.zoneFocus;
                    }
                } else if (e.target.closest('[data-zone-focus-clear]')) {
                    zoneFocus = null;
                } else if (e.target.closest('[data-zone-csv]')) {
                    downloadZoneFacilityCSV();
                    return;
                } else {
                    return;
                }
                rerenderZoneActivity();
            });
            zoneActivityWrap.addEventListener('change', (e) => {
                if (!e.target.matches('[data-zone-owner]')) return;
                zoneOwnerSel = e.target.value;
                zoneFocus = null;
                rerenderZoneActivity();
            });
            zoneActivityWrap.addEventListener('keydown', (e) => {
                const t = e.target.closest && e.target.closest('[data-zone-focus]');
                if (e.key === 'Enter' && t) t.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            });
            zoneActivityWrap.addEventListener('mousemove', (e) => {
                const t = e.target.closest('[data-tip]');
                if (t) showDvTip(t, e.clientX, e.clientY); else hideDvTip();
            });
            zoneActivityWrap.addEventListener('mouseleave', hideDvTip);
            zoneActivityWrap.addEventListener('focusin', (e) => {
                const t = e.target.closest('[data-tip]');
                if (t) { const b = t.getBoundingClientRect(); showDvTip(t, b.left + b.width / 2, b.bottom); }
            });
            zoneActivityWrap.addEventListener('focusout', hideDvTip);
            rerenderZoneActivity(); // 서버 렌더 결과를 조회 기능이 붙은 화면으로 교체
        }
        renderCustomerMap();
        renderPerfReport();
        const perfWrap = document.getElementById('perfReportWrap');
        if (perfWrap) perfWrap.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-perf-copy]');
            if (btn) copyPerfTable(btn);
            const htmlBtn = e.target.closest('[data-perf-html]');
            if (htmlBtn) perfHtmlExport(htmlBtn.dataset.perfHtml);
        });

        wireFilterPillRow('hqFilterRow', 'hq');
        wireFilterPillRow('branchFilterRow', 'branch');
        wireFilterPillRow('activityFilterRow', 'activity');
        wireOwnerSelect();

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


# ---------------------------------------------------------------------------
# Page layout -- shared by the GUI report and the web version (web_app.py) so
# both always show the same sections in the same order. Ordered by the job:
# headline -> where the gaps are (구역·담당자) -> report tables -> who to act on
# -> contract background -> raw data; admin settings last (rarely used).
# ---------------------------------------------------------------------------

DASH_NAV_ITEMS = [
    ('secOverview', '📊 핵심 요약'),
    ('secZone', '🗺️ 구역·담당자'),
    ('secMap', '📍 지도'),
    ('secPerf', '📋 실적현황표'),
    ('secAction', '🎯 조치 대상'),
    ('secCore', '💎 코어고객'),
    ('secContract', '📑 계약·고객'),
    ('secDetail', '🔎 상세 데이터'),
]


def render_dashboard_nav(extra_items=(), hidden=False):
    """Sticky one-line section menu under the top bar (active section is
    highlighted; the current filter + row count sits on the right)."""
    links = "".join(
        f'<a href="#{sid}" data-sec="{sid}">{_e(label)}</a>'
        for sid, label in list(DASH_NAV_ITEMS) + list(extra_items)
    )
    return (f'<nav class="dash-nav" id="dashNav"{" hidden" if hidden else ""} aria-label="섹션 이동">'
            f'<div class="dash-nav-inner"><div class="dash-nav-links">{links}</div>'
            f'<span class="dash-nav-filter" id="navFilterSummary"></span></div></nav>')


def render_dashboard_sections(p):
    """p: server-rendered HTML per slot (all optional -- the web version passes
    none and lets the browser fill every container)."""
    g = lambda k: p.get(k) or ""
    cancel_block = (f'<div id="secCancel" class="dash-anchor">{g("cancel")}{g("nudge")}</div>'
                    if g("cancel") or g("nudge") else "")
    return f"""
        {g("filter_bar")}

        <details class="section-collapse dash-sec" id="secOverview" open>
        <summary class="section-title">📊 핵심 요약 <span class="sec-hint">활동 처리율 · 지사별 진척율 (보고서 상단 표)</span></summary>
        <div id="overviewTiles"></div>
        <div id="progressInsightWrap">{g("branch_insights")}</div>
        <div class="table-section" id="progressSection">{g("progress_table")}{g("progress_chart")}</div>
        <details class="subsection-collapse">
        <summary class="subsection-title">유형별 지사 순위 (SP / SE / SG)</summary>
        <div id="progressTypeWrap">{g("progress_type")}</div>
        </details>
        </details>

        <details class="section-collapse dash-sec" id="secZone" open>
        <summary class="section-title">🗺️ 구역·담당자별 활동 <span class="sec-hint">상태·담당자 선택 → 집중관리 구역 → 구역별 현황 → 시설 조회</span></summary>
        <div id="zoneActivityWrap">{g("zone_activity")}</div>
        </details>

        <details class="section-collapse dash-sec" id="secMap" open{"" if g("geo_json") else " hidden"}>
        <summary class="section-title">📍 지도로 보기 <span class="sec-hint">설치주소 위치 · 지사/대상/상태/담당자/구역 선택 · 마커를 누르면 컬럼 내역 (위 필터 연동)</span></summary>
        <div id="custMapWrap"></div>
        {g("geo_json")}
        </details>

        <details class="section-collapse dash-sec" id="secPerf" open>
        <summary class="section-title">📋 실적현황표 <span class="sec-hint">영업사원 · 기술사원 · 출동사원 (엑셀용 복사)</span></summary>
        <div id="perfReportWrap"></div>
        </details>

        <details class="section-collapse dash-sec" id="secAction" open>
        <summary class="section-title">🎯 조치 대상 <span class="sec-hint">부진 담당자 · 미접수/접수 발송 리스트</span></summary>
        <details class="subsection-collapse" open>
        <summary class="subsection-title">SP 부진자 추가분석 (담당자 기준)</summary>
        <div id="spRepSectionWrap">{g("sp_rep")}</div>
        </details>
        <details class="subsection-collapse" open>
        <summary class="subsection-title">SP 미접수/접수 발송용 리스트 (담당자별)</summary>
        <div id="spPendingSectionWrap">{g("sp_pending")}</div>
        </details>
        </details>

        <details class="section-collapse dash-sec" id="secCore" open{"" if g("core") else " hidden"}>
        <summary class="section-title">💎 코어고객 활동관리 <span class="badge">독립 데이터</span> <span class="sec-hint">관리주체 · 지사 · 활동내역 · 설치주소 지도 (위 필터와 무관)</span></summary>
        <div id="coreSectionWrap"></div>
        {g("core_json")}
        </details>

        <details class="section-collapse dash-sec" id="secContract" open>
        <summary class="section-title">📑 계약·고객 현황 <span class="sec-hint">계약 · 금액 · VOC · 재계약 · 분포</span></summary>
        {g("eda_button")}
        <div class="stat-grid" id="statGrid">{g("stat")}</div>
        <div class="chart-grid" id="chartGrid">{g("chart_grid")}</div>
        <div id="top10Section"></div>
        <div id="treeSummarySection"></div>
        <details class="subsection-collapse">
        <summary class="subsection-title">🔄 재계약대상(SP)</summary>
        <div id="recontractSectionWrap">{g("recontract")}</div>
        </details>
        <details class="subsection-collapse">
        <summary class="subsection-title">데이터 분포/이상치 분석 (EDA, 월정산금액 기준)</summary>
        <div id="edaSectionWrap">{g("eda")}</div>
        </details>
        </details>

        <details class="section-collapse dash-sec" id="secDetail" open>
        <summary class="section-title">🔎 상세 데이터 <span class="sec-hint">관리고객 전체 목록 · 필터/검색 · CSV</span></summary>
        <button id="btnExportCSV" class="export-btn" title="현재 조건으로 필터링된 모든 데이터를 엑셀(CSV)로 다운로드합니다.">📥 필터링된 데이터 엑셀(CSV) 다운로드</button>
        <div class="table-section" id="tableSection">{g("table")}</div>
        </details>

        {cancel_block}

        <div class="dash-admin">{g("admin_panel")}</div>"""


def generate_html_report(df, voc_df=None, patrol_df=None, cancel_df=None,
                          cancelled_facility_df=None, raw_files=None, matching_config=None,
                          password=None, admin_password=None, expiry_date=None, encrypt=True,
                          eda_link=False, core_df=None, core_voc_df=None, kakao_key=None, kakao_js_key=None, log=print):
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
    encrypt: wrap the finished report with real AES encryption (see
        secure_report.py) so the data can't be read without a password --
        needed for anything hosted on the web. Blank passwords then become
        strong random ones (the fixed DEFAULT_ADMIN_PASSWORD is public in
        this repo, so it must never be a decryption key).
    core_df / core_voc_df: 9. 코어고객 활동관리 (+ 9-1 VOC매칭) -- independent
        section with a 설치주소 map (see core_customers.py). kakao_key: Kakao
        REST key for geocoding; None -> this PC's saved key.
    kakao_js_key: Kakao JavaScript key -- map background becomes 카카오맵 (see
        render_map_config). None -> this PC's saved key.
    eda_link: show the '딥 다이브 EDA' button, which opens a sibling
        Data_Intel_PRO_EDA.html. Only generate_report.py's CLI writes that
        file (unencrypted, full data) -- the GUI and a deployed page never
        have it, so the button would just be a dead link there.
    """
    if encrypt:
        from .secure_report import generate_strong_password, load_admin_password
        if password is None:
            password = generate_strong_password()
        if admin_password is None:
            # this PC's fixed admin password (GUI setting), else a random one
            admin_password = load_admin_password() or generate_strong_password()
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
    zone_activity_html = render_zone_activity_section(build_zone_activity(df))
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

    eda_button_html = """
        <div class="eda-btn-wrap">
            <a href="Data_Intel_PRO_EDA.html" target="_blank" class="eda-btn">🚀 딥 다이브 EDA 분석기 열기 (별도 창)</a>
        </div>""" if eda_link else ""
    core_json = ""
    if core_df is not None:
        from .core_customers import build_core_payload, load_kakao_key
        core_payload = build_core_payload(core_df, core_voc_df, kakao_key=kakao_key or load_kakao_key(), log=log)
        if core_payload:
            core_json = ('<script type="application/json" id="coreData">'
                         + json.dumps(core_payload, ensure_ascii=False).replace('<', '\\u003c') + '</script>')
    # '지도로 보기' 좌표표: 설치주소 -> 좌표 (이 PC의 카카오 키·캐시). 주소 문자열만 카카오로 보낸다.
    geo_json = ""
    if raw_files is not None and '설치주소' in df.columns:
        from .core_customers import geocode_addresses, load_kakao_key
        geo, geo_note = geocode_addresses(df['설치주소'], kakao_key or load_kakao_key(), log=log)
        geo_json = (f'<script type="application/json" id="geoData" data-note="{_e(geo_note or "")}">'
                    + json.dumps(geo, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c') + '</script>')
    dash_nav_html = render_dashboard_nav(
        extra_items=[('secCancel', '⚠️ 해지')] if (cancel_section_html or nudge_section_html) else [])
    dash_sections_html = render_dashboard_sections({
        "filter_bar": filter_bar_html, "admin_panel": admin_panel_html,
        "branch_insights": branch_insights_html, "progress_table": progress_table_html,
        "progress_chart": progress_chart_html, "progress_type": progress_type_dashboard_html,
        "zone_activity": zone_activity_html, "sp_rep": sp_rep_section_html,
        "sp_pending": sp_pending_section_html, "stat": stat_html, "chart_grid": chart_grid_html,
        "recontract": recontract_section_html, "eda": eda_section_html, "table": table_html,
        "cancel": cancel_section_html, "nudge": nudge_section_html, "eda_button": eda_button_html,
        "core": bool(core_json), "core_json": core_json, "geo_json": geo_json,
    })
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
    {dash_nav_html}
    <div class="container">
{dash_sections_html}
    </div>
</div>

{embedded_script}
{render_map_config(kakao_js_key) if (geo_json or core_json) else ""}
<script>{script}</script>
</body>
</html>"""

    if encrypt:
        from .secure_report import encrypt_report
        html_out = encrypt_report(html_out, [password, admin_password], expiry_date=expiry_date)

    return html_out, password, expiry_date, admin_password


def render_map_config(kakao_js_key=None):
    """<script id="mapConfig"> for DataIntelMapLib: the Kakao JavaScript key
    (this PC's saved one by default) switches the map background to 카카오맵 on
    domains registered in the Kakao console. No key -> "" and the Leaflet map."""
    if kakao_js_key is None:
        from .core_customers import load_kakao_js_key
        kakao_js_key = load_kakao_js_key()
    key = re.sub(r'[^0-9A-Za-z]', '', str(kakao_js_key or ''))
    if not key:
        return ""
    return '<script type="application/json" id="mapConfig">' + json.dumps({"kakaoJsKey": key}) + '</script>'


CORE_REPORT_TITLE = "코어고객 활동현황"
# 코어고객 전용 리포트는 총괄DB 엔진(매칭·집계)이 필요 없다 -- 잠금/테마/코어 섹션까지만 싣는다
CORE_SCRIPT_TEMPLATE = APP_SCRIPT_TEMPLATE.split('// ===== Client-side matching + dashboard engine =====')[0]


def generate_core_report(core_df, core_voc_df=None, password=None, admin_password=None, expiry_date=None,
                         encrypt=True, kakao_key=None, kakao_js_key=None, log=print):
    """9. 코어고객 활동관리(+ 9-1 VOC매칭)만으로 만드는 독립 리포트 -- 총괄DB 없이.

    Same lock/encryption as generate_html_report: a blank password becomes a
    strong random one, expiry defaults to the end of this month. There is no
    admin panel here; admin_password (this PC's saved one by default) is just
    a second password that also opens the report, so the owner can always
    open what they shared.

    Returns (html, password, expiry_date, admin_password, customer_count).
    Raises ValueError when the sheet has no 관리고객 명 / 계약번호 column.
    """
    from .core_customers import build_core_payload, load_kakao_key
    payload = build_core_payload(core_df, core_voc_df, kakao_key=kakao_key or load_kakao_key(), log=log)
    if not payload:
        raise ValueError("코어고객 파일에서 '관리고객 명' 또는 '계약번호' 열을 찾을 수 없습니다 (시트·헤더 행을 확인하세요).")

    if encrypt:
        from .secure_report import generate_strong_password, load_admin_password
        if password is None:
            password = generate_strong_password()
        if admin_password is None:
            admin_password = load_admin_password()
    if password is None:
        import random
        password = str(random.randint(1000, 9999))
    if admin_password is None:
        admin_password = password
    if expiry_date is None:
        expiry_date = get_end_of_month_iso()

    core_json = ('<script type="application/json" id="coreData">'
                 + json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c') + '</script>')
    script = (CORE_SCRIPT_TEMPLATE.replace('__PASSWORD__', password)
              .replace('__ADMIN_PASSWORD__', admin_password).replace('__EXPIRY__', expiry_date))
    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M')
    count = len(payload['rows'])

    html_out = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{CORE_REPORT_TITLE}</title>
<style>{CSS}</style>
</head>
<body class="core-only">

<div id="lockScreen" class="lock-screen">
    <h2>{CORE_REPORT_TITLE} 보안 리포트</h2>
    <p>만료일: {_e(expiry_date)}</p>
    <input type="password" id="pwd" placeholder="비밀번호 입력" autocomplete="off">
    <button onclick="checkPassword()">확인</button>
    <div id="errorMsg" class="error"></div>
</div>

<div id="content">
    <div class="topbar">
        <div>
            <h1>{CORE_REPORT_TITLE}</h1>
            <div class="meta" id="reportMeta">생성일시 {_e(generated_at)} · 코어고객 {count:,}곳 · 만료일 {_e(expiry_date)}</div>
        </div>
        <button class="theme-toggle" onclick="toggleTheme()">🌓 테마 전환</button>
    </div>
    <div class="container">
        <div id="secCore">
        <div id="coreSectionWrap"></div>
        {core_json}
        </div>
    </div>
</div>

{render_map_config(kakao_js_key)}
<script>{script}</script>
</body>
</html>"""

    if encrypt:
        from .secure_report import encrypt_report
        html_out = encrypt_report(html_out, [password, admin_password], expiry_date=expiry_date,
                                  title=f"{CORE_REPORT_TITLE} 보안 리포트")
    return html_out, password, expiry_date, admin_password, count
