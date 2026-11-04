"""Find where a stored key appears on this machine, without ever printing it.

Reports path, line, whether the file is tracked by git, whether the key is in
git history, and a risk level. Also flags other LLM keys by provider pattern.
"""
import math
import os
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from .security import fingerprint, mask

SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build",
             ".next", "target", "Library", ".cache", ".Trash"}
MAX_BYTES = 2_000_000
MAX_FILES = 50_000
PATTERNS = {
    "groq": r"gsk_[A-Za-z0-9]{52}",
    "xai": r"xai-[A-Za-z0-9]{80}",
    "openrouter": r"sk-or-v1-[a-f0-9]{64}",
    "anthropic": r"sk-ant-(?:api03|admin01)-[A-Za-z0-9_\-]{90,}",
    "openai": r"sk-(?:proj|svcacct|admin)-[A-Za-z0-9_\-]{20,}",
    "google": r"AIza[0-9A-Za-z_\-]{35}",
}
_RX = {k: re.compile(v) for k, v in PATTERNS.items()}
SPECIAL = ["~/.zsh_history", "~/.bash_history", "~/.config/fish/fish_history"]


def _entropy(s: str) -> float:
    counts = Counter(s)
    return -sum(c / len(s) * math.log2(c / len(s)) for c in counts.values())


def _walk(root: Path):
    stack, seen = [root], 0
    while stack and seen < MAX_FILES:
        try:
            entries = list(os.scandir(stack.pop()))
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            continue
        for e in entries:
            try:
                if e.is_symlink():
                    continue
                if e.is_dir():
                    if e.name not in SKIP_DIRS:
                        stack.append(e.path)
                elif e.is_file() and e.stat().st_size <= MAX_BYTES:
                    seen += 1
                    yield Path(e.path)
            except OSError:
                continue


def _repo_root(path: Path) -> Path | None:
    for parent in [path.parent, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _git(repo: Path, *args, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], input=stdin, capture_output=True,
                          text=True, timeout=30)


def _history_commits(repo: Path, secret: str) -> list[str]:
    """Commits whose diff contains the secret. The secret goes via a temp file, not argv."""
    with tempfile.NamedTemporaryFile("w", delete=False) as fh:
        fh.write(secret)
        tmp = fh.name
    try:
        revs = _git(repo, "rev-list", "--all", "--max-count=500").stdout.split()
        hits = []
        for rev in revs:
            r = _git(repo, "grep", "-l", "-F", "-f", tmp, rev)
            if r.returncode == 0:
                hits.append(rev[:10])
                if len(hits) >= 5:
                    break
        return hits
    finally:
        os.unlink(tmp)


def _classify(path: Path) -> str:
    name = path.name
    if name.startswith(".env") or name.endswith(".envrc"):
        return "env"
    if "history" in name:
        return "shell_history"
    if ".github" in path.parts or name in (".gitlab-ci.yml", "Jenkinsfile"):
        return "ci"
    if name.endswith(".ipynb"):
        return "notebook"
    return "source"


def scan(root: str, stored_keys: dict[str, str]) -> dict:
    """stored_keys: {fingerprint: raw_key}. Returns findings with masked keys only."""
    base = Path(os.path.expanduser(root)).resolve()
    targets = list(_walk(base)) if base.is_dir() else [base]
    targets += [Path(os.path.expanduser(p)) for p in SPECIAL if Path(os.path.expanduser(p)).exists()]
    findings, files = [], 0
    repo_cache: dict[Path, dict] = {}

    for path in targets:
        try:
            with open(path, "rb") as fh:
                head = fh.read(8192)
                if b"\0" in head:
                    continue
                buf = (head + fh.read()).decode("utf-8", "ignore")
        except OSError:
            continue
        files += 1
        if not any(k in buf for k in stored_keys.values()) and not any(r.search(buf) for r in _RX.values()):
            continue
        for ln, line in enumerate(buf.splitlines(), 1):
            for prov, rx in _RX.items():
                for m in rx.finditer(line):
                    secret = m.group(0)
                    if _entropy(secret) < 3.5:
                        continue
                    fp = fingerprint(secret)
                    findings.append(_finding(path, ln, prov, secret, fp in stored_keys, repo_cache))
    findings.sort(key=lambda f: ["critical", "high", "medium", "low"].index(f["risk"]))
    return {"root": str(base), "files_scanned": files, "findings": findings[:200]}


def _finding(path: Path, line: int, provider: str, secret: str, mine: bool, cache: dict) -> dict:
    repo = _repo_root(path)
    tracked, history, remote = False, [], None
    if repo:
        if repo not in cache:
            remote = _git(repo, "remote", "get-url", "origin").stdout.strip() or None
            cache[repo] = {"remote": remote, "hist": {}}
        remote = cache[repo]["remote"]
        rel = str(path.relative_to(repo))
        tracked = _git(repo, "ls-files", "--error-unmatch", rel).returncode == 0
        fp = fingerprint(secret)
        if fp not in cache[repo]["hist"]:
            cache[repo]["hist"][fp] = _history_commits(repo, secret)
        history = cache[repo]["hist"][fp]
    kind = _classify(path)
    if (tracked or history) and remote:
        risk = "critical"
    elif tracked or history:
        risk = "high"
    elif kind in ("shell_history", "ci", "source", "notebook"):
        risk = "medium"
    else:
        risk = "low"
    return {"path": str(path), "line": line, "file_type": kind, "provider": provider,
            "masked": mask(secret), "fingerprint": fingerprint(secret), "yours": mine,
            "git_tracked": tracked, "in_history": history, "remote": remote, "risk": risk,
            "fix": _fix(risk, kind)}


def _fix(risk: str, kind: str) -> str:
    if risk in ("critical", "high"):
        return "Rotate the key now, then remove it from git history (git filter-repo --replace-text)."
    if kind == "shell_history":
        return "Delete the line from your shell history and prefer env files."
    if kind == "env":
        return "Fine if this file is gitignored. Keep it out of commits."
    return "Move the key to an env file that git ignores."
