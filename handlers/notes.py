"""Notes save / list / clear handlers."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from handlers.common import send_main_menu
from services.notes_store import notes_store


async def note_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await update.message.reply_text("Usage: /note <text to save>")
        return
    note = notes_store.add(update.effective_user.id, text)
    await update.message.reply_text(f"Saved note #{note['id']}.")


async def notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    notes = notes_store.list(update.effective_user.id)
    if not notes:
        await update.message.reply_text("No notes yet. Add one with /note <text>.")
        return
    lines = [f"{n['id']}. {n['text']}" for n in notes]
    body = "Your notes:\n\n" + "\n".join(lines)
    if len(body) > 3900:
        body = body[:3900] + "\n…"
    await update.message.reply_text(body)


async def clearnotes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    count = notes_store.clear(update.effective_user.id)
    await update.message.reply_text(f"Cleared {count} note(s).")


async def notes_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_main_menu(
        update,
        "Notes\n\n"
        "/note <text> – save\n"
        "/notes – list\n"
        "/clearnotes – clear all",
    )
