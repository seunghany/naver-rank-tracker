import importlib.util
import unittest
from pathlib import Path

module_path = Path(__file__).resolve().parents[1] / "src" / "naver_login.py"
spec = importlib.util.spec_from_file_location("naver_login", module_path)
naver_login = importlib.util.module_from_spec(spec)
spec.loader.exec_module(naver_login)


class NaverLoginSessionStatusTests(unittest.TestCase):
    def test_device_add_redirect_is_not_a_login_failure(self):
        status = naver_login.session_status(
            "https://nid.naver.com/login/ext/deviceAdd",
            "<html><body>기기등록</body></html>",
        )
        self.assertEqual(status, "security_redirect")

    def test_logout_text_counts_as_logged_in(self):
        status = naver_login.session_status(
            "https://www.naver.com",
            "<html><body>로그아웃</body></html>",
        )
        self.assertTrue(status)


if __name__ == "__main__":
    unittest.main()
