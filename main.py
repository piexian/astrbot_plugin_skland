"""
AstrBot Plugin - 森空岛签到 (Skland Sign-In)

Commands:
- skd (group): Show sign-in status for all bound users in the group
- skd (private): Show user's own sign-in status
- skdlogin (private): Login with token and immediately sign in
- skdlogout (private): Logout and remove token
- skdusers (all): Show users and stats 

Config (AstrBot plugin config):
- auto_sign_enabled: 自动签到开关
- auto_sign_hour: 自动签到时间（小时，0-23）
- show_player_name: 显示玩家昵称（否则显示QQ昵称）
- auto_sign_delay: 签到延时
- max_users: 最大用户数量
"""

from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter, MessageChain
from astrbot.api.star import Context, Star
import asyncio
import random

from .skland_api import SklandAPI


class SklandPlugin(Star):
    """森空岛签到插件"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.api = SklandAPI(max_retries=3)
        self.scheduler = AsyncIOScheduler()


    def _get_config(self) -> dict:
        """获取当前配置"""
        return {
            "auto_sign_enabled": self.config.get("auto_sign_enabled", False),
            "auto_sign_hour": self.config.get("auto_sign_hour", 9),
            "show_player_name": self.config.get("show_player_name", True),
            "auto_sign_delay": self.config.get("auto_sign_delay", 10),
            "max_users": self.config.get("max_users", 10),
        }

    async def initialize(self):
        """插件初始化"""
        logger.info("森空岛签到插件已加载")
        config = self._get_config()
        if config.get("auto_sign_enabled", False):
            hour = config.get("auto_sign_hour", 9)
            self._start_auto_sign_job(hour)
        if not self.scheduler.running:
            self.scheduler.start()

    async def terminate(self):
        """插件卸载"""
        if self.scheduler.running:
            self.scheduler.shutdown()
        await self.api.close()
        logger.info("森空岛签到插件已卸载")

    # ==================== Auto Sign-In ====================

    def _start_auto_sign_job(self, hour: int = 9):
        """启动自动签到定时任务"""
        hour = max(0, min(23, hour))
        trigger = CronTrigger(hour=hour, minute=0)
        try:
            self.scheduler.remove_job("skland_auto_sign")
        except Exception:
            pass

        self.scheduler.add_job(
            self._auto_sign_all_users,
            trigger=trigger,
            id="skland_auto_sign",
            misfire_grace_time=3600,
        )
        logger.info(f"森空岛自动签到任务已启动，每天 {hour:02d}:00 执行")

    async def _auto_sign_all_users(self):
        """为所有已注册用户执行自动签到"""
        config = self._get_config()
        if not config.get("auto_sign_enabled", False):
            logger.info("自动签到已关闭，跳过执行")
            return

        logger.info("开始执行自动签到...")
        users = await self.get_kv_data("users", {})
        if not users:
            logger.info("没有已注册的用户，跳过自动签到")
            return
        
        # 自动签到的最大随机延时时间
        max_delay = config.get("auto_sign_delay", 10)

        for user_id, user_data in users.items():
            if "token" not in user_data:
                continue

            # 随机延时防风控
            if max_delay > 0:
                delay = random.uniform(0, max_delay)
                logger.info(f"处理下一个用户前等待 {delay:.2f} 秒")
                await asyncio.sleep(delay)

            try:
                token = user_data["token"]
                results, nickname = await self.api.do_full_sign_in(token)

                # 更新签到状态（按游戏聚合，全部角色已签才标记）
                self._apply_sign_results(user_data, results)

                # 构建消息
                message = f"🎮 森空岛自动签到结果\n\n{self._format_sign_status(results, nickname)}"
                await self._send_private_message(user_id, user_data, message)
                logger.info(f"用户 {user_id} ({nickname}) 自动签到完成")
            except Exception as e:
                logger.error(f"用户 {user_id} 自动签到失败: {e}")
                message = f"⚠️ 自动签到失败\n错误: {str(e)}\n请使用 /skdlogin 重新登录"
                await self._send_private_message(user_id, user_data, message)

        await self._merge_users_patch(users)
        logger.info("自动签到执行完毕")

    async def _send_private_message(self, user_id: str, user_data: dict, message: str):
        """使用统一会话ID发送私聊消息"""
        try:
            umo = user_data.get("umo")
            if not umo:
                logger.warning(f"用户 {user_id} 没有统一会话ID，无法发送私聊消息")
                return

            message_chain = MessageChain().message(message)
            await self.context.send_message(umo, message_chain)
            logger.info(f"已发送私聊消息给用户 {user_id}")
        except Exception as e:
            logger.error(f"发送私聊消息失败: {e}")

    # ==================== Helpers ====================

    def _is_signed_today(self, result) -> bool:
        if result.success:
            return True
        error = result.error.lower() if result.error else ""
        return any(k in error for k in ["已签到", "请勿重复", "重复签到", "already", "签到过", "今日已"])


    @staticmethod
    def _signed_today(user_data: dict, game: str) -> bool:
        """last_sign 存日期，等于今天才算已签"""
        return user_data.get("last_sign", {}).get(game) == datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _mark_signed(user_data: dict, game: str) -> None:
        user_data.setdefault("last_sign", {})[game] = datetime.now().strftime("%Y-%m-%d")

    # 签到流程允许写回的用户字段；token/umo 等绑定字段不在此列，避免并发覆盖
    _SIGN_MUTABLE_FIELDS = ("last_sign", "nickname", "last_username")
    _GAME_KEY = {"明日方舟": "arknights", "终末地": "endfield"}

    def _apply_sign_results(self, user_data: dict, results: list) -> None:
        """按游戏聚合结果，该游戏全部角色都已签才标记，避免多角色部分失败被当作已签"""
        by_game: dict = {}
        for r in results:
            by_game.setdefault(r.game, []).append(r)
        for game, rs in by_game.items():
            key = self._GAME_KEY.get(game)
            if key and all(self._is_signed_today(r) for r in rs):
                self._mark_signed(user_data, key)

    async def _merge_users_patch(self, patch: dict) -> None:
        """持久化前把字段级变更合并进最新 KV：不恢复已登出用户，不覆盖并发更新的绑定字段"""
        latest = await self.get_kv_data("users", {})
        for uid, user_data in patch.items():
            if uid not in latest:
                continue
            for key in self._SIGN_MUTABLE_FIELDS:
                if key in user_data:
                    latest[uid][key] = user_data[key]
        await self.put_kv_data("users", latest)
    def _format_sign_status(self, results: list, nickname: str = "") -> str:
        if not results:
            return "没有绑定游戏"
        lines = []
        if nickname:
            lines.append(f"【{nickname}】")
        for r in results:
            if r.success or self._is_signed_today(r):
                award = ", ".join(r.awards) if getattr(r, "awards", None) else "无奖励"
                lines.append(f"{r.game} 已签到 ({award})")
            else:
                lines.append(f"{r.game} 签到失败: {r.error}")
        return "\n".join(lines)

    # ==================== Commands ====================

    @filter.command("skdhelp")
    async def skdhelp(self, event: AstrMessageEvent):
        """森空岛签到插件帮助"""
        yield event.plain_result(
            "森空岛签到插件帮助\n"
            "1. 私聊机器人发送/skdlogin <token> 登录并签到\n"
            "2. 私聊机器人发送/skdlogout 登出\n"
            "3. /skd 查看签到状态"
        )
    
    # @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.command("skdlogin")
    async def skdlogin(self, event: AstrMessageEvent, token: str = ""):
        # 验证是否在群内登录 如果是 则提示用户撤回消息且在私聊中使用
        group_id = getattr(event.message_obj, "group_id", None)
        user_name = event.get_sender_name()
        if group_id:
            yield event.plain_result(" 请在私聊中使用此命令登录\n为保护隐私，请将发送在群内的登录消息撤回")
            return
        
        config = self._get_config()
        
        user_id = event.get_sender_id()
        users = await self.get_kv_data("users", {})
        max_users = config.get("max_users", 10)
        
        if user_id not in users and max_users > 0 and len(users) >= max_users:
            yield event.plain_result(f"❌ 绑定失败：已达到最大用户数限制（{max_users}个）\n请联系管理员调整配置")
            return
        
        token = token.strip()
        if not token:
            yield event.plain_result(
                "请先获取token，方法如下:\n"
                "1. 登录 鹰角网络通行证 后，打开 (https://web-api.hypergryph.com/account/info/hg) 记下 content 字段的值（推荐）。\n"
                "   或者登录 森空岛网页版 (https://www.skland.com/) 后，\n"
                "   打开 (https://web-api.skland.com/account/info/hg) 记下 content 字段的值。\n"
                "   请复制类似这样的段落，content字段示例：N6QKb2C3d4A1b2C3d4，如果其中包含符号也一起复制\n"
                "2. 使用方法:\n"
                "   /skdlogin <content>")
            return
        yield event.plain_result("正在登录并签到，请稍候...")
        try:
            results, nickname = await self.api.do_full_sign_in(token)
            user_data = {
                "token": token,
                "nickname": nickname,
                "last_username": user_name,
                "last_sign": {},
                "bound_at": datetime.now().isoformat(),
                "platform_name": event.get_platform_name(),
                "umo": event.unified_msg_origin,  # 保存统一会话ID
            }
            self._apply_sign_results(user_data, results)
            await self.put_kv_data("users", {**(await self.get_kv_data("users", {})), user_id: user_data})
            yield event.plain_result(f"登录成功！\n{self._format_sign_status(results, nickname)}")
        except Exception as e:
            logger.error(f"skdlogin失败: {e}")
            yield event.plain_result(f"登录失败: {str(e)}")

    # @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.command("skdlogout")
    async def skdlogout(self, event: AstrMessageEvent):
        # 验证是否在群内登出 如果是 则提示用户撤回消息且在私聊中使用
        group_id = getattr(event.message_obj, "group_id", None)
        if group_id:
            yield event.plain_result(" 请在私聊中使用此命令登出\n为保护隐私，请将发送在群内的登出消息撤回")
            return
        
        user_id = event.get_sender_id()
        users = await self.get_kv_data("users", {})
        if user_id in users:
            del users[user_id]
            await self.put_kv_data("users", users)
            yield event.plain_result("已退出登录并清除绑定信息")
        else:
            yield event.plain_result("您尚未绑定森空岛账号")

    @filter.command("skdusers")
    async def skdusers(self, event: AstrMessageEvent):
        """查询当前注册用户数量"""
        
        users = await self.get_kv_data("users", {})
        groups = await self.get_kv_data("groups", {})
        config = self._get_config()
        max_users = config.get("max_users", 10)
        
        # 计算群聊分布
        group_stats = []
        for group_id, user_ids in groups.items():
            if user_ids:
                group_name = group_id  # 这里可以尝试获取群名称，如果可能的话
                group_stats.append(f"  • 群 {group_name}: {len(user_ids)} 人")
        
        # 今日已签用户（任一游戏）
        online_users = 0
        for user_data in users.values():
            if self._signed_today(user_data, "arknights") or self._signed_today(user_data, "endfield"):
                online_users += 1
        
        # 构建统计信息
        lines = [
            "📊 森空岛签到用户统计",
            "═══════════════════",
            f"📝 总注册用户: {len(users)} 人",
            # f"📈 今日活跃: {online_users} 人",
            f"📉 未签到用户: {len(users) - online_users} 人",
        ]
        
        # 检查管理员
        if event.is_admin():
            if max_users > 0:
                remaining = max(0, max_users - len(users))
                lines.append(f"🎯 最大限制: {max_users} 人")
                lines.append(f"🆓 剩余名额: {remaining} 人")
            
            # 限定私信查看
            if not getattr(event.message_obj, "group_id", None):
                # 添加群聊分布
                if group_stats:
                    lines.append("\n📌 群聊分布（仅代表群内同玩的数量）:")
                    lines.extend(group_stats)
                # 添加用户列表（如果用户数不多）
                if len(users) <= 20:
                    lines.append("\n👤 用户列表:")
                    for user_id, user_data in users.items():
                        nickname = user_data.get("nickname") or user_data.get("last_username", "未知")
                        last_sign = list(user_data.get("last_sign", {}).values())[-1] if user_data.get("last_sign") else "未签到"
                        lines.append(f"  • {nickname} (最后签到: {last_sign})")
                else:
                    lines.append("\n💡 用户数过多，不显示详细列表")
            else:
                lines.append("\n💡 如需查看详细用户列表请私信")
        yield event.plain_result("\n".join(lines))

    @filter.command("skd")
    async def skd(self, event: AstrMessageEvent):
        """群聊显示群成员签到状态，私聊显示自己"""
        user_id = event.get_sender_id()
        user_name = event.get_sender_name()
        group_id = getattr(event.message_obj, "group_id", None)
        is_group = bool(group_id)
        users_data = await self.get_kv_data("users", {})

        if is_group: # 群聊模式
            # 如果发送者已绑定，自动添加到该群
            if user_id in users_data:
                groups = await self.get_kv_data("groups", {})
                if group_id not in groups:
                    groups[group_id] = []
                if user_id not in groups[group_id]:
                    groups[group_id].append(user_id)
                    await self.put_kv_data("groups", groups)
            
            message_lines = [" 森空岛签到统计", "═══════════════", "方舟 | 终末 | 昵称", "-----------------"]
            group_users = (await self.get_kv_data("groups", {})).get(group_id, [])
            for uid in group_users:
                user_data = users_data.get(uid)
                if not user_data:
                    continue
                # 快速路径：仅当无需显示玩家名称或已有缓存玩家昵称时本地解析显示名，
                # 缺缓存昵称时落入完整流程补全；显示时统一应用 QQ 昵称回退
                both_signed = self._signed_today(user_data, "arknights") and self._signed_today(user_data, "endfield")
                show_name = self.config.get("show_player_name", True)
                cached = (user_data.get("nickname") or "").strip()
                if both_signed and (cached or not show_name):
                    if show_name:
                        nickname = cached
                    else:
                        nickname = (user_data.get("last_username") or "").strip() or "(未知)"
                    message_lines.append(f" ✅ | ✅ | {nickname}")
                    continue
                try:
                    results, nickname = await self.api.do_full_sign_in(user_data["token"])

                    # 同步发送者最新昵称（仅发送者本人记录，循环结束后统一合并持久化）
                    if uid == user_id and user_name and user_name != user_data.get("last_username"):
                        user_data["last_username"] = user_name

                    # 保存原始玩家昵称，显示时按配置回退 QQ 昵称
                    user_data["nickname"] = (nickname or "").strip()
                    display = user_data["nickname"]
                    if not display or not self.config.get("show_player_name", True):
                        display = (user_data.get("last_username") or "").strip() or "(未知)"

                    self._apply_sign_results(user_data, results)
                    users_data[uid] = user_data

                    ak_icon = "✅" if self._signed_today(user_data, "arknights") else "❌"
                    ef_icon = "✅" if self._signed_today(user_data, "endfield") else "❌"
                    message_lines.append(f" {ak_icon} | {ef_icon} | {display}")
                except Exception as e:
                    logger.error(f"skd 查询用户 {uid} 失败: {e}")
                    message_lines.append(" ⚠️ | ⚠️ | (Error)")
            await self._merge_users_patch(users_data)
            yield event.plain_result("\n".join(message_lines))
        else: # 私聊模式
            user_data = users_data.get(user_id)
            if not user_data:
                yield event.plain_result("你还未绑定账号，请使用 /skdlogin <token>")
                return
            try:
                results, nickname = await self.api.do_full_sign_in(user_data["token"])
                self._apply_sign_results(user_data, results)
                if nickname:
                    user_data["nickname"] = nickname.strip()
                await self._merge_users_patch({user_id: user_data})
                response = self._format_sign_status(results, nickname)
                yield event.plain_result(response)
            except Exception as e:
                yield event.plain_result(f"查询失败: {str(e)}")
