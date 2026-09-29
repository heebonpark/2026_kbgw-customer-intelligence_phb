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

Excel files with several sheets get a sheet picker; the sheet whose header
row has the column that slot needs (SHEET_HINT_COLUMNS) is pre-selected.

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
            <input type="file" accept=".xlsx,.xls,.csv" data-key="{key}">
            <select class="web-sheet" data-sheet-for="{key}" hidden title="시트 선택"></select>
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
    embedded_json = json.dumps(embedded, ensure_ascii=False).replace('</', '<\\/')
    upload_json = json.dumps(upload_config, ensure_ascii=False).replace('</', '<\\/')

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
.web-slot-status { font-size: 11.5px; color: var(--text-muted); }
.web-sheet { font-size: 12.5px; padding: 5px 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-1); color: var(--text-primary); max-width: 100%; }
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
                <span class="web-progress" id="webProgress">1. 총괄관리DB는 필수입니다.</span>
            </div>
            <p class="web-footnote">엑셀에서 열어 둔 파일도 선택할 수 있습니다 -- 단, <b>마지막으로 저장된 내용</b>을 읽으므로 수정 중이면 먼저 저장하세요. 시트가 여러 개면 필요한 컬럼이 있는 시트를 자동으로 고르고, 목록에서 바꿀 수 있습니다.</p>
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

    const refreshRun = () => {
        runBtn.disabled = !picked.db;
        progress.textContent = picked.db ? '준비 완료 -- 대시보드 만들기를 누르세요.' : '1. 총괄관리DB는 필수입니다.';
    };
    const pickToken = {};  // key -> latest pick; a slower, older read must not overwrite a newer one
    document.querySelectorAll('.web-slot input[type=file]').forEach(input => {
        input.addEventListener('change', async () => {
            const key = input.dataset.key;
            const token = pickToken[key] = {};
            const sel = document.querySelector('[data-sheet-for="' + key + '"]');
            sel.hidden = true; sel.innerHTML = '';
            delete picked[key];
            const file = input.files && input.files[0];
            if (!file) { setStatus(key, '파일을 선택하세요'); refreshRun(); return; }
            setStatus(key, file.name + ' -- 여는 중...', 'ok');
            runBtn.disabled = true;
            await new Promise(r => setTimeout(r, 0));
            try {
                const wb = await readWorkbook(file);
                if (pickToken[key] !== token) return;
                sel.innerHTML = '';
                const sheets = wb.SheetNames.map(name => ({ name, rows: sheetRowCount(wb.Sheets[name]) }));
                // 필요한 컬럼이 있고 데이터 행도 있는 시트를 우선 (헤더만 있는 빈 시트는 뒤로)
                const hasHint = sh => sheetHasColumn(wb.Sheets[sh.name], CFG.sheetHints[key] || []);
                const hinted = sheets.find(sh => sh.rows > 0 && hasHint(sh)) || sheets.find(hasHint);
                const nonEmpty = sheets.find(sh => sh.rows > 0);
                const chosen = (hinted || nonEmpty || sheets[0]).name;
                picked[key] = { file, wb, sheet: chosen };
                if (sheets.length > 1) {
                    sheets.forEach(sh => sel.appendChild(new Option(sh.name + ' (' + sh.rows.toLocaleString('ko-KR') + '행)', sh.name)));
                    sel.value = chosen;
                    sel.hidden = false;
                }
                setStatus(key, file.name + (sheets.length > 1 ? ' · 시트 ' + sheets.length + '개' + (hinted ? ' (자동 선택: ' + chosen + ')' : '') : ''), 'ok');
            } catch (e) {
                console.error(e);
                if (pickToken[key] !== token) return;
                setStatus(key, '파일을 열 수 없습니다: ' + (e && e.message ? e.message : e), 'err');
            }
            refreshRun();
        });
    });
    document.querySelectorAll('.web-sheet').forEach(sel => {
        sel.addEventListener('change', () => {
            const key = sel.dataset.sheetFor;
            if (picked[key]) { picked[key].sheet = sel.value; setStatus(key, picked[key].file.name + ' · 시트: ' + sel.value, 'ok'); }
        });
    });

    // ---- file -> {columns, rows} (mirrors handlers.py load_data) ----
    function decodeCsv(buf) {
        try { return new TextDecoder('utf-8', { fatal: true }).decode(buf).replace(/^﻿/, ''); }
        catch (e) { return new TextDecoder('euc-kr').decode(buf); }  // 사내 CSV 내보내기는 대부분 cp949
    }
    function isoLocal(d) {
        const p = n => String(n).padStart(2, '0');
        return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
    }
    function hasTitleRow(header) {
        // handlers.py _has_title_row: 첫 줄이 제목 한 칸(나머지는 빈 칸)이면 실제 헤더는 다음 줄
        if (header.length < 3) return false;
        const blank = header.slice(1).filter(c => c === null || c === undefined || String(c).trim() === '').length;
        return blank >= header.length - 2;
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
        // 첫 두 줄(제목 줄이 있는 경우 대비)에 원하는 컬럼명이 있는지만 본다 -- 시트 전체를 변환하지 않음
        if (!ws || !ws['!ref'] || !names.length) return false;
        const r = XLSX.utils.decode_range(ws['!ref']);
        for (let row = r.s.r; row <= Math.min(r.s.r + 1, r.e.r); row++) {
            for (let col = r.s.c; col <= r.e.c; col++) {
                const cell = ws[XLSX.utils.encode_cell({ r: row, c: col })];
                if (cell && cell.v != null && names.includes(String(cell.v).replace(/\u00a0/g, ' ').trim())) return true;
            }
        }
        return false;
    }
    function readTable(wb, sheetName) {
        const ws = wb.Sheets[sheetName];
        let aoa = XLSX.utils.sheet_to_json(ws, { header: 1, raw: true, defval: null, blankrows: false });
        if (!aoa.length) return { columns: [], rows: [] };
        if (hasTitleRow(aoa[0]) && aoa.length > 1) aoa = aoa.slice(1);
        const width = aoa.reduce((m, r) => Math.max(m, r.length), 0);
        const seen = {};
        const columns = Array.from({ length: width }, (_, i) => {
            const raw = aoa[0][i];
            let name = (raw === null || raw === undefined || String(raw).trim() === '')
                ? 'Unnamed: ' + i : String(raw).replace(/ /g, ' ').trim();
            if (seen[name] !== undefined) { seen[name] += 1; name = name + '.' + seen[name]; } else { seen[name] = 0; }
            return name;
        });
        const rows = aoa.slice(1).map(r => columns.map((_, i) => {
            const v = r[i];
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
        if (!picked.db) return;
        if (typeof XLSX === 'undefined') { progress.textContent = '엑셀 읽기 모듈을 불러오지 못했습니다. 인터넷 연결을 확인 후 새로고침하세요.'; return; }
        runBtn.disabled = true;
        const t0 = performance.now();
        try {
            const payload = { db: null, files: {}, zoneOwnerMap: {} };
            for (const key of Object.keys(picked)) {
                const p = picked[key];
                progress.textContent = p.file.name + ' 읽는 중...';
                await new Promise(r => setTimeout(r, 0));  // let the status paint before a long parse
                const table = readTable(p.wb, p.sheet);
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
                setStatus(key, p.file.name + (p.wb.SheetNames.length > 1 ? ' · 시트 ' + p.sheet : '') + ' · ' + table.rows.length.toLocaleString('ko-KR') + '행', 'ok');
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
            runBtn.disabled = !picked.db;
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
