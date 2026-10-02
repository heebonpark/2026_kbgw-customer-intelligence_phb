"""
Web version of the report generator -- a single static page (GitHub Pages)
where the source files are picked in the browser instead of the desktop GUI.

Nothing is uploaded anywhere: SheetJS reads the Excel/CSV files inside the
browser, the page turns them into the same {columns, rows} tables the Python
report embeds, and hands them to the report's own client-side engine
(window.DataIntelLoad in report.py's APP_SCRIPT_TEMPLATE) -- the same merge
and the same dashboard code the GUI's report runs, so both show identical
numbers for identical files.

The page itself carries no customer data, so it needs no password; whoever
opens it only ever sees the files they pick themselves.

Files come in three ways: 📂 pick (Chrome/Edge keep the file handle in
IndexedDB, so 🔄 re-reads the latest saved version -- e.g. a workbook left open
in Excel -- without picking again, even after a reload), or 📋 paste what was
copied in Excel (Ctrl+A, Ctrl+C), which includes unsaved edits.

Each picked file gets load settings: sheet (pre-selected by SHEET_HINT_COLUMNS),
header row (auto-detected, or typed), and for 1번/8번 a column mapping
(FIELD_SPECS -- matched by name, or chosen by column letter such as F열),
with a 3-row preview. The settings are remembered per slot in the browser.

After the dashboard is built, the page can also produce the shareable report:
it copies its own source, embeds the parsed data, turns the report's lock
screen back on, and encrypts the whole thing in the browser (WebCrypto) into
the exact unlock-page format secure_report.py writes -- then either downloads
it or publishes it to GitHub Pages through the GitHub API with a token the
user types in (never stored unless they tick "remember").

Not covered here (desktop GUI only): 4. 해지 파이프라인 and 7. 해지시설
내역 -- their sections are rendered server-side in Python only.
"""

from datetime import datetime, timedelta, timezone
import json
import random
import string

from .handlers import HQ_ORDER, BRANCH_ORDER
from .matching_config import (
    MATCHABLE_FILES, FILE_LABELS, DB_KEY_CANDIDATES, FILE_KEY_CANDIDATES,
    FILE_DISPLAY_COLUMNS, default_config,
)
from .report import (
    CSS, APP_SCRIPT_TEMPLATE, render_admin_panel_shell, render_dashboard_nav, render_dashboard_sections,
)
from .secure_report import UNLOCK_PAGE_TEMPLATE, PBKDF2_ITERATIONS

SHEETJS_URL = "https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"

# A sheet whose first rows contain one of these columns is picked by default
# when a workbook has several sheets.
SHEET_HINT_COLUMNS = {
    'db': ['활동대상구분', '담당채널'],
    'voc': ['VOC유형대', '계약번호'],
    'patrol': ['고객번호'],
    'original': ['계약번호'],
    'facility': ['계약번호'],
    'zone_owner': ['구역번호'],
    'core': ['관리주체', '관리고객 명'],
    'core_voc': ['VOC유형', 'VOC유형대'],
}

# The report lock screen (same markup as report.py), switched back on in the
# exported copy. __EXPIRY_TEXT__ is filled in the browser.
LOCK_SCREEN_HTML = """<div id="lockScreen" class="lock-screen">
    <h2>Data Intel PRO 보안 리포트</h2>
    <p>만료일: __EXPIRY_TEXT__</p>
    <input type="password" id="pwd" placeholder="비밀번호 입력" autocomplete="off">
    <button onclick="checkPassword()">확인</button>
    <div id="errorMsg" class="error"></div>
</div>"""

# Columns the engine reads by exact name. When a file's header uses another
# name, or the user points at a column by letter, the loader renames that
# column to `name` before handing the table to the engine.
FIELD_SPECS = {
    'db': [
        {'name': '활동대상구분', 'aliases': ['활동대상구분', '담당채널'], 'required': True},
        {'name': '지사', 'aliases': ['지사', '관리지사명', '관리지사'], 'required': True},
        {'name': '계약번호', 'aliases': ['계약번호']},
        {'name': '서비스번호', 'aliases': ['서비스번호']},
        {'name': '상호', 'aliases': ['상호', '상호명', '고객명']},
        {'name': '설치주소', 'aliases': ['설치주소', '주소']},
        # zone: 값이 구역 코드(G230001 / T001 / Z01) 형태인 열만 자동 선택 -- 재계약대상 구분 같은 글자 열 제외
        {'name': '영업구역정보', 'aliases': ['영업구역정보', '영업구역번호', '영업구역'], 'zone': True, 'fuzzy': ['영업구역']},
        {'name': '기술구역정보', 'aliases': ['기술구역정보', '기술구역번호', '기술구역'], 'zone': True, 'fuzzy': ['기술구역']},
        {'name': '구역정보', 'aliases': ['구역정보', '구역'], 'zone': True, 'fuzzy': ['출동구역', '구역정보'], 'fuzzyExclude': ['영업', '기술']},
        {'name': '활동유무', 'aliases': ['활동유무', '활동유무(o,x)'], 'prefix': '활동유무', 'required': True},
        {'name': 'SP담당', 'aliases': ['SP담당', 'SP_담당', 'SP 담당', 'SP담당자', 'SP_담당자', 'SP 담당자']},
    ],
    'zone_owner': [
        {'name': '구역번호', 'aliases': ['구역번호', '영업구역번호'], 'required': True},
        {'name': '담당자명', 'aliases': ['담당자명', '담당자', '사원명', '(신규)사원명'], 'required': True},
    ],
}

# (key, label, note) -- same numbering as the desktop GUI
WEB_UPLOAD_SLOTS = [
    ('db', '1. 총괄관리DB', '필수 · 코어고객 현황만 만들 때는 생략'),
    ('voc', '2. 월/일일 SP관리활동 (VOC)', '선택'),
    ('patrol', '3. 월/일일 SE,SG 정기점검', '선택'),
    ('original', '5. 2026년 관리고객원본', '선택'),
    ('facility', '6. 시설현황', '선택'),
    ('zone_owner', '8. 영업구역담당자', '선택 · SP 구역번호→담당자명'),
    ('core', '9. 코어고객 활동관리', '선택 · 이 파일만 올려도 코어고객 현황 생성'),
    ('core_voc', '9-1. 코어고객 VOC매칭', '선택 · 9번과 계약번호로 연결'),
]


def _upload_slots_html():
    cards = []
    for key, label, note in WEB_UPLOAD_SLOTS:
        required = ' web-slot-required' if key == 'db' else ''
        cards.append(f"""
        <div class="web-slot{required}" data-key="{key}">
            <span class="web-slot-title">{label} <span class="web-slot-note">({note})</span></span>
            <div class="web-slot-actions">
                <button type="button" class="web-mini web-pick" data-pick-for="{key}">📂 파일 선택</button>
                <button type="button" class="web-mini web-reload" data-reload-for="{key}" hidden>🔄 다시 불러오기</button>
                <button type="button" class="web-mini" data-paste-for="{key}">📋 엑셀에서 붙여넣기</button>
            </div>
            <input type="file" accept=".xlsx,.xls,.csv" data-key="{key}" hidden>
            <textarea class="web-paste" data-paste-area="{key}" hidden
                placeholder="엑셀에서 시트를 전체 선택(Ctrl+A) → 복사(Ctrl+C) 후, 여기를 누르고 붙여넣기(Ctrl+V) -- 저장 안 한 내용도 그대로 들어옵니다"></textarea>
            <div class="web-map" data-map-for="{key}" hidden></div>
            <span class="web-slot-status" data-status-for="{key}">파일을 선택하세요</span>
        </div>""")
    return "".join(cards)


def generate_web_app_html(built_at=None):
    """built_at: 이 페이지를 만든 시각(기본: 지금). 화면에 '버전'으로 보이고, 열려 있는 화면이 최신
    배포본인지 비교하는 값(data-build)이 된다."""
    built_at = built_at or datetime.now(timezone(timedelta(hours=9)))  # 한국 시간으로 표시
    embedded = {
        "db": {"columns": [], "rows": []},
        "files": {k: {"columns": [], "rows": []} for k in MATCHABLE_FILES},
        "matchingConfig": default_config(),
        "fileLabels": FILE_LABELS,
        "dbKeyCandidates": DB_KEY_CANDIDATES,
        "fileKeyCandidates": FILE_KEY_CANDIDATES,
        "displayColumns": FILE_DISPLAY_COLUMNS,
        "hqOrder": HQ_ORDER,
        "branchOrder": BRANCH_ORDER,
        "zoneOwnerMap": {},
    }
    upload_config = {
        "matchable": MATCHABLE_FILES,
        "keyCandidates": FILE_KEY_CANDIDATES,
        "displayColumns": FILE_DISPLAY_COLUMNS,
        "sheetHints": SHEET_HINT_COLUMNS,
        "fieldSpecs": FIELD_SPECS,
        "dbKeyCandidates": DB_KEY_CANDIDATES,
        "unlockTemplate": UNLOCK_PAGE_TEMPLATE,
        "lockScreenHtml": LOCK_SCREEN_HTML,
        "iterations": PBKDF2_ITERATIONS,
        # 배포 저장소 첫 화면에 두는 빈 페이지 (deploy_report.py PLACEHOLDER_PAGE와 같은 내용)
        "placeholderPage": ('<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">'
                            '<meta name="robots" content="noindex, nofollow"><title>Not Found</title></head>'
                            '<body></body></html>'),
    }
    # The report script's lock screen is never shown here (no data is embedded),
    # so its password constants are just unguessable filler.
    filler = ''.join(random.choice(string.ascii_letters) for _ in range(24))
    upload_config["filler"] = filler  # the export swaps these back to the real passwords
    app_script = (APP_SCRIPT_TEMPLATE.replace('__PASSWORD__', filler)
                  .replace('__ADMIN_PASSWORD__', filler).replace('__EXPIRY__', '9999-12-31'))
    # '<' -> \u003c: no tag-like text (the unlock page template has <script>) can
    # ever reach the HTML parser from inside these JSON blocks
    embedded_json = json.dumps(embedded, ensure_ascii=False).replace('<', '\\u003c')
    upload_json = json.dumps(upload_config, ensure_ascii=False).replace('<', '\\u003c')

    return (WEB_PAGE_TEMPLATE
            .replace('__CSS__', CSS)
            .replace('__SLOTS__', _upload_slots_html())
            .replace('__DASH_NAV__', render_dashboard_nav(hidden=True))
            .replace('__DASH_SECTIONS__', render_dashboard_sections({
                "filter_bar": '<div id="globalFilterBarWrap"></div>',
                "admin_panel": render_admin_panel_shell(),
            }))
            .replace('__EMBEDDED__', embedded_json)
            .replace('__UPLOAD_CONFIG__', upload_json)
            .replace('__SHEETJS__', SHEETJS_URL)
            .replace('__BUILD_ID__', built_at.strftime('%Y%m%d%H%M%S'))
            .replace('__BUILD_LABEL__', built_at.strftime('%Y-%m-%d %H:%M'))
            .replace('__BUILT_AT__', built_at.isoformat(timespec='seconds'))
            .replace('__APP_SCRIPT__', app_script))


WEB_PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex, nofollow">
<title>Data Intel PRO 웹 대시보드</title>
<style>__CSS__</style>
<style id="webVisibleStyle">#content { display: block; }</style>
<style>
.web-upload { background: var(--surface-1); border: 1px solid var(--border); border-radius: 14px; padding: 20px; margin-bottom: 24px; }
.web-upload h2 { font-size: 17px; margin: 0 0 4px; color: var(--text-primary); }
.web-upload[open] > .web-upload-sum { margin-bottom: 10px; }
.web-upload-sum { display: flex; align-items: center; gap: 12px; cursor: pointer; list-style: none; flex-wrap: wrap; }
.web-upload-sum::-webkit-details-marker { display: none; }
.web-upload-title { font-size: 16px; font-weight: 700; color: var(--text-primary); }
.web-upload-files { font-size: 12.5px; color: var(--text-secondary); flex: 1; min-width: 0; }
.web-upload-toggle { font-size: 12px; font-weight: 600; color: var(--brand); border: 1px solid color-mix(in srgb, var(--brand) 40%, var(--border)); border-radius: 6px; padding: 3px 9px; }
.web-upload[open] > .web-upload-sum .web-upload-toggle { display: none; }
.web-upload:not([open]) { padding: 12px 20px; margin-bottom: 12px; }
.web-share[hidden] { display: none; }
.web-quickbar { display: flex; justify-content: flex-end; margin: -4px 0 12px; }
.web-quickbar[hidden] { display: none; }
.web-upload .web-privacy { font-size: 12.5px; color: var(--text-secondary); margin: 0 0 16px; }
.web-slots { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 300px), 1fr)); gap: 10px; }
.web-slot { display: flex; flex-direction: column; gap: 6px; border: 1px dashed var(--baseline); border-radius: 10px; padding: 12px 14px; background: var(--page-plane); }
.web-slot:hover { border-color: var(--brand); }
.web-slot-required { border-style: solid; border-color: color-mix(in srgb, var(--brand) 55%, var(--border)); }
.web-slot-title { font-size: 13px; font-weight: 700; color: var(--text-primary); }
.web-slot-note { font-weight: 400; color: var(--text-muted); font-size: 11.5px; }
.web-slot input[type=file] { font-size: 12px; color: var(--text-secondary); max-width: 100%; }
.web-slot-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.web-reload { border-color: color-mix(in srgb, var(--brand) 55%, var(--border)); font-weight: 700; }
.web-paste { width: 100%; min-height: 64px; font-size: 12px; padding: 8px 10px; border: 1px dashed var(--brand); border-radius: 8px; background: var(--surface-1); color: var(--text-primary); resize: vertical; }
.web-slot-status { font-size: 11.5px; color: var(--text-muted); }
.web-sheet, .web-map select, .web-map input { font-size: 12.5px; padding: 5px 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-1); color: var(--text-primary); max-width: 100%; }
.web-slot.web-slot-wide { grid-column: 1 / -1; }
.web-map { border-top: 1px dashed var(--baseline); padding-top: 8px; display: flex; flex-direction: column; gap: 8px; }
.web-map[hidden], .web-paste[hidden] { display: none; }
.web-map-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; font-size: 12.5px; color: var(--text-secondary); }
.web-map-row label { display: flex; align-items: center; gap: 6px; }
.web-map-row input[type=number] { width: 64px; }
.web-map-fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 6px 12px; }
.web-map-field { display: grid; grid-template-columns: 96px minmax(0, 1fr); align-items: center; gap: 6px; font-size: 12px; color: var(--text-secondary); }
.web-map-field.req > span::after { content: ' *'; color: var(--critical); }
.web-map-field select.missing { border-color: var(--critical); color: var(--critical); }
.web-map-preview { overflow-x: auto; }
.web-map-preview table { border-collapse: collapse; font-size: 11.5px; }
.web-map-preview th, .web-map-preview td { border: 1px solid var(--grid-line); padding: 3px 7px; white-space: nowrap; max-width: 180px; overflow: hidden; text-overflow: ellipsis; text-align: left; }
.web-map-preview th { background: var(--page-plane); color: var(--text-secondary); font-weight: 600; }
.web-map-warn { font-size: 12px; color: var(--critical); font-weight: 600; }
.web-mini { font-size: 11.5px; padding: 4px 9px; border-radius: 6px; border: 1px solid var(--border); background: var(--surface-1); color: var(--text-primary); cursor: pointer; }
.web-slot-status.ok { color: var(--good); font-weight: 600; }
.web-slot-status.err { color: var(--critical); font-weight: 600; }
.web-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-top: 16px; }
.web-run { font-size: 15px; font-weight: 700; padding: 11px 22px; border: 0; border-radius: 10px; background: var(--brand); color: #fff; cursor: pointer; }
.web-run:disabled { opacity: .5; cursor: not-allowed; }
.web-progress { font-size: 13px; color: var(--text-secondary); }
.web-footnote { font-size: 11.5px; color: var(--text-muted); margin: 12px 0 0; }
.web-version { display: flex; flex-wrap: wrap; align-items: center; gap: 2px 8px; font-size: 11.5px; color: var(--text-muted); margin-top: 3px; font-variant-numeric: tabular-nums; }
.web-version-status.ok { color: var(--text-secondary); font-weight: 600; }
.web-version-status.old { color: var(--text-primary); font-weight: 700; background: color-mix(in srgb, var(--warning) 30%, transparent); padding: 2px 10px; border-radius: 999px; }
.web-version button { font: inherit; font-weight: 700; border: 1px solid var(--brand); background: var(--brand); color: #fff; border-radius: 999px; padding: 2px 10px; cursor: pointer; }
.web-share-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 240px), 1fr)); gap: 10px 14px; }
.web-field { display: flex; flex-direction: column; gap: 5px; font-size: 12.5px; font-weight: 600; color: var(--text-secondary); }
.web-field input { font-size: 14px; padding: 9px 11px; border: 1px solid var(--border); border-radius: 8px; background: var(--page-plane); color: var(--text-primary); }
.web-field small { font-weight: 400; color: var(--text-muted); font-size: 11.5px; }
.web-btn2 { font-size: 14px; font-weight: 700; padding: 10px 18px; border-radius: 10px; border: 1px solid var(--border); background: var(--page-plane); color: var(--text-primary); cursor: pointer; }
.web-btn2:disabled { opacity: .5; cursor: progress; }
.web-deploy { margin-top: 14px; border-top: 1px solid var(--border); padding-top: 12px; }
.web-deploy summary { cursor: pointer; font-size: 13px; font-weight: 700; color: var(--text-primary); }
.web-result { margin-top: 14px; }
.web-result pre { white-space: pre-wrap; background: var(--page-plane); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; font-size: 13px; margin: 6px 0; }
.web-remember { flex-direction: row; align-items: center; gap: 6px; font-weight: 400; }
#webDashboard[hidden] { display: none; }
</style>
</head>
<body>

<div class="fab-container">
    <button id="btnTheme" class="fab-btn" title="다크 모드 변경">🌙</button>
    <button id="btnTop" class="fab-btn" title="맨 위로 가기">⬆️</button>
</div>

<div id="content">
    <div class="topbar">
        <div>
            <h1>Data Intel PRO 관리고객 대시보드 <span style="font-weight:400;font-size:0.7em;color:var(--text-muted)">웹</span></h1>
            <div class="meta" id="reportMeta">파일을 선택하면 이 브라우저 안에서 바로 대시보드를 만듭니다.</div>
            <div class="web-version" id="webVersion" data-build="__BUILD_ID__" data-built-at="__BUILT_AT__">
                <span>버전 __BUILD_LABEL__</span><span id="webVersionCommit"></span><span id="webVersionStatus" class="web-version-status"></span>
            </div>
        </div>
        <button class="theme-toggle" onclick="toggleTheme()">🌓 테마 전환</button>
    </div>
    __DASH_NAV__
    <div class="container">
        <details class="web-upload" id="webUpload" open>
            <summary class="web-upload-sum"><span class="web-upload-title">📂 원본 파일</span>
                <span class="web-upload-files" id="webUploadSummary">파일을 선택하세요</span>
                <span class="web-upload-toggle">파일·설정 변경</span></summary>
            <p class="web-privacy">🔒 파일은 서버로 전송되지 않습니다 -- 이 브라우저 안에서만 읽고 계산하며, 창을 닫으면 사라집니다.</p>
            <div class="web-slots">__SLOTS__</div>
            <div class="web-actions">
                <button type="button" class="web-run" id="webRunBtn" disabled>📊 대시보드 만들기</button>
                <button type="button" class="web-btn2" id="webReloadAll" hidden title="엑셀에서 저장(Ctrl+S)한 최신 내용으로 모든 파일을 다시 읽고 대시보드를 새로 만듭니다">🔄 모두 다시 불러오고 대시보드 갱신</button>
                <span class="web-progress" id="webProgress">1. 총괄관리DB(종합 대시보드) 또는 9. 코어고객(코어고객 현황만)을 선택하세요.</span>
            </div>
            <p class="web-footnote">엑셀에서 열어 둔 파일도 선택할 수 있습니다 -- 단, <b>마지막으로 저장된 내용</b>을 읽으므로 수정 중이면 먼저 저장하세요. 한 번 고른 파일은 <b>🔄 다시 불러오기</b>로 다시 고르지 않고 최신 저장본을 읽습니다 (Chrome·Edge). 저장 안 한 내용까지 쓰려면 <b>📋 엑셀에서 붙여넣기</b>를 쓰세요. 파일을 고르면 <b>시트 · 헤더 행 · 컬럼(열 위치)</b>을 자동으로 맞추고 미리보기를 보여줍니다 -- 다르면 드롭다운에서 바꾸세요. 설정은 이 브라우저에 기억되어 다음에 같은 양식이면 자동 적용됩니다.</p>
            <p class="web-footnote"><b>9. 코어고객</b>(+9-1)만 올리면 총괄DB 없이 <b>코어고객 현황</b>만 만들고 공유할 수 있습니다 (설치주소 지도는 데스크톱 GUI 리포트에서). 4. 해지파이프라인 · 7. 해지시설내역 섹션도 데스크톱 GUI 리포트에서 제공합니다.</p>
        </details>
        <div class="web-quickbar" id="webQuickbar" hidden>
            <button type="button" class="web-btn2" id="webQuickReload" hidden>🔄 모두 다시 불러오고 갱신</button>
        </div>

        <details class="web-upload web-share" id="webShare" hidden>
            <summary class="web-upload-sum"><span class="web-upload-title">🔒 공유용 리포트 만들기</span>
                <span class="web-upload-files">암호화 HTML 다운로드 · GitHub Pages 배포</span>
                <span class="web-upload-toggle">열기</span></summary>
            <p class="web-privacy">위 대시보드를 <b>암호화된 HTML 파일</b>로 만듭니다 (GUI 리포트와 같은 방식 -- 비밀번호 없이는 내용을 볼 수 없음). 암호화도 이 브라우저 안에서 합니다.</p>
            <div class="web-share-grid">
                <label class="web-field">사용자 비밀번호 <small>받는 사람이 입력 · 비우면 12자리 랜덤</small>
                    <input type="text" id="shareUserPwd" autocomplete="off" placeholder="예: Kbgw2026!oct"></label>
                <label class="web-field">관리자 비밀번호 <small>매칭설정 패널까지 열림 · 비우면 12자리 랜덤</small>
                    <input type="password" id="shareAdminPwd" autocomplete="new-password" placeholder="예: GUI와 같은 관리자 비밀번호"></label>
                <label class="web-field">만료일 <small>이 날짜가 지나면 열리지 않음</small>
                    <input type="date" id="shareExpiry"></label>
                <label class="web-field web-remember" id="shareCoreOnlyWrap" hidden><input type="checkbox" id="shareCoreOnly"> 코어고객 현황만 공유 <small>총괄DB 내용은 넣지 않음</small></label>
            </div>
            <div class="web-actions">
                <button type="button" class="web-run" id="shareDownloadBtn">📥 암호화 HTML 다운로드</button>
                <span class="web-progress" id="shareProgress"></span>
            </div>
            <details class="web-deploy">
                <summary>🌐 GitHub Pages에 바로 배포 (공유 링크)</summary>
                <p class="web-footnote">GitHub 토큰이 필요합니다 -- github.com → Settings → Developer settings → Personal access tokens → Tokens (classic) → <b>repo</b> 권한으로 생성. 토큰은 GitHub에만 전송되고, '기억'을 체크하지 않으면 저장되지 않습니다.</p>
                <div class="web-share-grid">
                    <label class="web-field">GitHub 토큰 <input type="password" id="ghToken" autocomplete="off" placeholder="ghp_..."></label>
                    <label class="web-field">배포 저장소 이름 <input type="text" id="ghRepo" value="kbgw-report"></label>
                    <label class="web-field web-remember"><input type="checkbox" id="ghRemember"> 이 브라우저에 토큰 기억</label>
                </div>
                <div class="web-actions">
                    <button type="button" class="web-btn2" id="shareDeployBtn">🌐 암호화해서 배포</button>
                </div>
            </details>
            <div class="web-result" id="shareResult" hidden>
                <div class="web-field">공유 문구 (받는 사람에게 보내세요)</div>
                <pre id="shareText"></pre>
                <button type="button" class="web-btn2" id="shareCopyBtn">📋 공유 문구 복사</button>
                <p class="web-footnote" id="shareAdminNote"></p>
            </div>
        </details>

        <div id="webDashboard" hidden>
__DASH_SECTIONS__
        </div>
    </div>
</div>

<script type="application/json" id="embeddedData">__EMBEDDED__</script>
<script type="application/json" id="uploadConfig">__UPLOAD_CONFIG__</script>
<script src="__SHEETJS__"></script>
<script>__APP_SCRIPT__</script>
<script>
// ===== 버전 표시: 지금 열려 있는 화면이 최신 배포본인지 알려 준다 =====
// 브라우저가 예전 화면을 저장해 두고 보여 주는 일이 잦다. (1) 같은 주소를 저장본 없이 다시 받아 빌드 값을 비교하고,
// (2) GitHub에 올라간 최근 커밋 날짜를 보여 준다 (github.io 주소일 때만).
(function () {
    const box = document.getElementById('webVersion');
    if (!box || getComputedStyle(box).display === 'none') return;  // 공유용으로 내보낸 리포트에서는 숨김
    const status = document.getElementById('webVersionStatus'), commitEl = document.getElementById('webVersionCommit');
    const say = (text, cls) => { status.textContent = text; status.className = 'web-version-status' + (cls ? ' ' + cls : ''); };
    const reloadButton = () => {
        if (box.querySelector('button')) return;
        const b = document.createElement('button');
        b.type = 'button'; b.textContent = '최신 버전으로 새로고침';
        b.addEventListener('click', () => location.replace(location.href.split('#')[0].split('?')[0] + '?v=' + Date.now()));
        box.appendChild(b);
    };
    const kst = d => d.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
    let lastCheck = 0;
    async function check() {
        lastCheck = Date.now();
        if (location.protocol === 'file:') { say('파일로 열림 (최신 여부는 확인하지 않음)'); return; }
        let current = null;
        try {
            const r = await fetch(location.href.split('#')[0].split('?')[0] + '?v=' + Date.now(), { cache: 'no-store' });
            const m = r.ok ? /id="webVersion" data-build="(\d+)"/.exec(await r.text()) : null;
            current = m ? m[1] : null;
        } catch (e) { /* 연결 없음 -- 아래에서 '확인 못 함' */ }
        if (current && current !== box.dataset.build) {
            say('새 버전이 올라와 있습니다', 'old');
            reloadButton();
            return;
        }
        say(current ? '✓ 최신 버전' : '최신 여부 확인 못 함 (연결 확인)', current ? 'ok' : '');
        const host = /^([^.]+)\.github\.io$/.exec(location.hostname), repo = location.pathname.split('/').filter(Boolean)[0];
        if (!host || !repo) return;
        try {
            const r = await fetch('https://api.github.com/repos/' + host[1] + '/' + repo + '/commits?path=docs/index.html&per_page=1');
            if (!r.ok) return;
            const latest = new Date((await r.json())[0].commit.committer.date);
            commitEl.textContent = '· 최근 커밋 ' + kst(latest);
            // 커밋은 올라갔는데 이 화면보다 한참 뒤의 것: GitHub가 아직 새 화면을 내보내는 중
            if (current && latest - new Date(box.dataset.builtAt) > 30 * 60 * 1000) say('새 버전을 배포하는 중입니다 -- 1~2분 뒤 새로고침', 'old');
        } catch (e) { /* 커밋 날짜는 참고용 -- 못 가져와도 된다 */ }
    }
    window.DataIntelVersionCheck = check;
    check();
    // 화면을 켜 둔 채 며칠 지나도 돌아왔을 때 다시 확인한다 (5분에 한 번까지)
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && Date.now() - lastCheck > 5 * 60 * 1000) check(); });
})();
</script>
<script>
// ===== 웹 업로드: 브라우저 안에서 파일을 읽어 리포트 엔진(window.DataIntelLoad)에 넘긴다 =====
(function () {
    const CFG = JSON.parse(document.getElementById('uploadConfig').textContent);
    const picked = {};   // key -> { file, wb (parsed workbook), sheet }
    let lastPayload = null;  // the tables behind the dashboard on screen -- what the export embeds
    const runBtn = document.getElementById('webRunBtn');
    const progress = document.getElementById('webProgress');
    const setStatus = (key, text, cls) => {
        const el = document.querySelector('[data-status-for="' + key + '"]');
        if (el) { el.textContent = text; el.className = 'web-slot-status' + (cls ? ' ' + cls : ''); }
    };

    // 총괄DB가 있으면 종합 대시보드, 없이 9. 코어고객만 올리면 코어고객 현황만 만든다
    const canRun = () => picked.db ? !missingRequired('db').length : !!picked.core;
    const refreshRun = (keepMessage) => {
        const dbMissing = picked.db ? missingRequired('db') : [];
        const coreOnly = !picked.db && !!picked.core;
        runBtn.disabled = !canRun();
        runBtn.textContent = coreOnly ? '💎 코어고객 현황 만들기' : '📊 대시보드 만들기';
        if (keepMessage) return;  // 실행 결과(완료/실패) 문구는 그대로 둔다
        progress.textContent = coreOnly ? '준비 완료 -- 총괄DB 없이 코어고객 현황만 만듭니다.'
            : !picked.db ? '1. 총괄관리DB(종합 대시보드) 또는 9. 코어고객(코어고객 현황만)을 선택하세요.'
            : dbMissing.length ? '총괄관리DB의 필수 컬럼을 지정하세요: ' + dbMissing.join(', ')
            : '준비 완료 -- 대시보드 만들기를 누르세요.';
    };
    const pickToken = {};  // key -> latest pick; a slower, older read must not overwrite a newer one
    const SAVE_KEY = key => 'dataintel-load-' + key;
    const loadSaved = key => { try { return JSON.parse(localStorage.getItem(SAVE_KEY(key)) || 'null'); } catch (e) { return null; } };

    // ---- 시트 / 헤더 행 / 컬럼(열 위치) 설정 ----
    const norm = v => (v === null || v === undefined) ? '' : String(v).replace(/ /g, ' ').trim();
    function sheetRange(ws) { return ws && ws['!ref'] ? XLSX.utils.decode_range(ws['!ref']) : null; }
    function headerCells(ws, headerRow) {
        // headerRow: 엑셀 행 번호(1부터). -> [{idx(시트 첫 열 기준), letter, name}]
        const r = sheetRange(ws);
        if (!r) return [];
        const out = [];
        for (let c = r.s.c; c <= r.e.c; c++) {
            const cell = ws[XLSX.utils.encode_cell({ r: headerRow - 1, c })];
            out.push({ idx: c - r.s.c, letter: XLSX.utils.encode_col(c), name: norm(cell ? cell.v : null) });
        }
        return out;
    }
    const aliasHit = (spec, name) => spec.aliases.includes(name) || !!(spec.prefix && name.startsWith(spec.prefix));
    function detectHeaderRow(ws, key) {
        // 앞쪽 15줄 중 필요한 컬럼명이 가장 많이 들어있는 줄이 헤더. 없으면 제목 줄 규칙(handlers.py _has_title_row).
        const r = sheetRange(ws);
        if (!r) return 1;
        const specs = (CFG.fieldSpecs[key] || []).concat((CFG.sheetHints[key] || []).map(n => ({ aliases: [n] })));
        let best = null, bestHits = 0;
        for (let row = r.s.r; row <= Math.min(r.s.r + 14, r.e.r); row++) {
            const names = headerCells(ws, row + 1).map(c => c.name).filter(Boolean);
            const hits = specs.filter(spec => names.some(n => aliasHit(spec, n))).length;
            if (hits > bestHits) { best = row + 1; bestHits = hits; }
        }
        if (best) return best;
        const first = headerCells(ws, r.s.r + 1).map(c => c.name);
        const blank = first.slice(1).filter(n => !n).length;
        return (first.length >= 3 && blank >= first.length - 2) ? r.s.r + 2 : r.s.r + 1;
    }
    // 구역 값 검사 (엔진 findZoneCol과 같은 규칙): 대부분 G230001 / T001 / Z01 같은 코드여야 구역 열
    const ZONE_CODE_RE = /^[A-Za-z]{0,4}\d{2,}[A-Za-z0-9-]*$/;
    function zoneSample(ws, headerRow, idx, limit) {
        const r = sheetRange(ws);
        const vals = [];
        if (!r) return vals;
        for (let rr = headerRow; rr <= r.e.r && vals.length < (limit || 300); rr++) {
            const cell = ws[XLSX.utils.encode_cell({ r: rr, c: r.s.c + idx })];
            if (!cell || cell.v === null || cell.v === undefined) continue;
            let v = String(cell.v).replace(/ /g, ' ').trim();
            if (v.endsWith('.0')) v = v.slice(0, -2);
            if (v) vals.push(v);
        }
        return vals;
    }
    function zoneLike(ws, headerRow, idx) {
        const vals = zoneSample(ws, headerRow, idx);
        if (!vals.length) return true;
        return vals.filter(v => ZONE_CODE_RE.test(v)).length / vals.length >= 0.6;
    }
    const normHeader = n => String(n).replace(/[\s_()\-·]/g, '');
    function autoMap(key, cells, saved, ws, headerRow) {
        // 헤더 이름으로만 찾는다 (예전처럼 'G열' 같은 위치로 기억하면, 양식이 바뀐 파일에서 엉뚱한 열이 잡힌다)
        const map = {};
        (CFG.fieldSpecs[key] || []).forEach(spec => {
            const sv = saved && saved.fields ? saved.fields[spec.name] : undefined;
            if (sv === '') { map[spec.name] = -1; return; }  // 사용자가 '(없음)'으로 둔 항목
            const ok = c => !spec.zone || !ws || zoneLike(ws, headerRow, c.idx);
            let hit = sv && sv.header ? cells.find(c => c.name === sv.header && ok(c)) : null;
            if (!hit) hit = spec.aliases.map(a => cells.find(c => c.name === a && ok(c))).find(Boolean);
            if (!hit && spec.prefix) hit = cells.find(c => c.name.startsWith(spec.prefix) && ok(c));
            if (!hit && spec.fuzzy) hit = cells.find(c => {
                const n = normHeader(c.name);
                return spec.fuzzy.some(f => n.includes(f)) && !(spec.fuzzyExclude || []).some(x => n.includes(x)) && ok(c);
            });
            map[spec.name] = hit ? hit.idx : -1;
        });
        return map;
    }
    function missingRequired(key) {
        const p = picked[key];
        if (!p) return [];
        return (CFG.fieldSpecs[key] || []).filter(f => f.required && !(p.map[f.name] >= 0)).map(f => f.name);
    }
    function saveSettings(key) {
        const p = picked[key];
        if (!p) return;
        const cells = headerCells(p.wb.Sheets[p.sheet], p.headerRow);
        const fields = {};
        Object.keys(p.map).forEach(n => {
            const c = cells.find(x => x.idx === p.map[n]);
            fields[n] = c ? { header: c.name, letter: c.letter } : '';
        });
        try { localStorage.setItem(SAVE_KEY(key), JSON.stringify({ sheet: p.sheet, headerRow: p.headerRow, fields })); } catch (e) {}
    }
    // 처리완료/접수/미접수가 실제로 든 열 (이름이 아니라 값으로 -- 엔진의 detectStatusCol과 같은 규칙)
    const STATUS_VALUES = ['처리완료', '접수', '미접수'];
    function statusHits(ws, headerRow, idx) {
        const r = sheetRange(ws);
        if (!r) return 0;
        let hits = 0;
        for (let rr = headerRow; rr <= Math.min(r.e.r, headerRow + 5000); rr++) {
            const cell = ws[XLSX.utils.encode_cell({ r: rr, c: r.s.c + idx })];
            if (cell && typeof cell.v === 'string' && STATUS_VALUES.includes(cell.v.replace(/\s+/g, ''))) hits++;
        }
        return hits;
    }
    function applySheet(key, sheet, headerRow, saved) {
        const p = picked[key];
        p.sheet = sheet;
        const ws = p.wb.Sheets[sheet];
        p.headerRow = headerRow || detectHeaderRow(ws, key);
        const cells = headerCells(ws, p.headerRow);
        p.map = autoMap(key, cells, saved, ws, p.headerRow);
        if (key === 'db') {
            // 활동유무: 이름으로 잡은 열에 상태값이 하나도 없을 때만, 상태값이 가장 많은 열로 (예: 상태 열).
            // 활동유무 열에 상태값이 있으면 그대로 둔다 -- 엔진 detectStatusCol과 같은 규칙.
            const cur = p.map['활동유무'];
            if (cur === undefined || cur < 0 || statusHits(ws, p.headerRow, cur) === 0) {
                let best = -1, bestHits = 0;
                cells.forEach(c => { const h = statusHits(ws, p.headerRow, c.idx); if (h > bestHits) { best = c.idx; bestHits = h; } });
                if (best >= 0) p.map['활동유무'] = best;
            }
        }
    }
    function renderMapBox(key) {
        const p = picked[key];
        const box = document.querySelector('[data-map-for="' + key + '"]');
        const slot = document.querySelector('.web-slot[data-key="' + key + '"]');
        box.innerHTML = '';
        if (!p) { box.hidden = true; slot.classList.remove('web-slot-wide'); return; }
        box.hidden = false;
        const specs = CFG.fieldSpecs[key] || [];
        slot.classList.toggle('web-slot-wide', specs.length > 0);
        const ws = p.wb.Sheets[p.sheet];
        const cells = headerCells(ws, p.headerRow);
        const mk = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };

        const row = mk('div', 'web-map-row');
        const sheetLabel = mk('label', null, '시트');
        const sheetSel = mk('select', 'web-sheet');
        sheetSel.dataset.sheetFor = key;
        p.wb.SheetNames.forEach(n => {  // 시트 전체 줄 수 (제목·헤더 줄 포함)
            const r = sheetRange(p.wb.Sheets[n]);
            sheetSel.appendChild(new Option(n + ' (' + (r ? r.e.r - r.s.r + 1 : 0).toLocaleString('ko-KR') + '줄)', n));
        });
        sheetSel.value = p.sheet;
        sheetSel.addEventListener('change', () => { applySheet(key, sheetSel.value, null, null); saveSettings(key); renderMapBox(key); refreshRun(); });
        sheetLabel.appendChild(sheetSel);
        row.appendChild(sheetLabel);

        const hrLabel = mk('label', null, '헤더 행');
        const hr = mk('input');
        hr.type = 'number'; hr.min = '1'; hr.value = p.headerRow; hr.dataset.headerFor = key;
        hr.addEventListener('change', () => {
            p.headerRow = Math.max(1, parseInt(hr.value, 10) || 1);
            p.map = autoMap(key, headerCells(ws, p.headerRow), null, ws, p.headerRow);
            saveSettings(key); renderMapBox(key); refreshRun();
        });
        hrLabel.appendChild(hr);
        row.appendChild(hrLabel);
        const auto = mk('button', 'web-mini', '자동 다시 맞추기');
        auto.type = 'button';
        auto.addEventListener('click', () => {
            try { localStorage.removeItem(SAVE_KEY(key)); } catch (e) {}
            applySheet(key, p.sheet, null, null); renderMapBox(key); refreshRun();
        });
        row.appendChild(auto);
        box.appendChild(row);

        if (specs.length) {
            const grid = mk('div', 'web-map-fields');
            specs.forEach(spec => {
                const f = mk('label', 'web-map-field' + (spec.required ? ' req' : ''));
                f.appendChild(mk('span', null, spec.name));
                const sel = mk('select');
                sel.dataset.fieldFor = key + ':' + spec.name;
                sel.appendChild(new Option('(없음)', '-1'));
                cells.forEach(c => sel.appendChild(new Option(c.letter + '열 · ' + (c.name || '(빈 헤더)'), String(c.idx))));
                sel.value = String(p.map[spec.name] >= 0 ? p.map[spec.name] : -1);
                if (spec.required && !(p.map[spec.name] >= 0)) sel.classList.add('missing');
                sel.addEventListener('change', () => { p.map[spec.name] = parseInt(sel.value, 10); saveSettings(key); renderMapBox(key); refreshRun(); });
                f.appendChild(sel);
                grid.appendChild(f);
            });
            box.appendChild(grid);
            const miss = missingRequired(key);
            if (miss.length) box.appendChild(mk('div', 'web-map-warn', '필수 컬럼을 찾지 못했습니다: ' + miss.join(', ') + ' -- 드롭다운에서 열을 지정하세요.'));
            specs.filter(sp => sp.zone && p.map[sp.name] >= 0 && !zoneLike(ws, p.headerRow, p.map[sp.name])).forEach(sp => {
                const c = cells.find(x => x.idx === p.map[sp.name]);
                const ex = zoneSample(ws, p.headerRow, p.map[sp.name], 3).join(', ');
                box.appendChild(mk('div', 'web-map-warn', sp.name + '(' + c.letter + '열 · ' + (c.name || '빈 헤더') + ')의 값이 구역번호 형태가 아닙니다 (예: ' + ex + ') -- 다른 열을 지정하세요.'));
            });
        }
        // 미리보기: 지정한 컬럼(매핑이 없는 파일은 앞 6개 열) 기준 데이터 앞 3행
        const shown = specs.length
            ? specs.filter(sp => p.map[sp.name] >= 0).map(sp => ({ label: sp.name, idx: p.map[sp.name] }))
            : cells.slice(0, 6).map(c => ({ label: c.letter + '열 ' + (c.name || ''), idx: c.idx }));
        const r = sheetRange(ws);
        if (shown.length && r) {
            const pv = mk('div', 'web-map-preview');
            const t = mk('table');
            const head = mk('tr');
            shown.forEach(x => head.appendChild(mk('th', null, x.label)));
            t.appendChild(head);
            let added = 0;
            for (let rr = p.headerRow; rr <= r.e.r && added < 3; rr++) {
                const vals = shown.map(x => { const cell = ws[XLSX.utils.encode_cell({ r: rr, c: r.s.c + x.idx })]; return cell ? (cell.w || norm(cell.v)) : ''; });
                if (vals.every(v => v === '')) continue;
                const tr = mk('tr');
                vals.forEach(v => tr.appendChild(mk('td', null, v)));
                t.appendChild(tr);
                added++;
            }
            pv.appendChild(t);
            box.appendChild(pv);
        }
    }

    // ---- 파일 받기: 📂 선택 / 🔄 다시 불러오기(기억한 파일의 최신 저장본) / 📋 엑셀에서 붙여넣기 ----
    const handles = {};  // key -> FileSystemFileHandle (Chrome/Edge). IndexedDB에 보관해 새로고침 후에도 유지.
    const canHandle = typeof window.showOpenFilePicker === 'function';
    function idb() {
        return new Promise((res, rej) => {
            const rq = indexedDB.open('dataintel-web', 1);
            rq.onupgradeneeded = () => rq.result.createObjectStore('handles');
            rq.onsuccess = () => res(rq.result);
            rq.onerror = () => rej(rq.error);
        });
    }
    async function idbSet(key, value) {
        try {
            const db = await idb();
            await new Promise((res, rej) => {
                const tx = db.transaction('handles', 'readwrite');
                if (value) tx.objectStore('handles').put(value, key); else tx.objectStore('handles').delete(key);
                tx.oncomplete = res; tx.onerror = () => rej(tx.error);
            });
        } catch (e) { /* 저장 못 해도 이번 창에서는 동작 */ }
    }
    async function idbAll() {
        try {
            const db = await idb();
            return await new Promise((res, rej) => {
                const out = {};
                const rq = db.transaction('handles').objectStore('handles').openCursor();
                rq.onsuccess = () => { const c = rq.result; if (c) { out[c.key] = c.value; c.continue(); } else res(out); };
                rq.onerror = () => rej(rq.error);
            });
        } catch (e) { return {}; }
    }
    function showReload(key) {
        const btn = document.querySelector('[data-reload-for="' + key + '"]');
        if (btn) {
            btn.hidden = !handles[key];
            if (handles[key]) btn.textContent = '🔄 다시 불러오기 (' + handles[key].name + ')';
        }
        document.getElementById('webReloadAll').hidden = !Object.keys(handles).length;
        const quick = document.getElementById('webQuickReload');
        if (quick) quick.hidden = !Object.keys(handles).length;
    }

    // 어느 경로로 들어오든 같은 처리: 시트 고르기 -> 헤더 행/컬럼 자동 맞춤 -> 설정 칸 표시
    async function takeWorkbook(key, name, getWb) {
        const token = pickToken[key] = {};
        delete picked[key];
        renderMapBox(key);
        setStatus(key, name + ' -- 여는 중...', 'ok');
        runBtn.disabled = true;
        await new Promise(r => setTimeout(r, 0));
        let ok = false;
        try {
            const wb = await getWb();
            if (pickToken[key] !== token) return false;
            const saved = loadSaved(key);
            const sheets = wb.SheetNames.map(n => ({ name: n, rows: sheetRowCount(wb.Sheets[n]) }));
            // 지난번 시트(같은 이름, 데이터 있음) -> 필요한 컬럼 + 데이터 있는 시트 -> 데이터 있는 첫 시트
            const hasHint = sh => sheetHasColumn(wb.Sheets[sh.name], CFG.sheetHints[key] || []);
            const savedSheet = saved ? sheets.find(sh => sh.name === saved.sheet && sh.rows > 0) : null;
            const hinted = sheets.find(sh => sh.rows > 0 && hasHint(sh)) || sheets.find(hasHint);
            const chosen = (savedSheet || hinted || sheets.find(sh => sh.rows > 0) || sheets[0]).name;
            picked[key] = { name, wb };
            // 컬럼 지정은 헤더 이름으로 기억하므로 붙여넣기(시트 이름이 다름)에도 적용, 헤더 행은 같은 시트일 때만
            applySheet(key, chosen, savedSheet ? saved.headerRow : null, saved);
            renderMapBox(key);
            setStatus(key, name + ' · 시트 ' + chosen + ' · 헤더 ' + picked[key].headerRow + '행'
                + (savedSheet ? ' (지난 설정 적용)' : sheets.length > 1 && hinted ? ' (자동 선택)' : ''), 'ok');
            ok = true;
        } catch (e) {
            console.error(e);
            if (pickToken[key] !== token) return false;
            setStatus(key, name + ' 을(를) 열 수 없습니다: ' + (e && e.message ? e.message : e), 'err');
        }
        refreshRun();
        return ok;
    }
    async function readHandle(key) {
        const h = handles[key];
        if (!h) return false;
        try {
            if (h.queryPermission && (await h.queryPermission({ mode: 'read' })) !== 'granted'
                && (await h.requestPermission({ mode: 'read' })) !== 'granted') {
                setStatus(key, '파일 읽기를 허용해야 다시 불러올 수 있습니다', 'err');
                return false;
            }
            const file = await h.getFile();  // 엑셀에서 마지막으로 저장된 내용
            return await takeWorkbook(key, file.name, () => readWorkbook(file));
        } catch (e) {
            console.error(e);
            setStatus(key, '다시 불러오지 못했습니다 (파일이 옮겨졌거나 삭제됨?) -- 📂 파일 선택으로 다시 고르세요', 'err');
            return false;
        }
    }

    document.querySelectorAll('[data-pick-for]').forEach(btn => btn.addEventListener('click', async () => {
        const key = btn.dataset.pickFor;
        const input = document.querySelector('.web-slot input[data-key="' + key + '"]');
        if (!canHandle) { input.click(); return; }  // Safari/Firefox: 일반 파일 선택
        let h;
        try {
            [h] = await window.showOpenFilePicker({ types: [{ description: '엑셀/CSV',
                accept: { 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'], 'application/vnd.ms-excel': ['.xls'], 'text/csv': ['.csv'] } }] });
        } catch (e) {
            if (!e || e.name !== 'AbortError') input.click();
            return;
        }
        handles[key] = h;
        idbSet(key, h);
        showReload(key);
        await readHandle(key);
    }));
    document.querySelectorAll('.web-slot input[type=file]').forEach(input => input.addEventListener('change', () => {
        const file = input.files && input.files[0];
        if (file) takeWorkbook(input.dataset.key, file.name, () => readWorkbook(file));
    }));
    document.querySelectorAll('[data-reload-for]').forEach(btn => btn.addEventListener('click', () => readHandle(btn.dataset.reloadFor)));
    document.getElementById('webQuickReload').addEventListener('click', () => document.getElementById('webReloadAll').click());
    document.getElementById('webReloadAll').addEventListener('click', async () => {
        const btn = document.getElementById('webReloadAll');
        btn.disabled = true;
        try {
            for (const key of Object.keys(handles)) await readHandle(key);
            if (canRun()) runBtn.click();
        } finally { btn.disabled = false; }
    });

    // 📋 엑셀에서 복사한 셀(탭 구분 텍스트) -> 시트 하나짜리 통합문서
    function parseTsv(text) {
        // 엑셀 복사 형식: 탭/줄바꿈 구분, 줄바꿈·탭·따옴표가 든 셀만 "..."로 감싸고 " 는 "" 로
        const rows = [];
        let row = [], cell = '', quoted = false;
        for (let i = 0; i < text.length; i++) {
            const ch = text[i];
            if (quoted) {
                if (ch === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else quoted = false; } else cell += ch;
                continue;
            }
            if (ch === '"' && cell === '') { quoted = true; continue; }
            if (ch === '\t') { row.push(cell); cell = ''; continue; }
            if (ch === '\n' || ch === '\r') {
                if (ch === '\r' && text[i + 1] === '\n') i++;
                row.push(cell); rows.push(row); row = []; cell = '';
                continue;
            }
            cell += ch;
        }
        if (cell !== '' || row.length) { row.push(cell); rows.push(row); }
        return rows;
    }
    function pastedValue(v) {
        // 화면에 보이는 값이 들어오므로 숫자만 숫자로 (앞자리 0이 있는 코드는 글자 그대로)
        if (v === '') return null;
        const t = v.trim();
        if (/^-?(0|[1-9]\d*)(\.\d+)?$/.test(t)) return Number(t);
        if (/^-?[1-9]\d{0,2}(,\d{3})+(\.\d+)?$/.test(t)) return Number(t.replace(/,/g, ''));
        return v;
    }
    document.querySelectorAll('[data-paste-for]').forEach(btn => btn.addEventListener('click', () => {
        const ta = document.querySelector('[data-paste-area="' + btn.dataset.pasteFor + '"]');
        ta.hidden = !ta.hidden;
        if (!ta.hidden) { ta.value = ''; ta.focus(); }
    }));
    document.querySelectorAll('[data-paste-area]').forEach(ta => ta.addEventListener('paste', (e) => {
        const text = e.clipboardData ? e.clipboardData.getData('text/plain') : '';
        e.preventDefault();
        const key = ta.dataset.pasteArea;
        ta.hidden = true;
        if (!text.trim()) { setStatus(key, '붙여넣은 내용이 없습니다 -- 엑셀에서 복사(Ctrl+C) 후 다시 시도하세요', 'err'); return; }
        takeWorkbook(key, '엑셀 붙여넣기', async () => {
            const ws = XLSX.utils.aoa_to_sheet(parseTsv(text).map(r => r.map(pastedValue)));
            return { SheetNames: ['붙여넣기'], Sheets: { '붙여넣기': ws } };
        });
    }));

    // 지난번에 고른 파일 기억 복원 -> 🔄 버튼이 바로 보인다
    if (canHandle) idbAll().then(all => Object.keys(all).forEach(k => { handles[k] = all[k]; showReload(k); }));

    // ---- 9. 코어고객 -> 리포트의 코어고객 섹션 rows (core_customers.py build_core_payload와 같은 필드) ----
    // 웹에서는 주소 -> 좌표 변환(카카오 키 필요)을 하지 않는다: 파일에 좌표 열이 있으면 그 좌표만 지도에 쓴다.
    const CORE_FIELDS = {
        계약번호: ['계약번호'], 관리고객명: ['관리고객 명', '관리고객명', '고객명', '상호'], 관리주체: ['관리주체'],
        본부: ['본부', '관리본부'], 지사: ['지사', '관리지사'], 설치주소: ['설치주소', '주소'], 영업구역: ['영업구역', '영업구역정보'],
        영업구역담당: ['영업구역담당'], 관리고객담당자: ['관리고객담당자'], 영업자: ['영업자'], 시설수: ['시설수'], 월정료: ['월정료'],
        재계약대상시설수: ['재계약대상시설수'], 재계약대상월정료: ['재계약대상월정료'], 계약종료일: ['계약종료일'],
        방문일자: ['방문일자'], '3Q방문일자': ['1회 방문일자'], '2회방문일자': ['2회 방문일자'], 방문대상: ['방문대상'], 방문자: ['방문자'],
        해지징후: ['해지징후'], 불만요구: ['불만사항/요구사항/추가영업기회'], 요약정리: ['요약정리'], 약정여부: ['약정여부'],
        해지건수: ['해지건수'], 해지월정료: ['해지월정료'],
        '3Q징후': ['해지징후 및 불만 여부'], '2회징후': ['해지징후 및 불만 여부.1'],
        '3Q불만': ['불만사항/요구사항/추가영업기회 (구체적으로 작성)'], '2회불만': ['불만사항/요구사항/추가영업기회 (구체적으로 작성).1'],
        약정시설수: ['약정시설수'], 약정월정료: ['약정월정료'], 해지일자: ['해지일자'], 수동재계약: ['수동재계약'],
        만기비중: ['만기도래 비중 금액', '만기도래 비중'], 업셀링: ['업셀링('], 업셀링금액: ['업셀링 금액'],
    };
    const CORE_EXACT = ['3Q징후', '2회징후', '3Q불만', '2회불만'];  // 비슷한 이름의 다른 열로 번지지 않게 정확히 같은 이름만
    const BRANCH_ORDER_WEB = ['중앙', '강북', '서대문', '고양', '의정부', '남양주', '강릉', '원주'];
    function coreCol(columns, names) {
        return columns.find(c => names.includes(c)) || columns.find(c => names.some(n => c.startsWith(n))) || null;
    }
    const cv = v => (v === null || v === undefined || (typeof v === 'string' && v.trim() === '')) ? null : (typeof v === 'string' ? v.trim() : v);
    function coreDate(v) {
        v = cv(v);
        if (v === null) return null;
        if (typeof v === 'number') return v > 20000 && v < 80000 ? new Date(Date.UTC(1899, 11, 30) + v * 86400000).toISOString().slice(0, 10) : String(v);
        const s = String(v);
        if (s.startsWith('9999')) return null;
        const m = s.match(/^(\d{4})[-./](\d{1,2})[-./](\d{1,2})/);
        if (m) return m[1] + '-' + m[2].padStart(2, '0') + '-' + m[3].padStart(2, '0');
        const f = Number(s);
        return !isNaN(f) && f > 20000 && f < 80000 ? new Date(Date.UTC(1899, 11, 30) + f * 86400000).toISOString().slice(0, 10) : s;
    }
    const coreNum = v => { v = cv(v); if (v === null) return null; const n = Number(String(v).replace(/,/g, '')); return isNaN(n) ? null : n; };
    const coreContract = v => { v = cv(v); if (v === null) return null; const s = String(v); return s.endsWith('.0') ? s.slice(0, -2) : s; };
    function buildCorePayload(core, voc) {
        if (!core || !core.rows.length) return null;
        const col = {};
        Object.keys(CORE_FIELDS).forEach(k => {
            col[k] = CORE_EXACT.includes(k) ? (core.columns.find(c => CORE_FIELDS[k].includes(c)) || null) : coreCol(core.columns, CORE_FIELDS[k]);
        });
        // '1회 방문일자_3Q 내' -> '3Q' (화면 문구용). 분기 방문 열이 없으면 방문일자 하나로만 본다.
        const qm = /(\d)\s*Q/.exec(col['3Q방문일자'] || '');
        const period = qm ? qm[1] + 'Q' : (col['3Q방문일자'] ? '분기' : null);
        if (!col.관리고객명 && !col.계약번호) return null;
        const coordCol = coreCol(core.columns, ['위치좌표(위도,경도)', '위치좌표', '좌표']);
        const latCol = coreCol(core.columns, ['위도', 'lat', 'LAT']), lngCol = coreCol(core.columns, ['경도', 'lng', 'LNG', 'lon']);
        const idx = c => core.columns.indexOf(c);
        const vocs = {};
        if (voc && voc.rows.length && voc.columns.includes('계약번호')) {
            const vi = c => voc.columns.indexOf(c);
            let last = null;
            voc.rows.forEach(r => {
                const get = c => vi(c) >= 0 ? cv(r[vi(c)]) : null;
                const contract = coreContract(get('계약번호')) || last;  // 병합셀: 비어 있으면 바로 위 고객
                last = contract;
                const kind = get('VOC유형') || get('VOC유형대');
                if (!kind && !get('상태')) return;
                (vocs[contract] = vocs[contract] || []).push({ 상태: get('상태'), 유형: kind, 처리내용: get('처리내용'), 접수일: coreDate(get('접수일시')), 처리자: get('처리자') });
            });
        }
        const rows = core.rows.map(r => {
            const g = k => col[k] ? cv(r[idx(col[k])]) : null;
            const visit3q = coreDate(g('3Q방문일자')), visit = coreDate(g('방문일자')), visit2 = coreDate(g('2회방문일자'));
            const yn = k => { const v = String(g(k) || '').trim().toUpperCase(); return v === 'Y' || v === 'N' ? v : null; };
            const note = k => { const v = g(k); return v === null ? null : String(v); };
            // 방문 회차별 기록 (이전 -> 분기 1회 -> 분기 2회)
            const history = [
                { 회차: period ? '이전' : '방문', 일자: visit, 징후: yn('해지징후'), 내용: note('불만요구'), 대상: g('방문대상'), 방문자: g('방문자') },
                { 회차: (period || '') + ' 1회', 일자: visit3q, 징후: yn('3Q징후'), 내용: note('3Q불만') },
                { 회차: (period || '') + ' 2회', 일자: visit2, 징후: yn('2회징후'), 내용: note('2회불만') },
            ].filter(h => h.일자 || h.내용 || h.징후);
            const signs = history.map(h => h.징후);
            const sign = signs.includes('Y') ? 'Y' : (signs.includes('N') ? 'N' : null);
            const notes = history.map(h => h.내용).filter(Boolean);
            const inPeriod = period ? (visit3q || visit2) : visit;
            const dates = [visit, visit3q, visit2].filter(Boolean).sort();
            const contract = coreContract(g('계약번호'));
            let lat = null, lng = null, src = null;
            if (coordCol) { const v = cv(r[idx(coordCol)]); if (typeof v === 'string' && v.includes(',')) { const [a, b] = v.split(',').map(Number); if (a && b) { lat = a; lng = b; src = '파일'; } } }
            if (lat === null && latCol && lngCol) { const a = coreNum(r[idx(latCol)]), b = coreNum(r[idx(lngCol)]); if (a && b) { lat = a; lng = b; src = '파일'; } }
            return {
                계약번호: contract, 관리고객명: g('관리고객명'), 관리주체: g('관리주체') || '미지정', 본부: g('본부'), 지사: g('지사') || '미지정',
                설치주소: g('설치주소'), 영업구역: g('영업구역'), 영업구역담당: g('영업구역담당'), 관리고객담당자: g('관리고객담당자'), 영업자: g('영업자'),
                시설수: coreNum(g('시설수')), 월정료: coreNum(g('월정료')), 재계약대상시설수: coreNum(g('재계약대상시설수')),
                재계약대상월정료: coreNum(g('재계약대상월정료')), 계약종료일: coreDate(g('계약종료일')), 방문일자: visit, '3Q방문일자': visit3q,
                '2회방문일자': visit2, 방문대상: g('방문대상'), 방문자: g('방문자'),
                해지징후: sign, 불만요구: notes.length ? notes[notes.length - 1] : null,  // 가장 최근 회차의 메모
                방문이력: history, 최근방문: dates.length ? dates[dates.length - 1] : null,
                요약정리: g('요약정리'), 약정여부: g('약정여부'), 약정시설수: coreNum(g('약정시설수')), 약정월정료: coreNum(g('약정월정료')),
                해지건수: coreNum(g('해지건수')), 해지월정료: coreNum(g('해지월정료')), 해지일자: coreDate(g('해지일자')),
                수동재계약: coreDate(g('수동재계약')), 만기비중: coreNum(g('만기비중')), 업셀링: note('업셀링'), 업셀링금액: coreNum(g('업셀링금액')),
                VOC: vocs[contract] || [],
                // 방문완료 = 이번 분기 안에 방문 (분기 열이 없는 파일은 방문일자 기준)
                활동상태: sign === 'Y' ? '해지징후' : (inPeriod ? '방문완료' : '미방문'), lat, lng, 좌표출처: src,
            };
        });
        const rank = b => { const i = BRANCH_ORDER_WEB.indexOf(b); return i < 0 ? BRANCH_ORDER_WEB.length : i; };
        rows.sort((a, b) => (rank(a.지사) - rank(b.지사)) || String(a.지사).localeCompare(String(b.지사)) || String(a.관리고객명 || '').localeCompare(String(b.관리고객명 || '')));
        const noCoord = rows.filter(r => r.lat === null).length;
        return { rows, coord_stats: { 파일: rows.length - noCoord, 카카오: 0, 없음: noCoord }, voc_matched: rows.filter(r => r.VOC.length).length, period,
                 kakao_key_set: null, kakao_error: null, coord_note: noCoord ? '웹 업로드는 주소→좌표 변환을 하지 않음 -- 지도는 데스크톱 GUI 리포트에서' : null };
    }

    // ---- file -> {columns, rows} (mirrors handlers.py load_data) ----
    function decodeCsv(buf) {
        try { return new TextDecoder('utf-8', { fatal: true }).decode(buf).replace(/^﻿/, ''); }
        catch (e) { return new TextDecoder('euc-kr').decode(buf); }  // 사내 CSV 내보내기는 대부분 cp949
    }
    function isoLocal(d) {
        const p = n => String(n).padStart(2, '0');
        return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
    }
    async function readWorkbook(file) {
        const buf = await file.arrayBuffer();
        return /\.csv$/i.test(file.name)
            ? XLSX.read(decodeCsv(buf), { type: 'string', cellDates: true })
            : XLSX.read(buf, { type: 'array', cellDates: true });
    }
    function sheetRowCount(ws) {
        if (!ws || !ws['!ref']) return 0;
        const r = XLSX.utils.decode_range(ws['!ref']);
        return Math.max(0, r.e.r - r.s.r);  // 헤더 줄 제외
    }
    function sheetHasColumn(ws, names) {
        // 앞쪽 15줄(제목 줄이 몇 줄 있어도)에 원하는 컬럼명이 있는지만 본다 -- 시트 전체를 변환하지 않음
        const r = sheetRange(ws);
        if (!r || !names.length) return false;
        for (let row = r.s.r; row <= Math.min(r.s.r + 14, r.e.r); row++) {
            for (let col = r.s.c; col <= r.e.c; col++) {
                const cell = ws[XLSX.utils.encode_cell({ r: row, c: col })];
                if (cell && cell.v != null && names.includes(norm(cell.v))) return true;
            }
        }
        return false;
    }
    function readTable(p) {
        // p: { wb, sheet, headerRow(엑셀 행 번호), map: {표준컬럼명: 열 인덱스} }
        const ws = p.wb.Sheets[p.sheet];
        const r = sheetRange(ws);
        if (!r) return { columns: [], rows: [] };
        const rng = { s: { r: p.headerRow - 1, c: r.s.c }, e: r.e };
        const aoa = XLSX.utils.sheet_to_json(ws, { header: 1, raw: true, defval: null, blankrows: false, range: rng });
        if (!aoa.length) return { columns: [], rows: [] };
        const width = Math.max(r.e.c - r.s.c + 1, aoa.reduce((m, row) => Math.max(m, row.length), 0));
        const seen = {};
        const columns = Array.from({ length: width }, (_, i) => {
            let name = norm(aoa[0][i]) || 'Unnamed: ' + i;
            if (seen[name] !== undefined) { seen[name] += 1; name = name + '.' + seen[name]; } else { seen[name] = 0; }
            return name;
        });
        // 지정한 열을 엔진이 읽는 표준 이름으로 바꾼다 (같은 이름의 다른 열은 '(원본)'으로 비켜둔다)
        const renamed = {};  // 표준 이름 -> 파일의 원래 헤더 (화면 안내용)
        Object.keys(p.map || {}).forEach(std => {
            const idx = p.map[std];
            if (!(idx >= 0) || idx >= width || columns[idx] === std) return;
            const clash = columns.indexOf(std);
            if (clash >= 0) columns[clash] = std + '(원본)';
            renamed[std] = columns[idx];
            columns[idx] = std;
        });
        const rows = aoa.slice(1).map(row => columns.map((_, i) => {
            const v = row[i];
            if (v === undefined || v === null) return null;
            if (v instanceof Date) return isoLocal(v);
            if (typeof v === 'string') { const t = v.replace(/ /g, ' '); return t.trim() === '' ? null : t; }
            return v;
        }));
        return { columns, rows, renamed };
    }
    function pickColumns(table, wanted) {
        const idx = wanted.map(c => table.columns.indexOf(c)).filter(i => i >= 0);
        return { columns: idx.map(i => table.columns[i]), rows: table.rows.map(r => idx.map(i => r[i])) };
    }
    function zoneKey(v) {
        if (v === null || v === undefined) return null;
        let s = String(v).replace(/ /g, ' ').trim();
        if (s.endsWith('.0')) s = s.slice(0, -2);
        return s || null;
    }
    function buildZoneOwnerMap(table) {
        // handlers.py build_zone_owner_map: 구역번호 -> 담당자명 (여러 명은 '/', 빈 이름은 '담당자없음')
        const zi = table.columns.indexOf('구역번호'), ni = table.columns.indexOf('담당자명');
        if (zi < 0 || ni < 0) return null;
        const owners = new Map();
        table.rows.forEach(r => {
            const key = zoneKey(r[zi]);
            if (!key) return;
            const name = r[ni] === null || r[ni] === undefined ? '' : String(r[ni]).trim();
            if (!owners.has(key)) owners.set(key, []);
            if (name && !owners.get(key).includes(name)) owners.get(key).push(name);
        });
        const out = {};
        owners.forEach((names, k) => { out[k] = names.join('/') || '담당자없음'; });
        return out;
    }

    runBtn.addEventListener('click', async () => {
        if (!canRun()) return;
        if (typeof XLSX === 'undefined') { progress.textContent = '엑셀 읽기 모듈을 불러오지 못했습니다. 인터넷 연결을 확인 후 새로고침하세요.'; return; }
        runBtn.disabled = true;
        const t0 = performance.now();
        try {
            const payload = { db: null, files: {}, zoneOwnerMap: {} };
            const loadedNames = [];
            const coreTables = {};
            for (const key of Object.keys(picked)) {
                const p = picked[key];
                progress.textContent = p.name + ' 읽는 중...';
                await new Promise(r => setTimeout(r, 0));  // let the status paint before a long parse
                const table = readTable(p);
                saveSettings(key);
                if (key === 'db') {
                    payload.db = table;
                } else if (key === 'zone_owner') {
                    const map = buildZoneOwnerMap(table);
                    if (!map) { setStatus(key, '시트 "' + p.sheet + '"에 구역번호/담당자명 컬럼이 없습니다', 'err'); continue; }
                    payload.zoneOwnerMap = map;
                } else if (key === 'core' || key === 'core_voc') {
                    coreTables[key] = table;
                } else if (CFG.matchable.includes(key)) {
                    const wanted = Array.from(new Set(CFG.keyCandidates[key].concat(CFG.displayColumns[key])));
                    payload.files[key] = pickColumns(table, wanted);
                }
                loadedNames.push(p.name + ' (' + table.rows.length.toLocaleString('ko-KR') + '행)');
                setStatus(key, p.name + ' · 시트 ' + p.sheet + ' · 헤더 ' + p.headerRow + '행 · ' + table.rows.length.toLocaleString('ko-KR') + '행', 'ok');
            }
            const coreOnly = !payload.db;
            payload.core = buildCorePayload(coreTables.core, coreTables.core_voc);
            if (coreOnly && !payload.core) throw new Error('코어고객 시트에서 "관리고객 명" 또는 "계약번호" 열을 찾을 수 없습니다 (시트·헤더 행을 확인하세요).');
            if (!coreOnly && !payload.db.rows.length) throw new Error('총괄DB 시트 "' + picked.db.sheet + '"에 데이터 행이 없습니다.');
            progress.textContent = '병합·집계 중...';
            await new Promise(r => setTimeout(r, 0));
            document.body.classList.toggle('core-only', coreOnly);  // 코어고객 섹션만 보이게 (report.py CSS)
            document.getElementById('webDashboard').hidden = false;
            const adminWrap = document.getElementById('adminOnlyWrap');
            if (adminWrap) adminWrap.style.display = '';
            const res = coreOnly ? { rows: 0 } : window.DataIntelLoad(payload);
            if (window.DataIntelCore) window.DataIntelCore(payload.core);
            lastPayload = payload;
            document.getElementById('webShare').hidden = false;
            updateShareMode();
            // 대시보드가 바로 보이게 업로드 칸은 한 줄로 접고 섹션 메뉴를 켠다
            document.getElementById('webUploadSummary').textContent = loadedNames.join(' · ');
            document.getElementById('webUpload').open = false;
            document.getElementById('webQuickbar').hidden = false;
            document.getElementById('webQuickReload').hidden = document.getElementById('webReloadAll').hidden;
            const nav = document.getElementById('dashNav');
            if (nav) { nav.hidden = false; window.dispatchEvent(new Event('resize')); }
            const secs = ((performance.now() - t0) / 1000).toFixed(1);
            const what = coreOnly ? '코어고객 ' + payload.core.rows.length.toLocaleString('ko-KR') + '곳' : '관리계약 ' + res.rows.toLocaleString('ko-KR') + '건';
            progress.textContent = '✅ 완료 -- ' + what + ' (' + secs + '초). 파일을 바꾸면 다시 만들 수 있습니다.';
            document.getElementById('reportMeta').textContent = what + ' · 이 브라우저에서 계산됨';
        } catch (e) {
            console.error(e);
            progress.textContent = '⚠️ 처리 실패: ' + (e && e.message ? e.message : e);
        } finally {
            refreshRun(true);
        }
    });

    // ===== 공유용 암호화 리포트 (report.py + secure_report.py와 같은 결과를 브라우저에서) =====
    const $ = id => document.getElementById(id);
    const PWD_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789';
    function strongPassword(len) {
        const out = []; const buf = new Uint32Array(len * 2);
        while (out.length < len) {
            crypto.getRandomValues(buf);
            for (const v of buf) { if (v < 4294967296 - (4294967296 % 56) && out.length < len) out.push(PWD_ALPHABET[v % 56]); }
        }
        return out.join('');
    }
    function daysFromToday(days) {  // local date (not UTC -- toISOString would be a day off after midnight KST)
        const d = new Date(); d.setDate(d.getDate() + days);
        const p = n => String(n).padStart(2, '0');
        return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
    }
    $('shareExpiry').value = daysFromToday(30);
    // 코어고객 현황만 공유: 9번이 있을 때만 고를 수 있고, 총괄DB 없이 만들었으면 항상 코어고객만.
    // 종합 리포트(kbgw-report)를 덮어쓰지 않도록 배포 저장소도 따로 쓴다.
    const CORE_TITLE = '코어고객 활동현황', CORE_REPO = 'kbgw-core-report', MAIN_REPO = 'kbgw-report';
    const coreOnlyShare = () => !!(lastPayload && lastPayload.core && (!lastPayload.db || $('shareCoreOnly').checked));
    function updateShareMode() {
        const hasCore = !!(lastPayload && lastPayload.core), noDb = !!(lastPayload && !lastPayload.db);
        $('shareCoreOnlyWrap').hidden = !hasCore;
        if (noDb) $('shareCoreOnly').checked = true;
        if (!hasCore) $('shareCoreOnly').checked = false;
        $('shareCoreOnly').disabled = noDb;
        const repo = $('ghRepo');
        if (coreOnlyShare() && repo.value.trim() === MAIN_REPO) repo.value = CORE_REPO;
        else if (!coreOnlyShare() && repo.value.trim() === CORE_REPO) repo.value = MAIN_REPO;
    }
    $('shareCoreOnly').addEventListener('change', updateShareMode);
    try { const t = localStorage.getItem('dataintel-gh-token'); if (t) { $('ghToken').value = t; $('ghRemember').checked = true; } } catch (e) {}

    function prefilterFiles(payload) {
        // report.py _prefilter_to_possible_matches: 총괄DB 키와 맞을 수 있는 행만 담아 파일 크기를 줄인다
        const db = payload.db;
        const keys = new Set();
        CFG.dbKeyCandidates.forEach(c => { const i = db.columns.indexOf(c); if (i >= 0) db.rows.forEach(r => { if (r[i] != null) keys.add(String(r[i])); }); });
        const files = {};
        Object.keys(payload.files).forEach(k => {
            const t = payload.files[k];
            const idx = (CFG.keyCandidates[k] || []).map(c => t.columns.indexOf(c)).filter(i => i >= 0);
            files[k] = { columns: t.columns, rows: idx.length ? t.rows.filter(r => idx.some(i => r[i] != null && keys.has(String(r[i])))) : [] };
        });
        return { db, files, zoneOwnerMap: payload.zoneOwnerMap, core: payload.core || null };
    }
    async function pageSource() {
        try {
            const r = await fetch(location.href.split('#')[0], { cache: 'no-store' });
            if (r.ok) return await r.text();
        } catch (e) { /* file:// -- fall back to the live DOM */ }
        return '<!DOCTYPE html>\n' + document.documentElement.outerHTML;
    }
    const esc = t => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    async function buildReportHtml(userPwd, adminPwd, expiry, coreOnly) {
        let src = await pageSource();
        const must = (from, to) => {
            if (!src.includes(from)) throw new Error('리포트 틀을 만들 수 없습니다 (페이지를 새로고침 후 다시 시도).');
            src = src.split(from).join(to);
        };
        must('const CORRECT_PWD = "' + CFG.filler + '";', 'const CORRECT_PWD = ' + JSON.stringify(userPwd) + ';');
        must('const ADMIN_PWD = "' + CFG.filler + '";', 'const ADMIN_PWD = ' + JSON.stringify(adminPwd) + ';');
        must('new Date("9999-12-31T23:59:59")', 'new Date("' + expiry + 'T23:59:59")');
        must('<style id="webVisibleStyle">#content { display: block; }</style>', '<style>#webUpload, #webShare, #webVersion { display: none !important; }</style>');
        const bodyAt = src.indexOf('<body>');
        const lock = CFG.lockScreenHtml.replace('__EXPIRY_TEXT__', esc(expiry));
        src = src.slice(0, bodyAt + 6) + '\n' + (coreOnly ? lock.replace('Data Intel PRO 보안 리포트', CORE_TITLE + ' 보안 리포트') : lock) + src.slice(bodyAt + 6);
        // 코어고객만 공유할 때는 총괄DB·매칭 파일을 아예 싣지 않는다
        const data = coreOnly ? { db: null, files: {}, zoneOwnerMap: {}, core: lastPayload.core } : prefilterFiles(lastPayload);
        const generated = new Date();
        const meta = '생성일시 ' + generated.toLocaleString('ko-KR') + ' · '
            + (coreOnly ? '코어고객 ' + data.core.rows.length.toLocaleString('ko-KR') + '곳' : '관리계약 ' + data.db.rows.length.toLocaleString('ko-KR') + '건') + ' · 만료일 ' + expiry;
        const boot = '<script type="application/json" id="webPreload">' + JSON.stringify(data).replace(/<\//g, '<\\/') + '<\/script>\n'
            + '<script>document.addEventListener("DOMContentLoaded", function () {'
            + ' var p = JSON.parse(document.getElementById("webPreload").textContent);'
            + ' document.getElementById("webDashboard").hidden = false;'
            + (coreOnly
                ? ' document.body.classList.add("core-only"); document.title = ' + JSON.stringify(CORE_TITLE) + ';'
                  + ' var h = document.querySelector(".topbar h1"); if (h) h.textContent = ' + JSON.stringify(CORE_TITLE) + ';'
                : ' document.body.classList.remove("core-only"); var nav = document.getElementById("dashNav"); if (nav) nav.hidden = false; window.DataIntelLoad(p);')
            + ' if (window.DataIntelCore) window.DataIntelCore(p.core || null);'
            + ' document.getElementById("reportMeta").textContent = ' + JSON.stringify(meta) + ';'
            + '});<\/script>\n';
        const endAt = src.lastIndexOf('</body>');
        return src.slice(0, endAt) + boot + src.slice(endAt);
    }
    function b64(bytes) {
        let s = '';
        for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
        return btoa(s);
    }
    async function encryptReport(reportHtml, passwords, expiry, title) {
        // secure_report.py encrypt_report와 같은 형식: 본문은 랜덤 키로 AES-256-GCM, 그 키를 비밀번호별 PBKDF2 키로 감쌈
        const contentKey = crypto.getRandomValues(new Uint8Array(32));
        const iv = crypto.getRandomValues(new Uint8Array(12));
        const ck = await crypto.subtle.importKey('raw', contentKey, 'AES-GCM', false, ['encrypt']);
        const data = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, ck, new TextEncoder().encode(reportHtml)));
        const slots = [];
        for (const pwd of Array.from(new Set(passwords.filter(Boolean)))) {
            const salt = crypto.getRandomValues(new Uint8Array(16));
            const siv = crypto.getRandomValues(new Uint8Array(12));
            const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(pwd), 'PBKDF2', false, ['deriveKey']);
            const kek = await crypto.subtle.deriveKey({ name: 'PBKDF2', salt, iterations: CFG.iterations, hash: 'SHA-256' },
                base, { name: 'AES-GCM', length: 256 }, false, ['encrypt']);
            const wrapped = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv: siv }, kek, contentKey));
            slots.push({ salt: b64(salt), iv: b64(siv), key: b64(wrapped) });
        }
        const payload = JSON.stringify({ v: 1, iter: CFG.iterations, slots, iv: b64(iv), data: b64(data) });
        return CFG.unlockTemplate.split('__TITLE__').join(title)
            .replace('__EXPIRY__', '<p class="exp">만료일: ' + esc(expiry) + '</p>')
            .replace('__PAYLOAD__', payload);
    }
    async function makeEncrypted() {
        if (!lastPayload) throw new Error('먼저 대시보드를 만드세요.');
        const expiry = $('shareExpiry').value;
        if (!/^\d{4}-\d{2}-\d{2}$/.test(expiry)) throw new Error('만료일을 선택하세요.');
        const userPwd = $('shareUserPwd').value.trim() || strongPassword(12);
        const adminPwd = $('shareAdminPwd').value.trim() || strongPassword(12);
        if (userPwd === adminPwd) throw new Error('사용자 비밀번호와 관리자 비밀번호는 달라야 합니다.');
        const coreOnly = coreOnlyShare();
        $('shareProgress').textContent = '리포트 만드는 중...';
        const reportHtml = await buildReportHtml(userPwd, adminPwd, expiry, coreOnly);
        $('shareProgress').textContent = '암호화 중...';
        const html = await encryptReport(reportHtml, [userPwd, adminPwd], expiry, coreOnly ? CORE_TITLE + ' 보안 리포트' : 'Data Intel PRO 보안 리포트');
        return { html, userPwd, adminPwd, expiry, coreOnly, file: coreOnly ? 'Core_Customer_Report.html' : 'Data_Intel_PRO_Report.html' };
    }
    function showShare(r, url) {
        const lines = [r.coreOnly ? '[' + CORE_TITLE + ' 리포트]' : '[Data Intel PRO 리포트]'];
        lines.push(url ? '링크: ' + url : '첨부 파일: ' + r.file + ' (브라우저로 열기)');
        lines.push('비밀번호: ' + r.userPwd, '만료일: ' + r.expiry);
        $('shareText').textContent = lines.join('\n');
        $('shareAdminNote').textContent = '관리자 비밀번호(공유하지 마세요): ' + r.adminPwd;
        $('shareResult').hidden = false;
    }
    $('shareCopyBtn').addEventListener('click', async () => {
        try { await navigator.clipboard.writeText($('shareText').textContent); $('shareCopyBtn').textContent = '✅ 복사됨'; }
        catch (e) { $('shareCopyBtn').textContent = '직접 선택해서 복사하세요'; }
        setTimeout(() => { $('shareCopyBtn').textContent = '📋 공유 문구 복사'; }, 1500);
    });
    $('shareDownloadBtn').addEventListener('click', async () => {
        const btn = $('shareDownloadBtn'); btn.disabled = true;
        try {
            const r = await makeEncrypted();
            const url = URL.createObjectURL(new Blob([r.html], { type: 'text/html;charset=utf-8' }));
            const a = document.createElement('a'); a.href = url; a.download = r.file; a.click();
            setTimeout(() => URL.revokeObjectURL(url), 5000);
            $('shareProgress').textContent = '✅ 다운로드 완료 -- 파일은 암호화되어 있어 메일·메신저로 보내도 됩니다.';
            showShare(r, null);
        } catch (e) { console.error(e); $('shareProgress').textContent = '⚠️ ' + (e.message || e); }
        finally { btn.disabled = false; }
    });

    // ---- GitHub Pages 배포 (deploy_report.py와 같은 결과: 저장소에 index.html 단일 커밋) ----
    async function gh(token, method, path, body) {
        const r = await fetch('https://api.github.com' + path, {
            method, headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json', 'Content-Type': 'application/json' },
            body: body ? JSON.stringify(body) : undefined,
        });
        const text = await r.text();
        let json = null; try { json = text ? JSON.parse(text) : null; } catch (e) {}
        return { status: r.status, ok: r.ok, json };
    }
    function ghFail(step, res) {
        const msg = res.json && res.json.message ? res.json.message : 'HTTP ' + res.status;
        if (res.status === 401) return new Error('토큰이 올바르지 않거나 만료되었습니다.');
        if (res.status === 403 || res.status === 404) return new Error(step + ' 권한이 없습니다 -- 토큰에 repo 권한이 있는지 확인하세요. (' + msg + ')');
        return new Error(step + ' 실패: ' + msg);
    }
    async function deployToPages(token, repo, html) {
        const say = t => { $('shareProgress').textContent = t; };
        const me = await gh(token, 'GET', '/user');
        if (!me.ok) throw ghFail('로그인', me);
        const full = me.json.login + '/' + repo;
        let info = await gh(token, 'GET', '/repos/' + full);
        if (info.status === 404) {
            say('배포 저장소 만드는 중: ' + full);
            const made = await gh(token, 'POST', '/user/repos', { name: repo, private: false, auto_init: true, description: 'Data Intel PRO 암호화 리포트 배포용' });
            if (!made.ok) throw ghFail('저장소 생성', made);
        } else if (!info.ok) throw ghFail('저장소 확인', info);
        // 링크를 추측할 수 없게 무작위 폴더 아래에 올린다 (deploy_report.py와 같은 규칙). 이미 있는 폴더는 그대로 써서
        // 다시 배포해도 링크가 유지된다. 저장소 첫 화면에는 빈 페이지를 둔다.
        let slug = null;
        const listing = await gh(token, 'GET', '/repos/' + full + '/contents/');
        if (listing.ok && Array.isArray(listing.json)) {
            const dir = listing.json.find(f => f.type === 'dir' && /^[a-z0-9]{10}$/.test(f.name));
            if (dir) slug = dir.name;
        }
        if (!slug) {
            const alphabet = 'abcdefghjkmnpqrstuvwxyz23456789';
            slug = Array.from(crypto.getRandomValues(new Uint8Array(10)), v => alphabet[v % alphabet.length]).join('');
        }
        say('리포트 올리는 중...');
        const blob = await gh(token, 'POST', '/repos/' + full + '/git/blobs', { content: b64(new TextEncoder().encode(html)), encoding: 'base64' });
        if (!blob.ok) throw ghFail('업로드', blob);
        const front = await gh(token, 'POST', '/repos/' + full + '/git/blobs', { content: CFG.placeholderPage, encoding: 'utf-8' });
        if (!front.ok) throw ghFail('업로드', front);
        const nojekyll = await gh(token, 'POST', '/repos/' + full + '/git/blobs', { content: '', encoding: 'utf-8' });
        if (!nojekyll.ok) throw ghFail('업로드', nojekyll);
        const tree = await gh(token, 'POST', '/repos/' + full + '/git/trees', { tree: [
            { path: slug + '/index.html', mode: '100644', type: 'blob', sha: blob.json.sha },
            { path: 'index.html', mode: '100644', type: 'blob', sha: front.json.sha },
            { path: '.nojekyll', mode: '100644', type: 'blob', sha: nojekyll.json.sha }] });
        if (!tree.ok) throw ghFail('업로드', tree);
        // parents: [] -- 이전 리포트를 기록에 남기지 않는 단일 커밋 (deploy_report.py의 force push와 같음)
        const commit = await gh(token, 'POST', '/repos/' + full + '/git/commits', { message: 'Deploy encrypted report', tree: tree.json.sha, parents: [] });
        if (!commit.ok) throw ghFail('업로드', commit);
        let ref = await gh(token, 'PATCH', '/repos/' + full + '/git/refs/heads/main', { sha: commit.json.sha, force: true });
        if (!ref.ok) ref = await gh(token, 'POST', '/repos/' + full + '/git/refs', { ref: 'refs/heads/main', sha: commit.json.sha });
        if (!ref.ok) throw ghFail('업로드', ref);
        let pages = await gh(token, 'GET', '/repos/' + full + '/pages');
        if (!pages.ok) {
            say('GitHub Pages 켜는 중...');
            pages = await gh(token, 'POST', '/repos/' + full + '/pages', { source: { branch: 'main', path: '/' } });
            if (!pages.ok) throw ghFail('GitHub Pages 설정', pages);
        }
        const base = (pages.json && pages.json.html_url) || ('https://' + me.json.login.toLowerCase() + '.github.io/' + repo + '/');
        return base.replace(/\/+$/, '') + '/' + slug + '/';
    }
    $('shareDeployBtn').addEventListener('click', async () => {
        const btn = $('shareDeployBtn'); btn.disabled = true; $('shareDownloadBtn').disabled = true;
        try {
            const token = $('ghToken').value.trim();
            const repo = $('ghRepo').value.trim() || (coreOnlyShare() ? CORE_REPO : MAIN_REPO);
            if (!token) throw new Error('GitHub 토큰을 입력하세요.');
            if (!/^[A-Za-z0-9._-]+$/.test(repo)) throw new Error('저장소 이름은 영문·숫자·-·_ 만 쓸 수 있습니다.');
            try { if ($('ghRemember').checked) localStorage.setItem('dataintel-gh-token', token); else localStorage.removeItem('dataintel-gh-token'); } catch (e) {}
            const r = await makeEncrypted();
            const url = await deployToPages(token, repo, r.html);
            $('shareProgress').textContent = '✅ 배포 완료 -- 1~2분 뒤 링크가 열립니다: ' + url;
            showShare(r, url);
        } catch (e) { console.error(e); $('shareProgress').textContent = '⚠️ ' + (e.message || e); }
        finally { btn.disabled = false; $('shareDownloadBtn').disabled = false; }
    });
})();
</script>
</body>
</html>"""
