"""Separate internal service; never mount this app in the public workbench."""
import hmac
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from .secret_provider import setting
from .engine import DomainError

app = FastAPI(title='QiangQuote internal key service', docs_url=None, redoc_url=None, openapi_url=None)


@app.get('/v1/secrets/ai-gateway')
def get_gateway_secret(request: Request):
    try:
        expected = setting('QIANG_SECRET_SERVICE_TOKEN')
        if not expected:
            raise HTTPException(503, '密钥服务尚未配置')
        supplied = request.headers.get('authorization', '')
        if not hmac.compare_digest(supplied.encode(), ('Bearer ' + expected).encode()):
            raise HTTPException(401, '服务认证失败')
        token = setting('QIANG_AI_GATEWAY_TOKEN')
        if not token:
            raise HTTPException(503, '模型密钥尚未配置')
    except DomainError:
        raise HTTPException(503, '密钥服务配置不可读取') from None
    return JSONResponse({'token': token, 'expires_in': 60}, headers={
        'Cache-Control': 'no-store, private', 'Pragma': 'no-cache', 'X-Content-Type-Options': 'nosniff'})
