import os
import asyncio
import sqlite3
import logging

import discord
from discord.ext import commands, tasks
from discord import app_commands


# ==================================================
# CẤU HÌNH
# ==================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

OWNER_ID = 1535146655999983698

# ID người dùng được loại trừ (không bao giờ tự động gửi nội quy)
EXCLUDED_USER_ID = 1535146655999983698

DB_FILE = "voice_lock.sqlite3"

# Nội dung bảng nội quy mẫu
RULE_CONTENT = """Hãy thể hiện mình tử tế . 
                   🕊️
💙T.Th💙
Ở đây có Táo 🍏.

                       
Giây phút -

Ai rồi cũng sẽ phá vỡ Quy tắc vì đó  -"""

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("voice_lock_bot")


# ==================================================
# DATABASE
# ==================================================

db = sqlite3.connect(DB_FILE)

# Bảng lưu phòng voice bị khóa
db.execute("""
CREATE TABLE IF NOT EXISTS voice_lock (
    guild_id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
)
""")

# Bảng lưu kênh văn bản nhận thông báo nội quy
db.execute("""
CREATE TABLE IF NOT EXISTS voice_rule (
    guild_id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
)
""")

db.commit()


def save_lock(guild_id: int, channel_id: int):
    db.execute(
        """
        INSERT INTO voice_lock (guild_id, channel_id, enabled)
        VALUES (?, ?, 1)
        ON CONFLICT(guild_id)
        DO UPDATE SET
            channel_id = excluded.channel_id,
            enabled = 1
        """,
        (guild_id, channel_id),
    )
    db.commit()


def remove_lock(guild_id: int):
    db.execute(
        "DELETE FROM voice_lock WHERE guild_id = ?",
        (guild_id,),
    )
    db.commit()


def get_locks():
    rows = db.execute(
        """
        SELECT guild_id, channel_id
        FROM voice_lock
        WHERE enabled = 1
        """
    ).fetchall()

    return {
        guild_id: channel_id
        for guild_id, channel_id in rows
    }


def save_rule_channel(guild_id: int, channel_id: int):
    db.execute(
        """
        INSERT INTO voice_rule (guild_id, channel_id, enabled)
        VALUES (?, ?, 1)
        ON CONFLICT(guild_id)
        DO UPDATE SET
            channel_id = excluded.channel_id,
            enabled = 1
        """,
        (guild_id, channel_id),
    )
    db.commit()


def remove_rule_channel(guild_id: int):
    db.execute(
        "DELETE FROM voice_rule WHERE guild_id = ?",
        (guild_id,),
    )
    db.commit()


def get_rule_channels():
    rows = db.execute(
        """
        SELECT guild_id, channel_id
        FROM voice_rule
        WHERE enabled = 1
        """
    ).fetchall()

    return {
        guild_id: channel_id
        for guild_id, channel_id in rows
    }


# ==================================================
# INTENTS
# ==================================================

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.voice_states = True
intents.message_content = True


# ==================================================
# BOT
# ==================================================

class VoiceLockBot(commands.Bot):

    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
        )

        self.voice_targets = get_locks()
        self.rule_channels = get_rule_channels()
        self.cleaned_guild_commands = False
        self.reconnect_tasks = {}

    async def setup_hook(self):
        try:
            synced = await self.tree.sync()
            logger.info("Đã đồng bộ %s lệnh toàn cục.", len(synced))
        except Exception:
            logger.exception("Không thể đồng bộ lệnh toàn cục.")

        self.voice_monitor.start()

    async def on_ready(self):
        logger.info(
            "Bot online: %s | ID: %s",
            self.user,
            self.user.id if self.user else "unknown",
        )

        if not self.cleaned_guild_commands:
            self.cleaned_guild_commands = True

            for guild in self.guilds:
                try:
                    self.tree.clear_commands(guild=guild)
                    await self.tree.sync(guild=guild)
                    logger.info(
                        "Đã dọn lệnh riêng tại server %s (%s).",
                        guild.name,
                        guild.id,
                    )
                except Exception:
                    logger.exception(
                        "Không thể dọn lệnh riêng tại server %s.",
                        guild.id,
                    )

    async def try_reconnect(self, guild_id: int):
        channel_id = self.voice_targets.get(guild_id)
        if channel_id is None:
            return

        guild = self.get_guild(guild_id)
        if guild is None:
            return

        channel = guild.get_channel(channel_id)
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            logger.warning("Không tìm thấy phòng đã khóa: %s", channel_id)
            return

        member = guild.me
        if member is None:
            return

        permissions = channel.permissions_for(member)
        if not permissions.view_channel or not permissions.connect:
            logger.warning("Bot không có quyền truy cập phòng %s.", channel_id)
            return

        try:
            voice_client = guild.voice_client

            if voice_client and voice_client.is_connected():
                if voice_client.channel.id != channel_id:
                    await voice_client.move_to(channel)
                    logger.info("Bot đã quay về phòng %s.", channel_id)
            else:
                await channel.connect(timeout=20, reconnect=True)
                logger.info("Bot đã kết nối lại phòng %s.", channel_id)

        except discord.Forbidden:
            logger.warning("Discord từ chối quyền kết nối phòng %s.", channel_id)
        except discord.HTTPException:
            logger.exception("Lỗi Discord khi kết nối phòng %s.", channel_id)
        except asyncio.TimeoutError:
            logger.warning("Hết thời gian chờ kết nối phòng %s.", channel_id)
        except Exception:
            logger.exception("Lỗi không xác định khi kết nối phòng %s.", channel_id)

    @tasks.loop(seconds=10)
    async def voice_monitor(self):
        for guild_id in list(self.voice_targets.keys()):
            await self.try_reconnect(guild_id)

    @voice_monitor.before_loop
    async def before_voice_monitor(self):
        await self.wait_until_ready()

    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if self.user is None:
            return

        # --------------------------------------------------
        # 1. Quản lý trạng thái Voice của chính Bot
        # --------------------------------------------------
        if member.id == self.user.id:
            guild_id = member.guild.id
            if guild_id not in self.voice_targets:
                return

            target_id = self.voice_targets[guild_id]
            moved_away = (
                after.channel is None
                or after.channel.id != target_id
            )

            if moved_away:
                await asyncio.sleep(2)
                await self.try_reconnect(guild_id)
            return

        # --------------------------------------------------
        # 2. Xử lý gửi bảng nội quy cho User vào phòng
        # --------------------------------------------------
        # Bỏ qua nếu là bot hoặc ID nằm trong danh sách loại trừ
        if member.bot or member.id == EXCLUDED_USER_ID:
            return

        guild = member.guild
        rule_channel_id = self.rule_channels.get(guild.id)
        if rule_channel_id is None:
            return

        rule_channel = guild.get_channel(rule_channel_id)
        if not rule_channel or not hasattr(rule_channel, "send"):
            return

        # Kiểm tra xem user có mới vào room hay không
        target_voice_id = self.voice_targets.get(guild.id)
        has_entered = False

        if target_voice_id is not None:
            # Nếu bot có khóa phòng cụ thể: chỉ gửi khi user vào đúng phòng đó
            if after.channel is not None and after.channel.id == target_voice_id:
                if before.channel is None or before.channel.id != target_voice_id:
                    has_entered = True
        else:
            # Nếu chưa khóa phòng nào: gửi khi user vào bất kỳ phòng thoại nào
            if after.channel is not None and (before.channel is None or before.channel.id != after.channel.id):
                has_entered = True

        if has_entered:
            try:
                msg = f"{member.mention}\n{RULE_CONTENT}"
                await rule_channel.send(msg)
                logger.info(
                    "Đã gửi nội quy cho %s (%s) tại kênh #%s.",
                    member.display_name,
                    member.id,
                    rule_channel.name,
                )
            except discord.Forbidden:
                logger.warning("Bot thiếu quyền gửi tin nhắn ở kênh %s.", rule_channel_id)
            except Exception:
                logger.exception("Lỗi khi gửi tin nhắn nội quy.")


bot = VoiceLockBot()


# ==================================================
# KIỂM TRA QUYỀN
# ==================================================

def owner_only():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            raise app_commands.CheckFailure("Bạn không có quyền sử dụng lệnh này.")
        return True

    return app_commands.check(predicate)


def owner_only_prefix():
    async def predicate(ctx: commands.Context):
        if ctx.author.id != OWNER_ID:
            await ctx.send("Bạn không có quyền sử dụng lệnh này.")
            return False
        return True

    return commands.check(predicate)


# ==================================================
# /join — KHÓA VỊ TRÍ PHÒNG THOẠI
# ==================================================

@bot.tree.command(name="join", description="Khóa vị trí phòng thoại của bot.")
@owner_only()
async def join(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("Lệnh này chỉ dùng được trong server.", ephemeral=True)
        return

    member = interaction.guild.get_member(interaction.user.id)
    if member is None or member.voice is None:
        await interaction.response.send_message("Bạn cần vào phòng thoại trước khi dùng /join.", ephemeral=True)
        return

    channel = member.voice.channel
    if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
        await interaction.response.send_message("Không thể khóa vị trí ở phòng này.", ephemeral=True)
        return

    bot_member = interaction.guild.me
    if bot_member is None:
        await interaction.response.send_message("Không thể lấy thông tin bot trong server.", ephemeral=True)
        return

    permissions = channel.permissions_for(bot_member)
    if not permissions.view_channel or not permissions.connect:
        await interaction.response.send_message("Bot không có quyền vào phòng thoại này.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    bot.voice_targets[interaction.guild.id] = channel.id
    save_lock(interaction.guild.id, channel.id)

    try:
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.is_connected():
            if voice_client.channel.id != channel.id:
                await voice_client.move_to(channel)
        else:
            await channel.connect(timeout=20, reconnect=True)

        await interaction.followup.send(
            f"🔒 Đã khóa vị trí phòng thoại tại {channel.mention}.\n"
            "Bot sẽ cố gắng quay lại đây nếu bị chuyển phòng hoặc ngắt kết nối.",
            ephemeral=True,
        )

    except Exception:
        logger.exception("Không thể kết nối phòng thoại.")
        await interaction.followup.send(
            "Đã lưu phòng cần khóa, nhưng bot chưa kết nối được. Hãy kiểm tra quyền Connect và thử lại.",
            ephemeral=True,
        )


# ==================================================
# /leave — TẮT KHÓA VỊ TRÍ
# ==================================================

@bot.tree.command(name="leave", description="Tắt khóa vị trí và cho bot rời phòng thoại.")
@owner_only()
async def leave(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("Lệnh này chỉ dùng được trong server.", ephemeral=True)
        return

    guild_id = interaction.guild.id
    bot.voice_targets.pop(guild_id, None)
    remove_lock(guild_id)

    voice_client = interaction.guild.voice_client
    if voice_client and (voice_client.is_connected() or voice_client.is_connecting()):
        try:
            await voice_client.disconnect(force=True)
        except Exception:
            logger.exception("Lỗi khi ngắt kết nối voice.")
            await interaction.response.send_message(
                "Đã tắt chế độ khóa, nhưng bot gặp lỗi khi rời phòng.",
                ephemeral=True,
            )
            return

    await interaction.response.send_message(
        "🔓 Đã tắt khóa vị trí. Bot sẽ không tự quay lại phòng cũ.",
        ephemeral=True,
    )


# ==================================================
# /sen — GỬI TIN NHẮN
# ==================================================

@bot.tree.command(name="sen", description="Gửi tin nhắn vào kênh hiện tại.")
@app_commands.describe(noi_dung="Nội dung tin nhắn muốn gửi")
@owner_only()
async def sen(interaction: discord.Interaction, noi_dung: str):
    if interaction.guild is None:
        await interaction.response.send_message("Lệnh này chỉ dùng được trong server.", ephemeral=True)
        return

    if not interaction.channel or not hasattr(interaction.channel, "send"):
        await interaction.response.send_message("Không thể gửi tin nhắn ở kênh này.", ephemeral=True)
        return

    if len(noi_dung) > 2000:
        await interaction.response.send_message("Nội dung vượt quá giới hạn 2000 ký tự của Discord.", ephemeral=True)
        return

    try:
        await interaction.channel.send(noi_dung, allowed_mentions=discord.AllowedMentions.none())
        await interaction.response.send_message("Đã gửi tin nhắn.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("Bot không có quyền gửi tin nhắn ở kênh này.", ephemeral=True)
    except discord.HTTPException:
        logger.exception("Không thể gửi tin nhắn.")
        await interaction.response.send_message("Discord gặp lỗi khi gửi tin nhắn.", ephemeral=True)


# ==================================================
# NHÓM LỆNH /rule (SLASH COMMANDS)
# ==================================================

rule_group = app_commands.Group(name="rule", description="Cài đặt thông báo nội quy phòng thoại")


@rule_group.command(name="on", description="Kích hoạt gửi nội quy tại kênh hiện tại khi có người vào room.")
@owner_only()
async def slash_rule_on(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("Lệnh chỉ dùng được trong server.", ephemeral=True)
        return

    bot.rule_channels[interaction.guild.id] = interaction.channel.id
    save_rule_channel(interaction.guild.id, interaction.channel.id)

    await interaction.response.send_message(
        f"✅ Đã kích hoạt gửi nội quy tại {interaction.channel.mention} khi có user vào room.",
        ephemeral=True,
    )


@rule_group.command(name="off", description="Hủy kích hoạt gửi thông báo nội quy.")
@owner_only()
async def slash_rule_off(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("Lệnh chỉ dùng được trong server.", ephemeral=True)
        return

    bot.rule_channels.pop(interaction.guild.id, None)
    remove_rule_channel(interaction.guild.id)

    await interaction.response.send_message(
        "❌ Đã tắt chế độ tự động gửi bảng nội quy.",
        ephemeral=True,
    )


@rule_group.command(name="user", description="Gửi riêng bảng nội quy cho 1 user tại kênh này.")
@app_commands.describe(thanh_vien="Thành viên cần gửi nội quy")
@owner_only()
async def slash_rule_user(interaction: discord.Interaction, thanh_vien: discord.Member):
    if interaction.guild is None:
        await interaction.response.send_message("Lệnh chỉ dùng được trong server.", ephemeral=True)
        return

    if not interaction.channel or not hasattr(interaction.channel, "send"):
        await interaction.response.send_message("Không thể gửi tin nhắn tại kênh này.", ephemeral=True)
        return

    msg = f"{thanh_vien.mention}\n{RULE_CONTENT}"
    try:
        await interaction.channel.send(msg)
        await interaction.response.send_message(f"Đã gửi nội quy cho {thanh_vien.mention}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("Bot không có quyền gửi tin nhắn ở kênh này.", ephemeral=True)


bot.tree.add_command(rule_group)


# ==================================================
# LỆNH PREFIX !rule (DÙNG ĐƯỢC CẢ DẠNG !rule on / !rule off / !rule @user)
# ==================================================

@bot.group(name="rule", invoke_without_command=True)
@owner_only_prefix()
async def prefix_rule(ctx: commands.Context, target: discord.Member = None):
    if target is not None:
        # Nếu gõ: !rule @user
        msg = f"{target.mention}\n{RULE_CONTENT}"
        await ctx.send(msg)
    else:
        await ctx.send(
            "📋 **Hướng dẫn dùng lệnh rule:**\n"
            "- `!rule on` hoặc `/rule on`: Bật nội quy tại kênh hiện tại\n"
            "- `!rule off` hoặc `/rule off`: Tắt thông báo nội quy\n"
            "- `!rule @user` hoặc `/rule user @user`: Gửi riêng nội quy cho người đó"
        )


@prefix_rule.command(name="on")
@owner_only_prefix()
async def prefix_rule_on(ctx: commands.Context):
    bot.rule_channels[ctx.guild.id] = ctx.channel.id
    save_rule_channel(ctx.guild.id, ctx.channel.id)
    await ctx.send(f"✅ Đã kích hoạt gửi nội quy tại {ctx.channel.mention} khi có user vào room.")


@prefix_rule.command(name="off")
@owner_only_prefix()
async def prefix_rule_off(ctx: commands.Context):
    bot.rule_channels.pop(ctx.guild.id, None)
    remove_rule_channel(ctx.guild.id)
    await ctx.send("❌ Đã tắt chế độ tự động gửi bảng nội quy.")


# ==================================================
# XỬ LÝ LỖI SLASH COMMAND
# ==================================================

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        message = "Bạn không có quyền sử dụng lệnh này."
    else:
        logger.exception("Lỗi slash command: %s", error)
        message = "Lệnh gặp lỗi khi thực thi."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        logger.exception("Không thể gửi thông báo lỗi.")


# ==================================================
# CHẠY BOT
# ==================================================

if not TOKEN:
    raise RuntimeError("Thiếu biến môi trường DISCORD_TOKEN.")

bot.run(TOKEN)