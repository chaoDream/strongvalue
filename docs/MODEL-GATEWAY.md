# 模型接入与数据处理边界

当前提供两条实际HTTP调用路径，均为可选配置。系统不读取或发送已有个人云服务密钥。凭据由运维在本机环境变量中配置，前端只能看到模型和接收方，不能读取token。

## OpenAI Responses

在部署进程的环境变量中设置：

```sh
export QIANG_AI_PROTOCOL=openai_responses
export QIANG_AI_GATEWAY_URL=https://api.openai.com/v1/responses
export QIANG_AI_MODEL=your-enabled-vision-and-structured-output-model
export QIANG_AI_GATEWAY_TOKEN=your-service-api-key
```

模型名称须是该企业账号实际可用且支持相应文件/视觉及结构化输出的模型，不预设模型权限或费用。不要把实际密钥写进版本库、截图或聊天。

实现依据：官方 [文件输入](https://developers.openai.com/api/docs/guides/file-inputs) 与 [结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)。使用 `input_file.file_data`、`text.format.json_schema`，请求 `store=false`，不声明或调用任何工具。`store=false` 是请求配置，不等于对供应商全部保留政策的保证；企业应单独确认保密、数据区域和服务协议。

支持本项目的PDF/DOCX/XLSX/TXT，单次最多8MB。PDF可包含页面图像；复杂CAD/BIM使用下方私有专业网关。响应必须完整且符合Schema，拒绝、截断、格式错误均不产生已审数量。实际模型标识取API响应元数据，而非模型生成文字。

## 私有 / 企业模型网关

```sh
export QIANG_AI_PROTOCOL=gateway
export QIANG_AI_GATEWAY_URL=http://127.0.0.1:9100/extract
export QIANG_AI_MODEL=your-private-model-version
# 可选
export QIANG_AI_GATEWAY_TOKEN=your-private-gateway-token
```

远程地址必须HTTPS，本机localhost允许HTTP。端点由运维配置，业务请求不能传入任意URL。HTTP客户端不跟随重定向、不继承系统代理，token只发送给配置端点。

网关收到JSON：`contract`、`model`、固定安全提取指令、`document`（文件名、SHA256、base64原件）、`text_evidence`、`response_schema`。协议版本 `qiangquote-extraction-v1`。可直接使用响应Schema定义服务输出。

返回结构示例（占位，不是实际工程数据）：

```json
{
  "model": "your-private-model-version",
  "candidates": [
    {
      "kind": "dimension",
      "key": "length",
      "label": "长度",
      "value": "10",
      "unit": "m",
      "component_name": "数学示例底板",
      "page": 1,
      "quote": "长度10000mm",
      "bbox": null
    }
  ],
  "warnings": []
}
```

`kind` 仅允许 `fact` 或 `dimension`。尺寸必须归一到米，原始标注保留在quote。引文必须与同页原始文字或人工标注匹配；否则只有真实PDF页内的标准化 `bbox=[x,y,width,height]` 可作为**未验证视觉证据**进入候选，仍需专业复核。没有引文匹配也没有可定位视觉框的候选被拒绝。模型自己的旧输出不会被当作新的原文证据。

## 使用与审计

启动服务后，在“图纸与算量 → 模型辅助解析”选择文件。界面显示接收方、模型和文件范围，用户明确确认后才调用。未配置时不会向外发文件。当前没有配置实际模型凭据，自动测试使用模拟HTTP，不产生付费调用。

任务保存原件哈希、来源证据、模型、提示版本、协议、任务键、响应哈希和解析记录。相同来源与版本的已完成任务不重复执行；来源文字/人工证据变化会形成新任务。失败可重试，配置接收方或模型改变后须重新确认。任何候选都不能自动变为已审工程量或清单金额。
# 专用密钥接口

支持由 `QIANG_SECRET_ENDPOINT` 指定独立认证接口，在每次调用前取得模型密钥。仓库提供 `backend.key_service:app`；接口启动、服务令牌注入和部署边界见 [迁移与密钥说明](TRANSFER-AND-SECRETS.md)。真实密钥与服务令牌均由部署系统注入，不纳入Git。此路径已通过模拟测试，尚未配置真实密钥服务。
