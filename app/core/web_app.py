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

SHEETJS_URL = "https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"

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
        <label class="web-slot{required}" data-key="{key}">
            <span class="web-slot-title">{label} <span class="web-slot-note">({note})</span></span>
            <input type="file" accept=".xlsx,.xls,.csv" data-key="{key}">
            <span class="web-slot-status" data-status-for="{key}">파일을 선택하세요</span>
        </label>""")
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
    }
    # The report script's lock screen is never shown here (no data is embedded),
    # so its password constants are just unguessable filler.
    filler = ''.join(random.choice(string.ascii_letters) for _ in range(24))
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
<style>
#content { display: block; }
.web-upload { background: var(--surface-1); border: 1px solid var(--border); border-radius: 14px; padding: 20px; margin-bottom: 24px; }
.web-upload h2 { font-size: 17px; margin: 0 0 4px; color: var(--text-primary); }
.web-upload .web-privacy { font-size: 12.5px; color: var(--text-secondary); margin: 0 0 16px; }
.web-slots { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 300px), 1fr)); gap: 10px; }
.web-slot { display: flex; flex-direction: column; gap: 6px; border: 1px dashed var(--baseline); border-radius: 10px; padding: 12px 14px; cursor: pointer; background: var(--page-plane); }
.web-slot:hover { border-color: var(--brand); }
.web-slot-required { border-style: solid; border-color: color-mix(in srgb, var(--brand) 55%, var(--border)); }
.web-slot-title { font-size: 13px; font-weight: 700; color: var(--text-primary); }
.web-slot-note { font-weight: 400; color: var(--text-muted); font-size: 11.5px; }
.web-slot input[type=file] { font-size: 12px; color: var(--text-secondary); max-width: 100%; }
.web-slot-status { font-size: 11.5px; color: var(--text-muted); }
.web-slot-status.ok { color: var(--good); font-weight: 600; }
.web-slot-status.err { color: var(--critical); font-weight: 600; }
.web-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-top: 16px; }
.web-run { font-size: 15px; font-weight: 700; padding: 11px 22px; border: 0; border-radius: 10px; background: var(--brand); color: #fff; cursor: pointer; }
.web-run:disabled { opacity: .5; cursor: not-allowed; }
.web-progress { font-size: 13px; color: var(--text-secondary); }
.web-footnote { font-size: 11.5px; color: var(--text-muted); margin: 12px 0 0; }
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
            <p class="web-footnote">4. 해지파이프라인 · 7. 해지시설내역 섹션은 데스크톱 GUI 리포트에서 제공합니다.</p>
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
    const picked = {};   // key -> File
    const runBtn = document.getElementById('webRunBtn');
    const progress = document.getElementById('webProgress');
    const setStatus = (key, text, cls) => {
        const el = document.querySelector('[data-status-for="' + key + '"]');
        if (el) { el.textContent = text; el.className = 'web-slot-status' + (cls ? ' ' + cls : ''); }
    };

    document.querySelectorAll('.web-slot input[type=file]').forEach(input => {
        input.addEventListener('change', () => {
            const key = input.dataset.key;
            if (input.files && input.files[0]) {
                picked[key] = input.files[0];
                setStatus(key, input.files[0].name, 'ok');
            } else {
                delete picked[key];
                setStatus(key, '파일을 선택하세요');
            }
            runBtn.disabled = !picked.db;
            progress.textContent = picked.db ? '준비 완료 -- 대시보드 만들기를 누르세요.' : '1. 총괄관리DB는 필수입니다.';
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
    async function readTable(file) {
        const buf = await file.arrayBuffer();
        const wb = /\.csv$/i.test(file.name)
            ? XLSX.read(decodeCsv(buf), { type: 'string', cellDates: true })
            : XLSX.read(buf, { type: 'array', cellDates: true });
        const ws = wb.Sheets[wb.SheetNames[0]];
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
                progress.textContent = picked[key].name + ' 읽는 중...';
                setStatus(key, picked[key].name + ' -- 읽는 중...', 'ok');
                await new Promise(r => setTimeout(r, 0));  // let the status paint before a long parse
                const table = await readTable(picked[key]);
                if (key === 'db') {
                    payload.db = table;
                } else if (key === 'zone_owner') {
                    const map = buildZoneOwnerMap(table);
                    if (!map) { setStatus(key, '구역번호/담당자명 컬럼이 없습니다', 'err'); continue; }
                    payload.zoneOwnerMap = map;
                } else if (CFG.matchable.includes(key)) {
                    const wanted = Array.from(new Set(CFG.keyCandidates[key].concat(CFG.displayColumns[key])));
                    payload.files[key] = pickColumns(table, wanted);
                }
                setStatus(key, picked[key].name + ' · ' + table.rows.length.toLocaleString('ko-KR') + '행', 'ok');
            }
            if (!payload.db.rows.length) throw new Error('총괄DB에 데이터 행이 없습니다.');
            progress.textContent = '병합·집계 중...';
            await new Promise(r => setTimeout(r, 0));
            document.getElementById('webDashboard').hidden = false;
            const adminWrap = document.getElementById('adminOnlyWrap');
            if (adminWrap) adminWrap.style.display = '';
            const res = window.DataIntelLoad(payload);
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
})();
</script>
</body>
</html>"""
