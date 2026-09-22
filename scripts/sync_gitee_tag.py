"""Reuse an existing Gitee release tag only when its commit matches GitHub."""
from __future__ import annotations

import argparse
import base64
import os
import re
import subprocess

REMOTE = "https://gitee.com/shekongsk/auto-bdsp-rng.git"


def sync_tag(tag: str, *, remote: str = REMOTE, cwd=None, env=None) -> None:
    if re.fullmatch(r"v\d+\.\d+\.\d+", tag) is None:
        raise ValueError("Invalid release tag")
    ref = f"refs/tags/{tag}"

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=cwd, env=env,
                                       text=True, encoding="utf-8", timeout=120).strip()

    expected = git("rev-parse", f"{ref}^{{commit}}")

    def already_present() -> bool:
        output = git("ls-remote", remote, ref, ref + "^{}")
        refs = dict((name, sha) for sha, name in (line.split() for line in output.splitlines()))
        actual = refs.get(ref + "^{}", refs.get(ref))
        if actual is None:
            return False
        if actual != expected:
            raise RuntimeError(f"Remote tag {tag} points to {actual}, expected {expected}; refusing to overwrite")
        print(f"Reusing {tag}: commit {expected} matches", flush=True)
        return True

    if already_present():
        return
    try:
        git("push", remote, f"{ref}:{ref}")
    except subprocess.CalledProcessError:
        # Another publisher may have created the matching tag while we pushed.
        if already_present():
            return
        raise
    if not already_present():
        raise RuntimeError(f"Remote tag {tag} is missing after push")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    token = os.environ.get("GITEE_ACCESS_TOKEN")
    if not token:
        raise SystemExit("GITEE_ACCESS_TOKEN is missing")
    auth = base64.b64encode(("shekongsk:" + token).encode()).decode()
    print("::add-mask::" + auth, flush=True)
    env = dict(os.environ, GIT_CONFIG_COUNT="1",
               GIT_CONFIG_KEY_0="http.https://gitee.com/.extraheader",
               GIT_CONFIG_VALUE_0="Authorization: Basic " + auth)
    sync_tag(args.tag, env=env)
