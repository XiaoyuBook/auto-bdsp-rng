import json

import pytest

from scripts import resolve_gitee_release_tag as resolver

REPOSITORY = "XiaoyuBook/auto-bdsp-rng"
COMMIT = "a" * 40


def completed_build(**changes):
    return {"workflow_run": dict(
        conclusion="success", event="push", head_branch="v3.4.0",
        head_sha=COMMIT, head_repository={"full_name": REPOSITORY}, **changes,
    )}


@pytest.mark.parametrize("event_name", ["workflow_dispatch", "workflow_call"])
def test_manual_retry_accepts_existing_stable_tag(event_name):
    assert resolver.resolve_tag(event_name, {}, "v3.4.0", REPOSITORY) == "v3.4.0"


@pytest.mark.parametrize("tag", ["", "main", "v3.4.0-rc1", "v3.4.0\ntag=main"])
def test_manual_retry_rejects_invalid_tag(tag):
    with pytest.raises(ValueError, match="stable release tag"):
        resolver.resolve_tag("workflow_dispatch", {}, tag, REPOSITORY)


@pytest.mark.parametrize("event_name", ["push", "workflow_dispatch"])
def test_automatic_sync_requires_tag_commit_from_completed_build(monkeypatch, event_name):
    event = completed_build()
    event["workflow_run"]["event"] = event_name
    def git(command, **kwargs):
        assert command == ["git", "rev-parse", "--verify", "refs/tags/v3.4.0^{commit}"]
        return COMMIT + "\n"
    monkeypatch.setattr(resolver.subprocess, "check_output", git)
    assert resolver.resolve_tag("workflow_run", event, "v9.9.9", REPOSITORY) == "v3.4.0"


@pytest.mark.parametrize("field,value", [
    ("conclusion", "failure"), ("event", "pull_request"), ("head_branch", "main"),
    ("head_branch", "v3.4.0-rc1"), ("head_branch", None),
    ("head_repository", {"full_name": "fork/auto-bdsp-rng"}),
])
def test_non_release_builds_do_not_mirror(monkeypatch, field, value):
    event = completed_build()
    event["workflow_run"][field] = value
    def unexpected_git(*args, **kwargs):
        pytest.fail("Skipped builds must not look up tags")
    monkeypatch.setattr(resolver.subprocess, "check_output", unexpected_git)
    assert resolver.resolve_tag("workflow_run", event, "", REPOSITORY) == ""


def test_mismatched_version_branch_cannot_publish_tag(monkeypatch):
    monkeypatch.setattr(resolver.subprocess, "check_output", lambda *a, **k: "b" * 40)
    with pytest.raises(ValueError, match="does not match"):
        resolver.resolve_tag("workflow_run", completed_build(), "", REPOSITORY)


def test_main_writes_validated_tag_to_github_output(tmp_path, monkeypatch):
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps({}), encoding="utf-8")
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("REQUESTED_TAG", "v3.4.0")
    resolver.main()
    assert output.read_text(encoding="utf-8") == "tag=v3.4.0\n"
