# production-agent

快速启动与开发说明

1) 准备环境

```bash
python3 -m pip install -r requirements.txt
python3 -m pip install playwright pytest
python3 -m playwright install chromium
```

2) 配置密钥

- 复制 `.env.example` 为 `.env` 并填写 `DEEPSEEK_API_KEY`、`DINGTALK_CLIENT_ID`、`DINGTALK_CLIENT_SECRET`（不要将 `.env` 提交到源码库）。
- 或在系统环境中导出这些变量。

3) 生成并保存 WPS session（Playwright）

```bash
python3 tools/wps_save_session.py
```

登录完成并打开文档后回车，session 会保存到 `data/wps_storage_state.json`。

4) 运行测试

```bash
python3 -m pytest -q
```

5) 运行服务

```bash
./start.sh stream   # 启动钉钉监听
./start.sh daily    # 生成并推送一次日报
```

安全提醒：请勿将 `data/wps_storage_state.json`、`.env` 或其他凭证文件提交到公共仓库。已在 `.gitignore` 中列出这些文件。
