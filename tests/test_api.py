import json

from fastapi.testclient import TestClient

from desoto.app import create_app
from desoto.state import PassState


def client(tmp_path, pin=None, monkeypatch=None):
    if pin:
        monkeypatch.setenv("DESOTO_ADMIN_PIN", pin)
    path = tmp_path / "settings.json"
    return TestClient(create_app(PassState({"1": ["A"]}), settings_path=path)), path


def test_settings_save_and_reload(tmp_path):
    c, path = client(tmp_path)
    assert c.put("/api/settings", json={"bar_style": "pill"}).status_code == 200
    assert json.loads(path.read_text()) == {"bar_style": "pill"}
    assert c.put("/api/settings", json={"bar_style": "x"}).status_code == 422
    c2 = TestClient(create_app(PassState({"1": ["A"]}), settings_path=path))
    assert c2.get("/api/state").json()["settings"]["bar_style"] == "pill"


def test_pin(tmp_path, monkeypatch):
    c, _ = client(tmp_path, "1234", monkeypatch)
    assert c.get("/api/admin/auth").json() == {"pin_required": True}
    assert c.put("/api/settings", json={"bar_style": "pill"}).status_code == 401
    assert c.put("/api/settings", json={"bar_style": "pill"}, headers={"X-Admin-Pin": "1234"}).status_code == 200
