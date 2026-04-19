"""Strategy Presets Management handler."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database.crud import (
    get_strategy_presets_by_user, create_strategy_preset, delete_strategy_preset,
    get_strategy_preset_by_id, ensure_default_presets, update_strategy_preset
)

logger = logging.getLogger(__name__)

PRESET_NAME, PRESET_TYPE, PRESET_P1, PRESET_P2 = range(4)
PRESET_EDIT_PARAMS = 10  # Unique state for edit conversation

async def presets_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    await ensure_default_presets(user_id)
    presets = await get_strategy_presets_by_user(user_id)
    
    message = "🎛️ **Strategy Presets**\n\nCreate and manage your indicator presets to use in Algo and Screener setups.\n\n"
    
    keyboard = [
        [InlineKeyboardButton("➕ Add New Preset", callback_data="preset_add_start")],
        [InlineKeyboardButton("✏️ Edit Preset", callback_data="preset_edit_list")],
        [InlineKeyboardButton("🗑️ Delete Preset", callback_data="preset_delete_list")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")]
    ]
    
    for preset in presets:
        ptype = preset.get("strategy_type", "unknown")
        name = preset.get("preset_name", "Unnamed")
        message += f"• **{name}** ({ptype.replace('_', ' ').title()})\n"
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")

async def preset_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("➕ Enter a name for your preset (e.g., Fast Scalper):", parse_mode="Markdown")
    return PRESET_NAME

async def preset_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['preset_name'] = update.message.text.strip()
    
    keyboard = [
        [InlineKeyboardButton("📈 Single SuperTrend", callback_data="preset_type_single_supertrend")],
        [InlineKeyboardButton("📊 Dual SuperTrend", callback_data="preset_type_dual_supertrend")],
        [InlineKeyboardButton("🏔️ Range Breakout (LazyBear)", callback_data="preset_type_range_breakout_lazybear")],
        [InlineKeyboardButton("🔙 Back", callback_data="preset_back_name")]
    ]
    await update.message.reply_text("Select base strategy:", reply_markup=InlineKeyboardMarkup(keyboard))
    return PRESET_TYPE

async def preset_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ptype = query.data.replace("preset_type_", "")
    context.user_data['strategy_type'] = ptype
    
    if ptype == "single_supertrend":
        await query.edit_message_text("Enter ATR Length and Factor separated by comma (e.g., 15,15):")
        return PRESET_P1
    elif ptype == "dual_supertrend":
        await query.edit_message_text("Enter Perusu ATR, Perusu Factor, Sirusu ATR, Sirusu Factor separated by comma (e.g., 20,20,10,10):")
        return PRESET_P1
    elif ptype == "range_breakout_lazybear":
        await query.edit_message_text("Enter EMA Length, SL Type (middle/opposite), Min Range Candles (e.g., 34,middle,2):")
        return PRESET_P1

async def preset_params_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    ptype = context.user_data['strategy_type']
    
    try:
        parts = [p.strip() for p in text.split(",")]
        params = {}
        if ptype == "single_supertrend":
            if len(parts) != 2: raise ValueError("Need 2 values")
            params = {"atr_length": int(parts[0]), "factor": float(parts[1])}
        elif ptype == "dual_supertrend":
            if len(parts) != 4: raise ValueError("Need 4 values")
            params = {
                "perusu_atr": int(parts[0]), "perusu_factor": float(parts[1]),
                "sirusu_atr": int(parts[2]), "sirusu_factor": float(parts[3])
            }
        elif ptype == "range_breakout_lazybear":
            if len(parts) != 3: raise ValueError("Need 3 values")
            params = {
                "ema_length": int(parts[0]), "sl_type": parts[1].strip().lower(),
                "min_range_candles": int(parts[2])
            }
            
        await create_strategy_preset({
            "user_id": str(update.effective_user.id),
            "preset_name": context.user_data['preset_name'],
            "strategy_type": ptype,
            "parameters": params,
            "is_default": False
        })
        
        await update.message.reply_text("✅ Preset saved successfully.\nUse /start to go to main menu.")
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"❌ Invalid format. Error: {e}\nTry again:")
        return PRESET_P1

async def cancel_preset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END

async def preset_delete_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    presets = await get_strategy_presets_by_user(str(query.from_user.id))
    
    keyboard = []
    for p in presets:
        if not p.get("is_default"):
            keyboard.append([InlineKeyboardButton(f"❌ {p['preset_name']}", callback_data=f"preset_del_{p['_id']}")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_indicator_settings")])
    
    await query.edit_message_text("Select a preset to delete (Default presets cannot be deleted):", reply_markup=InlineKeyboardMarkup(keyboard))

async def preset_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pid = query.data.replace("preset_del_", "")
    await delete_strategy_preset(pid, str(query.from_user.id))
    await query.edit_message_text("✅ Preset deleted. Use /start to return.")


# ==================== Edit Preset Handlers ====================

async def preset_edit_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of custom presets that can be edited."""
    query = update.callback_query
    await query.answer()
    presets = await get_strategy_presets_by_user(str(query.from_user.id))
    
    keyboard = []
    for p in presets:
        if not p.get("is_default"):
            pid = str(p['_id'])
            keyboard.append([InlineKeyboardButton(
                f"✏️ {p['preset_name']}", callback_data=f"preset_edit_select_{pid}"
            )])
    
    if not keyboard:
        keyboard.append([InlineKeyboardButton("➕ Add New Preset", callback_data="preset_add_start")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_indicator_settings")])
    
    await query.edit_message_text(
        "✏️ **Edit Preset**\n\nSelect a preset to edit (default [S] presets cannot be edited):",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )


async def preset_edit_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current preset parameters and prompt for new ones."""
    query = update.callback_query
    await query.answer()
    
    pid = query.data.replace("preset_edit_select_", "")
    preset = await get_strategy_preset_by_id(pid)
    
    if not preset:
        await query.edit_message_text("❌ Preset not found. Use /start to return.")
        return ConversationHandler.END
    
    if preset.get("is_default"):
        await query.edit_message_text("❌ Default presets cannot be edited.")
        return ConversationHandler.END
    
    context.user_data['edit_preset_id'] = pid
    context.user_data['edit_preset_type'] = preset['strategy_type']
    
    ptype = preset['strategy_type']
    params = preset.get('parameters', {})
    
    message = f"✏️ **Editing: {preset['preset_name']}**\n"
    message += f"Strategy: {ptype.replace('_', ' ').title()}\n\n"
    message += f"**Current parameters:**\n"
    
    if ptype == "single_supertrend":
        message += f"• ATR Length: {params.get('atr_length', '?')}\n"
        message += f"• Factor: {params.get('factor', '?')}\n\n"
        message += "Enter new ATR Length and Factor separated by comma (e.g., 15,15):"
    elif ptype == "dual_supertrend":
        message += f"• Perusu ATR: {params.get('perusu_atr', '?')}, Factor: {params.get('perusu_factor', '?')}\n"
        message += f"• Sirusu ATR: {params.get('sirusu_atr', '?')}, Factor: {params.get('sirusu_factor', '?')}\n\n"
        message += "Enter new Perusu ATR, Perusu Factor, Sirusu ATR, Sirusu Factor (e.g., 20,20,10,10):"
    elif ptype == "range_breakout_lazybear":
        message += f"• EMA Length: {params.get('ema_length', '?')}\n"
        message += f"• SL Type: {params.get('sl_type', '?')}\n"
        message += f"• Min Range Candles: {params.get('min_range_candles', '?')}\n\n"
        message += "Enter new EMA Length, SL Type (middle/opposite), Min Range Candles (e.g., 34,middle,2):"
    
    message += "\n\nSend /cancel to abort."
    
    await query.edit_message_text(message, parse_mode="Markdown")
    return PRESET_EDIT_PARAMS


async def preset_edit_params_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive new parameters for an existing preset."""
    text = update.message.text.strip()
    ptype = context.user_data.get('edit_preset_type')
    pid = context.user_data.get('edit_preset_id')
    
    if not pid or not ptype:
        await update.message.reply_text("❌ Session expired. Use /start.")
        return ConversationHandler.END
    
    try:
        parts = [p.strip() for p in text.split(",")]
        params = {}
        
        if ptype == "single_supertrend":
            if len(parts) != 2: raise ValueError("Need 2 values")
            params = {"atr_length": int(parts[0]), "factor": float(parts[1])}
        elif ptype == "dual_supertrend":
            if len(parts) != 4: raise ValueError("Need 4 values")
            params = {
                "perusu_atr": int(parts[0]), "perusu_factor": float(parts[1]),
                "sirusu_atr": int(parts[2]), "sirusu_factor": float(parts[3])
            }
        elif ptype == "range_breakout_lazybear":
            if len(parts) != 3: raise ValueError("Need 3 values")
            params = {
                "ema_length": int(parts[0]), "sl_type": parts[1].strip().lower(),
                "min_range_candles": int(parts[2])
            }
        
        await update_strategy_preset(pid, {"parameters": params})
        
        context.user_data.pop('edit_preset_id', None)
        context.user_data.pop('edit_preset_type', None)
        
        await update.message.reply_text(
            "✅ Preset parameters updated.\n\n"
            "⚠️ This only affects **new** Algo Setups. Existing running setups keep their original parameters.\n\n"
            "Use /start to return to main menu.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"❌ Invalid format. Error: {e}\nTry again:")
        return PRESET_EDIT_PARAMS


# ==================== Back Button Handlers (Preset Creation) ====================

async def preset_back_to_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Back from Step 2 (Type) to Step 1 (Name)."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "➕ Enter a name for your preset (e.g., Fast Scalper):",
        parse_mode="Markdown"
    )
    return PRESET_NAME


async def preset_back_to_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Back from Step 3 (Params) to Step 2 (Type) — not easily doable since Step 3
    is a text input. Instead we re-show the type selector."""
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("📈 Single SuperTrend", callback_data="preset_type_single_supertrend")],
        [InlineKeyboardButton("📊 Dual SuperTrend", callback_data="preset_type_dual_supertrend")],
        [InlineKeyboardButton("🏔️ Range Breakout (LazyBear)", callback_data="preset_type_range_breakout_lazybear")],
        [InlineKeyboardButton("🔙 Back", callback_data="preset_back_name")]
    ]
    await query.edit_message_text(
        "Select base strategy:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return PRESET_TYPE
