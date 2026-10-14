import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from binmirror import license  # noqa: E402


def _resp(obj):
    r = io.BytesIO(json.dumps(obj).encode())
    r.__enter__ = lambda s=r: s
    r.__exit__ = lambda *a: False
    return r


class LicenseTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(license, "PRODUCT_ID", "pid==")
        p.start()
        self.addCleanup(p.stop)

    def test_status(self):
        self.assertEqual(license.status({}), "ask")
        self.assertEqual(license.status({"license_key": "k", "license_last_verified": 1000},
                                        now=1000 + 3600), "ok")
        self.assertEqual(license.status({"license_key": "k", "license_last_verified": 1000},
                                        now=1000 + 25 * 3600), "check")
        with mock.patch.object(license, "PRODUCT_ID", "REPLACE_WITH_PRODUCT_ID"):
            self.assertEqual(license.status({}), "ok")

    def test_offline_grace(self):
        cfg = {"license_last_verified": 0}
        self.assertFalse(license.offline_expired(cfg, now=6 * 86400))
        self.assertTrue(license.offline_expired(cfg, now=8 * 86400))

    def _verify(self, side_effect=None, ret=None):
        with mock.patch("urllib.request.urlopen", side_effect=side_effect, return_value=ret):
            return license.verify(" key ")

    def test_valid(self):
        self.assertEqual(self._verify(ret=_resp({"success": True, "purchase": {}}))[0], True)

    def test_refunded(self):
        self.assertIs(self._verify(ret=_resp({"success": True,
                                              "purchase": {"refunded": True}}))[0], False)

    def test_invalid_http(self):
        err = urllib.error.HTTPError(license.VERIFY_URL, 404, "nf", {},
                                     io.BytesIO(b'{"success":false,"message":"That license does not exist"}'))
        ok, msg = self._verify(side_effect=err)
        self.assertIs(ok, False)
        self.assertIn("does not exist", msg)

    def test_network(self):
        self.assertIsNone(self._verify(side_effect=urllib.error.URLError("offline"))[0])


if __name__ == "__main__":
    unittest.main()
