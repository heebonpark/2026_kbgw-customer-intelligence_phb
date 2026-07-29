import re

with open("app/core/report.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. CSS
css_code = """
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
"""
content = content.replace("/* ---- tree-grid summary ---- */", css_code + "\n/* ---- tree-grid summary ---- */")

# 2. JS Logic
js_code = """
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
"""
content = content.replace("    // ---- tree-grid summary (HQ > Branch > Owner) ----", js_code + "\n    // ---- tree-grid summary (HQ > Branch > Owner) ----")

call_code = """
        const top10Section = document.getElementById('top10Section');
        if (top10Section) renderTop10El(top10Section, filtered);
"""
content = content.replace("        const treeSummarySection = document.getElementById('treeSummarySection');", call_code + "\n        const treeSummarySection = document.getElementById('treeSummarySection');")

# 3. HTML Placeholders
# EDA Button before statGrid
html_eda_btn = """
        <div class="eda-btn-wrap">
            <a href="Data_Intel_PRO_EDA.html" target="_blank" class="eda-btn">🚀 딥 다이브 EDA 분석기 열기 (별도 창)</a>
        </div>
"""
content = content.replace("        <h2 class=\"section-title\">총괄DB 기준 대시보드</h2>", html_eda_btn + "\n        <h2 class=\"section-title\">총괄DB 기준 대시보드</h2>")

# Top 10 Section before treeSummarySection
html_top10 = """
        <div id="top10Section"></div>
"""
content = content.replace("        <div id=\"treeSummarySection\"></div>", html_top10 + "\n        <div id=\"treeSummarySection\"></div>")


with open("app/core/report.py", "w", encoding="utf-8") as f:
    f.write(content)
