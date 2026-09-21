async def verify_admin_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    if query.data == 'back_to_channel_input':
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='back_to_buy_link')]])
        await query.edit_message_text("5️⃣ Send your Channel Username or Link (e.g., @mychannel):", reply_markup=keyboard)
        return CHANNEL

    await query.answer()
    channel_id = context.user_data.get('channel')
    bot_id = context.bot.id

    try:
        member = await context.bot.get_chat_member(chat_id=channel_id, user_id=bot_id)
        if member.status in ['administrator', 'creator']:
            user_id = query.from_user.id
            username = query.from_user.username or "Unknown"
            
            save_user_data(user_id, username, context.user_data)
            
            plan = context.user_data.get('selected_plan', 'free')
            if plan != 'free' and channel_id in ACTIVE_PUBLISH_TASKS:
                ACTIVE_PUBLISH_TASKS[channel_id].cancel()
            
            success_text = (
                f"🎉 SUCCESS! Auto-Promoter is now active for {channel_id}!\n\n"
                f"🌑 Token: {context.user_data.get('coin_name')}\n"
                f"💳 Plan: {plan.upper()}\n"
                f"⏱️ Frequency: {context.user_data.get('msg_per_hour', 2)} posts/hour\n"
                f"📊 Links Ratio: {context.user_data.get('link_ratio', 100)}%\n\n"
                f"Your automated crypto growth campaign has started! 🚀"
            )
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ Edit Settings / Manage", callback_data='menu_edit_existing')],
                [InlineKeyboardButton("🏠 Main Menu", callback_data='back_to_main')]
            ])
            
            await query.edit_message_text(success_text, reply_markup=keyboard)
            
            # جدولة مهام النشر الخلفية
            restart_all_active_tasks(context.application)
            
            context.user_data.clear()
            return ConversationHandler.END
        else:
            await query.answer("❌ The bot is not an administrator in this channel yet!", show_alert=True)
            return VERIFY_ADMIN
            
    except Exception as e:
        logging.error(f"Admin verification error: {e}")
        await query.answer("❌ Error verifying admin status. Make sure the bot is added to the channel.", show_alert=True)
        return VERIFY_ADMIN

async def finish_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    username = query.from_user.username or "Unknown"
    
    save_user_data(user_id, username, context.user_data)
    restart_all_active_tasks(context.application)
    
    await query.edit_message_text("✅ Settings updated and saved successfully! 🚀", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Main Menu", callback_data='back_to_main')]
    ]))
    context.user_data.clear()
    return ConversationHandler.END

async def background_publisher(application, user_data):
    channel = user_data['channel']
    plan = user_data['selected_plan']
    
    while True:
        try:
            current_data = get_user_channel_data(user_data['user_id'], channel)
            if not current_data or current_data.get('subscription_status') == 'cancelled':
                break
                
            post_text = generate_post(current_data)
            
            # إرسال المنشور للقناة
            if current_data.get('selected_plan') == 'free':
                await application.bot.send_message(
                    chat_id=channel,
                    text=post_text,
                    reply_markup=FREE_PLAN_FOOTER_BUTTONS,
                    parse_mode='Markdown'
                )
                await asyncio.sleep(6 * 3600)  # 4 مرات في اليوم للـ Free
            else:
                await application.bot.send_message(
                    chat_id=channel,
                    text=post_text,
                    parse_mode='Markdown'
                )
                msg_per_hour = current_data.get('msg_per_hour', 2)
                sleep_seconds = 3600 / max(1, msg_per_hour)
                await asyncio.sleep(sleep_seconds)
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"Publishing error for {channel}: {e}")
            await asyncio.sleep(60)

def restart_all_active_tasks(application):
    global ACTIVE_PUBLISH_TASKS
    for task in ACTIVE_PUBLISH_TASKS.values():
        task.cancel()
    ACTIVE_PUBLISH_TASKS.clear()
    
    active_users = get_active_users()
    for u_data in active_users:
        channel = u_data['channel']
        if channel not in ACTIVE_PUBLISH_TASKS:
            task = asyncio.create_task(background_publisher(application, u_data))
            ACTIVE_PUBLISH_TASKS[channel] = task

async def post_init(application):
    restart_all_active_tasks(application)
    logging.info("Bot initialized and background publishers started.")

# ==========================================
# MAIN FUNCTION & BOT STARTUP
# ==========================================
if __name__ == '__main__':
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        logging.error("No TELEGRAM_BOT_TOKEN found in environment variables!")
        exit(1)
        
    application = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            CallbackQueryHandler(start, pattern='^back_to_main$')
        ],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(main_menu_handler, pattern='^(menu_buy_new|menu_edit_existing)$'),
                CallbackQueryHandler(start, pattern='^back_to_main$')
            ],
            PLAN_SELECT: [
                CallbackQueryHandler(plan_selected, pattern='^plan_'),
                CallbackQueryHandler(start, pattern='^back_to_main$')
            ],
            COIN_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_coin_name),
                CallbackQueryHandler(back_to_edit_menu_handler, pattern='^back_to_edit_menu$'),
                CallbackQueryHandler(plan_selected, pattern='^back_to_coin_name$')
            ],
            COIN_DESC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_coin_desc),
                CallbackQueryHandler(back_to_edit_menu_handler, pattern='^back_to_edit_menu$'),
                CallbackQueryHandler(get_coin_name, pattern='^back_to_coin_desc$')
            ],
            CONTRACT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_contract),
                CallbackQueryHandler(back_to_edit_menu_handler, pattern='^back_to_edit_menu$'),
                CallbackQueryHandler(get_coin_desc, pattern='^back_to_contract$')
            ],
            BUY_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_buy_link),
                CallbackQueryHandler(back_to_edit_menu_handler, pattern='^back_to_edit_menu$'),
                CallbackQueryHandler(get_contract, pattern='^back_to_buy_link$')
            ],
            CHANNEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_channel),
                CallbackQueryHandler(back_to_edit_menu_handler, pattern='^back_to_edit_menu$'),
                CallbackQueryHandler(get_buy_link, pattern='^back_to_channel_input$')
            ],
            VERIFY_ADMIN: [
                CallbackQueryHandler(verify_admin_status, pattern='^(verify_admin|back_to_channel_input)$')
            ],
            EDIT_SELECT_CHANNEL: [
                CallbackQueryHandler(select_channel_to_edit, pattern='^edit_ch_'),
                CallbackQueryHandler(start, pattern='^back_to_main$')
            ],
            EDIT_OPTIONS_MENU: [
                CallbackQueryHandler(edit_options_handler, pattern='^(opt_|back_to_main)'),
                CallbackQueryHandler(start, pattern='^back_to_main$')
            ],
            MSG_PER_HOUR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: show_edit_options(u, c)), # أو معالجة المدخل
                CallbackQueryHandler(back_to_edit_menu_handler, pattern='^back_to_edit_menu$')
            ],
            LINK_RATIO: [
                CallbackQueryHandler(edit_options_handler, pattern='^ratio_'),
                CallbackQueryHandler(back_to_edit_menu_handler, pattern='^back_to_edit_menu$')
            ],
            ENABLE_NEW_BUY: [
                CallbackQueryHandler(edit_options_handler, pattern='^newbuy_'),
                CallbackQueryHandler(back_to_edit_menu_handler, pattern='^back_to_edit_menu$')
            ],
            CONFIRM_CANCEL_SUB: [
                CallbackQueryHandler(confirm_cancel_sub_handler, pattern='^(confirm_cancel_|back_to_edit_menu)'),
                CallbackQueryHandler(show_edit_options, pattern='^confirm_cancel_no$')
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel_command)],
        per_user=True
    )

    application.add_handler(conv_handler)
    
    # تشغيل البوت
    logging.info("Starting bot polling...")
    application.run_polling()
