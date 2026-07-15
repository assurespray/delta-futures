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

OHLC_PROMPTS = [
    "⏰ **Step 1/7: Reference Time**\nEnter the Reference Time (IST) in HH:MM format.\n\nExample: `09:15`",
    "⏳ **Step 2/7: Reference Timeframe**\nEnter the timeframe for the reference candle.\n\nExamples: `15m`, `1h`, `4h`, `1d`",
    "🔄 **Step 3/7: Merge Previous Candle**\nTake the max high/min low of the last two reference candles?",
    "🛑 **Step 4/7: Stop Loss Type**\nEnter Stop Loss Type:",
    "⚖️ **Step 5/7: Risk-Reward Ratio**\nEnter RR Ratio to calculate Take Profit.\n\nExample: `2.0`",
    "📏 **Step 6/7: Pip/Tick Multiplier**\nEnter the number of ticks/pips to offset the breakout.\n*(Automatically scales to the asset's tick size)*\n\nExample: `1`, `2`, `5`",
    "⚡ **Step 7/7: Entry Mode**\nEnter Entry Mode:"
]

OHLC_KEYBOARDS = [
    None, # Step 1
    None, # Step 2
    InlineKeyboardMarkup([[InlineKeyboardButton("✅ Yes", callback_data="ohlc_btn_true"), InlineKeyboardButton("❌ No", callback_data="ohlc_btn_false")]]), # Step 3
    InlineKeyboardMarkup([[InlineKeyboardButton("↕️ Opposite", callback_data="ohlc_btn_opposite"), InlineKeyboardButton("➖ Middle", callback_data="ohlc_btn_middle")]]), # Step 4
    None, # Step 5
    None, # Step 6
    InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Breakout", callback_data="ohlc_btn_breakout"), InlineKeyboardButton("✅ Confirmation", callback_data="ohlc_btn_confirmation")]]), # Step 7
]

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
        [InlineKeyboardButton("🐢 Donchian Channels", callback_data="preset_type_donchian")],
        [InlineKeyboardButton("⏰ OHLC Breakout", callback_data="preset_type_ohlc_breakout")],
        [InlineKeyboardButton("🛡️ Evasive SuperTrend", callback_data="preset_type_evasive_supertrend")],
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
        await query.edit_message_text("Enter ATR Length and Factor separated by comma (e.g., 20,20):")
        return PRESET_P1
    elif ptype == "dual_supertrend":
        await query.edit_message_text("Enter Perusu ATR, Perusu Factor, Sirusu ATR, Sirusu Factor separated by comma (e.g., 20,20,10,10):")
        return PRESET_P1
    elif ptype == "range_breakout_lazybear":
        await query.edit_message_text("Enter EMA Length, SL Type (middle/opposite), Min Range Candles (e.g., 34,middle,2):")
        return PRESET_P1
    elif ptype == "donchian":
        await query.edit_message_text("Enter Donchian Channel Period (e.g., 20):")
        return PRESET_P1
    elif ptype == "ohlc_breakout":
        context.user_data['ohlc_step'] = 0
        context.user_data['ohlc_params'] = {}
        await query.edit_message_text(OHLC_PROMPTS[0], parse_mode="Markdown", reply_markup=OHLC_KEYBOARDS[0])
        return PRESET_P1
    elif ptype == "evasive_supertrend":
        context.user_data['evasive_step'] = 0
        context.user_data['evasive_params'] = {}
        await query.edit_message_text("Step 1/3: Enter ATR Length and Multiplier separated by comma (e.g., 10,3.0):")
        return PRESET_P1

async def preset_params_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ptype = context.user_data.get('strategy_type')
    
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        text = query.data.replace("ohlc_btn_", "")
    else:
        text = update.message.text.strip()
    
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
        elif ptype == "donchian":
            if len(parts) != 1: raise ValueError("Need 1 value (period)")
            params = {"period": int(parts[0])}
        elif ptype == "ohlc_breakout":
            step = context.user_data.get('ohlc_step', 0)
            params_dict = context.user_data.get('ohlc_params', {})
            val = text.strip()
            
            if step == 0:
                if ":" not in val or len(val.split(":")) != 2: raise ValueError("Must be HH:MM format")
                params_dict['reference_time'] = val
            elif step == 1:
                params_dict['reference_timeframe'] = val.lower()
            elif step == 2:
                if val.lower() not in ['true', 'false', '1', '0', 'yes', 'no']: raise ValueError("Enter True or False")
                params_dict['use_prev_candle'] = val.lower() in ['true', '1', 'yes']
            elif step == 3:
                if val.lower() not in ['opposite', 'middle']: raise ValueError("Must be 'opposite' or 'middle'")
                params_dict['sl_type'] = val.lower()
            elif step == 4:
                params_dict['rr_ratio'] = float(val)
            elif step == 5:
                params_dict['pip_offset_multiplier'] = float(val)
            elif step == 6:
                if val.lower() not in ['breakout', 'confirmation']: raise ValueError("Must be 'breakout' or 'confirmation'")
                params_dict['entry_mode'] = val.lower()
                
            context.user_data['ohlc_params'] = params_dict
            step += 1
            context.user_data['ohlc_step'] = step
            
            if step < 7:
                if update.callback_query:
                    await update.callback_query.edit_message_text(OHLC_PROMPTS[step], parse_mode="Markdown", reply_markup=OHLC_KEYBOARDS[step])
                else:
                    await update.message.reply_text(OHLC_PROMPTS[step], parse_mode="Markdown", reply_markup=OHLC_KEYBOARDS[step])
                return PRESET_P1
            else:
                params = params_dict
                # Fall through to save preset
            
        elif ptype == "evasive_supertrend":
            step = context.user_data.get('evasive_step', 0)
            params_dict = context.user_data.get('evasive_params', {})
            val = text.strip()
            
            if step == 0:
                parts_evasive = [p.strip() for p in val.split(",")]
                if len(parts_evasive) != 2: raise ValueError("Need 2 values: ATR Length and Multiplier")
                params_dict['atr_length'] = int(parts_evasive[0])
                params_dict['multiplier'] = float(parts_evasive[1])
            elif step == 1:
                params_dict['noise_threshold'] = float(val)
            elif step == 2:
                params_dict['expansion_alpha'] = float(val)
                
            context.user_data['evasive_params'] = params_dict
            step += 1
            context.user_data['evasive_step'] = step
            
            if step == 1:
                msg_text = "Step 2/3: Enter Noise Threshold (e.g., 1.0):"
                if update.callback_query:
                    await update.callback_query.edit_message_text(msg_text)
                else:
                    await update.message.reply_text(msg_text)
                return PRESET_P1
            elif step == 2:
                msg_text = "Step 3/3: Enter Expansion Alpha (e.g., 0.5):"
                if update.callback_query:
                    await update.callback_query.edit_message_text(msg_text)
                else:
                    await update.message.reply_text(msg_text)
                return PRESET_P1
            else:
                params = params_dict
                context.user_data.pop('evasive_step', None)
                context.user_data.pop('evasive_params', None)
                # Fall through to save preset
            
        await create_strategy_preset({
            "user_id": str(update.effective_user.id),
            "preset_name": context.user_data['preset_name'],
            "strategy_type": ptype,
            "parameters": params,
            "is_default": False
        })
        
        msg = "✅ Preset saved successfully.\nUse /start to go to main menu."
        if update.callback_query:
            await update.callback_query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg)
        return ConversationHandler.END
    except Exception as e:
        msg = f"❌ Invalid format. Error: {e}\nTry again:"
        if update.callback_query:
            await update.callback_query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg)
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
        message += "Enter new ATR Length and Factor separated by comma (e.g., 20,20):"
    elif ptype == "dual_supertrend":
        message += f"• Perusu ATR: {params.get('perusu_atr', '?')}, Factor: {params.get('perusu_factor', '?')}\n"
        message += f"• Sirusu ATR: {params.get('sirusu_atr', '?')}, Factor: {params.get('sirusu_factor', '?')}\n\n"
        message += "Enter new Perusu ATR, Perusu Factor, Sirusu ATR, Sirusu Factor (e.g., 20,20,10,10):"
    elif ptype == "range_breakout_lazybear":
        message += f"• EMA Length: {params.get('ema_length', '?')}\n"
        message += f"• SL Type: {params.get('sl_type', '?')}\n"
        message += f"• Min Range Candles: {params.get('min_range_candles', '?')}\n\n"
        message += "Enter new EMA Length, SL Type (middle/opposite), Min Range Candles (e.g., 34,middle,2):"
    elif ptype == "donchian":
        message += f"• Period: {params.get('period', '?')}\n\n"
        message += "Enter new Donchian Channel Period (e.g., 20):"
        message += "\n\nSend /cancel to abort."
        await query.edit_message_text(message, parse_mode="Markdown")
    elif ptype == "ohlc_breakout":
        context.user_data['ohlc_step'] = 0
        context.user_data['ohlc_params'] = {}
        context.user_data['ohlc_old_params'] = params
        
        message += "Let's update them one by one.\n\n"
        message += OHLC_PROMPTS[0] + f"\n*(Current: {params.get('reference_time', '?')})*"
        message += "\n\nSend /cancel to abort."
        await query.edit_message_text(message, parse_mode="Markdown", reply_markup=OHLC_KEYBOARDS[0])
    elif ptype == "evasive_supertrend":
        context.user_data['evasive_step'] = 0
        context.user_data['evasive_params'] = {}
        context.user_data['evasive_old_params'] = params
        
        message += f"Let's update them one by one.\n\n"
        message += f"Step 1/3: Enter ATR Length and Multiplier (e.g., 10,3.0)\n*(Current: {params.get('atr_length', '?')},{params.get('multiplier', '?')})*"
        message += "\n\nSend /cancel to abort."
        await query.edit_message_text(message, parse_mode="Markdown")
        
    return PRESET_EDIT_PARAMS


async def preset_edit_params_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive new parameters for an existing preset."""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        text = query.data.replace("ohlc_btn_", "")
    else:
        text = update.message.text.strip()
        
    ptype = context.user_data.get('edit_preset_type')
    pid = context.user_data.get('edit_preset_id')
    
    if not pid or not ptype:
        msg = "❌ Session expired. Use /start."
        if update.callback_query:
            await update.callback_query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg)
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
        elif ptype == "donchian":
            if len(parts) != 1: raise ValueError("Need 1 value (period)")
            params = {"period": int(parts[0])}
        elif ptype == "ohlc_breakout":
            step = context.user_data.get('ohlc_step', 0)
            params_dict = context.user_data.get('ohlc_params', {})
            old_params = context.user_data.get('ohlc_old_params', {})
            val = text.strip()
            
            if step == 0:
                if ":" not in val or len(val.split(":")) != 2: raise ValueError("Must be HH:MM format")
                params_dict['reference_time'] = val
            elif step == 1:
                params_dict['reference_timeframe'] = val.lower()
            elif step == 2:
                if val.lower() not in ['true', 'false', '1', '0', 'yes', 'no']: raise ValueError("Enter True or False")
                params_dict['use_prev_candle'] = val.lower() in ['true', '1', 'yes']
            elif step == 3:
                if val.lower() not in ['opposite', 'middle']: raise ValueError("Must be 'opposite' or 'middle'")
                params_dict['sl_type'] = val.lower()
            elif step == 4:
                params_dict['rr_ratio'] = float(val)
            elif step == 5:
                params_dict['pip_offset_multiplier'] = float(val)
            elif step == 6:
                if val.lower() not in ['breakout', 'confirmation']: raise ValueError("Must be 'breakout' or 'confirmation'")
                params_dict['entry_mode'] = val.lower()
                
            context.user_data['ohlc_params'] = params_dict
            step += 1
            context.user_data['ohlc_step'] = step
            
            if step < 7:
                keys = ['reference_time', 'reference_timeframe', 'use_prev_candle', 'sl_type', 'rr_ratio', 'pip_offset_multiplier', 'entry_mode']
                current_val = old_params.get(keys[step], '?')
                prompt = OHLC_PROMPTS[step] + f"\n*(Current: {current_val})*"
                if update.callback_query:
                    await update.callback_query.edit_message_text(prompt, parse_mode="Markdown", reply_markup=OHLC_KEYBOARDS[step])
                else:
                    await update.message.reply_text(prompt, parse_mode="Markdown", reply_markup=OHLC_KEYBOARDS[step])
                return PRESET_EDIT_PARAMS
            else:
                params = params_dict
                context.user_data.pop('ohlc_step', None)
                context.user_data.pop('ohlc_params', None)
                context.user_data.pop('ohlc_old_params', None)
                # Fall through to save preset
        elif ptype == "evasive_supertrend":
            step = context.user_data.get('evasive_step', 0)
            params_dict = context.user_data.get('evasive_params', {})
            old_params = context.user_data.get('evasive_old_params', {})
            val = text.strip()
            
            if step == 0:
                parts_evasive = [p.strip() for p in val.split(",")]
                if len(parts_evasive) != 2: raise ValueError("Need 2 values: ATR Length and Multiplier")
                params_dict['atr_length'] = int(parts_evasive[0])
                params_dict['multiplier'] = float(parts_evasive[1])
            elif step == 1:
                params_dict['noise_threshold'] = float(val)
            elif step == 2:
                params_dict['expansion_alpha'] = float(val)
                
            context.user_data['evasive_params'] = params_dict
            step += 1
            context.user_data['evasive_step'] = step
            
            if step == 1:
                current_val = old_params.get('noise_threshold', '?')
                prompt = f"Step 2/3: Enter Noise Threshold (e.g., 1.0)\n*(Current: {current_val})*"
                if update.callback_query:
                    await update.callback_query.edit_message_text(prompt, parse_mode="Markdown")
                else:
                    await update.message.reply_text(prompt, parse_mode="Markdown")
                return PRESET_EDIT_PARAMS
            elif step == 2:
                current_val = old_params.get('expansion_alpha', '?')
                prompt = f"Step 3/3: Enter Expansion Alpha (e.g., 0.5)\n*(Current: {current_val})*"
                if update.callback_query:
                    await update.callback_query.edit_message_text(prompt, parse_mode="Markdown")
                else:
                    await update.message.reply_text(prompt, parse_mode="Markdown")
                return PRESET_EDIT_PARAMS
            else:
                params = params_dict
                context.user_data.pop('evasive_step', None)
                context.user_data.pop('evasive_params', None)
                context.user_data.pop('evasive_old_params', None)
                # Fall through to save preset
        
        await update_strategy_preset(pid, {"parameters": params})
        
        context.user_data.pop('edit_preset_id', None)
        context.user_data.pop('edit_preset_type', None)
        
        msg = (
            "✅ Preset parameters updated.\n\n"
            "⚠️ This only affects **new** Algo Setups. Existing running setups keep their original parameters.\n\n"
            "Use /start to return to main menu."
        )
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, parse_mode="Markdown")
        else:
            await update.message.reply_text(msg, parse_mode="Markdown")
        return ConversationHandler.END
    except Exception as e:
        msg = f"❌ Invalid format. Error: {e}\nTry again:"
        if update.callback_query:
            await update.callback_query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg)
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
        [InlineKeyboardButton("🐢 Donchian Channels", callback_data="preset_type_donchian")],
        [InlineKeyboardButton("⏰ OHLC Breakout", callback_data="preset_type_ohlc_breakout")],
        [InlineKeyboardButton("🛡️ Evasive SuperTrend", callback_data="preset_type_evasive_supertrend")],
        [InlineKeyboardButton("🔙 Back", callback_data="preset_back_name")]
    ]
    await query.edit_message_text(
        "Select base strategy:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return PRESET_TYPE
