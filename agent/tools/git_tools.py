"""git 읽기 전용 도구. 저장소 상태를 바꾸는 명령은 두지 않는다."""
import subprocess
from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def head_info(repo: Path) -> str:
    """'<짧은 sha> <커밋 날짜>'"""
    return _git(repo, "log", "-1", "--format=%h %cs").strip()


def recent_commits(repo: Path, days: int, max_chars: int = 15000) -> str:
    """최근 N일 커밋 메시지 + 변경 파일 통계. 길면 잘라서 토큰을 아낀다."""
    out = _git(repo, "log", f"--since={days} days ago", "--stat=120",
               "--format=### %h %cs%n%s%n%b")
    if not out.strip():
        return f"(최근 {days}일 커밋 없음)"
    if len(out) > max_chars:
        out = out[:max_chars] + "\n... (이하 생략)"
    return out


def tracked_files(repo: Path) -> str:
    """추적 중인 파일 목록 (이미지·데이터 파일 제외)."""
    skip = (".png", ".jpg", ".jpeg", ".csv", ".json", ".gpx", ".fit", ".html")
    files = [f for f in _git(repo, "ls-files").splitlines() if not f.lower().endswith(skip)]
    return "\n".join(files)
