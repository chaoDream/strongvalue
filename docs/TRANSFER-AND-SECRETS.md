# 数据、依赖与密钥迁移

`transfer/local-snapshot/` 保存本次本地工作区快照：SQLite 数据库和两份已有导出文件。目前工程没有上传原件，因此快照中没有 `originals/` 文件。项目、审计和导出记录保留；为避免公开仓库暴露可登录凭据，登录会话全部移除，所有账号密码哈希替换为不可登录值。原始运行目录不受影响。

## 恢复数据

停止目标工作台后，用 Python 3.12 执行。目标目录必须不存在，脚本先校验全部文件大小和 SHA-256，再恢复，不覆盖已有数据：

```sh
python3 scripts/restore_transfer.py transfer/local-snapshot --data-dir restored-data
export QIANG_DATA_DIR=restored-data
.venv/bin/python scripts/manage.py reset-password admin
.venv/bin/python scripts/manage.py reset-password estimator
.venv/bin/python scripts/manage.py reset-password reviewer
.venv/bin/python scripts/manage.py reset-password other
.venv/bin/python scripts/start.py
```

## 离线依赖目录

本次 `.venv`、`frontend/node_modules`、`.pnpm-store` 压缩为约52 MB的13个分卷，实际内容已纳入仓库，不是下载占位符。`manifest.json` 记录逐卷校验值、Python版本和平台（macOS arm64）。Python缓存和指向本机解释器的绝对符号链接未打包。

合并时同时校验迁移文件和分卷（两个输出路径均须不存在）：

```sh
python3 scripts/restore_transfer.py transfer/local-snapshot --data-dir restored-data --dependencies-archive dependencies.tar.gz
mkdir restored-dependencies
tar -xzf dependencies.tar.gz -C restored-dependencies
```

原生依赖适用于快照所列平台，虚拟环境脚本可能包含原机器路径；该包用于离线留档，不保证换目录后直接运行。安装独立Python后重建虚拟环境；跨平台部署仍按 README 使用 `requirements.lock.txt` 和 `frontend/pnpm-lock.yaml` 安装。前端构建产物可以重新生成。

重新生成迁移包（必须是新输出目录）：

```sh
.venv/bin/python scripts/package_transfer.py transfer/new-snapshot
```

公开仓库中的快照可以被所有人下载；将来包含真实图纸、合同或个人信息时，应先确认公开范围，再生成新快照。脚本只清除认证表中的密码和会话，不替代业务资料的人工审核。

## 独立密钥接口

真实模型密钥不在仓库中。独立服务通过服务令牌认证后提供 `GET /v1/secrets/ai-gateway`，成功响应为 `{"token":"<模型密钥>","expires_in":60}`。返回 `Cache-Control: no-store`，无公开接口文档。未配置返回503，未认证返回401。该服务不挂载到工作台，不应通过公网反向代理公开。

由部署系统创建两个仅服务账号可读的文件：模型密钥 `/run/secrets/qiang-model-key`，随机服务令牌 `/run/secrets/qiang-service-token`。不要把文件提交到Git，不要在命令行中写真实密钥。

密钥服务启动（独立终端或服务进程）：

```sh
export QIANG_AI_GATEWAY_TOKEN_FILE=/run/secrets/qiang-model-key
export QIANG_SECRET_SERVICE_TOKEN_FILE=/run/secrets/qiang-service-token
.venv/bin/python -m uvicorn backend.key_service:app --host 127.0.0.1 --port 8101
```

工作台进程设置：

```sh
export QIANG_SECRET_ENDPOINT=http://127.0.0.1:8101/v1/secrets/ai-gateway
export QIANG_SECRET_SERVICE_TOKEN_FILE=/run/secrets/qiang-service-token
export QIANG_AI_MODEL=your-enabled-model-version
export QIANG_AI_GATEWAY_URL=https://your-enterprise-gateway.example/extract
.venv/bin/python scripts/start.py
```

服务令牌是启动信任凭据，需要由部署系统注入，不能从无需认证的接口领取。远程密钥服务必须使用HTTPS，本机可用HTTP。工作台每次模型调用时获取一次密钥，不持久化、不通过浏览器返回，不缓存；接口故障、重定向、超时或无效响应时终止本次模型调用，不回退到其他密钥。`expires_in` 是客户端最大缓存约定，本服务不自动轮换供应商密钥；轮换时更新服务端密钥文件即可。未配置专用接口时仍兼容已有环境变量或密钥文件方式。

本次仅完成本地模拟测试；仓库中没有真实模型密钥，也未启动实际密钥服务或调用付费模型。
