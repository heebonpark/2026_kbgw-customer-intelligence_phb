"""
Builds the web version (docs/index.html) served by GitHub Pages:

    python build_web_app.py

Re-run after changing the report code (app/core/report.py / web_app.py) and
commit docs/index.html -- Pages serves that file straight from the main
branch. The page embeds no customer data (files are picked and processed in
the viewer's own browser), so it's safe in this public repository.
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(BASE_DIR, 'app'))

from core.web_app import generate_web_app_html  # noqa: E402


def main():
    out_dir = os.path.join(BASE_DIR, 'docs')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, 'index.html')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(generate_web_app_html())
    open(os.path.join(out_dir, '.nojekyll'), 'w').close()
    print(f"웹 버전 생성 완료: {out_path}")


if __name__ == '__main__':
    main()
