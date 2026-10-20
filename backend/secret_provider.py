"""Server-to-server credential retrieval. Never persists or returns credentials to UI."""
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from .engine import DomainError


def setting(name):
    filename = os.getenv(name + '_FILE')
    if filename:
        try:
            return Path(filename).read_text().strip()
        except OSError:
            raise DomainError('密钥文件不可读取') from None
    return os.getenv(name, '').strip()


def configured():
    return bool(os.getenv('QIANG_SECRET_ENDPOINT') or os.getenv('QIANG_AI_GATEWAY_TOKEN') or os.getenv('QIANG_AI_GATEWAY_TOKEN_FILE'))


def gateway_token():
    endpoint = os.getenv('QIANG_SECRET_ENDPOINT', '')
    if not endpoint:
        return setting('QIANG_AI_GATEWAY_TOKEN')
    parsed = urlsplit(endpoint)
    local = parsed.hostname in ('localhost', '127.0.0.1', '::1')
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not (
            parsed.scheme == 'https' or (local and parsed.scheme == 'http')):
        raise DomainError('密钥接口只允许 HTTPS 或本机 HTTP，且地址中不能包含凭据')
    service_token = setting('QIANG_SECRET_SERVICE_TOKEN')
    if not service_token:
        raise DomainError('密钥接口服务令牌未配置')
    try:
        with httpx.Client(timeout=httpx.Timeout(10, connect=3), follow_redirects=False, trust_env=False) as client:
            with client.stream('GET', endpoint, headers={'Authorization': 'Bearer ' + service_token}) as response:
                response.raise_for_status()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 16384:
                        raise ValueError('oversized')
        import json
        body = json.loads(raw)
        token = body.get('token')
        ttl = body.get('expires_in')
        if not isinstance(token, str) or not token.strip() or len(token) > 8192 or any(c.isspace() for c in token):
            raise ValueError('invalid token')
        if type(ttl) is not int or not 1 <= ttl <= 300:
            raise ValueError('invalid lifetime')
        return token
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise DomainError('密钥接口不可用或响应无效；未调用模型，请检查服务端配置') from None
