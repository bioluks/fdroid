#!/usr/bin/env python3
"""Import unchanged stable companion APKs, then build a signed binary F-Droid repo."""
import argparse
import hashlib
import html
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

PACKAGE_ID = "io.github.bioluks.tailscaled"
SIGNER_SHA256 = "0924bb045985c248e9ddf9e9139e090aeac72446ae94cf41d8896af5dc626ae3"
STABLE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ROOT = Path(__file__).resolve().parent


def https_url(value):
    url = urllib.parse.urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("Expected a plain HTTPS URL without credentials, query or fragment")
    return url


def source_api(source_type, source_url):
    url = https_url(source_url)
    parts = url.path.strip("/").split("/")
    if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
        raise ValueError("Source URL must identify one owner/repository")
    if source_type == "github":
        if url.netloc != "github.com":
            raise ValueError("GitHub source must use github.com")
        return f"https://api.github.com/repos/{'/'.join(parts)}/releases"
    if source_type != "forgejo":
        raise ValueError("Unsupported source type")
    return f"https://{url.netloc}/api/v1/repos/{'/'.join(parts)}/releases"


def request(url, *, api=False):
    headers = {"User-Agent": "KernelSU-Tailscaled-FDroid/1", "Accept": "application/json" if api else "*/*"}
    # Never send the GitHub token to APK/CDN/Forgejo URLs.
    if api and urllib.parse.urlsplit(url).netloc == "api.github.com" and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)


def download(url, destination):
    https_url(url)
    digest = hashlib.sha256()
    size = 0
    with request(url) as response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def fetch_releases(api_url):
    releases = []
    page = 1
    while True:
        with request(f"{api_url}?page={page}&per_page=50&limit=50", api=True) as response:
            batch = json.load(response)
        if not isinstance(batch, list):
            raise ValueError("Release API did not return an array")
        releases.extend(batch)
        if not batch:
            return releases
        page += 1


def release_assets(releases):
    assets = []
    seen = set()
    for release in releases:
        if not isinstance(release, dict):
            raise ValueError("Invalid release record")
        tag = release.get("tag_name", "")
        match = STABLE_TAG.fullmatch(tag)
        if release.get("draft") is not False or release.get("prerelease") is not False or not match:
            continue
        version = tuple(map(int, match.groups()))
        if version < (3, 0, 0):
            continue  # Companion APKs began at v3.0.0.
        if tag in seen:
            raise ValueError(f"Duplicate stable release: {tag}")
        seen.add(tag)
        expected = f"KernelSU-Tailscaled-companion-{tag}.apk"
        apks = [asset for asset in release.get("assets", []) if asset.get("name", "").endswith(".apk")]
        if len(apks) != 1 or apks[0].get("name") != expected:
            raise ValueError(f"Expected exactly one companion APK in {tag}")
        asset = apks[0]
        size = asset.get("size")
        if type(size) is not int or size <= 0:
            raise ValueError(f"Invalid APK size in {tag}")
        supplied = asset.get("digest")
        if supplied and (not isinstance(supplied, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", supplied)):
            raise ValueError(f"Unsupported APK digest in {tag}")
        url = asset.get("browser_download_url", "")
        https_url(url)
        assets.append({"tag": tag, "version": version, "url": url, "size": size,
                       "digest": supplied[7:] if supplied else None, "asset": expected})
    if not assets:
        raise ValueError("No supported stable companion releases found")
    return sorted(assets, key=lambda asset: asset["version"])


def check_immutable(pins, version, digest):
    if version in pins and pins[version] != digest:
        raise ValueError(f"Changed bytes for existing release {version}")


def check_apk_identity(identity, signer, tag):
    package, code, version = identity
    if package != PACKAGE_ID or signer != SIGNER_SHA256:
        raise ValueError(f"Unexpected package/signing certificate in {tag}")
    if type(code) is not int or code <= 0 or version != tag[1:]:
        raise ValueError(f"APK version does not match stable release {tag}")


def previous_pins(repo_url, fingerprint, bootstrap, work):
    from fdroidserver import index

    manifest_url = repo_url + "/releases.json"
    try:
        with request(manifest_url) as response:
            manifest = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404 and bootstrap:
            # Bootstrap is explicitly opt-in, and only valid for an absent repo.
            try:
                with request(repo_url + "/entry.jar"):
                    pass
            except urllib.error.HTTPError as entry_error:
                if entry_error.code == 404:
                    return {}
                raise
            raise ValueError("Refusing bootstrap: signed repository already exists")
        raise
    if (manifest.get("package") != PACKAGE_ID or manifest.get("source_url") != work["source_url"]
            or manifest.get("repo_url") != repo_url or not isinstance(manifest.get("releases"), list)):
        raise ValueError("Previous release manifest identity mismatch")
    # The public manifest records tag identity; a verified signed index authorizes
    # every hash. A modified web manifest cannot silently bless replacement bytes.
    signed, _ = index.download_repo_index_v2(repo_url + "?fingerprint=" + fingerprint)
    if set(signed.get("packages", {})) != {PACKAGE_ID}:
        raise ValueError("Previous index package identity mismatch")
    versions = signed["packages"][PACKAGE_ID]["versions"]
    pins = {}
    for release in manifest["releases"]:
        digest = release["sha256"]
        version = release["version_name"]
        entry = versions.get(digest)
        if not entry or entry["manifest"]["versionName"] != version or entry["file"]["sha256"] != digest:
            raise ValueError("Previous manifest does not match signed index")
        if version in pins:
            raise ValueError("Duplicate previous version")
        pins[version] = digest
    if len(pins) != len(versions) or not pins:
        raise ValueError("Incomplete previous release manifest")
    return pins


def build(args):
    if importlib.metadata.version("fdroidserver") != "2.4.5":
        raise ValueError("Install exactly fdroidserver==2.4.5")
    from fdroidserver import common
    import yaml

    args.source_url = args.source_url.rstrip("/")
    args.repo_url = args.repo_url.rstrip("/")
    api_url = source_api(args.source_type, args.source_url)
    if not https_url(args.repo_url).path.endswith("/repo"):
        raise ValueError("Repository URL must end in /repo")
    output = args.output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Output must be absent or an empty directory; publish it only after success")
    keystore = Path(os.environ["FDROID_KEYSTORE"]).expanduser().resolve(strict=True)
    if keystore.is_relative_to(output):
        raise ValueError("Signing key must not be inside public output")
    if keystore.stat().st_mode & 0o077:
        raise ValueError("Signing key must have private permissions (0600)")
    if not os.environ.get("FDROID_KEYSTORE_PASSWORD") or not os.environ.get("FDROID_KEY_ALIAS"):
        raise ValueError("FDROID_KEYSTORE_PASSWORD and FDROID_KEY_ALIAS must be set")
    keytool = str(Path(os.environ["JAVA_HOME"]) / "bin/keytool") if os.environ.get("JAVA_HOME") else "keytool"
    certificate = subprocess.run([keytool, "-exportcert", "-keystore", str(keystore),
                                  "-alias", os.environ["FDROID_KEY_ALIAS"],
                                  "-storepass:env", "FDROID_KEYSTORE_PASSWORD"],
                                 check=True, capture_output=True).stdout
    fingerprint = hashlib.sha256(certificate).hexdigest().upper()
    with tempfile.TemporaryDirectory(prefix="fdroid-private-") as temporary:
        work = Path(temporary)
        config = yaml.safe_load((ROOT / "config.template.yml").read_text())
        config.update(repo_url=args.repo_url, keystore=str(keystore),
                      repo_keyalias=os.environ["FDROID_KEY_ALIAS"])
        sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
        if sdk:
            config["sdk_path"] = sdk
        with (work / "config.yml").open("x") as config_file:
            os.chmod(config_file.name, 0o600)
            yaml.safe_dump(config, config_file)
        old_cwd = Path.cwd()
        os.chdir(work)
        try:
            common.config = None
            common.options = argparse.Namespace(verbose=False, quiet=False)
            common.read_config()
            # Set an explicit apksigner: a jarsigner-only fallback is not acceptable.
            common.config["apksigner"] = common.find_sdk_tools_cmd("apksigner")
            if not common.config["apksigner"]:
                raise ValueError("Android SDK apksigner is required")
            pins = previous_pins(args.repo_url, fingerprint, args.bootstrap, {"source_url": args.source_url})
            seed = json.loads((ROOT / "approved-releases.json").read_text())
            for version, digest in seed.items():
                check_immutable(pins, version, digest)
            pins.update(seed)
            repo = work / "repo"
            repo.mkdir()
            records = []
            codes = {}
            observed = set()
            for asset in release_assets(fetch_releases(api_url)):
                # Never use upstream-controlled filenames as paths.
                apk = repo / asset["asset"]
                source = https_url(asset["url"])
                expected_source = https_url(args.source_url)
                if source.netloc != expected_source.netloc or not source.path.startswith(expected_source.path + "/releases/download/"):
                    raise ValueError("APK download URL is not owned by the selected source repository")
                digest, size = download(asset["url"], apk)
                if size != asset["size"] or (asset["digest"] and digest != asset["digest"]):
                    raise ValueError(f"Release size/digest mismatch: {asset['tag']}")
                check_immutable(pins, asset["tag"][1:], digest)
                if not common.verify_apk_signature(str(apk)):
                    raise ValueError(f"Invalid APK signature: {asset['tag']}")
                identity = common.get_apk_id(str(apk))
                check_apk_identity(identity, common.apk_signer_fingerprint(str(apk)), asset["tag"])
                _, code, version = identity
                check_immutable(codes, code, digest)
                if code in codes:
                    raise ValueError("Duplicate APK versionCode")
                codes[code] = digest
                apk.rename(repo / f"{PACKAGE_ID}_{code}.apk")
                observed.add(version)
                records.append({"tag": asset["tag"], "version_name": version, "version_code": code,
                                "asset": asset["asset"], "source_url": asset["url"], "sha256": digest,
                                "size": size, "signer_sha256": SIGNER_SHA256})
            if not set(pins).issubset(observed):
                raise ValueError("Previously approved/published stable releases disappeared upstream")
            if any(left["version_code"] >= right["version_code"] for left, right in zip(records, records[1:])):
                raise ValueError("Stable release versionCodes are not increasing")
            metadata_dir = work / "metadata"
            metadata_dir.mkdir()
            metadata = yaml.safe_load((ROOT / "metadata" / f"{PACKAGE_ID}.yml").read_text())
            metadata.update(SourceCode=args.source_url, WebSite=args.source_url,
                            IssueTracker=args.source_url + "/issues",
                            CurrentVersion=records[-1]["version_name"], CurrentVersionCode=records[-1]["version_code"])
            (metadata_dir / f"{PACKAGE_ID}.yml").write_text(yaml.safe_dump(metadata, sort_keys=False))
            # fdroid update signs v1 plus v2 entry.jar, without touching APK signatures.
            # https://f-droid.org/en/docs/Setup_an_F-Droid_App_Repo/
            subprocess.run([sys.executable, "-m", "fdroidserver", "update", "--pretty", "--use-date-from-apk"], check=True)
            for required in ("index-v1.jar", "entry.jar", "index-v2.json"):
                if not (repo / required).is_file():
                    raise ValueError(f"fdroidserver did not generate {required}")
            # Catch accidental filtering by metadata/signature policy before publishing.
            generated = json.loads((repo / "index-v2.json").read_text())
            expected_hashes = {record["sha256"] for record in records}
            if set(generated.get("packages", {})) != {PACKAGE_ID} or set(generated["packages"][PACKAGE_ID]["versions"]) != expected_hashes:
                raise ValueError("Generated index does not contain all approved APKs")
            manifest = {"package": PACKAGE_ID, "source_type": args.source_type, "source_url": args.source_url,
                        "repo_url": args.repo_url, "repo_fingerprint": fingerprint, "releases": records}
            (repo / "releases.json").write_text(json.dumps(manifest, indent=2) + "\n")
            (repo / "fingerprint.txt").write_text(fingerprint + "\n")
            site = work / "site"
            site.mkdir()
            shutil.copytree(repo, site / "repo")
            add_url = args.repo_url + "?fingerprint=" + fingerprint
            (site / "index.html").write_text(
                '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                '<title>KernelSU-Tailscaled F-Droid repository</title><h1>KernelSU-Tailscaled companion</h1>'
                '<p>Independent, stable-only binary repository. Not the official F-Droid repository.</p>'
                '<p>Optional companion: requires root and the separately installed KernelSU-Tailscaled module.</p>'
                f'<p><a href="{html.escape(add_url, quote=True)}">Add repository to F-Droid</a></p>'
                f'<p>Repository SHA-256 fingerprint: <code>{fingerprint}</code></p>'
                f'<p><a href="{html.escape(args.source_url, quote=True)}">Source and release/build provenance</a> · '
                '<a href="repo/releases.json">Imported release hashes</a></p></html>\n')
            if output.exists():
                output.rmdir()  # It was checked empty; never replace an existing deployment.
            shutil.copytree(site, output)
        finally:
            os.chdir(old_cwd)
    print(f"Built {len(records)} stable APKs in {output}; repository fingerprint {fingerprint}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-type", choices=("github", "forgejo"), default="github")
    parser.add_argument("--source-url", default="https://github.com/bioluks/KernelSU-Tailscaled")
    parser.add_argument("--repo-url", default="https://bioluks.github.io/fdroid/repo")
    parser.add_argument("--output", type=Path, default=Path("_site"), help="Absent/empty public output directory")
    parser.add_argument("--bootstrap", action="store_true", help="Allow absent prior indexes on FIRST publication only")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        build(args)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        # CalledProcessError can contain command details, never the secret environment.
        print(f"Update refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
