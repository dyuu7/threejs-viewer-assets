import json
import subprocess
import urllib.error
from pathlib import Path

import pytest
import release
from project import ValidationError, digest


@pytest.fixture
def repository(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    release.git("init", "-b", "main")
    release.git("config", "user.email", "tests@example.invalid")
    release.git("config", "user.name", "Release tests")
    release.git("config", "commit.gpgsign", "false")
    package = work / "src/threejs_assets"
    (package / "assets").mkdir(parents=True)
    (package / "assets/manifest.json").write_text('{"upstream":{"version":"0.186.1"}}')
    (package / "__init__.py").write_text('__version__ = "0.186.1"\n')
    release.git("add", ".")
    release.git("commit", "-m", "Initial release")
    release.git("remote", "add", "origin", str(origin))
    release.git("push", "-u", "origin", "main")
    return work


def commit(path, contents):
    Path(path).write_text(contents)
    release.git("add", path)
    release.git("commit", "-m", "Update")
    return release.git("rev-parse", "HEAD")


def test_plan_pins_existing_release_after_main_changes(repository, monkeypatch):
    pinned = release.git("rev-parse", "HEAD")
    candidate = commit("README.md", "Later documentation")
    release.git("push", "origin", "main")
    monkeypatch.setattr(release, "tag_commit", lambda tag: pinned)
    monkeypatch.setattr(release, "completed", lambda version: False)
    assert release.plan(candidate, False, "refs/heads/main") == {
        "revision": pinned,
        "needed": True,
    }
    monkeypatch.setattr(release, "completed", lambda version: True)
    assert not release.plan(candidate, False, "refs/heads/main")["needed"]
    with pytest.raises(ValidationError, match="reuses"):
        release.plan(candidate, True, "refs/heads/main")


def test_plan_rejects_mismatched_tag_and_unmerged_manual_release(repository, monkeypatch):
    monkeypatch.setattr(release, "tag_commit", lambda tag: None)
    candidate = commit("uv.lock", "updated")
    with pytest.raises(ValidationError, match="tag does not match"):
        release.plan(candidate, False, "refs/tags/v0.187.0")
    with pytest.raises(ValidationError, match="already be merged"):
        release.plan(candidate, False, "refs/heads/main")
    assert release.plan(candidate, True, "refs/heads/main")["revision"] == candidate


def test_candidate_rejects_changes_outside_resource_scope(repository):
    main = release.git("rev-parse", "origin/main")
    candidate = commit("README.md", "Not an upstream resource update")
    with pytest.raises(ValidationError, match="only resources"):
        release.check_candidate(candidate, main)


def test_accept_promotes_exact_commit_and_preserves_existing_tag(repository, monkeypatch):
    candidate = commit("uv.lock", "updated")
    calls = []

    def api(endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        if endpoint == "git/refs/heads/main":
            assert kwargs["data"] == {"sha": candidate, "force": False}
            release.git("push", "origin", f"{candidate}:main")

    monkeypatch.setattr(release, "api", api)
    monkeypatch.setattr(release, "tag_commit", lambda tag: None)
    release.accept(candidate, True)
    assert release.git("rev-parse", "origin/main") == candidate
    assert calls[-1][1]["data"] == {"ref": "refs/tags/v0.186.1", "sha": candidate}
    calls.clear()
    monkeypatch.setattr(release, "tag_commit", lambda tag: candidate)
    release.accept(candidate, True)
    assert not calls
    monkeypatch.setattr(release, "tag_commit", lambda tag: "0" * 40)
    with pytest.raises(ValidationError, match="Refusing to move"):
        release.accept(candidate, False)


def test_main_change_after_validation_stops_promotion(repository, monkeypatch):
    candidate = commit("uv.lock", "candidate")
    release.git("checkout", "-b", "concurrent", "origin/main")
    main = commit("README.md", "Concurrent change")
    release.git("push", "origin", f"{main}:main")
    monkeypatch.setattr(release, "api", lambda *a, **kw: pytest.fail("Unexpected GitHub mutation"))
    with pytest.raises(ValidationError, match="main advanced"):
        release.accept(candidate, True)
    assert release.git("rev-parse", "origin/main") == main


def test_nonforced_promotion_rejects_race_after_ancestry_check(repository, monkeypatch):
    candidate = commit("uv.lock", "candidate")
    release.git("checkout", "-b", "concurrent", "origin/main")
    main = commit("README.md", "Concurrent change")

    def api(endpoint, **kwargs):
        assert endpoint == "git/refs/heads/main"
        assert kwargs["data"]["force"] is False
        release.git("push", "origin", f"{main}:main")
        # Exercise git's fast-forward rule rather than mocking a successful merge.
        release.git("push", "origin", f"{candidate}:main")

    monkeypatch.setattr(release, "api", api)
    with pytest.raises(subprocess.CalledProcessError):
        release.accept(candidate, True)
    assert release.git("rev-parse", "origin/main") == main


@pytest.mark.parametrize(
    "remote,message",
    [({"wheel": "different"}, "Published bytes differ"), ({"extra": "a"}, "Unexpected files")],
)
def test_published_conflicts_fail_closed(remote, message):
    with pytest.raises(ValidationError, match=message):
        release.missing_files({"wheel": "a", "sdist": "b"}, remote)


def test_partial_pypi_upload_stages_only_missing_file(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "wheel").write_bytes(b"wheel")
    (dist / "sdist").write_bytes(b"sdist")
    expected = {path.name: digest(path.read_bytes()) for path in dist.iterdir()}
    monkeypatch.setattr(release, "version_at", lambda revision: "0.186.1")
    monkeypatch.setattr(release, "distributions", lambda directory: expected)
    monkeypatch.setattr(release, "pypi_files", lambda version: {"wheel": expected["wheel"]})
    pending = tmp_path / "pending"
    assert release.stage(dist, pending) == {"pending": True}
    assert {path.name for path in pending.iterdir()} == {"sdist"}
    assert (pending / "sdist").read_bytes() == b"sdist"
    monkeypatch.setattr(release, "pypi_files", lambda version: expected)
    assert release.stage(dist, tmp_path / "complete") == {"pending": False}


def test_verify_waits_for_complete_registry_metadata(monkeypatch):
    expected = {"wheel": "a", "sdist": "b"}
    responses = iter([{}, {"wheel": "a"}, expected])
    monkeypatch.setattr(release, "version_at", lambda revision: "0.186.1")
    monkeypatch.setattr(release, "distributions", lambda directory: expected)
    monkeypatch.setattr(release, "pypi_files", lambda version: next(responses))
    monkeypatch.setattr(release.time, "sleep", lambda seconds: None)
    assert release.verify(Path("dist")) == ("0.186.1", expected)
    monkeypatch.setattr(release, "pypi_files", lambda version: {"wheel": "a"})
    with pytest.raises(ValidationError, match="incomplete"):
        release.verify(Path("dist"))


@pytest.mark.parametrize("status", [404, 403, 503])
def test_registry_errors_are_not_treated_as_unpublished(monkeypatch, status):
    def unavailable(url, **kwargs):
        raise urllib.error.HTTPError(url, status, "test", {}, None)

    monkeypatch.setattr(release.urllib.request, "urlopen", unavailable)
    if status == 404:
        assert release.pypi_files("0.186.1") == {}
    else:
        with pytest.raises(urllib.error.HTTPError):
            release.pypi_files("0.186.1")


@pytest.mark.parametrize("state", ["complete", "partial", "mismatch", "draft"])
def test_completed_release_requires_both_registries_to_agree(monkeypatch, state):
    version = "0.186.1"
    expected = {name: digest(name.encode()) for name in release.filenames(version)}
    assets = [
        {"name": name, "digest": "sha256:" + checksum, "state": "uploaded"}
        for name, checksum in expected.items()
    ]
    if state == "partial":
        assets.pop()
    elif state == "mismatch":
        assets[0]["digest"] = "sha256:changed"
    monkeypatch.setattr(
        release,
        "api",
        lambda *a, **kw: {"draft": state == "draft", "prerelease": False, "assets": assets},
    )
    monkeypatch.setattr(release, "pypi_files", lambda version: expected)
    assert release.completed(version) == (state == "complete")


def test_github_release_resumes_draft_without_replacing_uploaded_files(monkeypatch):
    expected = {"wheel": "a", "sdist": "b"}
    uploaded = []
    published = []
    record = {
        "id": 42,
        "draft": True,
        "prerelease": False,
        "assets": [{"name": "wheel", "digest": "sha256:a", "state": "uploaded"}],
    }
    monkeypatch.setattr(release, "verify", lambda directory: ("0.186.1", expected))
    monkeypatch.setattr(release, "tag_commit", lambda tag: "checked")
    monkeypatch.setattr(release, "git", lambda *args: "checked")

    def api(endpoint, **kwargs):
        if kwargs.get("method") == "PATCH":
            assert len(record["assets"]) == 2
            published.append(kwargs["data"])
        return json.loads(json.dumps(record))

    def upload(*args):
        uploaded.append(args)
        record["assets"].append({"name": "sdist", "digest": "sha256:b", "state": "uploaded"})

    monkeypatch.setattr(release, "api", api)
    monkeypatch.setattr(release, "command", upload)
    release.github_release(Path("dist"))
    assert len(uploaded) == 1 and str(Path("dist/sdist")) in uploaded[0]
    assert published == [{"draft": False}]


@pytest.mark.parametrize(
    "ref,needed,sync",
    [
        ("refs/heads/main", True, False),
        ("refs/heads/main", False, True),
        ("refs/tags/v0.186.1", True, False),
    ],
)
def test_preparation_recovers_before_advancing_upstream(monkeypatch, ref, needed, sync):
    monkeypatch.setattr(release, "git", lambda *args: "current")
    monkeypatch.setattr(release, "plan", lambda *args: {"revision": "pinned", "needed": needed})
    result = release.prepare(ref)
    assert result["sync"] is sync
    assert result["revision"] == ("pinned" if ref == "refs/heads/main" and needed else "current")


def test_annotated_tags_resolve_to_the_release_commit(monkeypatch):
    responses = iter(
        [
            {"object": {"type": "tag", "sha": "annotated"}},
            {"object": {"type": "commit", "sha": "release"}},
        ]
    )
    monkeypatch.setattr(release, "api", lambda *args, **kwargs: next(responses))
    assert release.tag_commit("v0.186.1") == "release"
