import re

with open("app/core/report.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. CSS for Premium Features
css_code = """
/* ---- Premium Direct Features ---- */
.fab-container { position: fixed; bottom: 30px; right: 30px; display: flex; flex-direction: column; gap: 12px; z-index: 9999; }
.fab-btn { width: 48px; height: 48px; border-radius: 24px; border: none; background: var(--surface-1); box-shadow: 0 4px 16px rgba(0,0,0,0.15); font-size: 20px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: transform 0.2s, background 0.2s; border: 1px solid var(--border); color: var(--text-primary); }
.fab-btn:hover { transform: scale(1.1); background: var(--surface-2); }
.export-btn { display: inline-flex; align-items: center; gap: 6px; padding: 10px 18px; border-radius: 8px; border: 1px solid var(--grid-line); background: var(--surface-1); color: var(--text-primary); font-weight: 600; font-size: 13px; cursor: pointer; transition: all 0.2s; box-shadow: 0 2px 8px rgba(0,0,0,0.04); margin-bottom: 20px; }
.export-btn:hover { border-color: var(--brand); color: var(--brand); background: var(--page-plane); transform: translateY(-1px); }
"""
content = content.replace("/* ---- lock screen ---- */", css_code + "\n/* ---- lock screen ---- */")


# 2. JS for Premium Features
js_code = """
    // ---- Premium Direct Features ----
    // 1. Dark Mode Toggle
    let isDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    function toggleTheme() {
        isDark = !isDark;
        document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
        document.getElementById('btnTheme').textContent = isDark ? '☀️' : '🌙';
        document.getElementById('btnTheme').title = isDark ? '라이트 모드로 변경' : '다크 모드로 변경';
    }
    
    // 2. CSV Export
    function downloadCSV() {
        if (!filtered || filtered.length === 0) {
            alert('다운로드할 데이터가 없습니다.');
            return;
        }
        // Extract headers from first object
        const headers = Object.keys(filtered[0]);
        let csvContent = "data:text/csv;charset=utf-8,\\uFEFF"; // UTF-8 BOM for Excel
        csvContent += headers.join(",") + "\\r\\n";
        
        filtered.forEach(row => {
            let r = headers.map(h => {
                let val = row[h];
                if (val === null || val === undefined) val = "";
                val = String(val).replace(/"/g, '""'); // escape quotes
                return '"' + val + '"';
            });
            csvContent += r.join(",") + "\\r\\n";
        });
        
        const encodedUri = encodeURI(csvContent);
        const link = document.createElement("a");
        link.setAttribute("href", encodedUri);
        link.setAttribute("download", "Data_Intel_PRO_Filtered_Data.csv");
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }
    
    // 3. Setup UI
    window.addEventListener('DOMContentLoaded', () => {
        // Init theme toggle
        const btnTheme = document.getElementById('btnTheme');
        if(btnTheme) {
            btnTheme.textContent = isDark ? '☀️' : '🌙';
            btnTheme.addEventListener('click', toggleTheme);
        }
        
        // Init scroll to top
        const btnTop = document.getElementById('btnTop');
        if(btnTop) {
            btnTop.addEventListener('click', () => window.scrollTo({top:0, behavior:'smooth'}));
        }
        
        // Init Export CSV
        const btnExport = document.getElementById('btnExportCSV');
        if(btnExport) {
            btnExport.addEventListener('click', downloadCSV);
        }
    });
"""
content = content.replace("    // ---- lock screen ----", js_code + "\n    // ---- lock screen ----")

# 3. HTML Placeholders
# Add Export button above tableSection
html_export = """
        <button id="btnExportCSV" class="export-btn" title="현재 조건으로 필터링된 모든 데이터를 엑셀(CSV)로 다운로드합니다.">📥 필터링된 데이터 엑셀(CSV) 다운로드</button>
"""
content = content.replace("        <h2 class=\"section-title\">관리고객 상세 (필터/검색 가능)</h2>", html_export + "\n        <h2 class=\"section-title\">관리고객 상세 (필터/검색 가능)</h2>")

# Add FAB inside body
html_fab = """
<div class="fab-container">
    <button id="btnTheme" class="fab-btn" title="다크 모드 변경">🌙</button>
    <button id="btnTop" class="fab-btn" title="맨 위로 가기">⬆️</button>
</div>
"""
content = content.replace("<body>", "<body>\n" + html_fab)

with open("app/core/report.py", "w", encoding="utf-8") as f:
    f.write(content)
