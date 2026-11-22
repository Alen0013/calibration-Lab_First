"""Publish the supplied Git history from the owner's computer, without force pushes.

Only the fixed laboratory repository is supported. Authentication is handled by
the official GitHub CLI web flow; this program never requests or reads a token.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
TARGET = "Alen0013/calibration-lab1"
OWNER = "Alen0013"
OWNER_ID = 186933822
REPO_ID = 1467680802
REMOTE_URL = "https://github.com/Alen0013/calibration-lab1.git"
EXPECTED_INITIAL_MAIN = "efe65628fdf5a30ca4fb45967ca69c504a5ee587"
REQUIRED_BRANCHES = {
    "main",
    "develop",
    "feature/lab1-app",
    "feature/lab1-delivery",
    "feature/lab1-eda",
    "feature/lab1-evidence",
    "feature/lab1-quality",
}
OPTIONAL_BRANCHES = {"feature/lab1-publish"}
WORKFLOW = "ci.yml"


class PublicationError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def clean_environment() -> dict[str, str]:
    # Let gh use its standard credential store, never an inherited PAT variable.
    excluded = {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
        "GH_DEBUG",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() not in excluded}
    environment.update(
        PYTHONUTF8="1",
        NO_COLOR="1",
        GH_HOST="github.com",
        GH_PAGER="cat",
        PAGER="cat",
        GIT_TERMINAL_PROMPT="0",
    )
    return environment


class Logger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("a", encoding="utf-8")

    def __call__(self, text: str) -> None:
        text = str(text).rstrip()
        print(text, flush=True)
        self.stream.write(text + "\n")
        self.stream.flush()

    def close(self) -> None:
        self.stream.close()


def execute(command, *, cwd=None, env=None, logger=None, timeout=120, check=True, quiet=False):
    if logger:
        logger("$ " + subprocess.list2cmdline([str(part) for part in command]))
    try:
        result = subprocess.run(
            [str(part) for part in command],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise PublicationError(
            f"Команда не завершилась за {timeout} секунд: {command[0]}"
        ) from error
    except OSError as error:
        raise PublicationError(f"Не удалось запустить {command[0]}: {error}") from error
    if logger and not quiet:
        for output in (result.stdout, result.stderr):
            if output.strip():
                logger(output[-100000:])
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()[-8000:]
        lowered = detail.lower()
        if "workflow" in lowered and ("scope" in lowered or "oauth" in lowered):
            detail += (
                "\nСуществующей авторизации может не хватать scope workflow. "
                "На своём компьютере выполните: gh auth refresh --hostname github.com --scopes workflow, "
                "затем повторите PUBLISH_GITHUB.bat. Токен вводить не нужно."
            )
        raise PublicationError(f"Команда завершилась с кодом {result.returncode}: {detail}")
    return result


def find_program(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return str(Path(found).resolve())
    if os.name == "nt":
        roots = [
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("LOCALAPPDATA"),
        ]
        relatives = (
            ["Git/cmd/git.exe", "Git/bin/git.exe", "Programs/Git/cmd/git.exe"]
            if name == "git"
            else ["GitHub CLI/gh.exe", "Programs/GitHub CLI/gh.exe"]
        )
        for parent in roots:
            if not parent:
                continue
            for relative in relatives:
                candidate = Path(parent) / relative
                if candidate.is_file():
                    return str(candidate.resolve())
    return None


class OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if (
            parsed.scheme != "https"
            or parsed.hostname
            not in {
                "github.com",
                "api.github.com",
                "release-assets.githubusercontent.com",
                "objects.githubusercontent.com",
            }
            or parsed.username
            or parsed.password
        ):
            raise PublicationError("Официальная загрузка перенаправлена на недопустимый адрес")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download_official(url: str, limit: int) -> bytes:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"api.github.com", "github.com"}
        or parsed.username
        or parsed.password
    ):
        raise PublicationError("Разрешены только официальные HTTPS-адреса GitHub CLI")
    request = Request(
        url,
        headers={"User-Agent": "CalibrationLab1Publisher", "Accept": "application/vnd.github+json"},
    )
    with build_opener(OfficialRedirects()).open(request, timeout=60) as response:
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise PublicationError("Загружаемый файл больше разрешённого размера")
    return payload


def safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if len(members) > 3000 or sum(item.file_size for item in members) > 300 * 1024 * 1024:
            raise PublicationError("Архив CLI превышает допустимый объём")
        for item in members:
            name = item.filename.replace("\\", "/")
            parts = PurePosixPath(name)
            if (
                parts.is_absolute()
                or ".." in parts.parts
                or any(":" in part for part in parts.parts)
            ):
                raise PublicationError("В архиве CLI найден небезопасный путь")
            if stat.S_ISLNK(item.external_attr >> 16):
                raise PublicationError("Символические ссылки в архиве CLI запрещены")
            target = destination.joinpath(*parts.parts).resolve()
            if not target.is_relative_to(destination):
                raise PublicationError("Путь архива выходит за каталог распаковки")
        bundle.extractall(destination)


def ensure_gh(tools: Path, logger: Logger) -> str:
    existing = find_program("gh")
    if existing:
        return existing
    tools.mkdir(parents=True, exist_ok=True)
    metadata_path = tools / "portable_gh.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        binary = (tools / metadata["executable"]).resolve()
        if not binary.is_relative_to(tools.resolve()):
            raise PublicationError("Неверный путь сохранённого portable gh")
        if binary.is_file() and file_hash(binary) == metadata["executable_sha256"]:
            return str(binary)
        raise PublicationError(
            "Сохранённый portable gh изменён или отсутствует; проверьте artifacts/github-publish/tools"
        )
    if os.name != "nt":
        raise PublicationError(
            "GitHub CLI не найден. На этой ОС установите официальный gh и повторите"
        )
    architecture = platform.machine().lower()
    if architecture in {"amd64", "x86_64"}:
        architecture = "amd64"
    elif architecture in {"arm64", "aarch64"}:
        architecture = "arm64"
    else:
        raise PublicationError(f"Не поддерживается архитектура Windows: {architecture}")
    release = json.loads(
        download_official("https://api.github.com/repos/cli/cli/releases/latest", 2 * 1024 * 1024)
    )
    tag = release.get("tag_name", "")
    if (
        not re.fullmatch(r"v\d+\.\d+\.\d+", tag)
        or release.get("draft")
        or release.get("prerelease")
    ):
        raise PublicationError("Неожиданный формат официального выпуска GitHub CLI")
    version = tag[1:]
    archive_name = f"gh_{version}_windows_{architecture}.zip"
    checksums_name = f"gh_{version}_checksums.txt"
    assets = {item["name"]: item["browser_download_url"] for item in release.get("assets", [])}
    for name in (archive_name, checksums_name):
        expected = f"https://github.com/cli/cli/releases/download/{tag}/{name}"
        if assets.get(name) != expected:
            raise PublicationError(f"Нет ожидаемого официального файла: {name}")
    logger(f"Загрузка официального GitHub CLI {version} для Windows {architecture}")
    checksums = download_official(assets[checksums_name], 1024 * 1024).decode("utf-8")
    expected_hash = None
    for line in checksums.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].lstrip("*") == archive_name:
            expected_hash = fields[0].lower()
    if not expected_hash or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise PublicationError("Официальный checksums.txt не содержит SHA-256 нужного ZIP")
    payload = download_official(assets[archive_name], 100 * 1024 * 1024)
    if hashlib.sha256(payload).hexdigest() != expected_hash:
        raise PublicationError("SHA-256 portable GitHub CLI не совпадает; распаковка отменена")
    archive = tools / archive_name
    archive.write_bytes(payload)
    with tempfile.TemporaryDirectory(prefix="gh_extract_", dir=tools) as temporary:
        safe_extract(archive, Path(temporary))
        binaries = list(Path(temporary).rglob("gh.exe"))
        if len(binaries) != 1:
            raise PublicationError("В официальном ZIP ожидался один gh.exe")
        target = tools / f"gh_{version}_{architecture}" / "gh.exe"
        if target.exists() and file_hash(target) != file_hash(binaries[0]):
            raise PublicationError("Каталог portable CLI уже содержит другой gh.exe")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(binaries[0], target)
    write_json(
        metadata_path,
        {
            "version": version,
            "archive": archive_name,
            "archive_sha256": expected_hash,
            "source_url": assets[archive_name],
            "checksums_url": assets[checksums_name],
            "executable": target.relative_to(tools).as_posix(),
            "executable_sha256": file_hash(target),
        },
    )
    logger(f"SHA-256 официального CLI совпал: {expected_hash}")
    return str(target.resolve())


class GitCommands:
    def __init__(
        self, git: str, repo: Path, logger=None, env=None, credential_helper: str | None = None
    ):
        self.git, self.repo, self.logger = git, Path(repo), logger
        self.env = env if env is not None else clean_environment()
        self.credential_helper = credential_helper

    def run(self, *arguments: str, check=True, timeout=180, quiet=False):
        command = [self.git]
        if self.credential_helper:
            command += [
                "-c",
                "credential.helper=",
                "-c",
                f"credential.helper={self.credential_helper}",
            ]
        return execute(
            command + list(arguments),
            cwd=self.repo,
            env=self.env,
            logger=self.logger,
            check=check,
            timeout=timeout,
            quiet=quiet,
        )

    def sha(self, ref: str, optional=False) -> str | None:
        result = self.run("rev-parse", "--verify", ref, check=not optional, quiet=True)
        if result.returncode:
            return None
        sha = result.stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise PublicationError(f"Неверный SHA для {ref}")
        return sha

    def ancestor(self, first: str, second: str) -> bool:
        result = self.run("merge-base", "--is-ancestor", first, second, check=False, quiet=True)
        if result.returncode not in {0, 1}:
            raise PublicationError("Git не смог проверить родство коммитов")
        return result.returncode == 0

    def tree(self, ref: str) -> str:
        return self.run("rev-parse", f"{ref}^{{tree}}", quiet=True).stdout.strip()


def clean_worktree(git: GitCommands) -> None:
    status = git.run("status", "--porcelain", "--untracked-files=all", quiet=True).stdout.strip()
    if status:
        raise PublicationError(
            "В подготовленном клоне есть несохранённые изменения. Они не будут перезаписаны:\n"
            + status
        )


def fast_forward_branch(git: GitCommands, branch: str, old: str, new: str) -> None:
    if old == new:
        return
    if not git.ancestor(old, new):
        raise PublicationError(
            f"Ветка {branch} разошлась с серверной; принудительное обновление запрещено"
        )
    current = git.run("branch", "--show-current", quiet=True).stdout.strip()
    if current == branch:
        git.run("merge", "--ff-only", new)
    else:
        git.run("update-ref", f"refs/heads/{branch}", new, old)


def prepare_history(
    git: GitCommands, source_refs: dict[str, str], expected_initial_main: str
) -> dict:
    """Bridge a known README bootstrap; callable with a local bare test remote."""
    clean_worktree(git)
    source = source_refs["develop"]
    main = git.sha("refs/remotes/origin/main")
    develop = git.sha("refs/heads/develop")
    remote_develop = git.sha("refs/remotes/origin/develop", optional=True)
    if not git.ancestor(source, develop) or git.tree(source) != git.tree(develop):
        raise PublicationError("Локальный develop не соответствует исходникам bundle")
    if remote_develop:
        if not git.ancestor(source, remote_develop) or git.tree(source) != git.tree(remote_develop):
            raise PublicationError(
                "Удалённый develop содержит неожиданные изменения; публикация остановлена"
            )
        if git.ancestor(develop, remote_develop):
            fast_forward_branch(git, "develop", develop, remote_develop)
            develop = remote_develop
        elif not git.ancestor(remote_develop, develop):
            raise PublicationError("Локальный и удалённый develop разошлись; force push запрещён")
    if main != expected_initial_main:
        if git.ancestor(expected_initial_main, main) and git.ancestor(develop, main):
            return {"mode": "already_published", "head": develop, "main_before": main}
        raise PublicationError(
            "Удалённый main изменён неизвестным образом. Ожидался закреплённый bootstrap либо уже опубликованная история проекта"
        )
    paths = git.run("ls-tree", "-r", "--name-only", main, quiet=True).stdout.splitlines()
    if paths != ["README.md"]:
        raise PublicationError("Закреплённый начальный main должен содержать только README.md")
    git.run("checkout", "develop")
    if git.ancestor(expected_initial_main, develop):
        return {"mode": "existing_bridge", "head": develop, "main_before": main}
    git.run(
        "merge",
        "--no-ff",
        "--allow-unrelated-histories",
        "-X",
        "ours",
        "origin/main",
        "-m",
        "chore: preserve initial GitHub README history",
    )
    head = git.sha("refs/heads/develop")
    if (
        not git.ancestor(source, head)
        or not git.ancestor(expected_initial_main, head)
        or git.tree(head) != git.tree(source)
    ):
        raise PublicationError("Объединённая история не прошла проверку сохранности исходников")
    return {"mode": "created_bridge", "head": head, "main_before": main}


def safe_push_branches(
    git: GitCommands, source_refs: dict[str, str], source_tags: dict[str, str] | None = None
) -> dict:
    """Validate all branches before one atomic, non-force push; never push main."""
    clean_worktree(git)
    branches = sorted(name for name in source_refs if name != "main")
    planned = {}
    tags = source_tags or {}
    if tags:
        listed = git.run("ls-remote", "--tags", "origin", quiet=True).stdout.splitlines()
        remote_tags = {line.split()[1]: line.split()[0] for line in listed}
        for name, expected in tags.items():
            ref = f"refs/tags/{name}"
            if git.sha(ref) != expected:
                raise PublicationError(f"Локальная метка {name} отличается от bundle")
            if ref in remote_tags and remote_tags[ref] != expected:
                raise PublicationError(
                    f"Удалённая метка {name} имеет другой SHA; перезапись запрещена"
                )
    # Validate the entire set before updating any local branch or pushing anything.
    for branch in branches:
        local = git.sha(f"refs/heads/{branch}")
        remote = git.sha(f"refs/remotes/origin/{branch}", optional=True)
        if not git.ancestor(source_refs[branch], local):
            raise PublicationError(f"Ветка {branch} потеряла исходную историю bundle")
        if remote and not (git.ancestor(remote, local) or git.ancestor(local, remote)):
            raise PublicationError(
                f"Удалённая ветка {branch} разошлась с локальной; ничего не отправлено"
            )
        target = remote if remote and git.ancestor(local, remote) else local
        planned[branch] = {"local_before": local, "remote_before": remote, "head": target}
    for branch, item in planned.items():
        fast_forward_branch(git, branch, item["local_before"], item["head"])
    refspecs = [f"refs/heads/{branch}:refs/heads/{branch}" for branch in branches]
    refspecs += [f"refs/tags/{name}:refs/tags/{name}" for name in sorted(tags)]
    git.run("push", "--atomic", "--porcelain", "origin", *refspecs, timeout=300)
    actual = git.run("ls-remote", "origin", quiet=True).stdout.splitlines()
    all_refs = {line.split()[1]: line.split()[0] for line in actual}
    remote_refs = {line.split()[1].removeprefix("refs/heads/"): line.split()[0] for line in actual}
    for branch, item in planned.items():
        if remote_refs.get(branch) != item["head"]:
            raise PublicationError(f"Серверная ветка {branch} не совпадает с отправленной")
    for name, expected in tags.items():
        if all_refs.get(f"refs/tags/{name}") != expected:
            raise PublicationError(f"Серверная метка {name} не совпадает с исходной")
    return {
        "atomic": True,
        "main_pushed": False,
        "branches": planned,
        "tags": tags,
        "remote_main": remote_refs.get("main"),
    }


def restore_workspace(
    git_binary: str, bundle: Path, work: Path, logger, environment
) -> tuple[GitCommands, dict]:
    work.mkdir(parents=True, exist_ok=True)
    marker = work / "managed_workspace.json"
    clone = work / "clone"
    digest = file_hash(bundle)
    heads = execute(
        [git_binary, "bundle", "list-heads", str(bundle)], env=environment, logger=logger
    ).stdout.splitlines()
    source_refs = {}
    source_tags = {}
    for line in heads:
        sha, ref = line.split(maxsplit=1)
        if ref.startswith("refs/heads/"):
            if not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise PublicationError("Неверный SHA в bundle")
            source_refs[ref.removeprefix("refs/heads/")] = sha
        elif ref == "refs/tags/lab1-v1.0.0":
            source_tags["lab1-v1.0.0"] = sha
    if (
        not REQUIRED_BRANCHES.issubset(source_refs)
        or set(source_refs) - REQUIRED_BRANCHES - OPTIONAL_BRANCHES
    ):
        raise PublicationError(
            "Набор веток bundle отличается от ожидаемой истории лабораторной работы"
        )
    expected = {
        "schema_version": 1,
        "target": TARGET,
        "repo_id": REPO_ID,
        "bundle_sha256": digest,
        "source_refs": source_refs,
        "source_tags": source_tags,
    }
    if marker.exists():
        stored = json.loads(marker.read_text(encoding="utf-8"))
        if any(stored.get(key) != value for key, value in expected.items()):
            raise PublicationError(
                "Каталог публикации относится к другому bundle или репозиторию; он не будет перезаписан"
            )
        if not (clone / ".git").is_dir():
            raise PublicationError(
                "Управляемый клон отсутствует или неполон. Проверьте artifacts/github-publish/clone"
            )
    else:
        if clone.exists():
            raise PublicationError(
                "Каталог artifacts/github-publish/clone уже существует без маркера; перезапись запрещена"
            )
        write_json(marker, {**expected, "created_at": now()})
        execute(
            [
                git_binary,
                "clone",
                "--origin",
                "bundle",
                "--branch",
                "develop",
                str(bundle),
                str(clone),
            ],
            env=environment,
            logger=logger,
        )
        commands = GitCommands(git_binary, clone, logger, environment)
        for branch, sha in source_refs.items():
            if branch != "develop":
                commands.run("branch", branch, sha)
        commands.run("remote", "remove", "bundle")
        commands.run("remote", "add", "origin", REMOTE_URL)
    commands = GitCommands(git_binary, clone, logger, environment)
    commands.run("bundle", "verify", str(bundle))
    clean_worktree(commands)
    remote = commands.run("remote", "get-url", "origin", quiet=True).stdout.strip()
    if remote != REMOTE_URL:
        raise PublicationError("В управляемом клоне origin указывает на другой адрес")
    for branch, source in source_refs.items():
        head = commands.sha(f"refs/heads/{branch}")
        if not commands.ancestor(source, head):
            raise PublicationError(f"История локальной ветки {branch} неожиданно изменена")
    return commands, expected


class Github:
    def __init__(self, gh: str, environment: dict, logger):
        self.gh, self.environment, self.logger = gh, environment, logger

    def run(self, *arguments, timeout=120, check=True, quiet=False):
        return execute(
            [self.gh, *arguments],
            env=self.environment,
            logger=self.logger,
            timeout=timeout,
            check=check,
            quiet=quiet,
        )

    def json(self, *arguments):
        return json.loads(self.run(*arguments, quiet=True).stdout)

    def api_optional(self, endpoint: str):
        result = self.run("api", endpoint, check=False, quiet=True)
        if result.returncode == 0:
            return 200, json.loads(result.stdout)
        found = re.search(r"HTTP (\d{3})", result.stderr + result.stdout)
        code = int(found.group(1)) if found else None
        return code, {"error": (result.stderr or result.stdout).strip()[-2000:]}

    def oauth_scopes(self) -> set[str] | None:
        # Only response headers are retained; gh handles credentials internally.
        # jq empty suppresses the user profile body, and quiet suppresses headers in logs.
        response = self.run(
            "api",
            "--hostname",
            "github.com",
            "--method",
            "GET",
            "--include",
            "user",
            "--jq",
            "empty",
            quiet=True,
        )
        header = re.search(r"(?im)^x-oauth-scopes:[ \t]*([^\r\n]*)", response.stdout)
        if header is None or not header.group(1).strip():
            return None
        return {item.strip().lower() for item in header.group(1).split(",") if item.strip()}

    def authenticate(self) -> dict:
        # Discard auth-status output entirely: it can include token metadata.
        status = subprocess.run(
            [self.gh, "auth", "status", "--hostname", "github.com"],
            env=self.environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        if status.returncode:
            self.logger(
                "Откроется стандартная авторизация GitHub CLI. Войдите как Alen0013; токен вводить не нужно."
            )
            login = subprocess.run(
                [
                    self.gh,
                    "auth",
                    "login",
                    "--hostname",
                    "github.com",
                    "--web",
                    "--git-protocol",
                    "https",
                    "--scopes",
                    "workflow",
                ],
                env=self.environment,
            )
            if login.returncode:
                raise PublicationError("Авторизация GitHub CLI не завершена")
        user = self.json("api", "user", "--jq", "{login: .login, id: .id}")
        if user.get("login", "").casefold() != OWNER.casefold() or user.get("id") != OWNER_ID:
            raise PublicationError(
                f"Авторизован другой аккаунт: {user.get('login')}. Нужен Alen0013; переключите аккаунт в gh и повторите"
            )
        scopes = self.oauth_scopes()
        refreshed = False
        if scopes is not None and "workflow" not in scopes:
            self.logger(
                "Текущая OAuth-авторизация не включает workflow. "
                "GitHub CLI предложит подтвердить это разрешение в браузере для публикации ci.yml."
            )
            refresh = subprocess.run(
                [self.gh, "auth", "refresh", "--hostname", "github.com", "--scopes", "workflow"],
                env=self.environment,
            )
            if refresh.returncode:
                raise PublicationError(
                    "Разрешение workflow не подтверждено; отправка изменений не выполнялась"
                )
            refreshed = True
            confirmed = self.json("api", "user", "--jq", "{login: .login, id: .id}")
            if (
                confirmed.get("login", "").casefold() != OWNER.casefold()
                or confirmed.get("id") != OWNER_ID
            ):
                raise PublicationError(
                    "После подтверждения разрешения активен другой аккаунт; публикация остановлена"
                )
            scopes = self.oauth_scopes()
            if scopes is not None and "workflow" not in scopes:
                raise PublicationError(
                    "GitHub не подтвердил scope workflow после refresh; публикация остановлена"
                )
        user["workflow_scope"] = (
            "confirmed" if scopes is not None and "workflow" in scopes else "not_disclosed"
        )
        user["workflow_scope_refreshed"] = refreshed
        return user


def ensure_identity(git: GitCommands) -> None:
    for key, fallback in (
        ("user.name", OWNER),
        ("user.email", f"{OWNER_ID}+{OWNER}@users.noreply.github.com"),
    ):
        result = git.run("config", "--get", key, check=False, quiet=True)
        if not result.stdout.strip():
            git.run("config", "--local", key, fallback)


def ensure_pull_request(github: Github, head: str, output: Path) -> dict:
    fields = "number,url,state,headRefOid,baseRefName,headRefName,isDraft,mergedAt"
    pulls = github.json(
        "pr",
        "list",
        "--repo",
        TARGET,
        "--state",
        "all",
        "--base",
        "main",
        "--head",
        "develop",
        "--limit",
        "100",
        "--json",
        fields,
    )
    opened = [item for item in pulls if item["state"] == "OPEN"]
    if len(opened) > 1:
        raise PublicationError(
            "Найдено несколько открытых PR develop→main; нужна проверка владельца"
        )
    if opened:
        pull = opened[0]
    else:
        merged = [
            item for item in pulls if item["state"] == "MERGED" and item.get("headRefOid") == head
        ]
        if merged:
            return merged[0]
        if any(item["state"] == "CLOSED" and item.get("headRefOid") == head for item in pulls):
            raise PublicationError(
                "PR с этим коммитом закрыт без слияния; автоматическое повторное открытие не выполняется"
            )
        body = output / "pull_request_body.md"
        body.write_text(
            "## Причина\n\nДля ЛР1 варианта 25 нужен воспроизводимый анализ калибровки и проверяемая история разработки.\n\n"
            "## Изменения\n\nДобавлены анализ WDBC, графики калибровки, локальное приложение, конфигурация, отчёт, MLflow и проверки Git. "
            "Исходные ветки и коммиты восстановлены из Git bundle. Начальная история README сохранена отдельным merge-коммитом.\n\n"
            "## Проверка\n\nWorkflow ci.yml запускает pre-commit, pytest, негативные контроли, проверку SHA-256, EDA и повтор. "
            "Фактический серверный результат проверяется до слияния.\n\n"
            f"Коммит develop: `{head}`.\n\n"
            "## Процесс\n\nПубликацию выполняет владелец через GitHub CLI. Использование ИИ раскрыто в defense_checklist.md. "
            "Внешнее человеческое ревью этим описанием не подтверждается.\n",
            encoding="utf-8",
        )
        github.run(
            "pr",
            "create",
            "--repo",
            TARGET,
            "--base",
            "main",
            "--head",
            "develop",
            "--title",
            "ЛР1 вариант 25 — калибровка вероятностей",
            "--body-file",
            str(body),
        )
        pulls = github.json(
            "pr",
            "list",
            "--repo",
            TARGET,
            "--state",
            "open",
            "--base",
            "main",
            "--head",
            "develop",
            "--json",
            fields,
        )
        if len(pulls) != 1:
            raise PublicationError("Не удалось однозначно подтвердить созданный pull request")
        pull = pulls[0]
    if pull.get("headRefOid") != head or pull.get("isDraft"):
        raise PublicationError(f"PR имеет другой head или остаётся черновиком: {pull.get('url')}")
    return pull


def wait_for_ci(github: Github, head: str, timeout: int, on_observed=None) -> dict:
    deadline = time.monotonic() + timeout
    selected = None

    def evidence(run):
        return {
            "workflow": WORKFLOW,
            "path": run.get("path"),
            "databaseId": run.get("id"),
            "headSha": run.get("head_sha"),
            "branch": run.get("head_branch"),
            "event": run.get("event"),
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "url": run.get("html_url"),
            "observed_at": now(),
        }

    def matches(run):
        return (
            run.get("head_sha") == head
            and run.get("head_branch") == "develop"
            and run.get("path") == ".github/workflows/ci.yml"
        )

    while time.monotonic() < deadline:
        response = github.run(
            "api",
            f"repos/{TARGET}/actions/runs?branch=develop&head_sha={head}&per_page=100",
            check=False,
            quiet=True,
        )
        if response.returncode == 0:
            runs = [
                run for run in json.loads(response.stdout).get("workflow_runs", []) if matches(run)
            ]
            if runs:
                selected = next(
                    (run for run in runs if run.get("event") == "pull_request"), runs[0]
                )
                break
        github.logger(f"Ожидание запуска {WORKFLOW} для {head[:12]}…")
        time.sleep(5)
    if selected is None:
        raise PublicationError(
            f"За {timeout} секунд не появился CI нужного коммита: https://github.com/{TARGET}/actions"
        )
    observed = evidence(selected)
    if on_observed:
        on_observed(observed)
    github.logger(f"CI: {observed['url']}")
    remaining = max(1, int(deadline - time.monotonic()))
    try:
        watched = github.run(
            "run",
            "watch",
            str(selected["id"]),
            "--repo",
            TARGET,
            "--exit-status",
            "--interval",
            "10",
            timeout=remaining,
            check=False,
        )
    except PublicationError as error:
        raise PublicationError(f"Ожидание CI остановлено: {observed['url']}\n{error}") from error
    verified = github.json("api", f"repos/{TARGET}/actions/runs/{selected['id']}")
    result = evidence(verified)
    if on_observed:
        on_observed(result)
    if (
        watched.returncode != 0
        or not matches(verified)
        or verified.get("status") != "completed"
        or verified.get("conclusion") != "success"
    ):
        raise PublicationError(f"CI не подтвердил успех нужного коммита: {result['url']}")
    return result


def protection_comparison(actual: dict) -> dict:
    status = actual.get("required_status_checks") or {}
    contexts = set(status.get("contexts") or []) | {
        item.get("context") for item in status.get("checks") or []
    }
    reviews = actual.get("required_pull_request_reviews") or {}
    return {
        "quality_required": "quality" in contexts,
        "strict_status_checks": status.get("strict") is True,
        "at_least_one_approval": reviews.get("required_approving_review_count", 0) >= 1,
        "stale_reviews_dismissed": reviews.get("dismiss_stale_reviews") is True,
        "admins_enforced": (actual.get("enforce_admins") or {}).get("enabled") is True,
        "conversation_resolution": (actual.get("required_conversation_resolution") or {}).get(
            "enabled"
        )
        is True,
        "force_pushes_forbidden": (actual.get("allow_force_pushes") or {}).get("enabled") is False,
        "deletions_forbidden": (actual.get("allow_deletions") or {}).get("enabled") is False,
    }


def protect_main(github: Github, configuration: Path, on_observed=None) -> dict:
    branch = github.json("api", f"repos/{TARGET}/branches/main")
    active = branch.get("protected")
    code, body = github.api_optional(f"repos/{TARGET}/branches/main/protection")
    if on_observed:
        on_observed(
            {
                "action": "read_before_write",
                "active": active,
                "readback_http_status": code,
                "readback_available": code == 200,
                "configuration_match": None,
                "readback": body,
            }
        )
    existing = active is True or code == 200
    if existing:
        comparison = protection_comparison(body) if code == 200 else None
        return {
            "action": "existing_protection_preserved",
            "active": active,
            "readback_http_status": code,
            "readback_available": code == 200,
            "configuration_match": all(comparison.values()) if comparison else None,
            "checks": comparison,
            "readback": body,
        }
    if active is not False or code != 404:
        return {
            "action": "not_modified",
            "active": active,
            "readback_http_status": code,
            "readback_available": False,
            "configuration_match": None,
            "error": body,
        }
    expected = json.loads(configuration.read_text(encoding="utf-8"))
    if expected.get("required_pull_request_reviews", {}).get(
        "required_approving_review_count", 0
    ) < 1 or "quality" not in expected.get("required_status_checks", {}).get("contexts", []):
        raise PublicationError(
            "Подготовленная branch_protection.json не содержит обязательных quality и внешнего approval"
        )
    github.run(
        "api",
        "--method",
        "PUT",
        f"repos/{TARGET}/branches/main/protection",
        "--input",
        str(configuration),
        quiet=True,
    )
    new_branch = github.json("api", f"repos/{TARGET}/branches/main")
    code, actual = github.api_optional(f"repos/{TARGET}/branches/main/protection")
    comparison = protection_comparison(actual) if code == 200 else None
    return {
        "action": "created",
        "active": new_branch.get("protected"),
        "readback_http_status": code,
        "readback_available": code == 200,
        "configuration_match": all(comparison.values()) if comparison else None,
        "checks": comparison,
        "readback": actual,
    }


def publish(args) -> int:
    artifacts = ROOT / "artifacts"
    work = artifacts / "github-publish"
    logger = Logger(artifacts / "github_publication.log")
    receipt_path = artifacts / "github_publication.json"
    receipt = {
        "schema_version": 1,
        "target": TARGET,
        "repo_id": REPO_ID,
        "started_at": now(),
        "status": "running",
        "success": False,
        "published": False,
        "events": [],
    }

    def stage(name: str, **fields):
        receipt["stage"] = name
        receipt["events"].append({"at": now(), "stage": name, **fields})
        write_json(receipt_path, receipt)
        logger(f"\n[{now()}] {name}")

    def record_observation(key: str, value: dict):
        receipt[key] = value
        write_json(receipt_path, receipt)

    try:
        stage("tools")
        environment = clean_environment()
        git_binary = find_program("git")
        if git_binary is None:
            raise PublicationError(
                "Git не найден. Установите Git for Windows и откройте новый терминал; программа не устанавливает Git автоматически"
            )
        gh = ensure_gh(work / "tools", logger)
        github = Github(gh, environment, logger)
        stage("authentication")
        receipt["account"] = github.authenticate()
        metadata = github.json(
            "api",
            f"repos/{TARGET}",
            "--jq",
            "{id: .id, full_name: .full_name, owner_id: .owner.id, default_branch: .default_branch, archived: .archived, disabled: .disabled}",
        )
        if (
            metadata.get("id") != REPO_ID
            or metadata.get("owner_id") != OWNER_ID
            or metadata.get("full_name", "").casefold() != TARGET.casefold()
            or metadata.get("default_branch") != "main"
            or metadata.get("archived")
            or metadata.get("disabled")
        ):
            raise PublicationError(
                "Свойства удалённого репозитория отличаются от согласованного назначения"
            )
        receipt["repository"] = metadata
        stage("restore_bundle")
        bundle = args.bundle
        if bundle is None:
            bundle = next(
                (
                    path
                    for path in (
                        ROOT / "calibration_lab1.bundle",
                        ROOT.parent / "calibration_lab1.bundle",
                    )
                    if path.is_file()
                ),
                None,
            )
        if bundle is None or not bundle.is_file():
            raise PublicationError(
                "Не найден calibration_lab1.bundle. Поместите его рядом с проектом либо укажите --bundle"
            )
        git, restored = restore_workspace(git_binary, bundle.resolve(), work, logger, environment)
        receipt["source"] = restored
        git.credential_helper = "!" + shlex.quote(gh.replace("\\", "/")) + " auth git-credential"
        ensure_identity(git)
        stage("inspect_remote_history")
        git.run("fetch", "--prune", "origin", timeout=180)
        receipt["history"] = prepare_history(git, restored["source_refs"], EXPECTED_INITIAL_MAIN)
        stage("push_branches")
        receipt["refs"] = safe_push_branches(git, restored["source_refs"], restored["source_tags"])
        head = receipt["refs"]["branches"]["develop"]["head"]
        receipt["published_head"] = head
        stage("pull_request")
        pull = ensure_pull_request(github, head, work)
        receipt["pull_request"] = pull
        stage("ci", url=pull["url"], head=head)
        receipt["ci"] = wait_for_ci(
            github, head, args.ci_timeout, on_observed=lambda value: record_observation("ci", value)
        )
        stage("merge", ci_url=receipt["ci"]["url"])
        git.run("fetch", "--prune", "origin")
        current_main = git.sha("refs/remotes/origin/main")
        if pull["state"] != "MERGED":
            if current_main != EXPECTED_INITIAL_MAIN:
                raise PublicationError(
                    "Main изменился после подготовки PR; автоматическое слияние остановлено"
                )
            github.run(
                "pr",
                "merge",
                str(pull["number"]),
                "--repo",
                TARGET,
                "--merge",
                "--match-head-commit",
                head,
            )
        merged = github.json(
            "pr",
            "view",
            str(pull["number"]),
            "--repo",
            TARGET,
            "--json",
            "number,url,state,headRefOid,mergedAt,mergeCommit",
        )
        if merged.get("state") != "MERGED" or merged.get("headRefOid") != head:
            raise PublicationError(
                f"PR ещё не слит либо head изменился. Требования не обходятся: {pull['url']}"
            )
        git.run("fetch", "--prune", "origin")
        final_main = git.sha("refs/remotes/origin/main")
        if not git.ancestor(head, final_main) or not git.ancestor(
            EXPECTED_INITIAL_MAIN, final_main
        ):
            raise PublicationError("Не подтверждено сохранение обеих историй в main")
        receipt.update(published=True, pull_request=merged, main_sha=final_main)
        stage("protect_main")
        receipt["protection"] = protect_main(
            github,
            git.repo / ".github" / "branch_protection.json",
            on_observed=lambda value: record_observation("protection", value),
        )
        if (
            receipt["protection"].get("active") is not True
            or receipt["protection"].get("configuration_match") is not True
        ):
            receipt["status"] = "published_protection_review_required"
            raise PublicationError(
                "Код и PR опубликованы, но требуемая защита main не подтверждена. Существующие правила сохранены; смотрите receipt и настройки GitHub"
            )
        receipt.update(status="completed", success=True, finished_at=now())
        stage("completed")
        logger(
            f"\nГотово: https://github.com/{TARGET}\nPR: {merged['url']}\nCI: {receipt['ci']['url']}\nЗащита main прочитана и проверена. Внешнее ревью не заявляется."
        )
        return 0
    except (
        PublicationError,
        OSError,
        ValueError,
        subprocess.SubprocessError,
        KeyboardInterrupt,
    ) as error:
        if receipt["status"] != "published_protection_review_required":
            receipt["status"] = "blocked" if receipt["published"] else "failed"
        receipt.update(error=str(error), finished_at=now())
        write_json(receipt_path, receipt)
        logger(f"\nОСТАНОВЛЕНО: {error}\nСостояние: {receipt_path}")
        return 1
    finally:
        logger.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Опубликовать Git bundle ЛР1 только в Alen0013/calibration-lab1"
    )
    parser.add_argument("--bundle", type=Path, help="Путь к calibration_lab1.bundle")
    parser.add_argument(
        "--ci-timeout", type=int, default=900, help="Ожидание CI, секунд; по умолчанию 900"
    )
    args = parser.parse_args(argv)
    if not 60 <= args.ci_timeout <= 900:
        parser.error("--ci-timeout должен быть от 60 до 900")
    return publish(args)


if __name__ == "__main__":
    raise SystemExit(main())
