# AGENTS.md - astrbot_plugin_skland

AstrBot 森空岛自动签到插件。API 层移植自 Skland-Sign-In，支持明日方舟与终末地，面向 AstrBot 4.29+。

## 结构

```
main.py            # Star 插件：命令、定时任务、KV 用户存储、私聊推送
skland_api.py      # 森空岛 API：设备指纹 → OAuth grant → cred → binding → 签到
_conf_schema.json  # 插件配置 schema，唯一配置来源
metadata.yaml
```

## 命令

- `/skdlogin <token>`（仅私聊）：绑定 token 并立即签到
- `/skdlogout`（仅私聊）：解绑
- `/skd`：群聊显示群成员签到状态；私聊显示自己
- `/skdusers`：用户统计（管理员私聊可见详情）

## 开发约定

- Python 3.12+；依赖 httpx、pycryptodome、apscheduler（见 requirements.txt）
- 日志必须 `from astrbot.api import logger`，不用 stdlib logging（代理自 v4.26.8 引入，按调用方模块解析插件专属 logger）
- 不使用 `@register` 装饰器：已废弃，且不带 repo 的调用会把 metadata.yaml 的 repo 覆盖为 None；插件类由 `Star.__init_subclass__` 自动注册，元数据以 metadata.yaml 为准
- 配置只用 `_conf_schema.json`，代码内 fallback 必须与 schema 默认值一致
- `last_sign` 存 `YYYY-MM-DD` 日期；判断是否已签必须与当天日期比较（见 `_signed_today`），不能只判键存在
- KV 持久化用 `get_kv_data` / `put_kv_data`；更新用户数据只能整体覆盖同一 user dict，禁止用部分字段 dict 替换
- AstrBot API 以目标版本源码与官方文档为准：https://docs.astrbot.app/dev/plugin.html
- 提交前如环境可用则运行 `ruff check` 与 `python -m py_compile`
