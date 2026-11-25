import importlib.util
from pathlib import Path
import unittest

from pydantic import ValidationError


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "amc_alarm"
    / "amc_alarm_api"
    / "amc_proto.py"
)
_SPEC = importlib.util.spec_from_file_location("amc_proto", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_PROTO = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_PROTO)


class AmcUserPasswordTests(unittest.TestCase):
    def test_response_user_accepts_omitted_password_and_retains_token(self):
        response_json = (
            '{"command":"loginUser","status":"Logged",'
            '"user":{"email":"user@example.com",'
            '"userState":"confirmed","token":"login-token"},'
            '"token":null}'
        )
        response = _PROTO.AmcCommandResponse.model_validate_json(
            response_json, strict=False
        )

        self.assertEqual(response.status, "Logged")
        self.assertIsNone(response.token)
        self.assertIsNone(response.user.password)
        self.assertEqual(response.user.token, "login-token")

    def test_response_user_accepts_password(self):
        user = _PROTO.AmcUser(
            email="user@example.com",
            password="secret",
            userState="Logged",
            token="login-token",
        )

        self.assertEqual(user.password, "secret")

    def test_response_user_still_requires_token(self):
        with self.assertRaises(ValidationError):
            _PROTO.AmcUser(
                email="user@example.com",
                userState="Logged",
            )

    def test_login_request_still_requires_password(self):
        with self.assertRaises(ValidationError):
            _PROTO.AmcLogin(email="user@example.com")


if __name__ == "__main__":
    unittest.main()
