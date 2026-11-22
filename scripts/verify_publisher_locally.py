"""Exercise publishing Git helpers against local bare repos, never GitHub."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
MODULE = BASE / "scripts/publish_github.py"
GIT = shutil.which("git")
ENV = {
    **os.environ,
    "GIT_ALLOW_PROTOCOL": "file",
    "GIT_AUTHOR_NAME": "Local publisher fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Local publisher fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": "2026-11-22T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-11-22T00:00:00+00:00",
    "GIT_TERMINAL_PROMPT": "0",
}


def git(repo, *args, check=True):
    result = subprocess.run(
        [GIT, "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        env=ENV,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode:
        raise RuntimeError(f"Git {args} failed: {result.stderr}")
    return result.stdout.strip() if check else result


def commit(repo, name, content, message):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git(repo, "add", "--", name)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def fixture(root):
    remote = root / "remote.git"
    git(root, "init", "--bare", str(remote))
    git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    owner = root / "owner"
    git(root, "init", "-b", "main", str(owner))
    bootstrap = commit(owner, "README.md", "# GitHub initial README\n", "Initial README")
    git(owner, "remote", "add", "origin", str(remote))
    git(owner, "push", "origin", "main")
    source = root / "source"
    git(root, "init", "-b", "main", str(source))
    original_main = commit(source, "README.md", "# Original local lab\n", "Local scaffold")
    git(source, "switch", "-c", "develop")
    git(source, "switch", "-c", "feature/lab1-eda")
    feature = commit(source, "src/example.py", "value = 25\n", "Local analysis")
    git(source, "switch", "develop")
    git(source, "merge", "--no-ff", "feature/lab1-eda", "-m", "Local integration")
    develop = git(source, "rev-parse", "HEAD")
    refs = {"main": original_main, "develop": develop, "feature/lab1-eda": feature}
    git(source, "remote", "add", "origin", str(remote))
    git(source, "fetch", "origin")
    return source, owner, remote, bootstrap, refs


def remote_refs(remote, namespace="refs/heads"):
    result = git(remote, "for-each-ref", "--format=%(refname) %(objectname)", namespace)
    return dict(line.split(" ", 1) for line in result.splitlines() if line)


def ancestor(repo, older, newer):
    return git(repo, "merge-base", "--is-ancestor", older, newer, check=False).returncode == 0


def main():
    spec = importlib.util.spec_from_file_location("lab1_publisher", MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    records = []

    with tempfile.TemporaryDirectory(prefix="lab1_publish_fixture_") as tmp:
        root = Path(tmp)
        source, owner, remote, bootstrap, refs = fixture(root)
        runner = module.GitCommands(GIT, source, env=ENV)
        prepared = module.prepare_history(runner, refs, bootstrap)
        bridge = git(source, "rev-parse", "develop")
        assert ancestor(source, refs["develop"], bridge)
        assert ancestor(source, bootstrap, bridge)
        assert git(source, "rev-parse", f"{bridge}^{{tree}}") == git(
            source, "rev-parse", f"{refs['develop']}^{{tree}}"
        )
        assert git(source, "show", f"{bootstrap}:README.md") == "# GitHub initial README"
        pushed = module.safe_push_branches(runner, refs)
        heads = remote_refs(remote)
        assert heads["refs/heads/main"] == bootstrap
        assert heads["refs/heads/develop"] == bridge
        assert heads["refs/heads/feature/lab1-eda"] == refs["feature/lab1-eda"]
        records.append(
            {
                "name": "first_publication_preserves_both_histories_and_source_tree",
                "passed": True,
                "prepare": prepared,
                "push": pushed,
            }
        )

        git(source, "fetch", "origin")
        repeated = module.prepare_history(runner, refs, bootstrap)
        module.safe_push_branches(runner, refs)
        assert git(source, "rev-parse", "develop") == bridge
        assert remote_refs(remote) == heads
        records.append(
            {"name": "repeat_before_merge_is_stable", "passed": True, "prepare": repeated}
        )

        git(owner, "fetch", "origin")
        git(owner, "merge", "--no-ff", "origin/develop", "-m", "Simulated accepted pull request")
        git(owner, "push", "origin", "main")
        merged_main = git(owner, "rev-parse", "main")
        git(source, "fetch", "origin")
        count_before = git(source, "rev-list", "--all", "--count")
        after_merge = module.prepare_history(runner, refs, bootstrap)
        module.safe_push_branches(runner, refs)
        assert remote_refs(remote)["refs/heads/main"] == merged_main
        assert ancestor(source, refs["develop"], merged_main)
        assert git(source, "rev-list", "--all", "--count") == count_before
        records.append(
            {
                "name": "repeat_after_merge_preserves_main_and_adds_no_commit",
                "passed": True,
                "prepare": after_merge,
            }
        )

    with tempfile.TemporaryDirectory(prefix="lab1_publish_wrong_main_") as tmp:
        source, owner, remote, bootstrap, refs = fixture(Path(tmp))
        commit(owner, "unexpected.txt", "Do not overwrite this work\n", "Unexpected user change")
        git(owner, "push", "origin", "main")
        git(source, "fetch", "origin")
        before_remote = remote_refs(remote)
        before_local = git(source, "rev-parse", "develop")
        runner = module.GitCommands(GIT, source, env=ENV)
        try:
            module.prepare_history(runner, refs, bootstrap)
        except (RuntimeError, ValueError) as error:
            message = str(error)
        else:
            raise AssertionError("Unexpected main history was accepted")
        assert remote_refs(remote) == before_remote
        assert git(source, "rev-parse", "develop") == before_local
        records.append(
            {
                "name": "unexpected_initial_main_refused_without_changes",
                "passed": True,
                "message": message,
            }
        )

    with tempfile.TemporaryDirectory(prefix="lab1_publish_collision_") as tmp:
        source, owner, remote, bootstrap, refs = fixture(Path(tmp))
        git(owner, "push", "origin", f"{bootstrap}:refs/heads/feature/lab1-eda")
        git(source, "fetch", "origin")
        runner = module.GitCommands(GIT, source, env=ENV)
        module.prepare_history(runner, refs, bootstrap)
        before_remote = remote_refs(remote)
        try:
            module.safe_push_branches(runner, refs)
        except (RuntimeError, ValueError) as error:
            message = str(error)
        else:
            raise AssertionError("Diverged remote feature branch was accepted")
        assert remote_refs(remote) == before_remote
        records.append(
            {
                "name": "diverged_feature_refused_without_partial_push",
                "passed": True,
                "message": message,
            }
        )

    with tempfile.TemporaryDirectory(prefix="lab1_publish_remote_ahead_") as tmp:
        source, owner, remote, bootstrap, refs = fixture(Path(tmp))
        runner = module.GitCommands(GIT, source, env=ENV)
        module.prepare_history(runner, refs, bootstrap)
        module.safe_push_branches(runner, refs)
        git(owner, "fetch", "origin")
        git(owner, "switch", "-c", "feature/lab1-eda", "origin/feature/lab1-eda")
        added = commit(owner, "remote_note.md", "A later user change\n", "User feature update")
        git(owner, "push", "origin", "feature/lab1-eda")
        git(source, "fetch", "origin")
        before = remote_refs(remote)
        pushed = module.safe_push_branches(runner, refs)
        assert remote_refs(remote) == before
        assert git(source, "rev-parse", "feature/lab1-eda") == added
        records.append(
            {
                "name": "remote_ahead_feature_preserved_and_local_ref_fast_forwarded",
                "passed": True,
                "push": pushed,
            }
        )

    with tempfile.TemporaryDirectory(prefix="lab1_publish_tag_") as tmp:
        source, owner, remote, bootstrap, refs = fixture(Path(tmp))
        git(source, "tag", "-a", "lab1-v1.0.0", "-m", "Original fixture release", refs["develop"])
        tag_sha = git(source, "rev-parse", "refs/tags/lab1-v1.0.0")
        assert git(source, "cat-file", "-t", tag_sha) == "tag"
        runner = module.GitCommands(GIT, source, env=ENV)
        module.prepare_history(runner, refs, bootstrap)
        pushed = module.safe_push_branches(runner, refs, {"lab1-v1.0.0": tag_sha})
        before = remote_refs(remote, "refs")
        assert before["refs/tags/lab1-v1.0.0"] == tag_sha
        assert git(remote, "rev-parse", "refs/tags/lab1-v1.0.0^{}") == refs["develop"]
        assert git(remote, "show", "--format=fuller", "refs/tags/lab1-v1.0.0") == git(
            source, "show", "--format=fuller", "refs/tags/lab1-v1.0.0"
        )
        git(source, "fetch", "origin")
        module.safe_push_branches(runner, refs, {"lab1-v1.0.0": tag_sha})
        assert remote_refs(remote, "refs") == before
        records.append(
            {
                "name": "annotated_tag_preserved_exactly_and_repeat_is_stable",
                "passed": True,
                "push": pushed,
            }
        )

    with tempfile.TemporaryDirectory(prefix="lab1_publish_tag_collision_") as tmp:
        source, owner, remote, bootstrap, refs = fixture(Path(tmp))
        git(source, "tag", "-a", "lab1-v1.0.0", "-m", "Original fixture release", refs["develop"])
        tag_sha = git(source, "rev-parse", "refs/tags/lab1-v1.0.0")
        git(owner, "tag", "-a", "lab1-v1.0.0", "-m", "Different remote release", bootstrap)
        git(owner, "push", "origin", "refs/tags/lab1-v1.0.0")
        runner = module.GitCommands(GIT, source, env=ENV)
        module.prepare_history(runner, refs, bootstrap)
        before = remote_refs(remote, "refs")
        try:
            module.safe_push_branches(runner, refs, {"lab1-v1.0.0": tag_sha})
        except (RuntimeError, ValueError) as error:
            message = str(error)
        else:
            raise AssertionError("A conflicting remote release tag was overwritten")
        assert remote_refs(remote, "refs") == before
        assert git(source, "rev-parse", "refs/tags/lab1-v1.0.0") == tag_sha
        assert "refs/heads/develop" not in before
        records.append(
            {
                "name": "conflicting_remote_tag_refused_before_any_branch_push",
                "passed": True,
                "message": message,
            }
        )

    with tempfile.TemporaryDirectory(prefix="lab1_publish_restore_") as tmp:
        root = Path(tmp)
        source, owner, remote, bootstrap, refs = fixture(root)
        for name in module.REQUIRED_BRANCHES - set(refs):
            git(source, "branch", name, refs["develop"])
        git(source, "tag", "-a", "lab1-v1.0.0", "-m", "Fixture release", refs["develop"])
        bundle = root / "calibration_lab1.bundle"
        git(source, "bundle", "create", str(bundle), "--all")
        work = root / "publication_work"
        restored, metadata = module.restore_workspace(GIT, bundle, work, None, ENV)
        for branch, head in metadata["source_refs"].items():
            assert git(restored.repo, "rev-parse", branch) == head
        assert git(restored.repo, "rev-parse", "refs/tags/lab1-v1.0.0") == git(
            source, "rev-parse", "refs/tags/lab1-v1.0.0"
        )
        again, metadata_again = module.restore_workspace(GIT, bundle, work, None, ENV)
        assert metadata_again == metadata
        records.append(
            {
                "name": "bundle_restore_preserves_all_refs_and_repeat_is_safe",
                "passed": True,
                "source_branch_count": len(metadata["source_refs"]),
            }
        )
        dirty = restored.repo / "unsaved.txt"
        dirty.write_text("Do not delete my work", encoding="utf-8")
        try:
            module.restore_workspace(GIT, bundle, work, None, ENV)
        except (RuntimeError, ValueError) as error:
            message = str(error)
        else:
            raise AssertionError("Dirty managed clone was accepted")
        assert dirty.read_text() == "Do not delete my work"
        records.append(
            {
                "name": "dirty_restore_refused_without_losing_user_file",
                "passed": True,
                "message": message,
            }
        )
        dirty.unlink()
        commit(source, "changed.txt", "A different bundle\n", "Changed bundle version")
        changed_bundle = root / "changed.bundle"
        git(source, "bundle", "create", str(changed_bundle), "--all")
        before_head = git(restored.repo, "rev-parse", "develop")
        try:
            module.restore_workspace(GIT, changed_bundle, work, None, ENV)
        except (RuntimeError, ValueError) as error:
            message = str(error)
        else:
            raise AssertionError("A different bundle overwrote existing managed workspace")
        assert git(restored.repo, "rev-parse", "develop") == before_head
        records.append(
            {
                "name": "different_bundle_refused_without_overwriting_existing_clone",
                "passed": True,
                "message": message,
            }
        )

    output = {
        "scope": "Actual local Git and bare remotes only; no GitHub, authentication, external network, Actions or protection writes.",
        "external_git_protocols_blocked": True,
        "all_passed": all(r["passed"] for r in records),
        "checks": records,
    }
    (BASE / "reports/LAB1/publisher_git_checks.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
