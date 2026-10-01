"""Shared test setup.

Every test runs against a throwaway settings folder: the app keeps keys,
passwords and the geocode cache under ~/.dataintelligence_pro, and a test must
never read the real ones (a saved Kakao key would mean real network calls) or
write over them.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, 'app')):
    if path not in sys.path:
        sys.path.insert(0, path)


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    import deploy_report
    from core import core_customers, pc_settings, secure_report, visit_sync

    app_dir = tmp_path / 'settings'
    settings = str(app_dir / 'report_settings.json')
    cache = str(app_dir / 'geocode_cache.json')
    monkeypatch.setattr(core_customers, 'APP_DIR', str(app_dir))
    monkeypatch.setattr(core_customers, 'SETTINGS_PATH', settings)
    monkeypatch.setattr(core_customers, 'GEOCODE_CACHE_PATH', cache)
    monkeypatch.setattr(secure_report, 'SETTINGS_PATH', settings)
    monkeypatch.setattr(visit_sync, 'SETTINGS_PATH', settings)
    monkeypatch.setattr(pc_settings, 'APP_DIR', str(app_dir))
    monkeypatch.setattr(pc_settings, 'SETTINGS_PATH', settings)
    monkeypatch.setattr(pc_settings, 'GEOCODE_CACHE_PATH', cache)
    monkeypatch.setattr(deploy_report, 'SETTINGS_PATH', settings)

    # No test may reach Kakao: fail loudly instead of geocoding real addresses.
    def no_network(*args, **kwargs):
        raise AssertionError("test tried to call the Kakao API")
    monkeypatch.setattr(core_customers, '_kakao_get', no_network)
    return app_dir
