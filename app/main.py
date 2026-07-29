import os
import sys

import streamlit as st

# Must be the first Streamlit command
st.set_page_config(
    page_title="Data Intel PRO",
    layout="wide",
    initial_sidebar_state="collapsed"
)

from ui.styles import apply_custom_css
from core.auth import init_app_dir, setup_session_state, render_login, add_log
from core.handlers import load_data, process_and_merge
from core.report import generate_html_report
from core.matching_config import (
    load_matching_config, save_matching_config, default_config,
    MATCHABLE_FILES, FILE_LABELS, DB_KEY_CANDIDATES, FILE_KEY_CANDIDATES,
)
from core.source_files import source_status, save_uploaded_file

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
from generate_report import build_report  # noqa: E402  (path must be set up first)

# Initialize directories and session state
init_app_dir()
setup_session_state()
apply_custom_css()


def render_matching_admin():
    """Admin panel: pick which column(s) each file matches the master DB on.
    Checking more than one condition for a file makes it a composite AND key
    (all checked conditions must agree for a row to match). Admin-only --
    a 'user' role still needs *a* matching_config to process data with, so
    it silently falls back to the persisted config instead of the UI."""
    if 'matching_config' not in st.session_state:
        st.session_state['matching_config'] = load_matching_config()
    config = st.session_state['matching_config']

    if st.session_state.get('user_role') != 'admin':
        return config

    with st.expander("🛠 관리자: 컬럼 매칭 설정 (다중조건)", expanded=False):
        st.caption("체크된 조건이 2개 이상이면 모두 동시에 일치해야 매칭됩니다 (AND). 저장하면 다음 실행에도 재사용됩니다.")

        for key in MATCHABLE_FILES:
            st.markdown(f"**{FILE_LABELS[key]}**")
            conditions = config.get(key, [])
            remove_idx = None

            for i, cond in enumerate(conditions):
                c1, c2, c3, c4 = st.columns([1, 3, 3, 1])
                cond['enabled'] = c1.checkbox(
                    "사용", value=cond.get('enabled', True), key=f"{key}_en_{i}", label_visibility="collapsed",
                )
                db_options = DB_KEY_CANDIDATES
                db_idx = db_options.index(cond['db_col']) if cond.get('db_col') in db_options else 0
                cond['db_col'] = c2.selectbox(
                    "총괄DB 컬럼", db_options, index=db_idx, key=f"{key}_db_{i}", label_visibility="collapsed",
                )
                file_options = FILE_KEY_CANDIDATES[key]
                file_idx = file_options.index(cond['file_col']) if cond.get('file_col') in file_options else 0
                cond['file_col'] = c3.selectbox(
                    f"{FILE_LABELS[key]} 컬럼", file_options, index=file_idx, key=f"{key}_file_{i}", label_visibility="collapsed",
                )
                if c4.button("삭제", key=f"{key}_del_{i}"):
                    remove_idx = i

            if remove_idx is not None:
                conditions.pop(remove_idx)
                st.rerun()

            if st.button(f"+ 조건 추가", key=f"{key}_add"):
                conditions.append({
                    'db_col': DB_KEY_CANDIDATES[0],
                    'file_col': FILE_KEY_CANDIDATES[key][0],
                    'enabled': True,
                })
                st.rerun()

            config[key] = conditions
            st.divider()

        col_a, col_b = st.columns(2)
        if col_a.button("💾 매칭 설정 저장 (재사용)"):
            save_matching_config(config)
            st.success("저장되었습니다. 다음 실행 시 자동으로 불러옵니다.")
        if col_b.button("↺ 기본값으로 초기화"):
            st.session_state['matching_config'] = default_config()
            st.rerun()

    return config


def render_admin_file_update():
    """Admin-only: replace a source file on disk (with automatic backup) and
    optionally regenerate the report straight from whatever's now on disk.
    Requires login as the 'admin' role -- see core/auth.py."""
    if st.session_state.get('user_role') != 'admin':
        return

    with st.expander("🗂 관리자: 원본 파일 업데이트 (디스크에 저장)", expanded=False):
        st.caption(
            "여기서 올린 파일은 프로젝트 폴더의 원본 파일을 실제로 교체합니다 (이전 파일은 backups/ 폴더에 자동 백업). "
            "교체 후 아래 '디스크의 최신 파일로 리포트 재생성'을 누르면 새 파일이 바로 반영됩니다."
        )

        for row in source_status(PROJECT_ROOT):
            c1, c2, c3 = st.columns([2, 2, 3])
            required_tag = " `필수`" if row['required'] else ""
            c1.markdown(f"**{row['label']}**{required_tag}")
            if row['exists']:
                c2.caption(row['filename'])
                c3.caption("마지막 업데이트: " + row['modified'].strftime('%Y-%m-%d %H:%M'))
            else:
                c2.caption("파일 없음")
                c3.caption("-")

            uploaded = st.file_uploader(
                f"{row['label']} 파일 교체", type=['xlsx', 'xls', 'csv'],
                key=f"replace_{row['key']}", label_visibility="collapsed",
            )
            if uploaded is not None and st.button(f"✅ 이 파일로 교체 확정 -- {row['label']}", key=f"confirm_{row['key']}"):
                try:
                    target_path, backup_path = save_uploaded_file(PROJECT_ROOT, row['key'], uploaded)
                except ValueError as e:
                    st.error(str(e))
                    st.stop()
                add_log(f"file_update:{row['key']}:{os.path.basename(target_path)}", st.session_state['username'])
                if backup_path:
                    st.success(f"업데이트됨: {os.path.basename(target_path)} (이전 파일은 backups/{os.path.basename(backup_path)}로 백업됨)")
                else:
                    st.success(f"업데이트됨: {os.path.basename(target_path)} (신규 파일)")
                st.rerun()
            st.divider()

        if st.button("📊 디스크의 최신 파일로 리포트 재생성", type="primary"):
            with st.spinner("디스크의 파일을 읽어 병합하고 리포트를 재생성하는 중..."):
                html_content, pwd, expiry, msg, merged_df, admin_pwd = build_report(PROJECT_ROOT)
            if html_content is None:
                st.error(f"재생성 실패: {msg}")
            else:
                output_path = os.path.join(PROJECT_ROOT, "Data_Intel_PRO_Report.html")
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(html_content)
                add_log("regenerate_report_from_disk", st.session_state['username'])
                st.success(f"재생성 완료: {msg}")
                st.info(f"조회용 비밀번호: **{pwd}** · 관리자 비밀번호(매칭설정 포함): **{admin_pwd}** · 만료일: **{expiry}** · 저장 위치: `{output_path}`")
                st.download_button(
                    "재생성된 리포트 다운로드", data=html_content,
                    file_name="Data_Intel_PRO_Report.html", mime="text/html", key="download_regenerated",
                )


def render_dashboard():
    st.markdown("<h1>Data Intel PRO Dashboard</h1>", unsafe_allow_html=True)

    render_admin_file_update()
    matching_config = render_matching_admin()

    col1, col2 = st.columns([3, 1])

    with col1:
        st.markdown("### 데이터 업로드")

        db_file = st.file_uploader("1. 총괄관리DB (Excel)", type=['xlsx', 'xls'])
        voc_file = st.file_uploader("2. 월/일일 SP관리활동 (VOC정보조회) (Excel/CSV)", type=['xlsx', 'xls', 'csv'])
        patrol_file = st.file_uploader("3. 월/일일 SE,SG 정기점검 (Excel/CSV)", type=['xlsx', 'xls', 'csv'])
        original_file = st.file_uploader("4. 2026년 관리고객원본 (Excel/CSV)", type=['xlsx', 'xls', 'csv'])
        facility_file = st.file_uploader("5. 시설현황 (Excel/CSV)", type=['xlsx', 'xls', 'csv'])
        st.markdown("---")
        st.caption("아래 두 파일은 총괄DB와 매칭하지 않고, 리포트 내 별도 섹션으로 독립 표시됩니다.")
        cancel_file = st.file_uploader("6. 해지 파이프라인 (Excel/CSV) - 독립 섹션", type=['xlsx', 'xls', 'csv'])
        cancelled_facility_file = st.file_uploader(
            "7. 해지시설 내역 (Excel/CSV) - 고액 미등록 알림용 [확장]", type=['xlsx', 'xls', 'csv'],
        )

        process_btn = st.button("데이터 병합 및 처리")

    with col2:
        st.markdown("### 관리 옵션")
        st.write(f"접속자: **{st.session_state['username']}** ({st.session_state['user_role']})")
        if st.button("로그아웃"):
            st.session_state["authenticated"] = False
            add_log("logout", st.session_state["username"])
            st.rerun()

    if process_btn:
        if db_file is None:
            st.error("총괄관리DB 파일은 필수입니다.")
            return

        with st.spinner("데이터 처리 중..."):
            files_dict = {
                'db': load_data(db_file),
                'voc': load_data(voc_file, voc_file.name.endswith('.csv')) if voc_file else None,
                'patrol': load_data(patrol_file, patrol_file.name.endswith('.csv')) if patrol_file else None,
                'original': load_data(original_file, original_file.name.endswith('.csv')) if original_file else None,
                'facility': load_data(facility_file, facility_file.name.endswith('.csv')) if facility_file else None,
            }
            cancel_df = load_data(cancel_file, cancel_file.name.endswith('.csv')) if cancel_file else None
            cancelled_facility_df = (
                load_data(cancelled_facility_file, cancelled_facility_file.name.endswith('.csv'))
                if cancelled_facility_file else None
            )

            merged_df, msg, match_report = process_and_merge(files_dict, matching_config=matching_config)

            if merged_df is not None:
                st.success(msg)
                st.session_state['merged_df'] = merged_df
                st.session_state['raw_files'] = files_dict
                st.session_state['raw_voc_df'] = files_dict.get('voc')
                st.session_state['raw_patrol_df'] = files_dict.get('patrol')
                st.session_state['raw_cancel_df'] = cancel_df
                st.session_state['raw_cancelled_facility_df'] = cancelled_facility_df
                st.session_state['match_report'] = match_report
                st.session_state['used_matching_config'] = matching_config
            else:
                st.error(msg)

    if 'merged_df' in st.session_state:
        st.markdown("### 처리 결과 (총괄DB 기준)")

        with st.expander("매칭 적용 현황", expanded=False):
            for key, used in st.session_state.get('match_report', {}).items():
                label = FILE_LABELS.get(key, key)
                if used:
                    cond_txt = " AND ".join(f"{c['db_col']}={c['file_col']}" for c in used)
                    st.write(f"✅ {label}: `{cond_txt}`")
                else:
                    st.write(f"⚪ {label}: 매칭 조건 미적용 (파일 없음 또는 조건 비활성화)")

        df = st.session_state['merged_df']

        if '관리본부' in df.columns:
            hqs = ["전체"] + [h for h in df['관리본부'].dropna().unique()]
            selected_hq = st.selectbox("본부 선택", hqs)
            if selected_hq != "전체":
                df = df[df['관리본부'] == selected_hq]

        if '관리지사' in df.columns:
            branches = ["전체"] + [b for b in df['관리지사'].dropna().unique()]
            selected_branch = st.selectbox("지사 선택", branches)
            if selected_branch != "전체":
                df = df[df['관리지사'] == selected_branch]

        st.dataframe(df, use_container_width=True)

        cancel_df = st.session_state.get('raw_cancel_df')
        if cancel_df is not None:
            st.markdown("### 해지 파이프라인 (독립 데이터, 전사 기준)")
            st.dataframe(cancel_df, use_container_width=True)

        st.markdown("### EDA 분석 (인터랙티브 시각화)")
        with st.expander("📊 PyGWalker EDA 열기", expanded=False):
            st.caption("아래 'EDA 시작' 버튼을 누르면 데이터를 자유롭게 탐색하고 차트를 생성할 수 있는 태블로(Tableau) 스타일의 화면이 열립니다. 데이터 양에 따라 초기 로딩에 수 초가 걸릴 수 있습니다.")
            if st.button("🚀 EDA 시작하기", key="start_eda"):
                import pygwalker as pyg
                # Extract Streamlit renderer
                from pygwalker.api.streamlit import StreamlitRenderer
                
                with st.spinner("EDA 환경을 구성하는 중입니다..."):
                    # Render pygwalker using the new component approach
                    walker = StreamlitRenderer(df, spec="")
                    walker.explorer()

        html_content, pwd, expiry, admin_pwd = generate_html_report(
            df,
            voc_df=st.session_state.get('raw_voc_df'),
            patrol_df=st.session_state.get('raw_patrol_df'),
            cancel_df=cancel_df,
            cancelled_facility_df=st.session_state.get('raw_cancelled_facility_df'),
            raw_files=st.session_state.get('raw_files'),
            matching_config=st.session_state.get('used_matching_config'),
        )

        st.markdown("### HTML 리포트 생성")
        st.info(f"조회용 비밀번호: **{pwd}** (일반 공유용 -- 매칭설정 패널은 보이지 않습니다)")
        st.info(f"관리자 비밀번호: **{admin_pwd}** (이 비밀번호로 열면 매칭설정 패널까지 접근 가능합니다)")
        st.info(f"리포트 만료일: **{expiry}**")

        st.download_button(
            label="보안 HTML 리포트 다운로드",
            data=html_content,
            file_name="Data_Intel_PRO_Report.html",
            mime="text/html"
        )


def main():
    if not st.session_state["authenticated"]:
        render_login()
    else:
        render_dashboard()


if __name__ == "__main__":
    main()
