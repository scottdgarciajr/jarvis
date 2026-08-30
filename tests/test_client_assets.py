from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_sphere_interface_assets_are_registered():
    index = read("src/jarvis/client/index.html")
    app = read("src/jarvis/client/app.js")
    sphere = read("src/jarvis/client/sphere.js")

    assert "/static/sphere.css" in index
    assert "/static/sphere.js" in index
    assert "interface-select" in index
    assert "controls-toggle" in index
    assert "top-actions" in index
    assert "JarvisSphere.create" in app
    assert "jarvisInterface" in app
    assert "setControlsVisible" in app
    assert "server_tv_mode" in app
    assert "colorFromTool" in sphere
    assert "setColor" in sphere
    assert "prefers-reduced-motion" in sphere
