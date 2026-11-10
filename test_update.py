"""Run with: python -m unittest -v"""
import unittest

from update import PACKAGE_ID, SIGNER_SHA256, check_apk_identity, check_immutable, release_assets, source_api


class UpdatePolicyTests(unittest.TestCase):
    def release(self, tag="v3.1.0", **changes):
        release = {
            "tag_name": tag, "draft": False, "prerelease": False,
            "assets": [{"name": f"KernelSU-Tailscaled-companion-{tag}.apk",
                        "size": 82335, "digest": "sha256:" + "a" * 64,
                        "browser_download_url": f"https://github.com/bioluks/KernelSU-Tailscaled/releases/download/{tag}/KernelSU-Tailscaled-companion-{tag}.apk"}],
        }
        release.update(changes)
        return release

    def test_stable_only_and_previous_supported_release(self):
        releases = [self.release(), self.release("v3.0.0"), self.release("v3.2.0-rc1"),
                    self.release("v3.2.0", prerelease=True), self.release("v3.3.0", draft=True),
                    self.release("v2.2.0", assets=[])]
        self.assertEqual([asset["tag"] for asset in release_assets(releases)], ["v3.0.0", "v3.1.0"])

    def test_duplicate_release_identity_fails(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            release_assets([self.release(), self.release()])

    def test_unexpected_digest_algorithm_fails(self):
        release = self.release()
        release["assets"][0]["digest"] = "sha512:" + "a" * 128
        with self.assertRaisesRegex(ValueError, "digest"):
            release_assets([release])

    def test_replaced_release_bytes_fail(self):
        with self.assertRaisesRegex(ValueError, "Changed"):
            check_immutable({"3.1.0": "a" * 64}, "3.1.0", "b" * 64)

    def test_package_signer_and_version_are_pinned(self):
        check_apk_identity((PACKAGE_ID, 30010000, "3.1.0"), SIGNER_SHA256, "v3.1.0")
        for identity, signer in [(("other.package", 30010000, "3.1.0"), SIGNER_SHA256),
                                 ((PACKAGE_ID, 30010000, "3.1.0"), "b" * 64),
                                 ((PACKAGE_ID, 30010000, "3.2.0"), SIGNER_SHA256)]:
            with self.assertRaises(ValueError):
                check_apk_identity(identity, signer, "v3.1.0")

    def test_http_and_non_repository_urls_fail(self):
        for url in ("http://github.com/a/b", "https://github.com/a/b?token=secret", "https://github.com/a/b/c"):
            with self.assertRaises(ValueError):
                source_api("github", url)


if __name__ == "__main__":
    unittest.main()
