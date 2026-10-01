import os
import shutil
import subprocess

import pandas as pd
import pytest

from core import handlers
from core.report import APP_SCRIPT_TEMPLATE, CORE_SCRIPT_TEMPLATE
from core.secure_report import SCOPED_UNLOCK_TEMPLATE, UNLOCK_PAGE_TEMPLATE


def test_status_comes_from_the_activity_column_even_if_another_has_more():
    df = pd.DataFrame({'활동유무': ['처리완료', None, None], '상태': ['접수', '접수', '미접수']})
    assert handlers.detect_status_col(df) == '활동유무'


def test_status_falls_back_to_a_prefixed_then_the_fullest_column():
    assert handlers.detect_status_col(pd.DataFrame({'활동유무(o,x)': ['처리완료', '접수'], '상태': ['접수', None]})) == '활동유무(o,x)'
    assert handlers.detect_status_col(pd.DataFrame({'활동유무(o,x)': ['방문상담', '재계약'], '상태': ['접수', '처리완료']})) == '상태'


def test_status_values_are_normalised():
    assert handlers._normalize_status(' 처리 완료 ') == '처리완료'
    assert handlers._normalize_status('미접수\xa0') == '미접수'


def test_zone_owner_map_joins_names_and_marks_missing_ones():
    df = pd.DataFrame({'구역번호': ['G1', 'G1', 'G2', 3.0], '담당자명': ['김', '이', None, '박']})
    owners = handlers.build_zone_owner_map(df)
    assert owners['G1'] == '김/이' and owners['G2'] == '담당자없음' and owners['3'] == '박'


def _script_of(page):
    return page.split('<script>')[-1].split('</script>')[0]


@pytest.mark.skipif(shutil.which('node') is None, reason='node is not installed')
@pytest.mark.parametrize('name, source', [
    ('report', APP_SCRIPT_TEMPLATE), ('core report', CORE_SCRIPT_TEMPLATE),
    ('unlock page', _script_of(UNLOCK_PAGE_TEMPLATE)), ('scoped unlock page', _script_of(SCOPED_UNLOCK_TEMPLATE)),
])
def test_embedded_javascript_parses(tmp_path, name, source):
    # 스크립트는 파이썬 문자열 안에 있어 역슬래시 하나만 잘못 써도 브라우저에서 전체가 죽는다
    path = tmp_path / 'script.js'
    path.write_text(source.replace('__PASSWORD__', 'x').replace('__ADMIN_PASSWORD__', 'y').replace('__EXPIRY__', '2026-12-31'), encoding='utf-8')
    result = subprocess.run(['node', '--check', str(path)], capture_output=True, text=True)
    assert result.returncode == 0, f"{name}: {result.stderr[:600]}"


@pytest.mark.skipif(shutil.which('node') is None, reason='node is not installed')
def test_web_page_scripts_parse(tmp_path):
    from core.web_app import generate_web_app_html
    import re
    page = generate_web_app_html()
    scripts = [s for s in re.findall(r'<script>(.*?)</script>', page, re.S) if s.strip()]
    assert len(scripts) >= 2
    for i, source in enumerate(scripts):
        path = tmp_path / f'web{i}.js'
        path.write_text(source, encoding='utf-8')
        result = subprocess.run(['node', '--check', str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[:600]
