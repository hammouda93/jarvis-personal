from jarvis_agent.tools import route


def test_open_youtube():
    intent = route("Jarvis, ouvre YouTube")
    assert intent.name == "browser.open_url"
    assert intent.args["url"] == "https://www.youtube.com"


def test_open_vscode():
    intent = route("ouvre VS Code")
    assert intent.name == "app.open"
    assert intent.args["app"] == "vscode"


def test_search_web():
    intent = route("recherche météo tunis sur internet")
    assert intent.name == "browser.search"
    assert intent.args["query"] == "meteo tunis"


def test_unknown_is_safe():
    intent = route("supprime tous mes fichiers")
    assert intent.name == "unknown"
