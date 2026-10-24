"""Exercise the documentation gate against a real temporary Git repository."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('guide_check', Path(__file__).resolve().parents[1] / 'scripts/check_user_guide.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def repo(tmp_path):
    for directory in ('frontend', 'backend', 'docs'):
        (tmp_path / directory).mkdir()
    (tmp_path / 'frontend/package.json').write_text(json.dumps({'version': '0.106.0'}))
    (tmp_path / 'backend/main.py').write_text("app = FastAPI(version='0.106.0')\n")
    (tmp_path / 'docs/USER-GUIDE.md').write_text(
        '适用版本：0.106.0\n最后更新：2026-10-24\n'
        '[操作](#usage)\n<a id="usage"></a>\n[源文件](../backend/main.py)\n')
    def git(*args):
        return subprocess.run(['git', '-C', str(tmp_path), *args], check=True, capture_output=True)
    git('init')
    git('add', '.')
    git('-c', 'user.name=Doc Check', '-c', 'user.email=doc-check@example.invalid', 'commit', '-m', 'baseline')
    return tmp_path


def test_valid_baseline_and_document_only_change(repo):
    assert module.check_guide(repo, 'HEAD') == []
    with (repo / 'docs/USER-GUIDE.md').open('a') as file:
        file.write('\n补充操作说明。\n')
    assert module.check_guide(repo, 'HEAD') == []


def test_tracked_and_new_features_require_guide_update(repo):
    (repo / 'backend/main.py').write_text("app = FastAPI(version='0.106.0')\n# new behavior\n")
    assert any('未同步' in issue for issue in module.check_guide(repo, 'HEAD'))
    (repo / 'backend/main.py').write_text("app = FastAPI(version='0.106.0')\n")
    (repo / 'backend/new_feature.py').write_text('# new feature\n')
    assert any('未同步' in issue for issue in module.check_guide(repo, 'HEAD'))
    with (repo / 'docs/USER-GUIDE.md').open('a') as file:
        file.write('\n新增功能的使用说明。\n')
    assert module.check_guide(repo, 'HEAD') == []


def test_version_drift_blocks_release(repo):
    (repo / 'frontend/package.json').write_text(json.dumps({'version': '0.107.0'}))
    assert any('版本' in issue for issue in module.check_guide(repo))


def test_broken_navigation_and_image_are_reported(repo):
    with (repo / 'docs/USER-GUIDE.md').open('a') as file:
        file.write('\n[缺失章节](#missing)\n![示例](qa/missing.png)\n')
    issues = module.check_guide(repo)
    assert any('#missing' in issue for issue in issues)
    assert any('qa/missing.png' in issue for issue in issues)


def test_bad_base_fails_and_first_push_zero_base_is_allowed(repo):
    assert any('基线' in issue for issue in module.check_guide(repo, 'no-such-ref'))
    assert module.check_guide(repo, '0' * 40) == []


def test_test_only_change_needs_no_operation_update(repo):
    (repo / 'tests').mkdir()
    (repo / 'tests/test_example.py').write_text('def test_example(): pass\n')
    assert module.check_guide(repo, 'HEAD') == []
