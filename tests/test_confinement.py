"""The /files boundary is the only remotely-reachable code that reads and now writes
arbitrary paths, so it gets a test. share_target() must resolve only inside SHARE;
within_share() must not be fooled by a sibling-prefix directory."""

import os
import tempfile
import unittest

import rc_config
import rc_share

from tests._harness import restore_globals, share_dir


class ConfinementTest(unittest.TestCase):
    def setUp(self):
        restore_globals(self)
        # the module reads SHARE as a global on every call
        self.share = rc_config.SHARE = share_dir(self)

    def test_allows_inside(self):
        open(os.path.join(self.share, "a.txt"), "w").close()
        self.assertEqual(rc_share.share_target(""), self.share)
        self.assertEqual(
            rc_share.share_target("/a.txt"), os.path.join(self.share, "a.txt")
        )

    def test_rejects_escapes(self):
        os.symlink(
            tempfile.mkdtemp(), os.path.join(self.share, "esc")
        )  # symlink out of the share
        for rel in (
            "/../../etc/passwd",
            "/%2e%2e/x",
            "/esc/secret",
            "/\x00",
            "/..%2f..%2fetc",
        ):
            self.assertIsNone(rc_share.share_target(rel), rel)

    def test_within_share_sibling_prefix(self):
        self.assertTrue(rc_share.within_share(self.share))
        self.assertTrue(rc_share.within_share(os.path.join(self.share, "deep", "x")))
        self.assertFalse(
            rc_share.within_share(self.share + "-evil")
        )  # startswith w/o sep would pass


if __name__ == "__main__":
    unittest.main()
