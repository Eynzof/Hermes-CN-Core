"""A ``session.create`` that names no model, in a profile that configures none, must still commit a
policy its owner can restore. ``hermes`` sends no ``model`` unless the user typed ``-m`` (the gateway
resolves the profile default), so a profile with no ``model`` leaves ``LocalSessionPolicy.model``
None -- a value the dataclass declares. Refusing it on the next open (create publishes the receipt,
every later open re-restores it) made the whole launch fail as ``Error: storage_unavailable``.
"""
from types import SimpleNamespace

import pytest


async def _owner(home, monkeypatch):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection
    monkeypatch.setattr(run, '_hermes_home', home)
    monkeypatch.setenv('HERMES_HOME', str(home))  # a single-profile owner: this IS the launch home
    store = SessionStore(home / 'sessions', GatewayConfig())
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False,
                             _cached_agent_for=lambda route: None, _adapter_for_source=lambda source: None,
                             _evict_cached_agent=lambda route: None)
    owner = await initialize_session_authority(runner, profile_id=str(home), instance_id='fixture')
    return owner, AuthorityConnection(owner, object(), {'user_id': 'owner'})


@pytest.mark.asyncio
async def test_a_create_with_no_profile_model_opens_again(tmp_path, monkeypatch):
    from gateway.session_local import create_local_session
    from gateway.session_policy import restore_policy
    from hermes_state_local import local_receipt
    from hermes_state_runtime import RuntimeStoreError

    home = tmp_path / 'home'
    home.mkdir()
    (home / 'config.yaml').write_text('platform_toolsets: {cli: []}\n')
    owner, connection = await _owner(home, monkeypatch)
    try:
        ref = create_local_session(owner, connection.actor,
                                   {'request_id': 'no-model', 'source': 'cli', 'cwd': str(home),
                                    'toolsets': []})
        saved = local_receipt(owner.db, ref.session_id)['policy']
        assert saved['model'] is None  # no -m and no profile default: the route resolves per turn
        restored = restore_policy(saved)
        assert restored.model is None
        # A blank string is still not a stored model, only "none chosen is": the guard keeps refusing it.
        with pytest.raises(RuntimeStoreError, match='storage_unavailable'):
            restore_policy({**saved, 'model': ''})
    finally:
        await connection.close()
        owner.db.close()
