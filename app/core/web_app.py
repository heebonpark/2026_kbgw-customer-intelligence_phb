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

import json
import random
import string

from .handlers import HQ_ORDER, BRANCH_ORDER
from .matching_config import (
    MATCHABLE_FILES, FILE_LABELS, DB_KEY_CANDIDATES, FILE_KEY_CANDIDATES,
    FILE_DISPLAY_COLUMNS, default_config,
)
from .report import CSS, APP_SCRIPT_TEMPLATE, render_admin_panel_shell
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
        {'name': '영업구역정보', 'aliases': ['영업구역정보', '영업구역번호', '영업구역']},
        {'name': '기술구역정보', 'aliases': ['기술구역정보', '기술구역번호', '기술구역']},
        {'name': '구역정보', 'aliases': ['구역정보', '구역']},
        {'name': '활동유무', 'aliases': ['활동유무', '활동유무(o,x)'], 'prefix': '활동유무', 'required': True},
        {'name': 'SP담당', 'aliases': ['SP담당']},
    ],
    'zone_owner': [
        {'name': '구역번호', 'aliases': ['구역번호', '영업구역번호'], 'required': True},
        {'name': '담당자명', 'aliases': ['담당자명', '담당자', '사원명', '(신규)사원명'], 'required': True},
    ],
}

# (key, label, note) -- same numbering as the desktop GUI
WEB_UPLOAD_SLOTS = [
    ('db', '1. 총괄관리DB', '필수'),
    ('voc', '2. 월/일일 SP관리활동 (VOC)', '선택'),
    ('patrol', '3. 월/일일 SE,SG 정기점검', '선택'),
    ('original', '5. 2026년 관리고객원본', '선택'),
    ('facility', '6. 시설현황', '선택'),
    ('zone_owner', '8. 영업구역담당자', '선택 · SP 구역번호→담당자명'),
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


def generate_web_app_html():
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
            .replace('__ADMIN_PANEL__', render_admin_panel_shell())
            .replace('__EMBEDDED__', embedded_json)
            .replace('__UPLOAD_CONFIG__', upload_json)
            .replace('__SHEETJS__', SHEETJS_URL)
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
        </div>
        <button class="theme-toggle" onclick="toggleTheme()">🌓 테마 전환</button>
    </div>
    <div class="container">
        <section class="web-upload" id="webUpload">
            <h2>📂 원본 파일 선택</h2>
            <p class="web-privacy">🔒 파일은 서버로 전송되지 않습니다 -- 이 브라우저 안에서만 읽고 계산하며, 창을 닫으면 사라집니다.</p>
            <div class="web-slots">__SLOTS__</div>
            <div class="web-actions">
                <button type="button" class="web-run" id="webRunBtn" disabled>📊 대시보드 만들기</button>
                <button type="button" class="web-btn2" id="webReloadAll" hidden title="엑셀에서 저장(Ctrl+S)한 최신 내용으로 모든 파일을 다시 읽고 대시보드를 새로 만듭니다">🔄 모두 다시 불러오고 대시보드 갱신</button>
                <span class="web-progress" id="webProgress">1. 총괄관리DB는 필수입니다.</span>
            </div>
            <p class="web-footnote">엑셀에서 열어 둔 파일도 선택할 수 있습니다 -- 단, <b>마지막으로 저장된 내용</b>을 읽으므로 수정 중이면 먼저 저장하세요. 한 번 고른 파일은 <b>🔄 다시 불러오기</b>로 다시 고르지 않고 최신 저장본을 읽습니다 (Chrome·Edge). 저장 안 한 내용까지 쓰려면 <b>📋 엑셀에서 붙여넣기</b>를 쓰세요. 파일을 고르면 <b>시트 · 헤더 행 · 컬럼(열 위치)</b>을 자동으로 맞추고 미리보기를 보여줍니다 -- 다르면 드롭다운에서 바꾸세요. 설정은 이 브라우저에 기억되어 다음에 같은 양식이면 자동 적용됩니다.</p>
            <p class="web-footnote">4. 해지파이프라인 · 7. 해지시설내역 섹션은 데스크톱 GUI 리포트에서 제공합니다.</p>
        </section>

        <section class="web-upload" id="webShare" hidden>
            <h2>🔒 공유용 리포트 만들기</h2>
            <p class="web-privacy">위 대시보드를 <b>암호화된 HTML 파일</b>로 만듭니다 (GUI 리포트와 같은 방식 -- 비밀번호 없이는 내용을 볼 수 없음). 암호화도 이 브라우저 안에서 합니다.</p>
            <div class="web-share-grid">
                <label class="web-field">사용자 비밀번호 <small>받는 사람이 입력 · 비우면 12자리 랜덤</small>
                    <input type="text" id="shareUserPwd" autocomplete="off" placeholder="예: Kbgw2026!oct"></label>
                <label class="web-field">관리자 비밀번호 <small>매칭설정 패널까지 열림 · 비우면 12자리 랜덤</small>
                    <input type="password" id="shareAdminPwd" autocomplete="new-password" placeholder="예: GUI와 같은 관리자 비밀번호"></label>
                <label class="web-field">만료일 <small>이 날짜가 지나면 열리지 않음</small>
                    <input type="date" id="shareExpiry"></label>
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
        </section>

        <div id="webDashboard" hidden>
        __ADMIN_PANEL__
        <div id="globalFilterBarWrap"></div>

        <details class="section-collapse">
        <summary class="section-title">🔄 재계약대상(SP)</summary>
        <div id="recontractSectionWrap"></div>
        </details>

        <details class="section-collapse" open>
        <summary class="section-title">총괄DB 기준 대시보드</summary>
        <div class="stat-grid" id="statGrid"></div>
        <div class="chart-grid" id="chartGrid"></div>
        <div id="top10Section"></div>
        <div id="treeSummarySection"></div>
        </details>

        <details class="section-collapse" open>
        <summary class="section-title">지사별 활동 진척율 (SP/SE/SG)</summary>
        <div id="progressInsightWrap"></div>
        <div id="progressTypeWrap"></div>
        <div class="table-section" id="progressSection"></div>
        </details>

        <details class="section-collapse" open>
        <summary class="section-title">구역별 활동 현황 (SP 영업구역 · SE 기술구역 · SG 구역)</summary>
        <div id="zoneActivityWrap"></div>
        </details>

        <details class="section-collapse" open>
        <summary class="section-title">구역별 실적현황 (영업·기술·출동사원)</summary>
        <div id="perfReportWrap"></div>
        </details>

        <details class="subsection-collapse" open>
        <summary class="subsection-title">SP 부진자 추가분석 (담당자 기준)</summary>
        <div id="spRepSectionWrap"></div>
        </details>

        <details class="subsection-collapse" open>
        <summary class="subsection-title">SP 미접수/접수 발송용 리스트 (담당자별)</summary>
        <div id="spPendingSectionWrap"></div>
        </details>

        <details class="section-collapse" open>
        <summary class="section-title">데이터 분포/이상치 분석 (EDA, 월정산금액 기준)</summary>
        <div id="edaSectionWrap"></div>
        </details>

        <details class="section-collapse" open>
        <summary class="section-title">관리고객 상세 (필터/검색 가능)</summary>
        <button id="btnExportCSV" class="export-btn" title="현재 조건으로 필터링된 모든 데이터를 엑셀(CSV)로 다운로드합니다.">📥 필터링된 데이터 엑셀(CSV) 다운로드</button>
        <div class="table-section" id="tableSection"></div>
        </details>
        </div>
    </div>
</div>

<script type="application/json" id="embeddedData">__EMBEDDED__</script>
<script type="application/json" id="uploadConfig">__UPLOAD_CONFIG__</script>
<script src="__SHEETJS__"></script>
<script>__APP_SCRIPT__</script>
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

    const refreshRun = (keepMessage) => {
        const dbMissing = picked.db ? missingRequired('db') : [];
        runBtn.disabled = !picked.db || dbMissing.length > 0;
        if (keepMessage) return;  // 실행 결과(완료/실패) 문구는 그대로 둔다
        progress.textContent = !picked.db ? '1. 총괄관리DB는 필수입니다.'
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
    function autoMap(key, cells, saved) {
        // 저장된 설정(헤더 이름 -> 열 위치 순) -> 이름 자동 매칭 순으로 각 표준 컬럼의 열을 정한다
        const map = {};
        (CFG.fieldSpecs[key] || []).forEach(spec => {
            const sv = saved && saved.fields ? saved.fields[spec.name] : undefined;
            if (sv === '') { map[spec.name] = -1; return; }  // 사용자가 '(없음)'으로 둔 항목
            let idx = -1;
            if (sv) {
                const byName = sv.header ? cells.find(c => c.name === sv.header) : null;
                const byLetter = cells.find(c => c.letter === sv.letter);
                idx = byName ? byName.idx : byLetter ? byLetter.idx : -1;
            }
            if (idx < 0) {
                const exact = spec.aliases.map(a => cells.find(c => c.name === a)).find(Boolean);
                const pref = spec.prefix ? cells.find(c => c.name.startsWith(spec.prefix)) : null;
                idx = exact ? exact.idx : pref ? pref.idx : -1;
            }
            map[spec.name] = idx;
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
    function applySheet(key, sheet, headerRow, saved) {
        const p = picked[key];
        p.sheet = sheet;
        const ws = p.wb.Sheets[sheet];
        p.headerRow = headerRow || detectHeaderRow(ws, key);
        p.map = autoMap(key, headerCells(ws, p.headerRow), saved);
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
            p.map = autoMap(key, headerCells(ws, p.headerRow), null);
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
    document.getElementById('webReloadAll').addEventListener('click', async () => {
        const btn = document.getElementById('webReloadAll');
        btn.disabled = true;
        try {
            for (const key of Object.keys(handles)) await readHandle(key);
            if (picked.db && !missingRequired('db').length) runBtn.click();
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
        Object.keys(p.map || {}).forEach(std => {
            const idx = p.map[std];
            if (!(idx >= 0) || idx >= width || columns[idx] === std) return;
            const clash = columns.indexOf(std);
            if (clash >= 0) columns[clash] = std + '(원본)';
            columns[idx] = std;
        });
        const rows = aoa.slice(1).map(row => columns.map((_, i) => {
            const v = row[i];
            if (v === undefined || v === null) return null;
            if (v instanceof Date) return isoLocal(v);
            if (typeof v === 'string') { const t = v.replace(/ /g, ' '); return t.trim() === '' ? null : t; }
            return v;
        }));
        return { columns, rows };
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
        if (!picked.db || missingRequired('db').length) return;
        if (typeof XLSX === 'undefined') { progress.textContent = '엑셀 읽기 모듈을 불러오지 못했습니다. 인터넷 연결을 확인 후 새로고침하세요.'; return; }
        runBtn.disabled = true;
        const t0 = performance.now();
        try {
            const payload = { db: null, files: {}, zoneOwnerMap: {} };
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
                } else if (CFG.matchable.includes(key)) {
                    const wanted = Array.from(new Set(CFG.keyCandidates[key].concat(CFG.displayColumns[key])));
                    payload.files[key] = pickColumns(table, wanted);
                }
                setStatus(key, p.name + ' · 시트 ' + p.sheet + ' · 헤더 ' + p.headerRow + '행 · ' + table.rows.length.toLocaleString('ko-KR') + '행', 'ok');
            }
            if (!payload.db.rows.length) throw new Error('총괄DB 시트 "' + picked.db.sheet + '"에 데이터 행이 없습니다.');
            progress.textContent = '병합·집계 중...';
            await new Promise(r => setTimeout(r, 0));
            document.getElementById('webDashboard').hidden = false;
            const adminWrap = document.getElementById('adminOnlyWrap');
            if (adminWrap) adminWrap.style.display = '';
            const res = window.DataIntelLoad(payload);
            lastPayload = payload;
            document.getElementById('webShare').hidden = false;
            const secs = ((performance.now() - t0) / 1000).toFixed(1);
            progress.textContent = '✅ 완료 -- 관리계약 ' + res.rows.toLocaleString('ko-KR') + '건 (' + secs + '초). 파일을 바꾸면 다시 만들 수 있습니다.';
            document.getElementById('reportMeta').textContent = '관리계약 ' + res.rows.toLocaleString('ko-KR') + '건 · 이 브라우저에서 계산됨';
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
        return { db, files, zoneOwnerMap: payload.zoneOwnerMap };
    }
    async function pageSource() {
        try {
            const r = await fetch(location.href.split('#')[0], { cache: 'no-store' });
            if (r.ok) return await r.text();
        } catch (e) { /* file:// -- fall back to the live DOM */ }
        return '<!DOCTYPE html>\n' + document.documentElement.outerHTML;
    }
    const esc = t => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    async function buildReportHtml(userPwd, adminPwd, expiry) {
        let src = await pageSource();
        const must = (from, to) => {
            if (!src.includes(from)) throw new Error('리포트 틀을 만들 수 없습니다 (페이지를 새로고침 후 다시 시도).');
            src = src.split(from).join(to);
        };
        must('const CORRECT_PWD = "' + CFG.filler + '";', 'const CORRECT_PWD = ' + JSON.stringify(userPwd) + ';');
        must('const ADMIN_PWD = "' + CFG.filler + '";', 'const ADMIN_PWD = ' + JSON.stringify(adminPwd) + ';');
        must('new Date("9999-12-31T23:59:59")', 'new Date("' + expiry + 'T23:59:59")');
        must('<style id="webVisibleStyle">#content { display: block; }</style>', '<style>#webUpload, #webShare { display: none !important; }</style>');
        const bodyAt = src.indexOf('<body>');
        src = src.slice(0, bodyAt + 6) + '\n' + CFG.lockScreenHtml.replace('__EXPIRY_TEXT__', esc(expiry)) + src.slice(bodyAt + 6);
        const data = prefilterFiles(lastPayload);
        const generated = new Date();
        const meta = '생성일시 ' + generated.toLocaleString('ko-KR') + ' · 관리계약 ' + data.db.rows.length.toLocaleString('ko-KR') + '건 · 만료일 ' + expiry;
        const boot = '<script type="application/json" id="webPreload">' + JSON.stringify(data).replace(/<\//g, '<\\/') + '<\/script>\n'
            + '<script>document.addEventListener("DOMContentLoaded", function () {'
            + ' var p = JSON.parse(document.getElementById("webPreload").textContent);'
            + ' document.getElementById("webDashboard").hidden = false;'
            + ' window.DataIntelLoad(p);'
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
    async function encryptReport(reportHtml, passwords, expiry) {
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
        return CFG.unlockTemplate.split('__TITLE__').join('Data Intel PRO 보안 리포트')
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
        $('shareProgress').textContent = '리포트 만드는 중...';
        const reportHtml = await buildReportHtml(userPwd, adminPwd, expiry);
        $('shareProgress').textContent = '암호화 중...';
        const html = await encryptReport(reportHtml, [userPwd, adminPwd], expiry);
        return { html, userPwd, adminPwd, expiry };
    }
    function showShare(r, url) {
        const lines = ['[Data Intel PRO 리포트]'];
        lines.push(url ? '링크: ' + url : '첨부 파일: Data_Intel_PRO_Report.html (브라우저로 열기)');
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
            const a = document.createElement('a'); a.href = url; a.download = 'Data_Intel_PRO_Report.html'; a.click();
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
        say('리포트 올리는 중...');
        const blob = await gh(token, 'POST', '/repos/' + full + '/git/blobs', { content: b64(new TextEncoder().encode(html)), encoding: 'base64' });
        if (!blob.ok) throw ghFail('업로드', blob);
        const nojekyll = await gh(token, 'POST', '/repos/' + full + '/git/blobs', { content: '', encoding: 'utf-8' });
        if (!nojekyll.ok) throw ghFail('업로드', nojekyll);
        const tree = await gh(token, 'POST', '/repos/' + full + '/git/trees', { tree: [
            { path: 'index.html', mode: '100644', type: 'blob', sha: blob.json.sha },
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
        return (pages.json && pages.json.html_url) || ('https://' + me.json.login.toLowerCase() + '.github.io/' + repo + '/');
    }
    $('shareDeployBtn').addEventListener('click', async () => {
        const btn = $('shareDeployBtn'); btn.disabled = true; $('shareDownloadBtn').disabled = true;
        try {
            const token = $('ghToken').value.trim();
            const repo = $('ghRepo').value.trim() || 'kbgw-report';
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
