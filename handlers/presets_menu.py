"""Strategy Presets Management handler."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database.crud import (
    get_strategy_presets_by_user, create_strategy_preset, delete_strategy_preset,
    get_strategy_preset_by_id, ensure_default_presets
)

logger = logging.getLogger(__name__)

PRESET_NAME, PRESET_TYPE, PRESET_P1, PRESET_P2 = range(4)

async def presets_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    await ensure_default_presets(user_id)
    presets = await get_strategy_presets_by_user(user_id)
    
    message = "🎛️ **Strategy Presets**\n\nCreate and manage your indicator presets to use in Algo and Screener setups.\n\n"
    
    keyboard = [
        [InlineKeyboardButton("➕ Add New Preset", callback_data="preset_add_start")],
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
        [InlineKeyboardButton("🏔️ Range Breakout (LazyBear)", callback_data="preset_type_range_breakout_lazybear")]
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
