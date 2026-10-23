# AGENTS.md - astrbot_plugin_skland

AstrBot 森空岛签到插件（Python）。移植自 Skland-Sign-In 的 API 实现，支持明日方舟与终末地签到。

## 结构

```
main.py          # Star 插件：命令、定时任务、KV 用户存储、私聊推送
skland_api.py    # 森空岛 API：数美设备指纹 → OAuth grant → cred → binding → 签到
_conf_schema.json # 插件配置 schema（唯一配置来源）
metadata.yaml
```

## 命令

- `/skdlogin <token>`（仅私聊）：绑定 token 并立即签到
- `/skdlogout`（仅私聊）：解绑
- `/skd`：群聊显示群成员签到状态；私聊显示自己
- `/skdusers`：用户统计（管理员私聊可见详情）
- `/skdhelp`：帮助

## 配置（_conf_schema.json）

auto_sign_enabled（默认 false）、auto_sign_hour（默认 9）、show_player_name、
auto_sign_delay（随机延迟上限秒数）、max_users。代码内 fallback 必须与 schema 默认值一致。

## 开发约定

- Python 3.10+；依赖 httpx、pycryptodome、apscheduler
- 配置用 _conf_schema.json，不使用已废弃的 astrbot.core.star.config.put_config
- last_sign 存 `YYYY-MM-DD` 日期；判断是否已签必须与当天日期比较（见 `_signed_today`）
- 持久化用 `get_kv_data` / `put_kv_data`（Star 的 KVStoreMixin）；更新用户数据只能整体覆盖同一 user dict，禁止用部分字段 dict 替换
- 日志用 `from astrbot.api import logger`，不用 stdlib logging
- 不使用 `@register` 装饰器：4.29 起正式废弃，且 4 参调用会把 metadata.yaml 的 repo 覆盖为 None；插件类由 Star.__init_subclass__ 自动注册
- 提交前如可用则运行 `ruff check`；不整体 `ruff format`（保持上游风格与最小 diff）
- AstrBot API 以本地检出 /root/work/AstrBot 为准（4.29.1）；`astrbot.api.logger` 自 v4.26.8 引入，为兼容下限
