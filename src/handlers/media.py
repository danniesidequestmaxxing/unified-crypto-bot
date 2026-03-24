"""Handles photos, documents, and media messages — routes through Claude Vision or text extraction."""
from __future__ import annotations

import asyncio
import io
import json
import re

import structlog
from telegram import InputFile, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from src.ai.market_analyst import MarketAnalyst
from src.ai.prompts import DOCUMENT_ANALYSIS_PROMPT, VISION_ANALYSIS_PROMPT
from src.chart.generator import fetch_klines, generate_chart
from src.core.message_utils import send_long

log = structlog.get_logger()

# Supported image MIME types for Claude Vision
_IMAGE_MIMES = {
    "image/jpeg": "image/jpeg",
    "image/png": "image/png",
    "image/gif": "image/gif",
    "image/webp": "image/webp",
}

# Max file size for documents (500KB of text)
_MAX_DOC_BYTES = 512_000


def _parse_decision_block(raw: str) -> tuple[str, dict]:
    """Extract JSON decision block and clean display text."""
    display_text = re.sub(
        r"\n*```json\s*\n\{[^}]*\"requires_chart\"[^}]*\}\s*\n```\n*",
        "", raw,
    ).strip()

    m = re.search(
        r"```json\s*\n(\{[^}]*\"requires_chart\"[^}]*\})\s*\n```",
        raw, re.DOTALL,
    )
    if not m:
        return display_text, {}
    try:
        return display_text, json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return display_text, {}


async def _send_chart_if_needed(
    update: Update, deps: dict, decision: dict,
) -> None:
    """Conditionally generate and send a chart based on the AI decision block."""
    if not decision.get("requires_chart"):
        return
    symbol = decision.get("chart_asset", "BTCUSDT")
    timeframe = decision.get("chart_timeframe", "1H")
    try:
        await update.message.chat.send_action(ChatAction.UPLOAD_PHOTO)
        binance = deps["binance"]
        df = await fetch_klines(binance, symbol, timeframe)
        img_bytes = await asyncio.to_thread(
            generate_chart, df, symbol, timeframe, None,
        )
        await update.message.reply_photo(
            photo=InputFile(io.BytesIO(img_bytes), filename=f"{symbol}_{timeframe}.png"),
        )
    except Exception as exc:
        log.warning("chart_generation_failed", error=str(exc))


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photo messages — analyze with Claude Vision."""
    msg = update.message
    if not msg.photo:
        return

    deps = ctx.bot_data
    db = deps["db"]
    settings = deps["settings"]
    chat_id = update.effective_chat.id

    calls = await db.get_user_calls_last_hour(chat_id)
    if calls >= settings.claude_calls_per_user_per_hour:
        await msg.reply_text("Rate limit reached. Please wait before using AI-powered commands.")
        return

    try:
        await msg.chat.send_action(ChatAction.TYPING)
        await db.record_user_call(chat_id)

        # Download the largest photo
        photo = msg.photo[-1]  # Highest resolution
        file = await photo.get_file()
        image_bytes = await file.download_as_bytearray()

        # Build context
        caption = msg.caption or ""
        context_parts = []
        if caption:
            context_parts.append(f"User's question/caption: {caption}")

        # Fetch BTC price for context
        try:
            binance = deps["binance"]
            ticker = await binance.get_ticker_24hr("BTCUSDT")
            if ticker:
                price = float(ticker.get("lastPrice", 0))
                change = float(ticker.get("priceChangePercent", 0))
                context_parts.append(f"Current BTC price: ${price:,.0f} ({change:+.2f}% 24h)")
        except Exception:
            pass

        context = "\n".join(context_parts) if context_parts else ""
        prompt = VISION_ANALYSIS_PROMPT.format(context=context)

        claude = deps["trading_engine"].claude
        result = await claude.vision(
            image_bytes=bytes(image_bytes),
            prompt=prompt,
            media_type="image/jpeg",
        )

        display_text, decision = _parse_decision_block(result)
        await send_long(update, display_text)
        await _send_chart_if_needed(update, deps, decision)

    except Exception as exc:
        log.error("photo_handler_error", error=str(exc), exc_info=True)
        try:
            await msg.reply_text(f"Error analyzing image: {type(exc).__name__}: {exc}")
        except Exception:
            pass


async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle document uploads — extract text and analyze."""
    msg = update.message
    doc = msg.document
    if not doc:
        return

    deps = ctx.bot_data
    db = deps["db"]
    settings = deps["settings"]
    chat_id = update.effective_chat.id

    calls = await db.get_user_calls_last_hour(chat_id)
    if calls >= settings.claude_calls_per_user_per_hour:
        await msg.reply_text("Rate limit reached. Please wait before using AI-powered commands.")
        return

    try:
        await msg.chat.send_action(ChatAction.TYPING)
        await db.record_user_call(chat_id)

        mime = doc.mime_type or ""
        file_name = doc.file_name or "unknown"

        # If it's an image disguised as a document, route to vision
        if mime in _IMAGE_MIMES:
            file = await doc.get_file()
            image_bytes = await file.download_as_bytearray()
            caption = msg.caption or ""
            context = f"User's question/caption: {caption}" if caption else ""
            prompt = VISION_ANALYSIS_PROMPT.format(context=context)

            claude = deps["trading_engine"].claude
            result = await claude.vision(
                image_bytes=bytes(image_bytes),
                prompt=prompt,
                media_type=_IMAGE_MIMES[mime],
            )
            display_text, decision = _parse_decision_block(result)
            await send_long(update, display_text)
            await _send_chart_if_needed(update, deps, decision)
            return

        # Text-based documents
        if doc.file_size and doc.file_size > _MAX_DOC_BYTES:
            await msg.reply_text(
                f"File too large ({doc.file_size // 1024}KB). Max supported: {_MAX_DOC_BYTES // 1024}KB."
            )
            return

        file = await doc.get_file()
        raw_bytes = await file.download_as_bytearray()

        # Try to extract text
        text_content = ""
        if mime == "application/pdf":
            # Basic PDF text extraction — look for text streams
            try:
                raw_str = bytes(raw_bytes).decode("latin-1")
                # Extract text between BT/ET markers (basic PDF text)
                text_parts = re.findall(r"\(([^)]+)\)", raw_str)
                text_content = " ".join(text_parts)[:10000]
            except Exception:
                pass
            if not text_content:
                await msg.reply_text(
                    "Could not extract text from this PDF. "
                    "Try copy-pasting the text directly or sending a screenshot."
                )
                return
        else:
            # Plain text, CSV, JSON, etc.
            try:
                text_content = bytes(raw_bytes).decode("utf-8")[:10000]
            except UnicodeDecodeError:
                try:
                    text_content = bytes(raw_bytes).decode("latin-1")[:10000]
                except Exception:
                    await msg.reply_text("Could not read this file format. Try a text file or screenshot.")
                    return

        if not text_content.strip():
            await msg.reply_text("File appears to be empty.")
            return

        # Build context
        caption = msg.caption or ""
        context_parts = [f"File: {file_name}", f"Content:\n{text_content}"]
        if caption:
            context_parts.insert(0, f"User's question: {caption}")

        prompt = DOCUMENT_ANALYSIS_PROMPT.format(context="\n\n".join(context_parts))
        claude = deps["trading_engine"].claude
        result = await claude.complete_deep(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
        )

        display_text, decision = _parse_decision_block(result)
        await send_long(update, display_text)
        await _send_chart_if_needed(update, deps, decision)

    except Exception as exc:
        log.error("document_handler_error", error=str(exc), exc_info=True)
        try:
            await msg.reply_text(f"Error analyzing document: {type(exc).__name__}: {exc}")
        except Exception:
            pass
