"""A minimal discord.py bot with a slash command."""

import logging
import os
import re

import discord
from discord import app_commands
from dotenv import load_dotenv


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("tristana-bot")

USER_MENTION_PATTERN = re.compile(r"<@!?(\d+)>")

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN 환경변수가 설정되지 않았습니다.")


class TristanaBot(discord.Client):
    """Discord client that owns the application command tree."""

    def __init__(self) -> None:
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        """Register slash commands with Discord when the bot starts."""
        # Keep the button working for settlement messages created before a
        # restart. The button has a stable custom_id and the view never times out.
        self.add_view(SettlementView())
        synced_commands = await self.tree.sync()
        logger.info("슬래시 명령어 %d개를 동기화했습니다.", len(synced_commands))


bot = TristanaBot()


class SettlementView(discord.ui.View):
    """Persistent controls for marking a settlement as completed."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="정산 완료",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="settlement_complete",
    )
    async def complete_settlement(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["SettlementView"],
    ) -> None:
        """Add the person who pressed the button to the completion list."""
        if interaction.message is None or not interaction.message.embeds:
            await interaction.response.send_message(
                "정산 임베드를 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        original_embed = interaction.message.embeds[0]
        completed_field_index = next(
            (
                index
                for index, field in enumerate(original_embed.fields)
                if field.name == "정산 완료자"
            ),
            None,
        )
        completed_value = "아직 없음"
        if completed_field_index is not None:
            completed_value = original_embed.fields[completed_field_index].value

        completed_ids = list(dict.fromkeys(USER_MENTION_PATTERN.findall(completed_value)))
        user_id = str(interaction.user.id)
        if user_id in completed_ids:
            await interaction.response.send_message(
                "이미 정산 완료 처리한 사용자입니다.",
                ephemeral=True,
            )
            return

        completed_mentions = [f"<@{completed_id}>" for completed_id in completed_ids]
        completed_mentions.append(interaction.user.mention)
        updated_value = "\n".join(completed_mentions)

        updated_embed = discord.Embed.from_dict(original_embed.to_dict())
        if completed_field_index is None:
            updated_embed.add_field(
                name="정산 완료자",
                value=updated_value,
                inline=False,
            )
        else:
            updated_embed.set_field_at(
                completed_field_index,
                name="정산 완료자",
                value=updated_value,
                inline=False,
            )

        await interaction.response.edit_message(
            embed=updated_embed,
            view=self,
            allowed_mentions=discord.AllowedMentions(
                users=False,
                roles=False,
                everyone=False,
                replied_user=False,
            ),
        )


@bot.tree.command(name="help", description="봇 사용 안내")
async def help_command(interaction: discord.Interaction) -> None:
    """Respond to /help with a greeting."""
    await interaction.response.send_message("안녕하세요")


@bot.tree.command(name="정산", description="총 금액을 참여자와 균등하게 정산합니다.")
@app_commands.describe(
    total_amount="정산할 총 금액(원)",
    user_mentions="정산할 유저 멘션 목록 (예: @유저1 @유저2)",
    account_info="입금 계좌 정보 (예: 국민은행 123-456-789)",
)
async def settle_command(
    interaction: discord.Interaction,
    total_amount: app_commands.Range[int, 1],
    user_mentions: str,
    account_info: str,
) -> None:
    """Split a total amount between mentioned users and the command author."""
    if not account_info.strip():
        await interaction.response.send_message(
            "입금 계좌 정보를 입력해주세요. (예: 국민은행 123-456-789)",
            ephemeral=True,
        )
        return

    if len(account_info) > 1024:
        await interaction.response.send_message(
            "입금 계좌 정보는 1,024자 이내로 입력해주세요.",
            ephemeral=True,
        )
        return

    mentioned_ids = USER_MENTION_PATTERN.findall(user_mentions)
    if not mentioned_ids:
        await interaction.response.send_message(
            "정산할 유저를 멘션으로 한 명 이상 입력해주세요.",
            ephemeral=True,
        )
        return

    # A comma or whitespace may separate mentions, but other text is invalid.
    remaining_text = USER_MENTION_PATTERN.sub("", user_mentions)
    if remaining_text.replace(",", "").strip():
        await interaction.response.send_message(
            "유저 멘션 목록에는 멘션만 입력해주세요. (예: @유저1 @유저2)",
            ephemeral=True,
        )
        return

    # Do not charge or display the command author twice if they mention themselves.
    unique_ids = list(dict.fromkeys(mentioned_ids))
    if str(interaction.user.id) in unique_ids:
        await interaction.response.send_message(
            "명령어를 사용한 본인은 멘션 목록에서 제외해주세요.",
            ephemeral=True,
        )
        return

    participant_count = len(unique_ids) + 1
    base_amount, remainder = divmod(total_amount, participant_count)
    caller_amount = base_amount + remainder

    participants = [(interaction.user.mention, caller_amount)]
    participants.extend(
        (f"<@{user_id}>", base_amount) for user_id in unique_ids
    )

    embed = discord.Embed(
        title="정산 완료",
        description=(
            f"총 금액: **{total_amount:,}원**\n"
            f"참여 인원: **{participant_count}명**"
        ),
        color=discord.Color.blurple(),
    )

    # Keep each field below Discord's 1,024-character field-value limit.
    for start in range(0, len(participants), 20):
        chunk = participants[start : start + 20]
        field_name = "정산 내역" if start == 0 else "정산 내역 (계속)"
        embed.add_field(
            name=field_name,
            value="\n".join(f"{mention} · **{amount:,}원**" for mention, amount in chunk),
            inline=False,
        )

    if remainder:
        embed.set_footer(text=f"나머지 {remainder:,}원은 명령어 사용자가 부담합니다.")

    # Account information is intentionally sent only through DM, not exposed in
    # the public settlement message.
    await interaction.response.defer()
    failed_dm_ids = []
    for user_id, (_, amount) in zip(unique_ids, participants[1:]):
        try:
            user = bot.get_user(int(user_id)) or await bot.fetch_user(int(user_id))
            dm_embed = discord.Embed(
                title="💸 돈 내 씨발롬아",
                description=(
                    f"아직 정산 안 하셨죠? 지금 바로 **{amount:,}원** 입금해주세요.\n"
                    "미루지 말고 입금한 뒤 정산 완료 버튼을 눌러주세요."
                ),
                color=discord.Color.green(),
            )
            dm_embed.add_field(name="입금 계좌", value=account_info, inline=False)
            dm_embed.add_field(
                name="총 정산 금액",
                value=f"{total_amount:,}원 / {participant_count}명",
                inline=False,
            )
            await user.send(
                embed=dm_embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException, ValueError):
            failed_dm_ids.append(user_id)

    if failed_dm_ids:
        failed_mentions = " ".join(f"<@{user_id}>" for user_id in failed_dm_ids)
        embed.add_field(
            name="DM 전송 결과",
            value=f"다음 유저에게 DM을 보내지 못했습니다: {failed_mentions}",
            inline=False,
        )
    else:
        embed.add_field(
            name="DM 전송 결과",
            value="멘션한 모든 유저에게 입금 정보를 DM으로 전송했습니다.",
            inline=False,
        )

    embed.add_field(name="정산 완료자", value="아직 없음", inline=False)

    all_mentions = " ".join(mention for mention, _ in participants)
    await interaction.edit_original_response(
        content=f"정산 참여자: {all_mentions}",
        embed=embed,
        view=SettlementView(),
        allowed_mentions=discord.AllowedMentions(
            users=True,
            roles=False,
            everyone=False,
            replied_user=False,
        ),
    )


@bot.event
async def on_ready() -> None:
    if bot.user is not None:
        logger.info("%s 로 로그인했습니다.", bot.user)


bot.run(TOKEN)
