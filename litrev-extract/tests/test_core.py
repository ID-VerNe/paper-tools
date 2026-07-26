"""Quick tests for litrev-extract core components."""
import json
import os
import sys
import tempfile

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from litrev_extract.utils.json_utils import clean_json_string, extract_json_block, validate_json_schema
from litrev_extract.config import ConfigLoader
from litrev_extract.state import StateManager
from litrev_extract.templates import TemplateManager


def test_clean_json_string():
    """Test stripping markdown code fences."""
    # With fences and language tag
    assert clean_json_string("```json\n{\"key\": \"value\"}\n```") == '{"key": "value"}'

    # Already clean
    assert clean_json_string('{"a": 1}') == '{"a": 1}'

    print("  [OK] clean_json_string")


def test_validate_json_schema():
    """Test JSON schema validation."""
    assert validate_json_schema({"a": 1}) == True
    assert validate_json_schema({}) == False
    assert validate_json_schema([]) == False
    assert validate_json_schema("not dict") == False
    print("  [OK] validate_json_schema")


def test_extract_json_block():
    """Test extracting JSON from surrounding text."""
    result = extract_json_block('Here is the result: {"answer": 42}.')
    assert json.loads(result) == {"answer": 42}
    print("  [OK] extract_json_block")


def test_state_manager(tmpdir=None):
    """Test state manager persistence."""
    if tmpdir is None:
        tmpdir = tempfile.mkdtemp()
    state_path = os.path.join(tmpdir, "test_state.json")

    sm = StateManager(state_path, flush_interval=1)
    assert sm.is_completed("opus", "task|1") == False

    sm.mark_success("opus", "task|1", retries=0)
    assert sm.is_completed("opus", "task|1") == True
    assert sm.is_completed("opus", "task|2") == False

    sm.mark_failed("opus", "task|2", retries=3, error="API error")
    sm.flush()

    # Reload from file
    sm2 = StateManager(state_path)
    assert sm2.is_completed("opus", "task|1") == True
    assert sm2.is_completed("opus", "task|2") == False

    summary = sm2.get_summary("opus")
    assert summary["success"] == 1
    assert summary["failed"] == 1

    # Reset model
    sm2.reset_model("opus")
    assert sm2.is_completed("opus", "task|1") == False

    os.unlink(state_path)
    print("  [OK] StateManager")


def test_template_manager():
    """Test template rendering."""
    tm = TemplateManager()
    template = "Analyze this paper: {content}"
    result = tm.render(template, "Paper text here")
    assert "Paper text here" in result

    # Test truncation
    long = "X" * 100
    result2 = tm.render(template, long, truncation=10)
    assert len(result2) < len(template) + 100
    print("  [OK] TemplateManager")


def test_minimal_config():
    """Test loading a minimal YAML configuration."""
    config_yaml = """
project:
  name: "test-review"
  description: "Test"

input:
  directory: ./docs
  formats: [.md]

models:
  - alias: "test"
    api_key_env: "TEST_KEY"
    base_url: "https://api.test.com/v1"
    model_name: "test-model"

prompts:
  - name: "metadata"
    id: "v1_meta"
    file: "nonexistent.txt"
    system_prompt: "Extract metadata."
"""
    import tempfile
    import yaml
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(config_yaml)
        tmp_path = f.name

    config = ConfigLoader.from_file(tmp_path)
    assert config.project_name == "test-review"
    assert len(config.models) == 1
    assert config.models[0].alias == "test"
    assert len(config.prompts) == 1
    assert config.prompts[0].name == "metadata"

    os.unlink(tmp_path)
    print("  [OK] ConfigLoader")


if __name__ == "__main__":
    print("\nRunning litrev-extract core tests...\n")
    test_clean_json_string()
    test_validate_json_schema()
    test_extract_json_block()
    test_state_manager()
    test_template_manager()
    test_minimal_config()
    print("\nAll tests passed!\n")