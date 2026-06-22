import sys
import logging
import asyncio
import os
import httpx
from openai import OpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# --- কনফিগারেশন ---
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "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")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8965954748:AAHm-JvA-UfH_IzfUQ9xTOQ90j94n0avMH8")
MODEL_NAME = "google/gemini-2.5-flash-lite"

# CRITICAL FIX: OpenAI ক্লায়েন্টের লেটেস্ট ভার্সনে স্ট্যান্ডার্ড httpx ক্লায়েন্ট পাস করা হয়েছে
http_client = httpx.Client()

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
    http_client=http_client
)

# প্রতিটি ইউজারের সেশন ট্র্যাক করার গ্লোবাল মেমোরি
user_sessions = {}

# প্রফেশনাল গ্লোবাল কান্ট্রি এবং ভাষা ম্যাপিং
COUNTRIES = {
    "bd": ("🇧🇩 Bangladesh (Bengali)", "Bengali"),
    "vn": ("🇻🇳 Vietnam (Vietnamese)", "Vietnamese"),
    "in": ("🇮🇳 India (Hindi)", "Hindi"),
    "ph": ("🇵🇭 Philippines (Filipino)", "Filipino"),
    "us": ("🇺🇸 USA (English)", "English"),
    "custom": ("⚙️ Custom System", "Custom")
}

# মেসেজ নিরাপদে স্ক্রিন থেকে মুছে ফেলার কোর লজিক (ক্লিন ইউআই-এর জন্য)
async def safe_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    if message_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass

# ৫ সেকেন্ডের রিয়েল-টাইম স্মুথ প্রোগ্রেস বার অ্যানিমেশন
async def run_loading_animation(context: ContextTypes.DEFAULT_TYPE, chat_id: int, process_text: str):
    msg = await context.bot.send_message(chat_id=chat_id, text=f"⏳ {process_text} [□□□□□] 0%")
    
    animation_steps = [
        (f"⏳ {process_text} [■□□□□] 25%", 1.0),
        (f"⏳ {process_text} [■■□□□] 50%", 1.0),
        (f"⏳ {process_text} [■■■□□] 75%", 1.0),
        (f"⏳ {process_text} [■■■■■] 100%", 1.0)
    ]
    
    for frame, delay in animation_steps:
        await asyncio.sleep(delay)
        try:
            await context.bot.edit_message_text(text=frame, chat_id=chat_id, message_id=msg.message_id)
        except Exception:
            pass
            
    await asyncio.sleep(0.2)
    await safe_delete(context, chat_id, msg.message_id)

# প্রফেশনাল গ্রিড লেআউটে ইনলাইন কিবোর্ড বাটন মেকার
def make_country_keyboard(prefix: str):
    keyboard = []
    row = []
    for code, (label, _) in COUNTRIES.items():
        row.append(InlineKeyboardButton(label, callback_data=f"{prefix}_{code}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

# টার্গেট ইঞ্জিন কনফিগারেশন মোড ট্রিপল এন্ট্রি
async def start_target_language_phase(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    user_sessions[chat_id]['state'] = 'SELECTING_TGT'
    await run_loading_animation(context, chat_id, "Configuring Target Translation Engine")
    
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="⚙️ *Step 2: Select Target Destination Language*\n\nPlease select the target language you wish to translate into:",
        parse_mode="Markdown",
        reply_markup=make_country_keyboard("tgt")
    )
    user_sessions[chat_id]['setup_msg_id'] = msg.message_id

# সিঙ্গেল পারসিস্টেন্ট কার্ড ইন্টারফেস যা স্ক্রিনে স্থায়ী থাকবে
async def finalize_translation_setup(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    user_sessions[chat_id]['state'] = 'READY'
    src = user_sessions[chat_id]['src_lang']
    tgt = user_sessions[chat_id]['tgt_lang']
    
    status_text = (
        f"✨ *Global Premium Translation Core Active* \n\n"
        f"🌐 *Routing Path:* `{src}` ↔️ `{tgt}`\n\n"
        f"✍️ *Operational Manual:* Type freely in either language. The AI will instantly auto-detect and transform it to the counterpart system.\n\n"
        f"🔄 To clear memory or change routing, type `/reset` or `/start`."
    )
    
    msg = await context.bot.send_message(chat_id=chat_id, text=status_text, parse_mode="Markdown")
    user_sessions[chat_id]['status_msg_id'] = msg.message_id


# --- কমান্ডস ইন্টারফেস ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # স্ক্রিন ডিক্লাটারিং লজিক
    if chat_id in user_sessions:
        await safe_delete(context, chat_id, user_sessions[chat_id].get('setup_msg_id'))
        await safe_delete(context, chat_id, user_sessions[chat_id].get('status_msg_id'))
        
    user_sessions[chat_id] = {
        'src_lang': None,
        'tgt_lang': None,
        'state': 'SELECTING_SRC',
        'setup_msg_id': None,
        'status_msg_id': None
    }
    
    await run_loading_animation(context, chat_id, "Initializing Neural Networks")
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    await asyncio.sleep(0.8)
    
    welcome_text = (
        f"👋 *Welcome to Global Translator Pro, {update.effective_user.first_name}!*\n\n"
        f"🤖 Powered by **Gemini 2.5 Flash-Lite**, I provide real-time seamless bidirectional translation services.\n\n"
        f"⚙️ *Step 1: Select Your Primary Language*\n"
        f"Please choose your native system interface below:"
    )
    
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=welcome_text,
        parse_mode="Markdown",
        reply_markup=make_country_keyboard("src")
    )
    user_sessions[chat_id]['setup_msg_id'] = msg.message_id


# --- বাটন ইভেন্ট ইন্টারсеপ্টর ---

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    data = query.data
    
    if chat_id not in user_sessions:
        return
        
    prefix, code = data.split('_')
    await safe_delete(context, chat_id, user_sessions[chat_id].get('setup_msg_id'))
    
    if prefix == "src":
        if code == "custom":
            user_sessions[chat_id]['state'] = 'AWAITING_CUSTOM_SRC'
            msg = await context.bot.send_message(chat_id=chat_id, text="📝 *Please enter custom primary country or language identifier:*", parse_mode="Markdown")
            user_sessions[chat_id]['setup_msg_id'] = msg.message_id
        else:
            user_sessions[chat_id]['src_lang'] = COUNTRIES[code][1]
            await start_target_language_phase(chat_id, context)
            
    elif prefix == "tgt":
        if code == "custom":
            user_sessions[chat_id]['state'] = 'AWAITING_CUSTOM_TGT'
            msg = await context.bot.send_message(chat_id=chat_id, text="📝 *Please enter custom target country or language identifier:*", parse_mode="Markdown")
            user_sessions[chat_id]['setup_msg_id'] = msg.message_id
        else:
            user_sessions[chat_id]['tgt_lang'] = COUNTRIES[code][1]
            await finalize_translation_setup(chat_id, context)


# --- ডাইনামিক মেসেজ প্রসেসর এবং মডেল রিকোয়েস্ট পাইপলাইন ---

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text
    
    if chat_id not in user_sessions:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ System unconfigured. Type `/start` to boot.")
        return
        
    state = user_sessions[chat_id].get('state')
    
    if state == 'AWAITING_CUSTOM_SRC':
        user_sessions[chat_id]['src_lang'] = user_text
        await safe_delete(context, chat_id, update.message.message_id)
        await safe_delete(context, chat_id, user_sessions[chat_id].get('setup_msg_id'))
        await start_target_language_phase(chat_id, context)
        return
        
    if state == 'AWAITING_CUSTOM_TGT':
        user_sessions[chat_id]['tgt_lang'] = user_text
        await safe_delete(context, chat_id, update.message.message_id)
        await safe_delete(context, chat_id, user_sessions[chat_id].get('setup_msg_id'))
        await finalize_translation_setup(chat_id, context)
        return
        
    if state == 'READY':
        src = user_sessions[chat_id]['src_lang']
        tgt = user_sessions[chat_id]['tgt_lang']
        
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        system_prompt = (
            f"You are an elite, enterprise-grade bidirectional translation middleware optimized for {src} and {tgt}. "
            f"Analyze the linguistic pattern of the input. If it is in {src}, translate it to {tgt}. "
            f"If it is in {tgt}, translate it to {src}. "
            f"CRITICAL RULES:\n"
            f"1. Return ONLY the strict translation.\n"
            f"2. Absolutely no definitions, introductory text, markdown wrappers, conversational filler, or side explanations.\n"
            f"3. Maintain formatting structural mapping exactly."
        )
        
        try:
            completion = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text}
                ]
            )
            ai_response = completion.choices[0].message.content.strip()
            await update.message.reply_text(ai_response)
        except Exception as e:
            await update.message.reply_text(f"❌ *Neural Network Communication Failure:* `{str(e)}`", parse_mode="Markdown")
    else:
        # সেটআপ ইন্টারফেসের সুরক্ষার্থে মাঝখানের অনাঙ্কিত মেসেজ ডিলিট করা
        await safe_delete(context, chat_id, update.message.message_id)

# --- মেইন এক্সিকিউশন মডিউল ---
def main():
    if TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        print("[Fatal] Configuration Variables missing.")
        sys.exit(1)
        
    print("[Success] Global Premium Framework Activated.")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("reset", start_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_polling()

if __name__ == "__main__":
    main()
                                                
