import subprocess
import base64
from pathlib import Path
import runpy
import sys

import pytest

from scripts.sync_gitee_tag import sync_tag


@pytest.mark.parametrize('actions', [False, True])
def test_cli_masks_credentials_only_inside_actions(monkeypatch, capsys, actions):
    token = 'test-local-token'
    commit = 'a' * 40
    monkeypatch.setenv('GITEE_ACCESS_TOKEN', token)
    if actions:
        monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    else:
        monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    monkeypatch.setattr(sys, 'argv', ['sync_gitee_tag.py', '--tag', 'v3.3.1'])
    def output(command, **kwargs):
        if command[1] == 'rev-parse': return commit
        if command[1] == 'ls-remote': return commit + '\trefs/tags/v3.3.1'
        pytest.fail('A matching existing tag must not be pushed')
    monkeypatch.setattr(subprocess, 'check_output', output)
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'sync_gitee_tag.py'
    runpy.run_path(str(path), run_name='__main__')
    console = capsys.readouterr().out
    auth = base64.b64encode(('shekongsk:' + token).encode()).decode()
    assert token not in console
    assert (auth in console) is actions


def git(cwd, *args):
    return subprocess.check_output(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args], cwd=cwd, text=True).strip()


@pytest.fixture
def repositories(tmp_path):
    local = tmp_path / "local"
    remote = tmp_path / "remote.git"
    local.mkdir()
    git(local, "init", "--initial-branch=main")
    git(local, "commit", "--allow-empty", "-m", "initial")
    git(local, "tag", "-a", "v3.3.1", "-m", "release")
    git(local, "init", "--bare", str(remote))
    return local, remote


def test_missing_tag_is_pushed_and_repeat_is_safe(repositories):
    local, remote = repositories
    sync_tag("v3.3.1", remote=str(remote), cwd=local)
    before = git(remote, "rev-parse", "v3.3.1")
    sync_tag("v3.3.1", remote=str(remote), cwd=local)
    assert git(remote, "rev-parse", "v3.3.1") == before


def test_existing_lightweight_tag_with_same_commit_is_reused(repositories):
    local, remote = repositories
    git(local, "push", str(remote), "HEAD:refs/tags/v3.3.1")
    sync_tag("v3.3.1", remote=str(remote), cwd=local)
    assert git(remote, "cat-file", "-t", "v3.3.1") == "commit"
    assert git(remote, "rev-parse", "v3.3.1") == git(local, "rev-parse", "HEAD")


def test_conflicting_commit_is_rejected_and_preserved(repositories):
    local, remote = repositories
    git(local, "commit", "--allow-empty", "-m", "different")
    git(local, "push", str(remote), "HEAD:refs/tags/v3.3.1")
    before = git(remote, "rev-parse", "v3.3.1")
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        sync_tag("v3.3.1", remote=str(remote), cwd=local)
    assert git(remote, "rev-parse", "v3.3.1") == before


def test_invalid_tag_is_rejected():
    with pytest.raises(ValueError, match="Invalid"):
        sync_tag("--all")
