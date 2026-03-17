"""Telegram message utilities — HTML formatting, splitting, and conversion."""
from __future__ import annotations

import html
import re

from telegram import Bot, Update
from telegram.constants import ParseMode

MAX_TG_MSG = 4096


def escape_html(text: str) -> str:
    """Escape HTML special characters for Telegram."""
    return html.escape(str(text))


def md_to_tg_html(text: str) -> str:
    """Convert common Markdown to Telegram-supported HTML tags."""
    code_blocks: list[str] = []

    def _save_block(m: re.Match) -> str:
        code_blocks.append(html.escape(m.group(2)))
        return f"\x00CB{len(code_blocks) - 1}\x00"

    text = re.sub(r"```(\w*)\n?(.*?)```", _save_block, text, flags=re.DOTALL)

    inline_codes: list[str] = []

    def _save_inline(m: re.Match) -> str:
        inline_codes.append(html.escape(m.group(1)))
        return f"\x00IC{len(inline_codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", _save_inline, text)

    text = html.escape(text)

    # Headings → bold
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    # Italic
    text = re.sub(r"(?<!\w)\*([^*]+?)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_([^_]+?)_(?!\w)", r"<i>\1</i>", text)
    # Strikethrough
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    for i, code in enumerate(code_blocks):
        text = text.replace(f"\x00CB{i}\x00", f"<pre>{code}</pre>")
    for i, code in enumerate(inline_codes):
        text = text.replace(f"\x00IC{i}\x00", f"<code>{code}</code>")

    return text


def split_html_chunks(text: str, max_len: int = MAX_TG_MSG) -> list[str]:
    """Split HTML text into <=max_len-char chunks without breaking <pre> tags."""
    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len

        candidate = text[:split_at]
        open_count = candidate.count("<pre>")
        close_count = candidate.count("</pre>")
        if open_count > close_count:
            candidate += "\n</pre>"
            text = "<pre>" + text[split_at:].lstrip("\n")
        else:
            text = text[split_at:].lstrip("\n")

        chunks.append(candidate)
    return chunks


async def send_long(update: Update, text: str) -> None:
    """Convert Markdown to Telegram HTML and send in chunks."""
    converted = md_to_tg_html(text)
    for chunk in split_html_chunks(converted):
        try:
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
        except Exception:
            plain = re.sub(r"<[^>]+>", "", chunk)
            await update.message.reply_text(plain)


async def send_long_to_chat(bot: Bot, chat_id: int, text: str) -> None:
    """Send formatted text directly to a chat_id (for scheduled jobs)."""
    converted = md_to_tg_html(text)
    for chunk in split_html_chunks(converted):
        try:
            await bot.send_message(chat_id, chunk, parse_mode=ParseMode.HTML)
        except Exception:
            plain = re.sub(r"<[^>]+>", "", chunk)
            await bot.send_message(chat_id, plain)


async def send_plain_chunks(update: Update, text: str) -> None:
    """Send plain text in 4000-char chunks."""
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i + 4000])
