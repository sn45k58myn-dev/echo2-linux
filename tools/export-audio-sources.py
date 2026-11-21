#!/usr/bin/env python3
"""Export pinned public audio source references from local Git object stores."""

import argparse
import fnmatch
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "compat/audio-0.16.8.json"
RECEIPT = "snapshot.json"


def git(repo, *args, data=None):
    command = ["git", "--no-replace-objects", "-C", str(repo), *args]
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": ""}
    return subprocess.run(command, input=data, stdout=subprocess.PIPE, check=True, env=env).stdout


def source_files(repo, source):
    commit = source["commit"]
    resolved = git(repo, "rev-parse", "--verify", commit + "^{commit}").decode().strip()
    tree = git(repo, "rev-parse", "--verify", commit + "^{tree}").decode().strip()
    if resolved != commit or tree != source["tree"]:
        raise ValueError(f"commit or tree differs from the release manifest for {repo}")

    inventory = {}
    for row in git(repo, "ls-tree", "-rz", commit).split(b"\0"):
        if row:
            metadata, name = row.split(b"\t", 1)
            inventory[os.fsdecode(name)] = metadata.decode("ascii").split()
    selected = {}
    for group, patterns in source["groups"].items():
        for pattern in patterns:
            folder, _, filename = pattern.rpartition("/")
            matches = [name for name in inventory
                       if name.rpartition("/")[0] == folder
                       and fnmatch.fnmatchcase(name.rpartition("/")[2], filename)]
            if not matches:
                raise ValueError(f"unmatched source pattern: {pattern}")
            for name in matches:
                mode, kind, oid = inventory[name]
                path = PurePosixPath(name)
                if (kind != "blob" or mode not in ("100644", "100755")
                        or path.is_absolute() or ".." in path.parts or "\\" in name):
                    raise ValueError(f"unexpected source tree entry: {name}")
                item = selected.setdefault(name, {"blob": oid, "mode": mode, "groups": []})
                if group not in item["groups"]:
                    item["groups"].append(group)
    return selected


def read_blobs(repo, entries):
    objects = sorted({entry["blob"] for entry in entries.values()})
    query = "".join(oid + "\n" for oid in objects).encode("ascii")
    response = io.BytesIO(git(repo, "cat-file", "--batch", data=query))
    blobs = {}
    for oid in objects:
        returned, kind, length = response.readline().decode("ascii").split()
        content = response.read(int(length))
        blob_header = f"blob {len(content)}\0".encode("ascii")
        if (returned != oid or kind != "blob" or len(content) != int(length)
                or hashlib.sha1(blob_header + content).hexdigest() != oid
                or response.read(1) != b"\n" or b"\0" in content):
            raise ValueError(f"invalid or binary source object: {oid}")
        blobs[oid] = content
    return blobs


def materialize(stage, manifest_bytes, repositories):
    manifest = json.loads(manifest_bytes)
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    receipt = {"release": manifest["release"], "target": manifest["target"],
               "canonical_manifest_sha256": hashlib.sha256(canonical).hexdigest(),
               "sources": {}, "files": {}}
    for name, source in sorted(manifest["sources"].items()):
        entries = source_files(repositories[name], source)
        blobs = read_blobs(repositories[name], entries)
        receipt["sources"][name] = {key: source[key] for key in ("repository", "commit", "tree")}
        for path, item in sorted(entries.items()):
            relative = name + "/" + path
            content = blobs[item["blob"]]
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            if item["mode"] == "100755":
                target.chmod(0o755)
            receipt["files"][relative] = {**item, "sha256": hashlib.sha256(content).hexdigest()}
    evidence = json.loads((stage / "product/docs/community/echo2-0.16.8.json").read_bytes())
    if evidence["version"] != manifest["release"] or evidence["target"] != manifest["target"]:
        raise ValueError("pinned product record describes another release")
    for name in ("ui", "platform"):
        record = evidence["sources"][name]
        repo = receipt["sources"][name]
        if (repo["commit"] != record["commit"] or repo["tree"] != record["tree"]
                or repo["repository"] != "https://github.com/" + record["repository"]):
            raise ValueError(f"{name} differs from the pinned product release record")
    receipt_bytes = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode("utf-8")
    (stage / RECEIPT).write_bytes(receipt_bytes)
    return len(receipt["files"]), hashlib.sha256(receipt_bytes).hexdigest()


def compare(expected, actual):
    if actual.is_symlink() or not actual.is_dir():
        raise ValueError("output is missing, is a symlink, or is not a directory")
    expected_paths = {p.relative_to(expected) for p in expected.rglob("*")}
    actual_paths = {p.relative_to(actual) for p in actual.rglob("*")}
    if expected_paths != actual_paths:
        raise ValueError("output contains missing or unexpected entries")
    for relative in sorted(expected_paths):
        original = expected / relative
        copy = actual / relative
        if copy.is_symlink() or original.is_dir() != copy.is_dir():
            raise ValueError(f"output entry type differs: {relative}")
        if original.is_file():
            if (not copy.is_file() or copy.read_bytes() != original.read_bytes()
                    or (os.name == "posix" and bool(original.stat().st_mode & 0o111)
                        != bool(copy.stat().st_mode & 0o111))):
                raise ValueError(f"output file differs: {relative}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("product", "ui", "platform"):
        parser.add_argument("--" + name, type=Path, required=True,
                            help=f"local {name} checkout containing the manifest's pinned commit")
    parser.add_argument("--output", type=Path, default=ROOT / "build/compat-src",
                        help="dedicated output directory (default: build/compat-src)")
    parser.add_argument("--verify", action="store_true",
                        help="compare existing snapshot with Git, checking all paths and bytes")
    args = parser.parse_args()
    repositories = {name: getattr(args, name) for name in ("product", "ui", "platform")}
    output = args.output.absolute()
    if args.verify and not output.is_dir():
        raise ValueError("no snapshot to verify")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".compat-export-") as temporary:
        stage = Path(temporary) / "snapshot"
        stage.mkdir()
        count, digest = materialize(stage, MANIFEST.read_bytes(), repositories)
        if args.verify or output.exists() or output.is_symlink():
            compare(stage, output)
            action = "verified"
        else:
            stage.rename(output)
            action = "exported"
    print(f"{action}: {count} source files and 1 receipt; snapshot SHA-256 {digest}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
