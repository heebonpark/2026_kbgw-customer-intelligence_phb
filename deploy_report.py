"""
Publishes the generated (encrypted) HTML report to GitHub Pages.

    python deploy_report.py                       # Data_Intel_PRO_Report.html -> kbgw-report
    python deploy_report.py report.html --repo my-report --public

Needs git and the GitHub CLI (`gh`, logged in with `gh auth login`).

- Refuses to publish a report that isn't encrypted (see core/secure_report.py)
  -- an unencrypted report carries every customer row in plain text.
- The report goes to its own repository, never this (public) source repo.
- Each deploy force-pushes a single commit, so earlier reports don't pile up
  in the repository's history.
- GitHub Pages on a private repository needs a paid plan; on a free account
  pass --public (the page is still unreadable without the password).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

DEFAULT_REPO = "kbgw-report"
ENCRYPTED_MARKER = 'id="encPayload"'


class DeployError(Exception):
    pass


def _run(cmd, cwd=None, check=True):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if check and result.returncode != 0:
        raise DeployError(f"{' '.join(cmd[:3])} 실패: {(result.stderr or result.stdout).strip()[:400]}")
    return result


def deploy(html_path, repo_name=DEFAULT_REPO, public=False, log=print):
    """Returns the published https URL. Raises DeployError with a readable
    reason on any failure."""
    if not os.path.exists(html_path):
        raise DeployError(f"리포트 파일이 없습니다: {html_path}")
    with open(html_path, encoding='utf-8') as f:
        if ENCRYPTED_MARKER not in f.read():
            raise DeployError("암호화되지 않은 리포트는 배포하지 않습니다 (고객 데이터가 그대로 노출됩니다).")
    for tool in ('git', 'gh'):
        if shutil.which(tool) is None:
            raise DeployError(f"'{tool}' 명령을 찾을 수 없습니다. 설치 후 다시 시도하세요.")

    owner = _run(['gh', 'api', 'user', '--jq', '.login']).stdout.strip()
    if not owner:
        raise DeployError("GitHub 로그인이 필요합니다 (gh auth login).")
    full = f"{owner}/{repo_name}"

    if _run(['gh', 'repo', 'view', full], check=False).returncode != 0:
        log(f"배포 저장소 생성: {full} ({'public' if public else 'private'})")
        _run(['gh', 'repo', 'create', full, '--public' if public else '--private',
              '--description', 'Data Intel PRO 암호화 리포트 배포용'])

    workdir = tempfile.mkdtemp(prefix='report_deploy_')
    try:
        shutil.copyfile(html_path, os.path.join(workdir, 'index.html'))
        open(os.path.join(workdir, '.nojekyll'), 'w').close()
        _run(['git', 'init', '-q', '-b', 'main'], cwd=workdir)
        _run(['git', 'add', '-A'], cwd=workdir)
        _run(['git', '-c', 'user.name=report-deploy', '-c', 'user.email=report-deploy@users.noreply.github.com',
              'commit', '-q', '-m', 'Deploy encrypted report'], cwd=workdir)
        log("리포트 업로드 중...")
        _run(['git', 'push', '-q', '--force', f"https://github.com/{full}.git", 'main'], cwd=workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    pages = _run(['gh', 'api', f"repos/{full}/pages"], check=False)
    if pages.returncode != 0:
        log("GitHub Pages 활성화 중...")
        created = _run(['gh', 'api', '-X', 'POST', f"repos/{full}/pages",
                        '-f', 'source[branch]=main', '-f', 'source[path]=/'], check=False)
        if created.returncode != 0:
            msg = (created.stderr or created.stdout).strip()
            if not public:
                raise DeployError(
                    "비공개 저장소에서는 GitHub Pages를 켤 수 없습니다 (유료 플랜 필요). "
                    f"--public 옵션으로 다시 실행하세요 -- 리포트는 암호화되어 있습니다. ({msg[:200]})")
            raise DeployError(f"GitHub Pages 활성화 실패: {msg[:300]}")
        pages = _run(['gh', 'api', f"repos/{full}/pages"], check=False)

    try:
        url = json.loads(pages.stdout).get('html_url')
    except (ValueError, AttributeError):
        url = None
    return url or f"https://{owner}.github.io/{repo_name}/"


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="암호화된 리포트를 GitHub Pages로 배포합니다.")
    parser.add_argument('html', nargs='?', default=os.path.join(base_dir, 'Data_Intel_PRO_Report.html'))
    parser.add_argument('--repo', default=DEFAULT_REPO, help=f"배포 저장소 이름 (기본 {DEFAULT_REPO})")
    parser.add_argument('--public', action='store_true', help="공개 저장소로 배포 (무료 계정의 GitHub Pages)")
    args = parser.parse_args()
    try:
        url = deploy(args.html, args.repo, public=args.public)
    except DeployError as e:
        print(f"배포 실패: {e}")
        sys.exit(1)
    print(f"배포 완료: {url}")
    print("(처음 배포하면 페이지가 열리기까지 1~2분 걸릴 수 있습니다)")


if __name__ == '__main__':
    main()
