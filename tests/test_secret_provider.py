import httpx
import pytest
from fastapi.testclient import TestClient
from backend import secret_provider
from backend.engine import DomainError
from backend.key_service import app


def test_key_service_requires_auth_and_disables_caching(monkeypatch, tmp_path):
    key = tmp_path / 'key'
    key.write_text('test-only-model-secret')
    monkeypatch.setenv('QIANG_AI_GATEWAY_TOKEN_FILE', str(key))
    monkeypatch.setenv('QIANG_SECRET_SERVICE_TOKEN', 'test-only-service-secret')
    client = TestClient(app)
    assert client.get('/v1/secrets/ai-gateway').status_code == 401
    assert client.get('/v1/secrets/ai-gateway', headers={'Authorization': 'Bearer wrong'}).status_code == 401
    response = client.get('/v1/secrets/ai-gateway', headers={'Authorization': 'Bearer test-only-service-secret'})
    assert response.json() == {'token': 'test-only-model-secret', 'expires_in': 60}
    assert 'no-store' in response.headers['cache-control']
    monkeypatch.delenv('QIANG_SECRET_SERVICE_TOKEN')
    assert client.get('/v1/secrets/ai-gateway').status_code == 503
    assert client.get('/docs').status_code == 404


def test_provider_retrieves_each_time_and_fails_closed(monkeypatch):
    monkeypatch.setenv('QIANG_SECRET_ENDPOINT', 'http://127.0.0.1:8101/v1/secrets/ai-gateway')
    monkeypatch.setenv('QIANG_SECRET_SERVICE_TOKEN', 'test-only-service-secret')
    monkeypatch.setenv('QIANG_AI_GATEWAY_TOKEN', 'must-not-fallback')
    original = httpx.Client
    seen = []
    def handler(request):
        seen.append(request)
        assert request.headers['authorization'] == 'Bearer test-only-service-secret'
        return httpx.Response(200, json={'token': 'test-only-model-secret', 'expires_in': 60})
    monkeypatch.setattr(httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    assert secret_provider.gateway_token() == 'test-only-model-secret'
    assert secret_provider.gateway_token() == 'test-only-model-secret'
    assert len(seen) == 2
    for response in [httpx.Response(302, headers={'location': 'https://evil.invalid'}),
                     httpx.Response(200, json={'token': 'leak', 'expires_in': 0}),
                     httpx.Response(500, text='private-debug-secret'),
                     httpx.Response(200, content=b'x' * 17000)]:
        monkeypatch.setattr(httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(lambda req: response), **kw))
        with pytest.raises(DomainError, match='密钥接口不可用') as error:
            secret_provider.gateway_token()
        assert 'private-debug-secret' not in str(error.value)
    monkeypatch.setenv('QIANG_SECRET_ENDPOINT', 'http://remote.invalid/key')
    with pytest.raises(DomainError, match='HTTPS'):
        secret_provider.gateway_token()
