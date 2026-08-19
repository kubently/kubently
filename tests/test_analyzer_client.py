"""The test-automation analyzer's client construction path.

`test-automation/analyzer.py` grades scenario runs with Gemini. It moved from the
end-of-life `google-generativeai` package to `google-genai` (#80), which is a
different API shape -- a `genai.Client` rather than module-level `configure()`
plus `GenerativeModel` objects -- so "does it still construct" is worth asserting
rather than discovering during a 20-minute scenario run.

Nothing here needs a real key or touches the network: `genai.Client(...)` does not
call out at construction time, and no `generate_content` call is made.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ANALYZER_PATH = Path(__file__).resolve().parents[1] / "test-automation" / "analyzer.py"

pytest.importorskip("google.genai", reason="test-automation extra: google-genai")


@pytest.fixture(scope="module")
def analyzer_module():
    """Import analyzer.py by path -- `test-automation` is not an importable name."""
    spec = importlib.util.spec_from_file_location("kubently_test_analyzer", ANALYZER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch):
    """A developer's real GOOGLE_API_KEY must not decide what these tests observe."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


def test_analyzer_uses_google_genai_not_the_retired_package(analyzer_module):
    """The EOL package must not be what got imported."""
    assert analyzer_module.HAS_GEMINI, "google-genai import failed"
    assert analyzer_module.genai.__name__ == "google.genai"
    assert "google.generativeai" not in sys.modules


def test_real_looking_key_constructs_a_client(analyzer_module):
    analyzer = analyzer_module.GeminiAnalyzer(api_key="AIzaSyNotARealKeyJustShaped")

    assert analyzer.initialized
    # The google-genai surface the analyzer actually calls, reached without a network trip.
    assert callable(analyzer.client.models.generate_content)


@pytest.mark.parametrize(
    "placeholder",
    [
        "your-gemini-api-key",  # README.md
        "your-google-key",  # docs/GETTING_STARTED.md
        "AIzaSy-replace-me-here",
    ],
)
def test_placeholder_keys_are_rejected_by_name(analyzer_module, placeholder):
    """Placeholders ship in the repo's own examples; they fail as an opaque 400
    from Gemini minutes into a run, so the analyzer rejects them up front."""
    analyzer = analyzer_module.GeminiAnalyzer(api_key=placeholder)

    assert not analyzer.initialized
    assert analyzer.client is None
    assert analyzer.api_key is None


def test_missing_key_does_not_construct_a_client(analyzer_module):
    analyzer = analyzer_module.GeminiAnalyzer()

    assert not analyzer.initialized
    assert analyzer.client is None


def test_model_ids_are_configurable(analyzer_module, monkeypatch):
    """#96 made the model ids configuration; the migration must not re-hardcode them."""
    monkeypatch.setenv("KUBENTLY_ANALYZER_MODEL", "gemini-test-pro")
    monkeypatch.setenv("KUBENTLY_ANALYZER_RCA_MODEL", "gemini-test-flash")

    spec = importlib.util.spec_from_file_location("kubently_test_analyzer_reload", ANALYZER_PATH)
    reloaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reloaded)

    assert reloaded.ANALYZER_MODEL == "gemini-test-pro"
    assert reloaded.ANALYZER_RCA_MODEL == "gemini-test-flash"
