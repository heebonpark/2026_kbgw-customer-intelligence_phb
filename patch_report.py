import re

with open("app/core/report.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Inject CSS
css_code = """
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
"""
content = content.replace("/* ---- progress matrix (지사 x SP/SE/SG) ---- */", css_code + "\n/* ---- progress matrix (지사 x SP/SE/SG) ---- */")

# 2. Inject JS Logic
js_code = """
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
                const ra = branchRank.has(a) ? branchRank.get(a) : 999;
                const rb = branchRank.has(b) ? branchRank.get(b) : 999;
                return ra - rb || a.localeCompare(b);
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
"""
content = content.replace("    // ---- progress matrix (지사 x SP/SE/SG) -- mirrors analytics.py build_progress_matrix / report.py render_progress_matrix ----",
                          js_code + "\n    // ---- progress matrix (지사 x SP/SE/SG) -- mirrors analytics.py build_progress_matrix / report.py render_progress_matrix ----")

# 3. Inject call in renderAll
call_code = """
        const treeSummarySection = document.getElementById('treeSummarySection');
        if (treeSummarySection) renderTreeTableEl(treeSummarySection, filtered);
"""
content = content.replace("const progressSection = document.getElementById('progressSection');",
                          call_code + "\n        const progressSection = document.getElementById('progressSection');")

# 4. Inject HTML Placeholder
html_code = """
        <div id="treeSummarySection"></div>
"""
content = content.replace("        <h2 class=\"section-title\">관리고객 상세 (필터/검색 가능)</h2>",
                          html_code + "\n        <h2 class=\"section-title\">관리고객 상세 (필터/검색 가능)</h2>")

with open("app/core/report.py", "w", encoding="utf-8") as f:
    f.write(content)
