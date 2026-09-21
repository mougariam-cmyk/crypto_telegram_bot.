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
            
            # حفظ بيانات المستخدم في قاعدة البيانات
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
            
            # تحديث الرسالة الحالية واستجابة الأزرار فوراً
            await query.edit_message_text(success_text, reply_markup=keyboard)
            
            # تفريغ الذاكرة المؤقتة وإنهاء المحادثة لمنع التعليق
            context.user_data.clear()
            return ConversationHandler.END
        else:
            await query.answer("❌ The bot is not an administrator in this channel yet!", show_alert=True)
            return VERIFY_ADMIN
            
    except Exception as e:
        logging.error(f"Admin verification error: {e}")
        await query.answer("❌ Error verifying admin status. Make sure the bot is added to the channel.", show_alert=True)
        return VERIFY_ADMIN
