"""
Publishes the generated (encrypted) HTML report to GitHub Pages.

    python deploy_report.py                       # Data_Intel_PRO_Report.html -> kbgw-report
    python deploy_report.py report.html --repo my-report --public
    python deploy_report.py Core_Customer_Report.html --repo kbgw-core-report --public   # 코어고객 리포트

Needs git. With the GitHub CLI (`gh`, logged in with `gh auth login`) it can
also create the deploy repository and switch GitHub Pages on. Without `gh` it
still updates a repository that already exists (plain `git push`, signing in
through Git's own credential window) -- enough for every deploy after the
first one, e.g. on a second PC.

- Refuses to publish a report that isn't encrypted (see core/secure_report.py)
  -- an unencrypted report carries every customer row in plain text.
- The report goes to its own repository, never this (public) source repo.
- Each deploy force-pushes a single commit, so earlier reports don't pile up
  in the repository's history.
- The report goes under a random folder (https://<you>.github.io/<repo>/<10 random
  characters>/) so the link can't be guessed; the short address shows an empty
  page. The same folder is reused on later deploys (looked up in the deploy
  repository itself, so every PC keeps the same link) unless a new link is
  asked for. The repository is public, so this hides the link from guessing,
  not from someone who browses the repository -- the encryption is what
  protects the data.
- GitHub Pages on a private repository needs a paid plan; on a free account
  pass --public (the page is still unreadable without the password).
"""

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile

DEFAULT_REPO = "kbgw-report"
CORE_REPO = "kbgw-core-report"  # 코어고객 전용 리포트 -- 종합 리포트(kbgw-report)를 덮어쓰지 않게 따로
ENCRYPTED_MARKER = 'id="encPayload"'


SETTINGS_PATH = os.path.join(os.path.expanduser("~/.dataintelligence_pro"), "report_settings.json")


class DeployError(Exception):
    pass


class OwnerNeeded(DeployError):
    """gh도 없고 git 저장소도 아니라(zip으로 받은 폴더) 어느 GitHub 계정으로 올릴지 모를 때.
    GUI는 이때 계정 이름을 물어 save_github_owner()로 저장한 뒤 다시 배포한다."""


def load_github_owner():
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            return json.load(f).get('github_owner') or None
    except (FileNotFoundError, ValueError, OSError):
        return None


def save_github_owner(owner):
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            settings = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        settings = {}
    settings['github_owner'] = owner or None
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def _run(cmd, cwd=None, check=True):
    # GIT_TERMINAL_PROMPT=0: 로그인 정보가 없을 때 보이지 않는 콘솔에서 입력을 기다리며 멈추지 않게 (로그인 창은 그대로 뜬다)
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                            env=dict(os.environ, GIT_TERMINAL_PROMPT='0'))
    if check and result.returncode != 0:
        raise DeployError(f"{' '.join(cmd[:3])} 실패: {(result.stderr or result.stdout).strip()[:400]}")
    return result


GH_INSTALL_HINT = ("GitHub CLI(gh)를 설치하면 저장소 생성까지 자동으로 됩니다: 명령 프롬프트에서 "
                   "'winget install --id GitHub.cli' 실행 후 'gh auth login' (맥은 'brew install gh').")


def _owner_from_source_repo():
    """gh 없이 배포할 때 GitHub 계정 이름: 이 프로그램을 내려받은 저장소(origin) 주소에서 읽는다."""
    here = os.path.dirname(os.path.abspath(__file__))
    result = _run(['git', '-C', here, 'remote', 'get-url', 'origin'], check=False)
    match = re.search(r'github\.com[:/]([^/\s]+)/', result.stdout or '')
    return match.group(1) if match else None


SLUG_RE = re.compile(r'^[a-z0-9]{10}$')
SLUG_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'  # 헷갈리는 글자(0/o, 1/l/i) 제외
# 짧은 주소(저장소 첫 화면)에 두는 빈 페이지 -- 리포트가 여기 있다는 것도 알리지 않는다
PLACEHOLDER_PAGE = ('<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">'
                    '<meta name="robots" content="noindex, nofollow"><title>Not Found</title></head>'
                    '<body></body></html>')


def new_slug():
    return ''.join(secrets.choice(SLUG_ALPHABET) for _ in range(10))


def _remote_slug(full):
    """배포 저장소에 이미 있는 무작위 폴더 이름 (없으면 None) -- 어느 PC에서 배포해도 같은 링크를 유지한다."""
    tmp = tempfile.mkdtemp(prefix='report_slug_')
    try:
        if _run(['git', 'clone', '--depth', '1', '-q', f"https://github.com/{full}.git", tmp], check=False).returncode != 0:
            return None
        for name in sorted(os.listdir(tmp)):
            if SLUG_RE.match(name) and os.path.isfile(os.path.join(tmp, name, 'index.html')):
                return name
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _pick_slug(full, hidden_link, new_link, log):
    if not hidden_link:
        return None
    slug = None if new_link else _remote_slug(full)
    if slug:
        log("기존 링크를 그대로 씁니다 (내용만 새 리포트로 바뀝니다)")
        return slug
    log("새 링크를 만듭니다 -- 이전 링크는 더 이상 열리지 않습니다")
    return new_slug()


def _push_single_commit(html_path, full, log, slug=None):
    """리포트 한 파일을 단일 커밋으로 강제 푸시 (이전 리포트를 기록에 남기지 않는다).
    slug가 있으면 <slug>/index.html 에 두고 첫 화면은 빈 페이지, 없으면 첫 화면이 리포트."""
    workdir = tempfile.mkdtemp(prefix='report_deploy_')
    try:
        if slug:
            os.makedirs(os.path.join(workdir, slug))
            shutil.copyfile(html_path, os.path.join(workdir, slug, 'index.html'))
            with open(os.path.join(workdir, 'index.html'), 'w', encoding='utf-8') as f:
                f.write(PLACEHOLDER_PAGE)
        else:
            shutil.copyfile(html_path, os.path.join(workdir, 'index.html'))
        open(os.path.join(workdir, '.nojekyll'), 'w').close()
        _run(['git', 'init', '-q', '-b', 'main'], cwd=workdir)
        _run(['git', 'add', '-A'], cwd=workdir)
        _run(['git', '-c', 'user.name=report-deploy', '-c', 'user.email=report-deploy@users.noreply.github.com',
              'commit', '-q', '-m', 'Deploy encrypted report'], cwd=workdir)
        log("리포트 업로드 중...")
        return _run(['git', 'push', '-q', '--force', f"https://github.com/{full}.git", 'main'], cwd=workdir, check=False)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _deploy_without_gh(html_path, repo_name, log, hidden_link=True, new_link=False):
    # 계정: 이 PC에 저장해 둔 이름 -> 이 폴더를 받은 저장소(origin)
    owner = load_github_owner() or _owner_from_source_repo()
    if not owner:
        raise OwnerNeeded("배포할 GitHub 계정 이름이 필요합니다 (gh가 없고, 이 폴더가 zip으로 받은 폴더라 계정을 알 수 없음).")
    if not re.fullmatch(r'[A-Za-z0-9-]{1,39}', owner):
        raise OwnerNeeded(f"GitHub 계정 이름이 올바르지 않습니다: {owner}")
    full = f"{owner}/{repo_name}"
    log(f"gh 없이 배포합니다: {full} (처음이면 GitHub 로그인 창이 뜹니다)")
    slug = _pick_slug(full, hidden_link, new_link, log)
    pushed = _push_single_commit(html_path, full, log, slug)
    if pushed.returncode != 0:
        msg = (pushed.stderr or pushed.stdout).strip()
        if 'not found' in msg.lower():
            raise DeployError(f"배포 저장소 {full} 를 찾을 수 없습니다 (계정 이름이 맞는지, 로그인한 계정에 권한이 있는지 확인). "
                              "저장소를 처음 만들 때는 gh가 필요합니다. " + GH_INSTALL_HINT)
        raise DeployError(f"업로드 실패 (GitHub 로그인·권한 확인): {msg[:300]}  " + GH_INSTALL_HINT)
    return f"https://{owner.lower()}.github.io/{repo_name}/" + (f"{slug}/" if slug else "")


def deploy(html_path, repo_name=DEFAULT_REPO, public=False, log=print, description='Data Intel PRO 암호화 리포트 배포용',
           hidden_link=True, new_link=False):
    """Returns the published https URL. Raises DeployError with a readable
    reason on any failure.

    hidden_link: publish under a random folder so the link can't be guessed
        (the same folder is kept across deploys). new_link: replace that
        folder with a fresh one -- the previous link stops working."""
    if not os.path.exists(html_path):
        raise DeployError(f"리포트 파일이 없습니다: {html_path}")
    with open(html_path, encoding='utf-8') as f:
        if ENCRYPTED_MARKER not in f.read():
            raise DeployError("암호화되지 않은 리포트는 배포하지 않습니다 (고객 데이터가 그대로 노출됩니다).")
    if shutil.which('git') is None:
        raise DeployError("'git' 명령을 찾을 수 없습니다. Git을 설치한 뒤 다시 시도하세요 (https://git-scm.com/download/win).")
    if shutil.which('gh') is None:
        return _deploy_without_gh(html_path, repo_name, log, hidden_link, new_link)

    owner = _run(['gh', 'api', 'user', '--jq', '.login']).stdout.strip()
    if not owner:
        raise DeployError("GitHub 로그인이 필요합니다 (gh auth login).")
    full = f"{owner}/{repo_name}"

    if _run(['gh', 'repo', 'view', full], check=False).returncode != 0:
        log(f"배포 저장소 생성: {full} ({'public' if public else 'private'})")
        _run(['gh', 'repo', 'create', full, '--public' if public else '--private',
              '--description', description])

    slug = _pick_slug(full, hidden_link, new_link, log)
    pushed = _push_single_commit(html_path, full, log, slug)
    if pushed.returncode != 0:
        raise DeployError(f"git push 실패: {(pushed.stderr or pushed.stdout).strip()[:400]}")

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
    base = url or f"https://{owner}.github.io/{repo_name}/"
    return base.rstrip('/') + '/' + (f"{slug}/" if slug else "")


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="암호화된 리포트를 GitHub Pages로 배포합니다.")
    parser.add_argument('html', nargs='?', default=os.path.join(base_dir, 'Data_Intel_PRO_Report.html'))
    parser.add_argument('--repo', default=DEFAULT_REPO, help=f"배포 저장소 이름 (기본 {DEFAULT_REPO})")
    parser.add_argument('--public', action='store_true', help="공개 저장소로 배포 (무료 계정의 GitHub Pages)")
    parser.add_argument('--plain-link', action='store_true', help="무작위 폴더 없이 저장소 첫 화면에 배포 (예전 방식)")
    parser.add_argument('--new-link', action='store_true', help="링크를 새로 만든다 (이전 링크는 닫힘)")
    args = parser.parse_args()
    try:
        url = deploy(args.html, args.repo, public=args.public, hidden_link=not args.plain_link, new_link=args.new_link)
    except DeployError as e:
        print(f"배포 실패: {e}")
        sys.exit(1)
    print(f"배포 완료: {url}")
    print("(처음 배포하면 페이지가 열리기까지 1~2분 걸릴 수 있습니다)")


if __name__ == '__main__':
    main()
