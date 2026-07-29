import re

with open("generate_report.py", "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace("return html_content, pwd, expiry, full_msg", "return html_content, pwd, expiry, full_msg, merged_df")
content = content.replace("html_content, pwd, expiry, msg = build_report(base_dir)", "html_content, pwd, expiry, msg, merged_df = build_report(base_dir)")

eda_code = """
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
"""

content = content.replace("print(f\"=========================================\\n\")", "print(f\"=========================================\\n\")\n" + eda_code)

with open("generate_report.py", "w", encoding="utf-8") as f:
    f.write(content)
