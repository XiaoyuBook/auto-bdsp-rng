"""Select an existing tag for the independent Gitee publishing workflow."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

TAG_PATTERN = re.compile(r"v\d+\.\d+\.\d+")


def resolve_tag(event_name: str, event: dict, requested_tag: str, repository: str) -> str:
    if event_name != "workflow_run":
        if TAG_PATTERN.fullmatch(requested_tag) is None:
            raise ValueError("Expected an existing stable release tag (vX.Y.Z)")
        return requested_tag

    run = event["workflow_run"]
    if (run["conclusion"] != "success"
            or run["event"] not in ("push", "workflow_dispatch")
            or run["head_repository"]["full_name"] != repository):
        return ""
    tag = run["head_branch"]
    if not isinstance(tag, str) or TAG_PATTERN.fullmatch(tag) is None:
        return ""
    # A branch with a version-like name must not publish an unrelated tag.
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"],
        text=True, encoding="utf-8", timeout=30,
    ).strip()
    if commit != run["head_sha"]:
        raise ValueError("Published tag commit does not match the completed build")
    return tag


def main() -> None:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    tag = resolve_tag(os.environ["GITHUB_EVENT_NAME"], event,
                      os.environ.get("REQUESTED_TAG", ""), os.environ["GITHUB_REPOSITORY"])
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"tag={tag}\n")
    print(f"Mirroring published release {tag}" if tag else "No stable release tag to mirror", flush=True)


if __name__ == "__main__":
    main()
