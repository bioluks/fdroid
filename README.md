# KernelSU-Tailscaled F-Droid repository

Independent **stable-only binary repository** for the optional
[KernelSU-Tailscaled Android companion](https://github.com/bioluks/KernelSU-Tailscaled).
This repository is independently managed, not a fork or automatic mirror.

## Add to F-Droid

[**Add this repository**](https://bioluks.github.io/fdroid/repo?fingerprint=5D8E267599C867F92FF842FF9C5AA056482A9EEE65B87257686248D57A8AF84A)

- Repository: `https://bioluks.github.io/fdroid/repo`
- Repository signing-certificate SHA-256:
  `5D8E267599C867F92FF842FF9C5AA056482A9EEE65B87257686248D57A8AF84A`
- App: `io.github.bioluks.tailscaled`
- App signing-certificate SHA-256:
  `0924bb045985c248e9ddf9e9139e090aeac72446ae94cf41d8896af5dc626ae3`

The repository certificate authenticates indexes; the separate app certificate
is the existing developer APK signer. APKs are never rebuilt or re-signed here.

**The companion is optional and requires a rooted Android device plus the
separately installed KernelSU-Tailscaled module.** It is not a standalone VPN
and does not install the module. Obtain the matching module ZIP from the
[source project's stable releases](https://github.com/bioluks/KernelSU-Tailscaled/releases).

This is **not the official F-Droid repository**. These are unchanged
upstream developer-signed release binaries, not official F-Droid source builds
or an endorsement by F-Droid, Tailscale, or KernelSU. Source, release notes and
[build/release workflow provenance](https://github.com/bioluks/KernelSU-Tailscaled/actions/workflows/release.yml)
remain in the source project. Published byte hashes and source asset links are
recorded in [`repo/releases.json`](https://bioluks.github.io/fdroid/repo/releases.json).

## How updates work

[Publish workflow](.github/workflows/publish.yml) runs daily at 04:17 UTC, when
source/configuration changes land on `main`, or via manual dispatch. It uses
`fdroidserver==2.4.5` with hash-pinned Python dependencies and Android SDK
`apksigner` to:

1. Read this host's source releases, rejecting drafts and prereleases. Stable
   tags must be `vMAJOR.MINOR.PATCH`; the companion stream begins at `v3.0.0`.
2. Download the original companion APK, validate the supplied GitHub SHA-256
   digest when present, its size, valid APK signature, package ID, pinned app
   signer, matching version name and increasing version codes.
3. Reject changed bytes for any approved/published version and disappearance
   of a previously published stable release. Initial approved v3.0.0/v3.1.0
   hashes are committed in `approved-releases.json`; later pins are read from
   the published manifest and authenticated against the signed v2 index using
   this repository's certificate fingerprint.
4. Generate signed F-Droid v1 (`index-v1.jar`) and v2 (`entry.jar` plus
   `index-v2.json`) indexes. Only the generated public site is uploaded as a
   Pages artifact. GitHub Pages publishes it; generated APKs/indexes do not
   enter the source branch.

The workflow has no APK signing secret and no personal access token. Its
read-only `GITHUB_TOKEN` accesses public GitHub releases; only the deploy job
has Pages/OIDC write permissions. Repository signing material is supplied by
Actions secrets, decoded outside the public output and removed afterward.

On a failed update the existing Pages deployment remains intact. Investigate
the failed run rather than accepting replacement bytes or rotating the key.
Rollback a bad updater change in `main` and dispatch again, keeping the same
repository signing key. Do not reset public pins to bypass an integrity failure.

### First publication

The Pages publishing source is **GitHub Actions**, and the source branch is
`main`. Use manual dispatch with `bootstrap=true` **only for the first
publication**. Bootstrap allows 404 responses for absent prior state, but
refuses an already-existing signed repository. Routine scheduled/push updates
and normal manual dispatches leave `bootstrap=false`; missing previous state
then fails closed. The initial bootstrap is not an automatic fallback.

## Run the same updater locally

Requires Python 3.13, Java (`keytool` and `jarsigner`) and an Android SDK with
build-tools `apksigner`. Install the committed dependencies in a virtualenv:

```sh
python3.13 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.txt
export ANDROID_HOME="$HOME/Library/Android/sdk"
# JAVA_HOME must point to your installed JDK if Java is not already on PATH.
# Set FDROID_KEYSTORE to a private existing PKCS12/JKS file outside the source
# and output tree, and FDROID_KEY_ALIAS to its repository key alias.
# Load FDROID_KEYSTORE_PASSWORD securely; do not paste it into shell history.
.venv/bin/python update.py --output /private/path/to/new-public-site
```

`FDROID_KEYSTORE_PASSWORD` is used for both the store and key password. The
key file must have mode `0600`. Output must be absent or empty and contains
`index.html` and `repo/`; never publish the source directory or temporary
configuration. The generated `config.yml` and keys remain outside public
output. Keep a protected offline backup of the key, alias and password:
losing the key prevents seamless updates for existing F-Droid subscribers.

The same script can operate independently against a Forgejo source and a
separate repository certificate, without fetching any GitHub releases:

```sh
.venv/bin/python update.py \
  --source-type forgejo \
  --source-url https://git.dreamscape.one/execd/KernelSU-Tailscaled \
  --repo-url https://git.dreamscape.one/execd/fdroid/raw/branch/site/repo \
  --output /private/path/to/new-forgejo-site
```

That channel must maintain its own source/configuration, signer, credentials
and publication mechanism; this workflow does not mirror GitHub into Forgejo.
Only an initial empty publication uses `--bootstrap`. Never share channel
signing keys. Run updater policy regressions with `python -m unittest -v`.
Regenerate the dependency lock deliberately using
`uv pip compile requirements.in --universal --generate-hashes --output-file requirements.txt`.

## References

- [F-Droid binary repository setup](https://f-droid.org/en/docs/Setup_an_F-Droid_App_Repo/)
- [F-Droid metadata reference](https://f-droid.org/en/docs/Build_Metadata_Reference/)
- [GitHub Pages custom artifact workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
