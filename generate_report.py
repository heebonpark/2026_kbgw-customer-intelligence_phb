import os
import sys

# Windows terminals often default to the cp949 code page, which can't encode
# characters like \xa0 that show up in pandas/openpyxl warning text -- without
# this, print()ing such a message crashes with UnicodeEncodeError instead of
# showing the actual message.
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

# Add the app directory to the path so we can import core modules
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from core.handlers import process_and_merge, load_data
from core.report import generate_html_report, generate_core_report
from core.matching_config import load_matching_config
from core.source_files import find_source_path, is_csv_path, SOURCE_FILES


def _load(base_dir, key):
    path = find_source_path(base_dir, key)
    if path is None:
        return None
    return load_data(path, is_csv=is_csv_path(path))


def build_report(base_dir, matching_config=None, password=None, expiry_date=None, eda_link=False,
                 admin_password=None):
    """Reads whatever source files are currently on disk (by their canonical
    stem -- see core/source_files.py) and builds the report. Shared by the
    CLI entry point below and the Streamlit admin '디스크의 최신 파일로
    리포트 재생성' button, so both always take the exact same path.

    Returns (html_content, password, expiry_date, message, merged_df,
    admin_password). html_content is None on failure -- check message.
    """
    db_df = _load(base_dir, 'db')
    if db_df is None:
        return None, None, None, f"총괄DB 파일을 찾을 수 없습니다 ({SOURCE_FILES['db']['stem']}.*)", None, None

    files_dict = {
        'db': db_df,
        'voc': _load(base_dir, 'voc'),
        'patrol': _load(base_dir, 'patrol'),
        'original': _load(base_dir, 'original'),
        'facility': _load(base_dir, 'facility'),
        'zone_owner': _load(base_dir, 'zone_owner'),
    }
    # 해지 파이프라인: 총괄DB와 매칭하지 않는 독립 데이터.
    # 해지시설내역: 독립 섹션(고액 미등록 알림)이면서, '일반해지'는 활동유무에 처리완료로 역반영된다.
    cancel_df = _load(base_dir, 'cancel')
    cancelled_facility_df = _load(base_dir, 'cancelled_facility')
    files_dict['cancelled_facility'] = cancelled_facility_df

    if matching_config is None:
        matching_config = load_matching_config()

    merged_df, msg, match_report = process_and_merge(files_dict, matching_config=matching_config)
    if merged_df is None:
        return None, None, None, msg, None, None

    match_lines = []
    for key, used in match_report.items():
        status = " AND ".join(f"{c['db_col']}={c['file_col']}" for c in used) if used else "미적용"
        match_lines.append(f"{key}: {status}")

    html_content, pwd, expiry, admin_pwd = generate_html_report(
        merged_df,
        voc_df=files_dict.get('voc'),
        patrol_df=files_dict.get('patrol'),
        cancel_df=cancel_df,
        cancelled_facility_df=cancelled_facility_df,
        raw_files=files_dict,
        matching_config=matching_config,
        password=password,
        expiry_date=expiry_date,
        admin_password=admin_password,
        eda_link=eda_link,
        core_df=_load(base_dir, 'core'),          # 9. 코어고객 (독립 섹션, 지도 좌표는 이 PC의 카카오 키)
        core_voc_df=_load(base_dir, 'core_voc'),
    )
    full_msg = msg + " (" + ", ".join(match_lines) + ")"
    return html_content, pwd, expiry, full_msg, merged_df, admin_pwd


def build_core_report(base_dir, password=None, expiry_date=None, admin_password=None):
    """9. 코어고객(+ 9-1 VOC매칭) 파일만으로 코어고객 활동현황 리포트를 만든다 (총괄DB 불필요).
    Returns (html, password, expiry_date, admin_password, message); html is None on failure."""
    core_df = _load(base_dir, 'core')
    if core_df is None:
        return None, None, None, None, f"코어고객 파일을 찾을 수 없습니다 ({SOURCE_FILES['core']['stem']}.*)"
    try:
        html_content, pwd, expiry, admin_pwd, count = generate_core_report(
            core_df, _load(base_dir, 'core_voc'), password=password, admin_password=admin_password, expiry_date=expiry_date)
    except ValueError as e:
        return None, None, None, None, str(e)
    return html_content, pwd, expiry, admin_pwd, f"코어고객 {count:,}곳"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Data Intel PRO Report Generator")
    parser.add_argument("--password", type=str, help="사용자 조회용 비밀번호 (지정하지 않으면 랜덤 생성)", default=None)
    parser.add_argument("--admin-password", type=str, default=None,
                        help="관리자 비밀번호 (지정하지 않으면 이 PC에 저장된 값, 없으면 랜덤)")
    parser.add_argument("--expiry", type=str, help="리포트 만료일 (YYYY-MM-DD, 지정하지 않으면 월말)", default=None)
    parser.add_argument("--core-only", action="store_true",
                        help="9. 코어고객(+9-1) 파일만으로 코어고객 활동현황 리포트 생성 (총괄DB 불필요)")
    args = parser.parse_args()

    base_dir = "/Users/heebonpark/Downloads/관리고객통합솔루션"

    if args.core_only:
        html_content, pwd, expiry, admin_pwd, msg = build_core_report(
            base_dir, password=args.password, expiry_date=args.expiry, admin_password=args.admin_password)
        if html_content is None:
            print(f"오류 발생: {msg}")
            return
        output_path = os.path.join(base_dir, "Core_Customer_Report.html")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"코어고객 리포트 생성 완료 ({msg})")
        print(f"저장 위치: {output_path}")
        print(f"리포트 만료일: {expiry}")
        print(f"리포트 비밀번호: {pwd}")
        return

    print("파일 로딩 및 병합 중...")
    html_content, pwd, expiry, msg, merged_df, admin_pwd = build_report(
        base_dir, 
        password=args.password, 
        expiry_date=args.expiry,
        admin_password=args.admin_password,
        eda_link=True,  # this CLI writes Data_Intel_PRO_EDA.html right after, below
    )

    if html_content is None:
        print(f"오류 발생: {msg}")
        return
    print(msg)

    output_path = os.path.join(base_dir, "Data_Intel_PRO_Report.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"\n=========================================")
    print(f"성공적으로 HTML 리포트가 생성되었습니다!")
    print(f"저장 위치: {output_path}")
    print(f"리포트 만료일: {expiry}")
    print(f"리포트 비밀번호(조회용): {pwd}")
    print(f"관리자 비밀번호(매칭설정 포함): {admin_pwd}")
    print(f"=========================================\n")

    print("EDA 리포트 생성 중 (데이터 용량에 따라 10~30초 소요됩니다)...")
    try:
        import pygwalker as pyg
        # NA값을 빈 문자열로 채워 pygwalker 내부 JSON 변환 에러 방지
        safe_df = merged_df.fillna("") 
        eda_html = pyg.to_html(safe_df, spec="")
        eda_output_path = os.path.join(base_dir, "Data_Intel_PRO_EDA.html")
        with open(eda_output_path, "w", encoding="utf-8") as f:
            f.write(eda_html)
        print(f"=========================================")
        print(f"성공적으로 EDA 리포트가 생성되었습니다!")
        print(f"저장 위치: {eda_output_path}")
        print(f"=========================================\n")
    except Exception as e:
        print(f"EDA 리포트 생성 실패: {e}")



if __name__ == "__main__":
    main()
