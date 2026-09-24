#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import ast
import asyncio
import base64
import datetime
import io
import logging
import operator
import os
import random
import re
import sys
import time
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from typing import Any, Dict, List, Optional, Tuple, Union

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("bot.log", encoding="utf-8")]
)
logger = logging.getLogger("BusinessBot")

from aiogram import Bot, Dispatcher, F, Router, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus, ChatType
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery, CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup,
    Message, ReplyParameters, User as TgUser, BusinessConnection,
    BufferedInputFile, FSInputFile, MessageEntity, BusinessMessagesDeleted,
    ChatPermissions, ErrorEvent, LinkPreviewOptions, Chat,
)
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.methods import (
    SendMessage, EditMessageText, SendPhoto, SendVideo, SendDocument,
    SendAnimation, AnswerCallbackQuery
)
from aiogram.methods.base import TelegramMethod

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float, Integer, String, Text,
    delete, func, select, update, or_, and_, text as sa_text, UniqueConstraint
)
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from cryptography.fernet import Fernet

try:
    from deep_translator import GoogleTranslator, MyMemoryTranslator
except ImportError:
    GoogleTranslator = None
    MyMemoryTranslator = None

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    from TelegramGifts import TelegramGifts as _TGifts
    _GIFTS_LIB = True
except ImportError:
    _TGifts = None
    _GIFTS_LIB = False
    logger.warning("⚠️ مكتبة TelegramGifts غير مثبتة — نفّذ: pip install TelegramGifts")

# =============================================================================
# 1. الإعدادات
# =============================================================================
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "8785870248:AAEx8AV6AmGj8-5Xnu-hSIMpY4lwvmmcq38").strip()
OWNER_ID = int(os.getenv("OWNER_ID") or 6577177859)
BOT_USERNAME = "@Bot"
SUPPORT_USERNAME = f"tg://user?id={OWNER_ID}" if OWNER_ID else "@Support"


def get_support_url() -> str:
    global SUPPORT_USERNAME, OWNER_ID
    if SUPPORT_USERNAME.startswith("@"):
        return f"https://t.me/{SUPPORT_USERNAME.replace('@', '')}"
    elif SUPPORT_USERNAME.startswith("tg://"):
        return SUPPORT_USERNAME
    elif OWNER_ID:
        return f"tg://user?id={OWNER_ID}"
    return "https://t.me"


FREE_TRIAL_HOURS = int(os.getenv("FREE_TRIAL_HOURS") or 24)
DATABASE_URL = (os.getenv("DATABASE_URL") or "sqlite+aiosqlite:///business_bot.db").strip()
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "AQ.Ab8RN6JcHImVzC7kg77Vp1fsN5rx2vzpwqrVP_Zn0nh9gqUXVg").strip()
CURRENCY_CACHE_TTL_MINUTES = int(os.getenv("CURRENCY_CACHE_TTL_MINUTES") or 5)
STARS_BUY_USD_PER_1000 = float(os.getenv("STARS_BUY_USD_PER_1000") or 15.0)
STARS_WITHDRAW_USD_PER_1000 = float(os.getenv("STARS_WITHDRAW_USD_PER_1000") or 13.0)
WALLET_TOKEN_ENCRYPTION_KEY = (os.getenv("WALLET_TOKEN_ENCRYPTION_KEY") or "OtmiofjMJNC5tyYH6RQ-p4D2is8pP3ANfqldLSHhXIY=").strip()

if not BOT_TOKEN:
    logger.warning("⚠️ لم يتم العثور على BOT_TOKEN في .env أو الكود!")
if not OPENAI_API_KEY and not GEMINI_API_KEY:
    logger.warning("⚠️ لا يوجد أي مفتاح AI — أمر .روك لن يعمل!")

_sub_check_cache: Dict[Tuple[int, int], Tuple[bool, datetime.datetime]] = {}
_SUB_CACHE_TTL = 30

_gift_price_cache: Dict[str, Tuple[dict, datetime.datetime]] = {}
_GIFT_PRICE_TTL = 300

_stars_live_cache: Dict[str, Any] = {}
_STARS_LIVE_TTL = 300  # 5 دقايق

# كاش إعدادات الاشتراك الإجباري في البوت نفسه
_bot_mandatory_sub_cache: Optional[Dict[str, Any]] = None
_BOT_SUB_CACHE_TTL = 60

# =============================================================================
# 2. النماذج
# =============================================================================
class Base(AsyncAttrs, DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), default="مستخدم")
    role: Mapped[str] = mapped_column(String(20), default="customer")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    trial_expires_at: Mapped[datetime.datetime] = mapped_column(DateTime)
    subscription_expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ServiceSetting(Base):
    __tablename__ = "service_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    service_key: Mapped[str] = mapped_column(String(50))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AutoReply(Base):
    __tablename__ = "auto_replies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    keyword: Mapped[str] = mapped_column(String(255), index=True)
    reply_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reply_type: Mapped[str] = mapped_column(String(20), default="text")
    media_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    match_type: Mapped[str] = mapped_column(String(20), default="contains")
    created_by: Mapped[int] = mapped_column(BigInteger, default=0)


class MutedUser(Base):
    __tablename__ = "muted_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, default=0)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)


class ExemptUser(Base):
    __tablename__ = "exempt_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)


class PlatformItem(Base):
    __tablename__ = "platforms"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(512))
    photo_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    price: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class ProductItem(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    price: Mapped[str] = mapped_column(String(64))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)


class TelegramPriceConfig(Base):
    __tablename__ = "telegram_prices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(32))
    key_name: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(128))
    price_amount: Mapped[str] = mapped_column(String(64))
    details_link: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)


class ScheduledMessage(Base):
    __tablename__ = "scheduled_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    business_connection_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    target_chat_id: Mapped[int] = mapped_column(BigInteger)
    text_content: Mapped[str] = mapped_column(Text)
    media_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    media_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    scheduled_time: Mapped[datetime.datetime] = mapped_column(DateTime)
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CashService(Base):
    __tablename__ = "cash_services"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    wallet_number: Mapped[str] = mapped_column(String(64))
    instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class BusinessConnectionRecord(Base):
    __tablename__ = "business_connections"
    connection_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)


class MandatorySubscription(Base):
    __tablename__ = "mandatory_subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    channel_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    channel_username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    channel_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    message_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    media_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    media_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)


class UserSecuritySettings(Base):
    __tablename__ = "user_security_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    hyperlink_protection: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_delete_malicious: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_security_alerts: Mapped[bool] = mapped_column(Boolean, default=True)


class UserMessageLog(Base):
    __tablename__ = "user_message_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_connection_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    owner_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    sender_full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sender_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    text_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    media_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    media_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, index=True)


class BroadcastTask(Base):
    __tablename__ = "broadcast_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_by: Mapped[int] = mapped_column(BigInteger)
    task_type: Mapped[str] = mapped_column(String(20))
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="running")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)


class BusinessChat(Base):
    __tablename__ = "business_chats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_connection_id: Mapped[str] = mapped_column(String(128), index=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    chat_type: Mapped[str] = mapped_column(String(20), default="private")
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)


class BusinessWelcomeSetting(Base):
    __tablename__ = "business_welcome_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    welcome_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    media_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    media_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class BusinessCustomer(Base):
    __tablename__ = "business_customers"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "business_connection_id", "customer_user_id", name="uq_owner_conn_customer"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    business_connection_id: Mapped[str] = mapped_column(String(128), index=True)
    customer_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    first_seen_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    last_seen_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    welcome_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    welcome_sent_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)


class SecurityAlertLog(Base):
    __tablename__ = "security_alert_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    alert_type: Mapped[str] = mapped_column(String(50))
    displayed_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    real_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)


class TonWallet(Base):
    __tablename__ = "ton_wallets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token: Mapped[str] = mapped_column(Text, nullable=False)
    wallet_address: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class TonTransaction(Base):
    __tablename__ = "ton_transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    sender: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    recipient: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    detected_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)


class BlacklistWord(Base):
    __tablename__ = "blacklist_words"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    word: Mapped[str] = mapped_column(String(255), index=True)
    action: Mapped[str] = mapped_column(String(50), default="delete")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)


class CustomerReminder(Base):
    __tablename__ = "customer_reminders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    chat_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    customer_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    customer_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    reminder_text: Mapped[str] = mapped_column(Text)
    remind_at: Mapped[datetime.datetime] = mapped_column(DateTime, index=True)
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)



class BotMandatorySubscription(Base):
    """الاشتراك الإجباري في البوت نفسه (يطبق على كل مستخدمي البوت)"""
    __tablename__ = "bot_mandatory_subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    channel_username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    channel_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        columns_to_ensure = [
            ("auto_replies", "user_id", "BIGINT"),
            ("platforms", "user_id", "BIGINT"),
            ("scheduled_messages", "created_by_user_id", "BIGINT"),
            ("scheduled_messages", "business_connection_id", "VARCHAR(128)"),
            ("service_settings", "user_id", "BIGINT"),
            ("muted_users", "owner_id", "BIGINT"),
            ("exempt_users", "owner_id", "BIGINT"),
            ("cash_services", "user_id", "BIGINT"),
            ("mandatory_subscriptions", "media_type", "VARCHAR(20)"),
            ("mandatory_subscriptions", "media_file_id", "VARCHAR(255)"),
            ("business_chats", "user_id", "BIGINT"),
            ("business_chats", "is_active", "BOOLEAN"),
            ("ton_wallets", "is_active", "BOOLEAN"),
            ("user_message_logs", "sender_full_name", "VARCHAR(255)"),
            ("user_message_logs", "sender_username", "VARCHAR(64)"),
        ]
        for tbl, col, col_type in columns_to_ensure:
            try:
                res = await conn.execute(sa_text(f"PRAGMA table_info({tbl})"))
                cols = [r[1] for r in res.fetchall()]
                if col not in cols:
                    await conn.execute(sa_text(f"ALTER TABLE {tbl} ADD COLUMN {col} {col_type}"))
            except Exception as e:
                logger.debug(f"mig {tbl}.{col}: {e}")

    async with async_session() as session:
        result = await session.execute(select(TelegramPriceConfig))
        if not result.scalars().first():
            session.add_all([
                TelegramPriceConfig(category="premium", key_name="3_months",
                                    title="تيليجرام بريميوم 3 شهور",
                                    price_amount="11.99 $ / 8.32 TON (~625 EGP)",
                                    details_link="https://fragment.com/premium/gift"),
                TelegramPriceConfig(category="premium", key_name="6_months",
                                    title="تيليجرام بريميوم 6 شهور",
                                    price_amount="15.99 $ / 11.10 TON (~835 EGP)",
                                    details_link="https://fragment.com/premium/gift"),
                TelegramPriceConfig(category="premium", key_name="12_months",
                                    title="تيليجرام بريميوم 12 شهر",
                                    price_amount="28.99 $ / 20.13 TON (~1510 EGP)",
                                    details_link="https://fragment.com/premium/gift"),
                TelegramPriceConfig(category="stars", key_name="stars_1000",
                                    title="1000 نجمة تيليجرام",
                                    price_amount="18.50 $ (شراء) | 13.00 $ (سحب)",
                                    details_link="https://fragment.com/stars"),
            ])
            await session.commit()


# =============================================================================
# 3. معرفات الإيموجي المميز ومساعدات التنسيق والأزرار والميدلوير الذكي
# =============================================================================
class _SafeEmojiDict(dict):
    _ALIASES = {
        "cross_ban": "ban",
        "download": "video",
        "cancel": "cross_red",
        "wave": "hand_wave",
        "alert": "warning",
    }
    def __getitem__(self, key: str) -> str:
        if key in self:
            return super().__getitem__(key)
        alt = self._ALIASES.get(key)
        if alt and alt in self:
            return super().__getitem__(alt)
        return super().get("sparkles", "6039709764710047914")

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        if key in self:
            return super().__getitem__(key)
        alt = self._ALIASES.get(key)
        if alt and alt in self:
            return super().__getitem__(alt)
        return default or super().get("sparkles", "6039709764710047914")


EMOJI_CHAR_TO_ID: Dict[str, str] = {
    '✅': '6039356946031582644',
    '💜': '6039499118039015593',
    '👻': '6039372824525676346',
    '💙': '6039782070484475191',
    '🐾': '6041878508446162499',
    '⚡️': '6039601175051902450',
    '🦋': '6039853169373093766',
    '🪫': '6039824740984561221',
    '♥️': '6039352032588996975',
    '🕷': '6039352792798208611',
    '🐐': '6039854706971385608',
    '🥸': '6039829156210941990',
    '📍': '6039794229536889989',
    '✨': '6039709764710047914',
    '🚫': '6039798533094128324',
    '⏫': '6039523457618681545',
    '⏬': '6039511839732146607',
    '🫀': '6039354626749242872',
    '🫡': '6039432700664750051',
    '💪': '6039469903671467364',
    '🚨': '6039552336978779515',
    '👤': '6039640735995666163',
    '🎸': '6039660527204966468',
    '🕊': '6039361588891230509',
    '🌙': '6039872105883902049',
    '🎮': '6039446006473432881',
    '💤': '6039758559833496913',
    '🛸': '6039859916766716524',
    '❤️': '6039740800143729430',
    '🙏': '6039455790408933411',
    '👾': '6039458925735060709',
    '🔇': '6039864508086755309',
    '‼️': '6039412183605975897',
    '🔥': '6039663022580964808',
    '⭐️': '6039367245363161212',
    '🔘': '6039347046131965239',
    '💬': '6041783877431729209',
    '🆓': '6039589286582427423',
    '💰': '6039496588303278706',
    '❓': '6039792850852388301',
    '🧠': '6039623740810076123',
    '😐': '6039845090539611468',
    '😇': '6039568297077251821',
    '👺': '6039685287691429285',
    '🥷': '6039787495028171913',
    '🏴☠️': '6039638622871757132',
    '🐍': '6039548381313900618',
    '🦇': '6039451564161113417',
    '💼': '6039797240308965054',
    '🎥': '6039693035812430628',
    '🐈': '6039434547500694532',
    '📶': '6039873609122454883',
    '💦': '6039362658338087382',
    '⚪️': '6039858744240643275',
    '💡': '6039372145920843255',
    '⏰': '6039724414843493979',
    '💉': '6039441518232608312',
    '⭕️': '6039724376188790323',
    '👎': '6039866526721384015',
    '👁': '6039829448268717429',
    '💣': '6039714261540805568',
    '☢️': '6039424583176560203',
    '⏹': '6039507896952168996',
    '🐋': '6039739391394455739',
    '👣': '6039657563677531281',
    '💋': '6039634594192434081',
    '🪳': '6041590333320469259',
    '💱': '6039369521695824813',
    '🕸': '6039512509747043329',
    '🫱': '6039886378060226482',
    '🐜': '6039349056176660616',
    '🌿': '6039806418654077030',
    '💫': '6041860443813715460',
    '🫗': '6039430407152212646',
    '🧪': '6039529410443354254',
    '🍷': '6039467240791744184',
    '💊': '6039739992689876618',
    '🖕': '6039364217411216795',
    '💀': '6039805559660616963',
    '🪐': '6039819084512633180',
    '💔': '6039766140450774222',
    '🌳': '6039742664159534708',
    '0️⃣': '6039342545006238885',
    '1️⃣': '6039470960233422559',
    '2️⃣': '6039813110213125232',
    '3️⃣': '6039685893281816066',
    '4️⃣': '6039828731009178434',
    '5️⃣': '6039843913718570092',
    '6️⃣': '6039547432126127049',
    '7️⃣': '6039616886042270929',
    '8️⃣': '6039336914304113568',
    '9️⃣': '6041946179950877364',
    '⭐': '6039846464929143843',
    '🐆': '6039794659033619801',
    '🌱': '6039805061444411361',
    '🌟': '6039400986626234994',
    '☀️': '6039872088704033987',
    '❄️': '6039863803712120413',
    '🥃': '6039365845203820061',
    '🕺': '6039805181703495789',
    '📞': '6039741826640911366',
    '🍹': '6039536733362594017',
    '🦎': '6039710189911809800',
    '🎵': '6039418467143130491',
    '\U0001fa75': '6039768317999193470',
    '✈️': '6039414283844983924',
    '😌': '6039787988949409086',
    '⚙️': '6039716284470402303',
    '✝️': '6039598340373488276',
    '🐉': '6041669936244333052',
    '🔫': '6039676538843045684',
    '🔪': '6039360691243065130',
    '🐺': '6039565492463607224',
    '🥺': '6039554200994585559',
    '☹️': '6039503503200624167',
    '😟': '6039446161092256388',
    '🙂': '6039637420280913020',
    '🐎': '6039420614626778814',
    '🎼': '6039659096980856119',
    '🔝': '6039412209375779934',
    '⚡': '6039372863180382081',
    '🌈': '6039400501294931152',
    '🗣️': '6039413523635789928',
    '🫶': '6039414026146947211',
    '😂': '6039614601119669905',
    '🦄': '6039500943400116835',
    '🕊️': '6039591790548361317',
    '🔒': '6039774717500465122',
    '🗿': '6039459879217799112',
    '🔋': '6039467343870959444',
    '🌐': '6039706985866207150',
    '💘': '6039429264690913235',
    '🔕': '6039658663189158884',
    '🔔': '6039810103736017240',
    '🆕': '6039407742609793302',
    '⚠️': '6039409709704814287',
    '🔗': '6039776968063326904',
    '📌': '6041850440834885588',
    '🖱️': '6039537248758668953',
    '⛔': '6039850532263174530',
    '👋': '6039876280592112521',
    '👍': '6039836612274167260',
    '🌴': '6041602750070923209',
    '🦶': '6039620188872120936',
    '🍴': '6039739743581774494',
    '🚁': '6039722477813249693',
    '📷': '6039643566379114876',
    '🔢': '6039805228948136760',
    '🖼': '6041915243301444470',
    '📂': '6039379035048385953',
    '❌': '6039814012156255361',
    '⚽️': '6039435032831991507',
    '🐻': '6041818542112775990',
    '📝': '6042118081721932894',
    '🪙': '6039512479682272958',
    '📖': '6039336115440197125',
    '🤖': '6039590961619673046',
    '👨🎨': '6039457581410295410',
    '🗓': '6039552474417732206',
    '🤙': '6039860157284884493',
    '📸': '6039534860756851792',
    '👩🎨': '6039416860825362641',
    '📣': '6041612551186292283',
    '⛓': '6039775228601573431',
    '📄': '6039782993902443640',
    '©': '6039562335662645860',
    '👥': '6039669245988577871',
    '🗑': '6039664766337688108',
    '✍️': '6039620966261203025',
    '↔️': '6039372506698096613',
    '🎭': '6039577934983864218',
    '📁': '6042065000221123463',
    '➡️': '6039385189736520705',
    '🎓': '6039329398111346628',
    '🏘': '6039737974055247692',
    '🚪': '6039423200197091098',
    '🌘': '6041965215245933425',
    '🎶': '6039490403550371731',
    '↗️': '6042008680314969783',
    '➕': '6041656093564738470',
    '🍑': '6039552452942896818',
    '👏': '6039847113469205524',
    '🔄': '6039846890130907629',
    '🌠': '6039365213843626881',
    '🔃': '6039509383010853350',
    '⬅️': '6041669167445188625',
    '❗️': '6039392263547658545',
    '🤚': '6039441763045750952',
    '🗳': '6039372270474896004',
    '⬇️': '6039514592806183625',
    '🕔': '6039572665058991567',
    '⏲': '6039829718851657806',
    '👀': '6039597670358588896',
    '⬆️': '6039678145160815793',
    '🔼': '6042024971125923776',
    '💻': '6039658323886743512',
    'ℹ️': '6039819140347208864',
    '📈': '6039704146892824643',
    '📚': '6039640358038544904',
    '📦': '6039757567696053443',
    '✋': '6039455176228610144',
    '📹': '6039866432232103292',
    '📼': '6039403301613609881',
    '🙃': '6039400398215718335',
    '🕘': '6039784729069233299',
    '🎙': '6039363448612068647',
    '⚙': '6039762498318508419',
    '♾': '6041932603559256174',
    '🔊': '6039730423502741487',
    '📰': '6039559243286191682',
    '🏷': '6039756463889457163',
    '🔎': '6039474993207713367',
    '⛔️': '6039721945237298756',
    '🔞': '6039634585602497930',
    '🔖': '6039591627339603036',
    '💎': '6042038955539439522',
    '✔️': '5843453012235788448',
    '☑️': '5841683172177223617',
    '👑': '5843886267061772989',
    '😡': '5843687165262831900',
    '🥹': '5843971994609000759',
    '😍': '5843734963953869743',
    '😚': '5843804490884456535',
    '😥': '5841171147651030874',
    '👽': '5841676940179677657',
    '😔': '5843823247006638552',
    '🐱': '5843513017223881150',
    '🐹': '5841701627651694320',
    '⚜️': '5841523558307604168',
    '🥲': '5843809756514360368',
    '😴': '5841441790720221315',
    '😱': '5843865526664700205',
    '😭': '5843786288813055877',
    '😢': '5843776135510368244',
    '😄': '5843421409866424399',
    '🗺': '5843502052172374457',
    '🧭': '5843815047914070255',
    '🚀': '5843471450530392639',
    '☺️': '5843834568540430157',
    '😊': '5841179969513857411',
    '😎': '5843486315412201047',
    '😃': '5843572416621584991',
    '🔺': '5843558178804998209',
    '🔻': '5843788732649447172',
    '✖️': '5843547441386758116',
    '😀': '5843893985118003899',
    '🔵': '5843841676711304899',
    '🆒': '5841253580958343345',
    '🌚': '5843783368235294697',
    '🤠': '5843546603868135959',
    '🧐': '5841702761523059710',
    '😒': '5843768632202502660',
    '👩🚒': '5843467885707534384',
    '🇮🇳': '5841695717776695179',
    '😑': '5843810636982657440',
    '😆': '5841283349376672256',
    '💥': '5841633333376720894',
    '😘': '5843560008461065627',
    '☑': '5843948187605279458',
    '✔': '5843646779685348111',
    '📱': '5841206619785929513',
    '🎁': '5843951228442124665',
    '⏺': '5841379758507567090',
    '🖥': '5843498422925008485',
    '🎞': '5843973016811216899',
    '👨💻': '5841713975682670802',
    '🎲': '5843816834620465107',
    '💲': '5843470110500594231',
    '🍎': '5843908566531972588',
    '🌄': '5841287476840244238',
    '🔦': '5843654639475498307',
    '📅': '5843548863020934664',
    '✈': '5843710194877472234',
    '🏞': '5843908751215566092',
    '🇺🇸': '5843587603625943751',
    '🇨🇦': '5841271508151836672',
    '🇬🇧': '5843907205027341395',
    '🏴': '5843438684224889439',
    '🇫🇷': '5843697941335777675',
    '🇮🇹': '5841515707107385208',
    '🇩🇪': '5843441579032845671',
    '🇫🇮': '5843820910544432107',
    '🇳🇱': '5843667863679804021',
    '🇪🇪': '5843657091901826118',
    '🇨🇳': '5843906255839567623',
    '🇯🇵': '5843554575327435938',
    '🇰🇷': '5843620185247850233',
    '📎': '5843826485411979176',
    '🎧': '5843765986502647378',
    '🎒': '5843568890453434431',
    '🎺': '5843900659497181358',
    '🏁': '5843488931047284227',
    '🕤': '5843721043964862025',
    '⚫': '5843630527529099391',
    '⚪': '5843957692367904925',
    '🕦': '5843946220510257252',
    '🔍': '5841580123026890236',
    '🟢': '5843643055948701589',
    '🟡': '5843878252652800900',
    '🤍': '5843481526523666855',
    '❤🩹': '5843430253204087082',
    '❤': '5843871346345387492',
    '♥': '5843763242018547062',
    '☝': '5841371185752845358',
}

CUSTOM_EMOJI_IDS: Dict[str, str] = _SafeEmojiDict({
    # علامات التحقق والنجاح
    "check": "6039356946031582644",
    "check_green": "6039477333964890643",
    "check_verified": "6039403937268768557",
    "check_round": "5841432809943607227",
    "check_tick": "5843453012235788448",

    # الخطأ والخطر والحذف
    "cross": "6039814012156255361",
    "cross_red": "6039471174981787582",
    "cross_bold": "6039702549164990043",
    "cross_ban": "6039798533094128324",
    "ban": "6039798533094128324",
    "stop": "6039850532263174530",
    "trash": "6039664766337688108",

    # الأمان والتنبيهات
    "alert": "6039409709704814287",
    "warning": "6039409709704814287",
    "siren": "6039552336978779515",
    "shield": "6039798533094128324",
    "lock": "6039774717500465122",
    "key_lock": "6039489205254495806",

    # التميز والنجوم والبريميوم
    "star": "6039367245363161212",
    "star_gold": "6039676929685069487",
    "crown": "5843886267061772989",
    "diamond": "6042038955539439522",
    "gift": "5843951228442124665",
    "sparkles": "6039709764710047914",
    "fire": "6039663022580964808",
    "bolt": "6039601175051902450",
    "heart_red": "6039740800143729430",
    "heart_blue": "6039782070484475191",
    "heart_purple": "6039499118039015593",

    # الذكاء الاصطناعي والأدوات
    "robot": "6039590961619673046",
    "brain": "6039623740810076123",
    "gear": "6039716284470402303",
    "bulb": "6039372145920843255",
    "laptop": "6039658323886743512",
    "mobile": "5841206619785929513",
    "search": "6039474993207713367",
    "stats": "6039704146892824643",
    "clock": "6039724414843493979",
    "calendar": "6039552474417732206",
    "infinity": "6041932603559256174",

    # الأموال والعملات والكاش
    "money": "6039496588303278706",
    "coin": "6039512479682272958",
    "dollar": "5843470110500594231",
    "exchange": "6039369521695824813",
    "briefcase": "6039797240308965054",

    # التواصل والمستخدمين
    "user": "6039640735995666163",
    "users": "6039669245988577871",
    "chat": "6041783877431729209",
    "broadcast": "6041612551186292283",
    "bell": "6039810103736017240",
    "mute": "6039864508086755309",
    "phone": "6039741826640911366",
    "link": "6039776968063326904",
    "pin": "6041850440834885588",
    "doc": "6039782993902443640",
    "folder": "6042065000221123463",

    # التنقل
    "back": "6039423200197091098",
    "arrow_left": "6041669167445188625",
    "arrow_right": "6039385189736520705",
    "arrow_up": "6039678145160815793",
    "arrow_down": "6039514592806183625",
    "refresh": "6039846890130907629",
    "plus": "6041656093564738470",

    # وسائط وإسلاميات
    "video": "6039693035812430628",
    "camera": "6041675859004235088",
    "music": "6039418467143130491",
    "game": "6039446006473432881",
    "book": "6039336115440197125",
    "pray": "6039455790408933411",
    "globe": "6039706985866207150",

    # أعلام
    "flag_gb": "5843907205027341395",
    "flag_fr": "5843697941335777675",
    "flag_de": "5843441579032845671",
    "flag_es": "6039706985866207150",

    # أرقام
    "num_0": "6039342545006238885",
    "num_1": "6039470960233422559",
    "num_2": "6039813110213125232",
    "num_3": "6039685893281816066",
    "num_4": "6039828731009178434",
    "num_5": "6039843913718570092",
    "num_6": "6039547432126127049",
    "num_7": "6039616886042270929",
    "num_8": "6039336914304113568",
    "num_9": "6041946179950877364",

    # إضافات مميزة
    "wave": "6039876280592112521",
    "hand_wave": "6039876280592112521",
    "ghost": "6039372824525676346",
    "butterfly": "6039853169373093766",
    "bomb": "6039714261540805568",
    "skull": "6039805559660616963",
    "eye": "6039829448268717429",
    "point": "6039347046131965239",
    "write": "6039620966261203025",
    "sun": "6039872088704033987",
    "moon": "6039872105883902049",
    "dove": "6039361588891230509",
})


def ce(key_or_char: str, fallback: str = "✨") -> str:
    """توليد وسم الإيموجي المميز لتليجرام أو الرمز الافتراضي"""
    eid = CUSTOM_EMOJI_IDS.get(key_or_char) or EMOJI_CHAR_TO_ID.get(key_or_char)
    if not eid:
        return fallback
    return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'


class _CEMeta(type):
    def __getattr__(cls, name: str) -> str:
        key = name.lower()
        if key in CUSTOM_EMOJI_IDS:
            return ce(key, "✨")
        aliases = {
            "cross_ban": "ban",
            "download": "video",
            "hand_wave": "wave",
        }
        if key in aliases and aliases[key] in CUSTOM_EMOJI_IDS:
            return ce(aliases[key], "✨")
        return "✨"


class CE(metaclass=_CEMeta):
    CHECK = ce("check", "✅")
    CHECK_VERIFIED = ce("check_verified", "✅")
    CROSS = ce("cross", "❌")
    CROSS_BAN = ce("ban", "🚫")
    BAN = ce("ban", "🚫")
    ALERT = ce("alert", "⚠️")
    SIREN = ce("siren", "🚨")
    SHIELD = ce("shield", "🛡️")
    STAR = ce("star", "⭐️")
    STAR_GOLD = ce("star_gold", "⭐️")
    CROWN = ce("crown", "👑")
    DIAMOND = ce("diamond", "💎")
    FIRE = ce("fire", "🔥")
    BOLT = ce("bolt", "⚡️")
    ROBOT = ce("robot", "🤖")
    BRAIN = ce("brain", "🧠")
    GEAR = ce("gear", "⚙️")
    LOCK = ce("lock", "🔒")
    KEY = ce("key_lock", "🔐")
    MONEY = ce("money", "💰")
    COIN = ce("coin", "🪙")
    USER = ce("user", "👤")
    USERS = ce("users", "👥")
    CHAT = ce("chat", "💬")
    BROADCAST = ce("broadcast", "📢")
    BELL = ce("bell", "🔔")
    MUTE = ce("mute", "🔇")
    BACK = ce("back", "🚪")
    PLUS = ce("plus", "➕")
    TRASH = ce("trash", "🗑")
    REFRESH = ce("refresh", "🔄")
    SPARKLES = ce("sparkles", "✨")
    GIFT = ce("gift", "🎁")
    GAME = ce("game", "🎮")
    PRAY = ce("pray", "🙏")
    BOOK = ce("book", "📖")
    SEARCH = ce("search", "🔎")
    STATS = ce("stats", "📈")
    GLOBE = ce("globe", "🌐")
    VIDEO = ce("video", "🎥")
    DOWNLOAD = ce("video", "📥")
    DOC = ce("doc", "📄")
    CAM = ce("camera", "📸")
    INFO = ce("bulb", "💡")
    TIME = ce("clock", "⏰")
    CLOCK = ce("clock", "⏰")
    CAL = ce("calendar", "🗓")
    LINK = ce("link", "🔗")
    PIN = ce("pin", "📌")
    HEART_RED = ce("heart_red", "❤️")
    HEART_BLUE = ce("heart_blue", "💙")
    HEART_PURPLE = ce("heart_purple", "💜")
    INFINITY = ce("infinity", "♾")
    WAVE = ce("wave", "👋")
    HAND_WAVE = ce("wave", "👋")
    POINT = ce("point", "🔘")
    WRITE = ce("write", "✍️")
    UP = ce("arrow_up", "⬆️")
    DOWN = ce("arrow_down", "⬇️")
    LEFT = ce("arrow_left", "⬅️")
    RIGHT = ce("arrow_right", "➡️")
    EYE = ce("eye", "👁")
    SKULL = ce("skull", "💀")
    BOMB = ce("bomb", "💣")
    BUTTERFLY = ce("butterfly", "🦋")
    GHOST = ce("ghost", "👻")
    SUN = ce("sun", "☀️")
    MOON = ce("moon", "🌙")
    DOVE = ce("dove", "🕊")
    BRIEFCASE = ce("briefcase", "💼")
    NUM_0 = ce("num_0", "0️⃣")
    NUM_1 = ce("num_1", "1️⃣")
    NUM_2 = ce("num_2", "2️⃣")
    NUM_3 = ce("num_3", "3️⃣")
    NUM_4 = ce("num_4", "4️⃣")
    NUM_5 = ce("num_5", "5️⃣")
    NUM_6 = ce("num_6", "6️⃣")
    NUM_7 = ce("num_7", "7️⃣")
    NUM_8 = ce("num_8", "8️⃣")
    NUM_9 = ce("num_9", "9️⃣")


def b(text: Any) -> str:
    """نص عريض"""
    return f"<b>{text}</b>"


def i(text: Any) -> str:
    """نص مائل"""
    return f"<i>{text}</i>"


def quote(text: Any, expandable: bool = False) -> str:
    """نص مقتبس (عادي أو قابل للطي)"""
    tag = "blockquote expandable" if expandable else "blockquote"
    return f"<{tag}>{text}</{tag}>"


def spoiler(text: Any) -> str:
    """نص مشوش / مغطى"""
    return f"<tg-spoiler>{text}</tg-spoiler>"


def code(text: Any) -> str:
    """كود برمجي أو نص مميز"""
    return f"<code>{text}</code>"


_SORTED_EMOJIS = sorted(EMOJI_CHAR_TO_ID.keys(), key=lambda s: len(s), reverse=True)
_EMOJI_REGEX = re.compile('(' + '|'.join(re.escape(e) for e in _SORTED_EMOJIS) + ')')
_HTML_SPLIT_REGEX = re.compile(r'(<tg-emoji[^>]*>.*?</tg-emoji>|<[^>]+>)', re.DOTALL)


def format_custom_emojis(text: str) -> str:
    """استبدال الرموز التعبيرية العادية برموز تليجرام المميزة المخصصة مع الحفاظ التام على وسوم HTML"""
    if not text:
        return text
    parts = _HTML_SPLIT_REGEX.split(str(text))
    for idx, part in enumerate(parts):
        if not part.startswith('<'):
            parts[idx] = _EMOJI_REGEX.sub(
                lambda m: f'<tg-emoji emoji-id="{EMOJI_CHAR_TO_ID[m.group(0)]}">{m.group(0)}</tg-emoji>',
                part
            )
    return ''.join(parts)


class RichFormattingMiddleware(BaseRequestMiddleware):
    """ميدلوير لمعالجة وتزيين جميع الرسائل الصادرة تلقائياً بالإيموجي المميز ووسوم HTML الآمنة"""
    async def __call__(self, make_request, bot, method: TelegramMethod):
        if isinstance(method, (SendMessage, EditMessageText)):
            if hasattr(method, "text") and method.text:
                method.text = format_custom_emojis(method.text)
                if not getattr(method, "parse_mode", None):
                    method.parse_mode = ParseMode.HTML
        elif isinstance(method, (SendPhoto, SendVideo, SendDocument, SendAnimation)):
            if hasattr(method, "caption") and method.caption:
                method.caption = format_custom_emojis(method.caption)
                if not getattr(method, "parse_mode", None):
                    method.parse_mode = ParseMode.HTML

        try:
            return await make_request(bot, method)
        except TelegramBadRequest as e:
            err_str = str(e).lower()
            if "can't parse entities" in err_str or "unclosed" in err_str:
                logger.warning(f"[RichFormatting] HTML parse failed, falling back to plain text: {e}")
                if hasattr(method, "parse_mode"):
                    method.parse_mode = None
                return await make_request(bot, method)
            raise


_BTN_EMOJI_RE = re.compile(
    r'[0-9#*][️]?[⃣]'
    r'|[𐀀-􏿿]'
    r'|[☀-➿]'
    r'|[⌀-⏿]'
    r'|[⭐⭕‍️⃣]'
    r'|[←-⇿]'
)


def _strip_btn_emojis(t: str) -> str:
    cleaned = _BTN_EMOJI_RE.sub('', t)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned or t


def make_btn(
    text: str,
    callback_data: Optional[str] = None,
    url: Optional[str] = None,
    style: Optional[str] = None,
    icon_custom_emoji_id: Optional[str] = None,
    **kwargs
) -> InlineKeyboardButton:
    raw_text = str(text)
    args: Dict[str, Any] = {}
    if callback_data is not None:
        args["callback_data"] = callback_data
    if url is not None:
        args["url"] = url

    # 1. ضبط الستايل (primary, success, danger)
    if not style or style not in ("primary", "success", "danger"):
        tl = text.lower()
        cb = (callback_data or "").lower()
        if any(w in tl or w in cb for w in [
            "حذف", "إلغاء", "رجوع", "إنهاء", "كتم", "حظر", "تعطيل", "إيقاف", "خروج",
            "delete", "cancel", "back", "end", "mute", "ban", "off", "del", "remove", "reset", "danger"
        ]):
            style = "danger"
        elif any(w in tl or w in cb for w in [
            "تفعيل", "إضافة", "تأكيد", "بدء", "حفظ", "اشتراك", "اشتركت", "تجديد", "إهداء",
            "success", "add", "confirm", "start", "save", "yes", "done", "enable", "on", "شراء", "سبح", "سبّح"
        ]):
            style = "success"
        else:
            style = "primary"
    args["style"] = style

    # 2. تعيين أيقونة الإيموجي المخصص (icon_custom_emoji_id)
    if not icon_custom_emoji_id:
        tl = text.lower()
        cb = (callback_data or "").lower()
        if any(w in tl or w in cb for w in ["رجوع", "خروج", "back"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["back"]
        elif any(w in tl or w in cb for w in ["حذف", "delete", "del", "remove"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["trash"]
        elif any(w in tl or w in cb for w in ["إلغاء", "cancel"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["cross_red"]
        elif any(w in tl or w in cb for w in ["كتم", "حظر", "mute", "ban"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["ban"]
        elif any(w in tl or w in cb for w in ["إضافة", "add", "جديد", "new"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["plus"]
        elif any(w in tl or w in cb for w in ["تأكيد", "تفعيل", "اشتركت", "done", "yes", "حفظ"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["check_verified"]
        elif any(w in tl or w in cb for w in ["تحديث", "refresh", "reset", "🔄"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["refresh"]
        elif any(w in tl or w in cb for w in ["تون", "محفظة", "محافظ", "ton", "wallet"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["diamond"]
        elif any(w in tl or w in cb for w in ["ذكاء", "ai", "gemini", "openai"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["brain"]
        elif any(w in tl or w in cb for w in ["أمان", "حماية", "security"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["shield"]
        elif any(w in tl or w in cb for w in ["stars", "نجم", "نجوم"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["star"]
        elif any(w in tl or w in cb for w in ["premium", "بريميوم"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["crown"]
        elif any(w in tl or w in cb for w in ["كاش", "فلوس", "أموال", "رصيد", "cash"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["money"]
        elif any(w in tl or w in cb for w in ["إذاعة", "إعلان", "broadcast"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["broadcast"]
        elif any(w in tl or w in cb for w in ["هدية", "هدايا", "gift"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["gift"]
        elif any(w in tl or w in cb for w in ["إعدادات", "ضبط", "settings"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["gear"]
        elif any(w in tl or w in cb for w in ["تواصل", "شات", "رسالة", "ردود", "replies"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["chat"]
        elif any(w in tl or w in cb for w in ["إسلاميات", "تسبيح", "أذكار", "سبح"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["pray"]
        elif any(w in tl or w in cb for w in ["ألعاب", "تسلية", "كت", "لو خيروك", "game"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["game"]
        elif any(w in tl or w in cb for w in ["تحميل", "فيديو", "download"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["video"]
        elif any(w in tl or w in cb for w in ["ترجمة", "translate"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["globe"]
        elif any(w in tl or w in cb for w in ["فحص", "كشف", "inspect"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["search"]
        elif any(w in tl or w in cb for w in ["حاسبة", "calc"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["bulb"]
        elif any(w in tl or w in cb for w in ["اشتراك", "قناة", "mandatory"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["book"]
        elif any(w in tl or w in cb for w in ["1️⃣", "wyr_1"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["num_1"]
        elif any(w in tl or w in cb for w in ["2️⃣", "wyr_2"]):
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["num_2"]
        else:
            icon_custom_emoji_id = CUSTOM_EMOJI_IDS["sparkles"]
    args["icon_custom_emoji_id"] = icon_custom_emoji_id
    args["text"] = _strip_btn_emojis(raw_text)

    args.update(kwargs)
    if "text" in kwargs:
        args["text"] = _strip_btn_emojis(str(kwargs["text"]))
    return InlineKeyboardButton(**args)

# =============================================================================
# 4. الآلة الحاسبة
# =============================================================================
class SafeCalculator:
    _OPS = {
        ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
    }

    @classmethod
    def evaluate(cls, expr):
        clean = expr.strip().replace("×", "*").replace("÷", "/")
        if not re.match(r"^[0-9\.\+\-\*\/\%\(\)\s]+$", clean) or len(clean) > 60:
            return None
        try:
            node = ast.parse(clean, mode='eval')
            result = cls._eval(node.body)
            if isinstance(result, (int, float)) and abs(result) <= 1e15:
                return round(result, 4) if isinstance(result, float) else result
        except Exception:
            pass
        return None

    @classmethod
    def _eval(cls, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp):
            l, r = cls._eval(node.left), cls._eval(node.right)
            t = type(node.op)
            if t not in cls._OPS:
                raise ValueError()
            if t == ast.Pow and (r > 50 or r < -50):
                raise ValueError()
            return cls._OPS[t](l, r)
        if isinstance(node, ast.UnaryOp):
            t = type(node.op)
            if t not in cls._OPS:
                raise ValueError()
            return cls._OPS[t](cls._eval(node.operand))
        raise ValueError()


# =============================================================================
# 5. محول العملات الاحترافي
# =============================================================================
DEFAULT_CURRENCIES_LAYOUT = [
    ("EGP", "USDT"),
    ("BTC", "ETH"),
    ("BNB", "SOL"),
    ("XRP", "ADA"),
    ("DOGE", "SHIB"),
    ("TRX", "DOT"),
    ("MATIC", "ASIA"),
]


def get_converter_grid(base_sym: str) -> List[Tuple[str, str]]:
    norm_base = base_sym.upper()
    if norm_base in ("USD", "USDT"):
        norm_base = "USDT"
    elif norm_base in ("TON", "GRAM"):
        norm_base = "TON"

    if norm_base == "TON":
        return DEFAULT_CURRENCIES_LAYOUT

    grid = []
    for left, right in DEFAULT_CURRENCIES_LAYOUT:
        new_left = "TON" if left == norm_base else left
        new_right = "TON" if right == norm_base else right
        grid.append((new_left, new_right))
    return grid

CURRENCY_DISPLAY = {
    "TON": ("⚗️", "Gram"),
    "GRAM": ("⚗️", "Gram"),
    "USDT": ("💵", "USDT"),
    "USD": ("💵", "USD"),
    "EGP": ("🇪🇬", "EGP"),
    "SAR": ("🇸🇦", "SAR"),
    "AED": ("🇦🇪", "AED"),
    "EUR": ("💶", "EUR"),
    "BTC": ("₿", "BTC"),
    "ETH": ("Ξ", "ETH"),
    "BNB": ("🟡", "BNB"),
    "SOL": ("🟣", "SOL"),
    "XRP": ("✕", "XRP"),
    "ADA": ("🔵", "ADA"),
    "DOGE": ("🐶", "DOGE"),
    "SHIB": ("🐕", "SHIB"),
    "TRX": ("🔴", "TRX"),
    "DOT": ("⚪", "DOT"),
    "MATIC": ("🔷", "MATIC"),
    "ASIA": ("📱", "ASIA"),
}

CURRENCY_ALIASES = {
    "تون": "TON", "ton": "TON",
    "جرام": "GRAM", "gram": "GRAM",
    "دولار": "USD", "usd": "USD", "usdt": "USDT", "تيزر": "USDT", "يو اس دي": "USD", "دولارات": "USD",
    "جنيه": "EGP", "جنية": "EGP", "egp": "EGP", "مصري": "EGP", "ج.م": "EGP", "ج": "EGP",
    "بيتكوين": "BTC", "بتكوين": "BTC", "btc": "BTC",
    "ايثريوم": "ETH", "اثيريوم": "ETH", "eth": "ETH",
    "بينانس": "BNB", "bnb": "BNB", "بي ان بي": "BNB",
    "سولانا": "SOL", "سول": "SOL", "sol": "SOL",
    "ريبل": "XRP", "xrp": "XRP",
    "كاردانو": "ADA", "ada": "ADA",
    "دوج": "DOGE", "دوجكوين": "DOGE", "doge": "DOGE",
    "شيبا": "SHIB", "shib": "SHIB",
    "ترون": "TRX", "trx": "TRX",
    "بولكادوت": "DOT", "dot": "DOT",
    "ماتيك": "MATIC", "matic": "MATIC", "بوليجون": "MATIC", "pol": "MATIC",
    "اسيا": "ASIA", "آسيا": "ASIA", "asia": "ASIA",
    "اسيا سيل": "ASIA", "آسيا سيل": "ASIA", "اسياسيل": "ASIA", "آسياسيل": "ASIA",
    "ريال": "SAR", "سعودي": "SAR", "sar": "SAR",
    "درهم": "AED", "اماراتي": "AED", "aed": "AED",
    "يورو": "EUR", "eur": "EUR",
}


def format_crypto_num(val: float) -> str:
    if val >= 1000:
        return f"{val:,.2f}"
    elif val >= 1:
        if val == int(val):
            return f"{val:.1f}"
        return f"{val:,.4f}".rstrip("0").rstrip(".")
    elif val >= 0.0001:
        return f"{val:.6f}".rstrip("0").rstrip(".")
    else:
        return f"{val:.8f}".rstrip("0").rstrip(".")


def parse_currency_query(text: str) -> Optional[Tuple[str, float]]:
    t = text.strip().lstrip(".").strip().lower()
    t = re.sub(r"^(?:تحويل|صرف|سعر|احسب|كم|كام)\s+", "", t).strip()

    m1 = re.match(r"^([\d,]+(?:\.\d+)?)\s*([a-zA-Z\u0600-\u06FF\.]+)$$", t)
    if m1:
        amt_str = m1.group(1).replace(",", "")
        cur_str = m1.group(2).lower()
        if cur_str in CURRENCY_ALIASES:
            try:
                amt = float(amt_str)
                if amt > 0:
                    return CURRENCY_ALIASES[cur_str], amt
            except ValueError:
                pass

    m2 = re.match(r"^([a-zA-Z\u0600-\u06FF\.]+)\s+([\d,]+(?:\.\d+)?)$$", t)
    if m2:
        cur_str = m2.group(1).lower()
        amt_str = m2.group(2).replace(",", "")
        if cur_str in CURRENCY_ALIASES:
            try:
                amt = float(amt_str)
                if amt > 0:
                    return CURRENCY_ALIASES[cur_str], amt
            except ValueError:
                pass

    if t in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[t], 1.0

    return None


class CurrencyCache:
    _data: Dict[str, float] = {}
    _all_usd: Dict[str, float] = {}
    _last: Optional[datetime.datetime] = None

    @classmethod
    async def get_rates(cls):
        now = datetime.datetime.utcnow()
        if cls._last and (now - cls._last).total_seconds() < CURRENCY_CACHE_TTL_MINUTES * 60 and cls._all_usd:
            return {
                "rates": cls._data,
                "all_usd": cls._all_usd,
                "updated_at": cls._last.strftime("%Y-%m-%d %H:%M UTC"),
                "source": "Cached",
            }
        rates = {"ton_usd": 1.366, "gram_usd": 1.366, "usd_egp": 52.14}
        all_usd: Dict[str, float] = {
            "USDT": 1.0,
            "USD": 1.0,
            "EGP": 1.0 / 52.14,
            "SAR": 1.0 / 3.75,
            "AED": 1.0 / 3.67,
            "EUR": 1.0 / 0.92,
            "TON": 1.366,
            "GRAM": 1.366,
            "BTC": 78000.0,
            "ETH": 2500.0,
            "BNB": 740.0,
            "SOL": 105.0,
            "XRP": 1.30,
            "ADA": 0.21,
            "DOGE": 0.085,
            "SHIB": 0.0000053,
            "TRX": 0.33,
            "DOT": 1.15,
            "MATIC": 0.38,
            "ASIA": 0.05,
        }
        try:
            async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
                # 1. أسعار العملات الرقمية من Binance مباشرة
                try:
                    r_binance = await client.get("https://api.binance.com/api/v3/ticker/price")
                    if r_binance.status_code == 200:
                        b_prices = {x["symbol"]: float(x["price"]) for x in r_binance.json()}
                        crypto_map = {
                            "TON": "TONUSDT",
                            "BTC": "BTCUSDT",
                            "ETH": "ETHUSDT",
                            "BNB": "BNBUSDT",
                            "SOL": "SOLUSDT",
                            "XRP": "XRPUSDT",
                            "ADA": "ADAUSDT",
                            "DOGE": "DOGEUSDT",
                            "SHIB": "SHIBUSDT",
                            "TRX": "TRXUSDT",
                            "DOT": "DOTUSDT",
                        }
                        for cur_key, pair in crypto_map.items():
                            if pair in b_prices and b_prices[pair] > 0:
                                all_usd[cur_key] = b_prices[pair]
                        pol_price = b_prices.get("MATICUSDT") or b_prices.get("POLUSDT")
                        if pol_price and pol_price > 0:
                            all_usd["MATIC"] = pol_price
                except Exception as e:
                    logger.debug(f"Binance rate error: {e}")

                # 2. سعر TON الرسمي من Fragment
                try:
                    rf = await client.get("https://fragment.com/stars", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0"})
                    m_ton = re.search(r'"tonRate":\s*([\d.]+)', rf.text)
                    if m_ton:
                        tr_val = float(m_ton.group(1))
                        if tr_val > 0:
                            all_usd["TON"] = tr_val
                except Exception:
                    pass

                all_usd["GRAM"] = all_usd["TON"]
                rates["ton_usd"] = all_usd["TON"]
                rates["gram_usd"] = all_usd["TON"]

                # 3. أسعار العملات النقدية (EGP, SAR, AED, EUR)
                try:
                    r_fiat = await client.get("https://open.er-api.com/v6/latest/USD")
                    if r_fiat.status_code == 200:
                        f_rates = r_fiat.json().get("rates", {})
                        for f_cur in ["EGP", "SAR", "AED", "EUR"]:
                            if f_cur in f_rates and f_rates[f_cur] > 0:
                                all_usd[f_cur] = 1.0 / float(f_rates[f_cur])
                        if "EGP" in f_rates:
                            rates["usd_egp"] = float(f_rates["EGP"])
                except Exception as e:
                    logger.debug(f"Fiat rate error: {e}")

                # 4. سعر رصيد آسيا سيل (Asiacell) المعتمد في السوق (~30 جنيه)
                asia_egp_val = float(os.getenv("ASIA_EGP_RATE", "30.0"))
                egp_curr = rates.get("usd_egp") or 52.14
                all_usd["ASIA"] = asia_egp_val / egp_curr

        except Exception as e:
            logger.warning(f"⚠️ فشل تحديث الأسعار: {e}")

        cls._data = rates
        cls._all_usd = all_usd
        cls._last = now
        return {
            "rates": rates,
            "all_usd": all_usd,
            "updated_at": now.strftime("%Y-%m-%d %H:%M UTC"),
            "source": "Live",
        }


def get_converter_keyboard(base_sym: str, amount: float, include_back: bool = False) -> InlineKeyboardMarkup:
    kb_rows = []
    amt_str = f"{amount:g}"
    grid = get_converter_grid(base_sym)
    for left, right in grid:
        kb_rows.append([
            make_btn(left, callback_data=f"ccnv:{base_sym}:{amt_str}:{left}", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["diamond"]),
            make_btn(right, callback_data=f"ccnv:{base_sym}:{amt_str}:{right}", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["diamond"]),
        ])
    if include_back:
        kb_rows.append([make_btn("رجوع للقائمة الرئيسية", callback_data="main_dashboard", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])])
    return InlineKeyboardMarkup(inline_keyboard=kb_rows)


def get_converter_message_content(base_sym: str, amount: float, target_sym: Optional[str] = None, all_usd: Optional[Dict[str, float]] = None) -> Tuple[str, str]:
    icon, name = CURRENCY_DISPLAY.get(base_sym, ("💰", base_sym))
    amt_fmt = format_crypto_num(amount)

    if not target_sym or not all_usd:
        return f"<blockquote>{icon} {amt_fmt} {name}</blockquote>", ""

    base_price = all_usd.get(base_sym, 1.0)
    target_price = all_usd.get(target_sym, 1.0)
    if target_price <= 0:
        target_price = 1.0

    usd_val = amount * base_price
    converted_amount = usd_val / target_price
    conv_fmt = format_crypto_num(converted_amount)
    t_icon, t_name = CURRENCY_DISPLAY.get(target_sym, ("💰", target_sym))

    text = f"<blockquote>{icon} {amt_fmt} {name} = {conv_fmt} {t_name}</blockquote>"
    toast = f"{amt_fmt} {name} = {conv_fmt} {t_name}"
    return text, toast


async def _fetch_stars_price_live() -> dict:
    """
    حساب سعر نجوم تيليجرام الدقيق 100% المطابق لمنصة Fragment:
    - سعر الشراء عبر Fragment الرسمي: 15.00$ لكل 1000 نجمة (0.015$ للنجمة)
    - سعر الشراء من داخل التطبيق (App Store / Play): 19.99$ لكل 1000 نجمة
    - سعر السحب للمنشئين (Withdrawal): 13.00$ لكل 1000 نجمة (0.013$ للنجمة)
    """
    global _stars_live_cache
    now = datetime.datetime.utcnow()

    # تحقق من الكاش
    if _stars_live_cache:
        ts = _stars_live_cache.get("_ts")
        if ts and (now - ts).total_seconds() < _STARS_LIVE_TTL:
            return _stars_live_cache

    cur = await CurrencyCache.get_rates()
    rates = cur["rates"]
    ton_usd = rates.get("ton_usd", 1.366)
    usd_egp = rates.get("usd_egp", 52.14)

    # سعر الشراء على فرَّاغمنت 15.00$ لكل 1000 نجمة رسمياً
    buy_per_1000 = STARS_BUY_USD_PER_1000 if STARS_BUY_USD_PER_1000 not in (18.5, 15.5) else 15.00
    buy_inapp_per_1000 = 19.99
    withdraw_per_1000 = STARS_WITHDRAW_USD_PER_1000

    result = {
        "buy": buy_per_1000,
        "buy_inapp": buy_inapp_per_1000,
        "withdraw": withdraw_per_1000,
        "ton_usd": ton_usd,
        "usd_egp": usd_egp,
        "source": cur.get("source", "Live"),
        "updated_at": cur.get("updated_at", now.strftime("%H:%M UTC")),
        "_ts": now,
    }
    _stars_live_cache = result
    logger.info(f"⭐ سعر النجوم محدّث | شراء Fragment=${buy_per_1000:.2f} | سحب=${withdraw_per_1000:.2f} | TON=${ton_usd:.4f} | EGP={usd_egp:.2f}")
    return result




# 6. الترجمة
# =============================================================================
_LOCALES = {"ar": "ar-SA", "en": "en-GB", "fr": "fr-FR", "de": "de-DE",
            "es": "es-ES", "tr": "tr-TR", "ru": "ru-RU"}
_LANG_CODES = {"ar": "ar", "en": "en", "fr": "fr", "de": "de",
               "es": "es", "tr": "tr", "ru": "ru"}


def _tr_google_api_direct(text, target):
    if not text or not text.strip():
        return ""
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        # إذا كان النص طويلاً نقسمه لفقرات حتى لا يتجاوز حد الطلب
        chunks = []
        if len(text) > 2000:
            lines = text.split("\n")
            cur = ""
            for l in lines:
                if len(cur) + len(l) < 2000:
                    cur += l + "\n"
                else:
                    if cur:
                        chunks.append(cur)
                    cur = l + "\n"
            if cur:
                chunks.append(cur)
        else:
            chunks = [text]

        translated_chunks = []
        with httpx.Client(timeout=15.0) as c:
            for chunk in chunks:
                if not chunk.strip():
                    translated_chunks.append("")
                    continue
                data = {"client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": chunk}
                r = c.post(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200:
                    d = r.json()
                    if isinstance(d, list) and d and isinstance(d[0], list):
                        parts = [seg[0] for seg in d[0] if isinstance(seg, list) and seg and isinstance(seg[0], str)]
                        translated_chunks.append("".join(parts))
                    else:
                        return None
                else:
                    return None
        res = "".join(translated_chunks).strip()
        if res:
            return res
    except Exception as e:
        logger.debug(f"[TR_HTTP] {e}")
    return None


def _tr_mymemory(text, target):
    if not MyMemoryTranslator:
        return None
    try:
        tl = _LOCALES.get(target, target)
        has_ar = bool(re.search(r'[\u0600-\u06FF]', text))
        sl = 'ar-SA' if has_ar else 'en-GB'
        if sl == tl:
            sl = 'en-GB' if tl == 'ar-SA' else 'ar-SA'
        r = MyMemoryTranslator(source=sl, target=tl).translate(text)
        if r and r.strip():
            return r
    except Exception as e:
        logger.debug(f"[TR_MYMEM] {e}")
    return None


def _tr_google_dt(text, target):
    if not GoogleTranslator:
        return None
    try:
        r = GoogleTranslator(source='auto', target=target).translate(text)
        if r and r.strip():
            return r
    except Exception as e:
        logger.debug(f"[TR_DT] {e}")
    return None


def _sync_tr(text, lang):
    lang = lang.lower().strip()
    if lang not in _LANG_CODES:
        return None
    r = _tr_google_api_direct(text, lang)
    if r:
        return r
    r = _tr_google_dt(text, lang)
    if r:
        return r
    r = _tr_mymemory(text, lang)
    if r:
        return r
    return None


async def translate_text_async(text, lang):
    if not text or not text.strip():
        return ""
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _sync_tr, text, lang)
        if result:
            return result
        # بديل ذكي عبر Gemini في حال فشل خدمات الترجمة التقليدية للنصوص الطويلة
        if GEMINI_API_KEY:
            try:
                ai_tr = await AIAssistant.reply(f"tr_{lang}", f"ترجم النص التالي بدقة واحترافية إلى اللغة ({lang}) دون إضافة أي تعليقات أو مقدمات واطبع الترجمة فقط:\n\n{text}")
                if ai_tr:
                    return ai_tr
            except Exception:
                pass
        logger.warning(f"[TR_FAIL] فشلت الترجمة إلى {lang}")
        return None
    except Exception as e:
        logger.error(f"[TR] {e}")
        return None


# =============================================================================
# 7. AI
# =============================================================================
class AIAssistant:
    _history: Dict[Any, List[Dict[str, str]]] = {}
    _sys = ("أنت مساعد ذكي واسع المعرفة ولبق ومحترف. "
            "أجب بدقة واكتمال بنفس لغة السؤال. "
            "قدم إجابة مفصلة، كاملة، ومفيدة تلبي طلب المستخدم تماماً دون اختصار مخل أو بتر للإجابة. "
            "نسق إجابتك بشكل جميل ومرتب وواضح.")

    @classmethod
    async def _try_gemini(cls, messages):
        if not GEMINI_API_KEY:
            return None
        sys_text = ""
        contents = []
        for m in messages:
            if m["role"] == "system":
                sys_text += m["content"] + "\n"
            elif m["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": m["content"]}]})
            elif m["role"] == "assistant":
                contents.append({"role": "model", "parts": [{"text": m["content"]}]})
        payload = {"contents": contents,
                   "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048}}
        if sys_text.strip():
            payload["systemInstruction"] = {"parts": [{"text": sys_text.strip()}]}
        for model in ["gemini-3.5-flash", "gemini-flash-latest", "gemini-3.6-flash",
                      "gemini-3.1-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash"]:
            try:
                url = (f"https://generativelanguage.googleapis.com/v1beta/"
                       f"models/{model}:generateContent?key={GEMINI_API_KEY}")
                async with httpx.AsyncClient(timeout=30.0) as c:
                    r = await c.post(url, json=payload)
                    if r.status_code == 200:
                        cand = r.json().get("candidates", [])
                        if cand:
                            parts = cand[0].get("content", {}).get("parts", [])
                            text_chunks = [p.get("text", "") for p in parts if p.get("text")]
                            if text_chunks:
                                return "".join(text_chunks).strip()
                    elif r.status_code in (400, 403, 404, 429, 503):
                        continue
            except Exception as e:
                logger.debug(f"[AI_GEMINI] {model}: {e}")
                continue
        return None

    @classmethod
    async def _try_openai(cls, messages):
        if not OPENAI_API_KEY:
            return None
        try:
            async with httpx.AsyncClient(timeout=25.0) as c:
                r = await c.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    json={"model": "gpt-4o-mini", "messages": messages,
                          "max_tokens": 2048, "temperature": 0.7})
                if r.status_code == 200:
                    return r.json()["choices"][0]["message"]["content"].strip()
                elif r.status_code == 429:
                    logger.warning(f"[AI_OPENAI] 429: {r.text[:150]}")
                else:
                    logger.warning(f"[AI_OPENAI] HTTP {r.status_code}")
        except Exception as e:
            logger.error(f"[AI_OPENAI] {e}")
        return None

    @classmethod
    async def reply(cls, key, msg):
        if not GEMINI_API_KEY and not OPENAI_API_KEY:
            return None
        h = cls._history.get(key, [])
        h.append({"role": "user", "content": msg})
        h = h[-6:]
        messages = [{"role": "system", "content": cls._sys}] + h
        resp = None
        if GEMINI_API_KEY:
            resp = await cls._try_gemini(messages)
        if not resp and OPENAI_API_KEY:
            resp = await cls._try_openai(messages)
        if resp:
            h.append({"role": "assistant", "content": resp})
            cls._history[key] = h
            return resp
        return None


# =============================================================================
# 8. تحميل الفيديو
# =============================================================================
_SUPPORTED_VIDEO_DOMAINS = re.compile(
    r"(tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com|"
    r"instagram\.com|instagr\.am|"
    r"facebook\.com|fb\.com|fb\.watch|m\.facebook\.com|web\.facebook\.com|"
    r"youtube\.com|youtu\.be|"
    r"twitter\.com|x\.com|"
    r"pinterest\.com|pin\.it|"
    r"threads\.net|"
    r"snapchat\.com|"
    r"reddit\.com|"
    r"vimeo\.com|"
    r"dailymotion\.com|"
    r"kwai\.com|kuaishou\.com)", re.I)


def _detect_video_url(text: str) -> Optional[str]:
    if not text:
        return None
    for u in re.findall(r"https?://[^\s]+", text):
        clean_url = u.rstrip(".,)>]\"'")
        if _SUPPORTED_VIDEO_DOMAINS.search(clean_url):
            return clean_url
    return None


def _run_ytdlp(url: str, out_dir: str) -> Tuple[Optional[str], Optional[str]]:
    if not yt_dlp:
        return None, None
    ts = int(datetime.datetime.utcnow().timestamp())
    tmpl = os.path.join(out_dir, f"vid_{ts}_%(id)s.%(ext)s")
    opts = {
        'outtmpl': tmpl,
        'format': 'bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4][height<=1080]/best[height<=1080]/best',
        'merge_output_format': 'mp4',
        'max_filesize': 49 * 1024 * 1024,
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'retries': 3,
        'geo_bypass': True,
        'nocheckcertificate': True,
        'noprogress': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        },
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return None, None
            title = info.get('title', 'فيديو')
            raw = ydl.prepare_filename(info)
            if os.path.exists(raw) and os.path.getsize(raw) > 0:
                return raw, title
            mp4 = os.path.splitext(raw)[0] + ".mp4"
            if os.path.exists(mp4) and os.path.getsize(mp4) > 0:
                return mp4, title
            for f in os.listdir(out_dir):
                p = os.path.join(out_dir, f)
                if f.startswith(f"vid_{ts}") and os.path.isfile(p) and os.path.getsize(p) > 0:
                    return p, title
    except Exception as e:
        logger.warning(f"[YTDLP] {e}")
    return None, None


async def download_video_safely(url: str, timeout: int = 120) -> Tuple[Optional[str], Optional[str]]:
    if not yt_dlp:
        return None, None
    os.makedirs("downloads", exist_ok=True)
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _run_ytdlp, url, "downloads"), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"[DL_TIMEOUT] Timeout downloading {url}")
        return None, None
    except Exception as e:
        logger.error(f"[DL_ERROR] {e}")
        return None, None


def _cleanup(path: Optional[str]):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass


async def is_downloader_enabled(owner_id: Optional[int]) -> bool:
    if not owner_id:
        return True
    try:
        async with async_session() as session:
            st = await session.scalar(select(ServiceSetting).where(
                ServiceSetting.user_id == owner_id,
                ServiceSetting.service_key == "downloader"))
            if st is not None:
                return st.is_enabled
            return True
    except Exception:
        return True


async def process_video_download(message: Message, bot: Bot, url: str, biz_id: Optional[str] = None):
    wait_msg = await send_safe_reply(
        message,
        f"<blockquote>⏳ <b>جاري تحميل الفيديو...</b>\n\n"
        f"🔗 <code>{html.quote(url[:60])}{'...' if len(url) > 60 else ''}</code>\n"
        f"<i>يرجى الانتظار ثوانٍ معدودة.</i></blockquote>",
        business_connection_id=biz_id
    )

    filepath, title = await download_video_safely(url)

    if not filepath or not os.path.exists(filepath):
        err_text = (
            f"<blockquote>{CE.ALERT} <b>تعذر تحميل الفيديو!</b></blockquote>\n\n"
            f"<blockquote>⚠️ <b>الأسباب المحتملة:</b>\n"
            f"• الحساب أو الفيديو خاص (Private)\n"
            f"• حجم الفيديو أكبر من الحد المسموح (50MB)\n"
            f"• الرابط غير مدعوم أو تم حذفه من المنصة</blockquote>"
        )
        if wait_msg:
            try:
                await wait_msg.edit_text(err_text, parse_mode=ParseMode.HTML)
                return
            except Exception:
                pass
        return await send_safe_reply(message, err_text, business_connection_id=biz_id)

    caption = (
        f"<blockquote>🎬 <b>{html.quote((title or 'فيديو')[:100])}</b></blockquote>\n\n"
        f"<blockquote>📥 <i>تم التحميل بنجاح</i> {CE.CHECK_VERIFIED}</blockquote>"
    )

    try:
        video_file = FSInputFile(filepath)
        if biz_id:
            await bot.send_video(
                chat_id=message.chat.id,
                video=video_file,
                caption=caption,
                parse_mode=ParseMode.HTML,
                business_connection_id=biz_id
            )
        else:
            await message.reply_video(
                video=video_file,
                caption=caption,
                parse_mode=ParseMode.HTML
            )
        if wait_msg:
            try:
                if biz_id:
                    await bot.delete_message(chat_id=message.chat.id, message_id=wait_msg.message_id, business_connection_id=biz_id)
                else:
                    await bot.delete_message(chat_id=wait_msg.chat.id, message_id=wait_msg.message_id)
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"[SEND_VIDEO_FAIL] {e}, trying document...")
        try:
            doc_file = FSInputFile(filepath)
            if biz_id:
                await bot.send_document(
                    chat_id=message.chat.id,
                    document=doc_file,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    business_connection_id=biz_id
                )
            else:
                await message.reply_document(
                    document=doc_file,
                    caption=caption,
                    parse_mode=ParseMode.HTML
                )
            if wait_msg:
                try:
                    if biz_id:
                        await bot.delete_message(chat_id=message.chat.id, message_id=wait_msg.message_id, business_connection_id=biz_id)
                    else:
                        await bot.delete_message(chat_id=wait_msg.chat.id, message_id=wait_msg.message_id)
                except Exception:
                    pass
        except Exception as e2:
            logger.error(f"[SEND_DOC_FAIL] {e2}")
            if wait_msg:
                try:
                    await wait_msg.edit_text(
                        f"<blockquote>{CE.ALERT} تعذر إرسال الفيديو: {html.quote(str(e2)[:100])}</blockquote>",
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
    finally:
        _cleanup(filepath)


# =============================================================================
# 9. FSM
# =============================================================================
class AddReplyFSM(StatesGroup):
    waiting_for_keyword = State()
    waiting_for_match_type = State()
    waiting_for_content = State()



class AddProductFSM(StatesGroup):
    waiting_for_name = State()
    waiting_for_price = State()
    waiting_for_desc = State()


class AddSubscriptionFSM(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_days = State()


class EndSubscriptionFSM(StatesGroup):
    waiting_for_user_id = State()


class ScheduleMessageFSM(StatesGroup):
    waiting_for_target = State()
    waiting_for_text = State()
    waiting_for_minutes = State()


class TranslateFSM(StatesGroup):
    waiting_for_language = State()
    waiting_for_text = State()


class MandatorySubFSM(StatesGroup):
    waiting_for_channel = State()
    waiting_for_message_text = State()


class BroadcastFSM(StatesGroup):
    waiting_for_message = State()


class GiftAllFSM(StatesGroup):
    waiting_for_hours = State()


class AccountBroadcastFSM(StatesGroup):
    waiting_for_confirm = State()


class AddTonWalletFSM(StatesGroup):
    waiting_for_name = State()
    waiting_for_token = State()


class ReverifyTonWalletFSM(StatesGroup):
    waiting_for_token = State()


class WelcomeMessageFSM(StatesGroup):
    waiting_for_message = State()


class BotMandatorySubFSM(StatesGroup):
    waiting_for_channel = State()


class TrialHoursFSM(StatesGroup):
    waiting_for_hours = State()


_user_translate_lang: Dict[int, str] = {}
_business_broadcast_pending: Dict[int, Dict] = {}

TRANSLATE_LANGUAGES = {
    "tr_ar": ("🇪🇬 العربية", "ar"), "tr_en": ("🇬🇧 English", "en"),
    "tr_fr": ("🇫🇷 Français", "fr"), "tr_de": ("🇩🇪 Deutsch", "de"),
    "tr_es": ("🇪🇸 Español", "es"), "tr_tr": ("🇹🇷 Türkçe", "tr"),
    "tr_ru": ("🇷🇺 Русский", "ru"),
}


# =============================================================================
# 10. الراوتر
# =============================================================================
dp = Dispatcher(storage=MemoryStorage())
ton_router = Router(name="ton_wallets")
dp.include_router(ton_router)
main_router = Router()
dp.include_router(main_router)
scheduler = AsyncIOScheduler()


@dp.error()
async def global_error_handler(event: ErrorEvent):
    ex = event.exception
    if isinstance(ex, TelegramBadRequest):
        err_msg = str(ex).lower()
        if "message is not modified" in err_msg:
            if event.update and event.update.callback_query:
                try:
                    await event.update.callback_query.answer()
                except Exception:
                    pass
            return True
        if "query is too old" in err_msg or "message to edit not found" in err_msg or "message can't be edited" in err_msg:
            if event.update and event.update.callback_query:
                try:
                    await event.update.callback_query.answer()
                except Exception:
                    pass
            return True
    logger.exception(f"Unhandled error in update: {ex}")
    return False


# =============================================================================
# 11. دوال مساعدة
# =============================================================================
async def get_or_create_user(tg_user: TgUser, session: AsyncSession) -> User:
    res = await session.execute(select(User).where(User.id == tg_user.id))
    user = res.scalar_one_or_none()
    now = datetime.datetime.utcnow()
    if not user:
        role = "owner" if tg_user.id == OWNER_ID else "customer"
        user = User(id=tg_user.id, username=tg_user.username,
                    full_name=tg_user.full_name or "مستخدم", role=role, created_at=now,
                    trial_expires_at=now + datetime.timedelta(hours=FREE_TRIAL_HOURS),
                    subscription_expires_at=None, is_active=True)
        session.add(user)
        await session.commit()
    else:
        user.full_name = tg_user.full_name or user.full_name
        user.username = tg_user.username or user.username
        if tg_user.id == OWNER_ID and user.role != "owner":
            user.role = "owner"
        await session.commit()
    return user


async def get_or_create_user_flagged(tg_user: TgUser, session: AsyncSession):
    res = await session.execute(select(User).where(User.id == tg_user.id))
    user = res.scalar_one_or_none()
    now = datetime.datetime.utcnow()
    is_new = False
    if not user:
        is_new = True
        role = "owner" if tg_user.id == OWNER_ID else "customer"
        user = User(id=tg_user.id, username=tg_user.username,
                    full_name=tg_user.full_name or "مستخدم", role=role, created_at=now,
                    trial_expires_at=now + datetime.timedelta(hours=FREE_TRIAL_HOURS),
                    subscription_expires_at=None, is_active=True)
        session.add(user)
        await session.commit()
    else:
        user.full_name = tg_user.full_name or user.full_name
        user.username = tg_user.username or user.username
        if tg_user.id == OWNER_ID and user.role != "owner":
            user.role = "owner"
        await session.commit()
    return user, is_new


async def is_subscription_active(user_id: int, session: AsyncSession) -> bool:
    user = await session.get(User, user_id)
    if not user:
        return False
    if user.role in ("owner", "admin"):
        return True
    now = datetime.datetime.utcnow()
    if user.trial_expires_at and user.trial_expires_at > now:
        return True
    if user.subscription_expires_at and user.subscription_expires_at > now:
        return True
    return False


async def resolve_business_owner(biz_conn_id, session, bot=None):
    if not biz_conn_id:
        return None
    res = await session.execute(select(BusinessConnectionRecord).where(
        BusinessConnectionRecord.connection_id == str(biz_conn_id)))
    rec = res.scalar_one_or_none()
    if rec:
        return rec.user_id
    if bot is None:
        return None
    try:
        conn = await bot.get_business_connection(str(biz_conn_id))
    except Exception as e:
        logger.warning(f"⚠️ get_business_connection failed: {e}")
        return None
    owner_id = conn.user.id
    session.add(BusinessConnectionRecord(
        connection_id=str(biz_conn_id), user_id=owner_id, is_enabled=conn.is_enabled))
    user_res = await session.execute(select(User).where(User.id == owner_id))
    user = user_res.scalar_one_or_none()
    if not user:
        role = "owner" if owner_id == OWNER_ID else "customer"
        user = User(id=owner_id, username=conn.user.username,
                    full_name=conn.user.full_name or "مستخدم", role=role,
                    created_at=datetime.datetime.utcnow(),
                    trial_expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=FREE_TRIAL_HOURS),
                    is_active=True)
        session.add(user)
    await session.commit()
    logger.info(f"✅ Business connection سُجّل: {biz_conn_id} → {owner_id}")
    return owner_id


async def check_user_subscription(user: User, session: AsyncSession) -> bool:
    return await is_subscription_active(user.id, session)


async def get_mandatory_sub(owner_id: int, session: AsyncSession):
    res = await session.execute(select(MandatorySubscription).where(
        MandatorySubscription.user_id == owner_id))
    return res.scalar_one_or_none()


async def get_or_create_security(uid: int, session: AsyncSession):
    res = await session.execute(select(UserSecuritySettings).where(
        UserSecuritySettings.user_id == uid))
    s = res.scalar_one_or_none()
    if not s:
        s = UserSecuritySettings(user_id=uid)
        session.add(s)
        await session.commit()
    return s


async def check_channel_subscription(bot: Bot, user_id: int, sub: MandatorySubscription,
                                     business_connection_id=None) -> bool:
    """التحقق من اشتراك المستخدم في القناة — بدون business_connection_id (غير مدعوم في get_chat_member)"""
    if not sub or not sub.channel_id or not sub.is_enabled:
        logger.info(f"[SUB_CHECK] skip — sub={bool(sub)}, channel_id={getattr(sub,'channel_id',None)}, enabled={getattr(sub,'is_enabled',None)}")
        return True
    cache_key = (sub.channel_id, user_id)
    now = datetime.datetime.utcnow()
    cached = _sub_check_cache.get(cache_key)
    if cached and (now - cached[1]).total_seconds() < _SUB_CACHE_TTL:
        logger.info(f"[SUB_CHECK] cached → user={user_id} result={cached[0]}")
        return cached[0]
    try:
        logger.info(f"[SUB_CHECK] checking user={user_id} in channel={sub.channel_id}")
        m = await bot.get_chat_member(chat_id=sub.channel_id, user_id=user_id)
        result = m.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR,
                              ChatMemberStatus.CREATOR, ChatMemberStatus.RESTRICTED)
        logger.info(f"[SUB_CHECK] user={user_id} status={m.status} → subscribed={result}")
        _sub_check_cache[cache_key] = (result, now)
        return result
    except TelegramBadRequest as e:
        err = str(e).lower()
        logger.warning(f"[SUB_CHECK] TelegramBadRequest for user={user_id} channel={sub.channel_id}: {e}")
        if "user not found" in err or "participant" in err or "chat not found" in err:
            _sub_check_cache[cache_key] = (False, now)
            return False
        # خطأ آخر — نعتبره غير مشترك لأمان أكثر
        _sub_check_cache[cache_key] = (False, now)
        return False
    except TelegramAPIError as e:
        logger.warning(f"[SUB_CHECK] TelegramAPIError for user={user_id} channel={sub.channel_id}: {e}")
        # خطأ في API — ممكن البوت مش admin في القناة
        # نعتبره غير مشترك لضمان تطبيق الاشتراك الإجباري
        _sub_check_cache[cache_key] = (False, now)
        return False


async def send_mandatory_sub_message(bot: Bot, chat_id: int, sub: MandatorySubscription,
                                     business_connection_id=None, user_id: Optional[int] = None):
    """
    إرسال رسالة الاشتراك الإجباري في شات البيزنس (شات أكونت صاحب البوت مع العميل).
    """
    text = sub.message_text or (
        f"<blockquote>{CE.BOOK} <b>تنبيه الاشتراك الإجباري للقناة</b>\n\n"
        f"<i>أهلاً بك! للتواصل ومراسلتنا، يرجى التكرم بالانضمام لقناتنا الرسمية:</i>\n"
        f"📢 <b>@{sub.channel_username}</b>\n\n"
        f"<tg-spoiler>اضغط على زر (اشترك في القناة) ثم اضغط (لقد اشتركت) لتأكيد عضويتك فوراً.</tg-spoiler></blockquote>"
        if sub.channel_username else f"<blockquote>{CE.BOOK} <b>تنبيه:</b> <i>يرجى الاشتراك في القناة الرسمية أولاً لمتابعة المحادثة.</i></blockquote>")
    link = (f"https://t.me/{sub.channel_username.replace('@','')}" if sub.channel_username
            else (f"https://t.me/c/{str(sub.channel_id).replace('-100','')}" if sub.channel_id else "https://t.me"))
    # زرار الاشتراك + زرار التحقق
    # نشفّر user_id وbiz_conn في callback_data عشان نقدر نتحقق لما يضغط
    check_data = f"subcheck_{user_id or chat_id}_{sub.channel_id}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("اشترك في القناة", url=link, style="primary",
                  icon_custom_emoji_id=CUSTOM_EMOJI_IDS["link"])],
        [make_btn("لقد اشتركت", callback_data=check_data, style="success",
                  icon_custom_emoji_id=CUSTOM_EMOJI_IDS["check_verified"])]
    ])
    # نرسل في شات البيزنس (الشات بين الأكونت والعميل)
    kw = {"chat_id": chat_id, "reply_markup": kb, "parse_mode": ParseMode.HTML}
    if business_connection_id:
        kw["business_connection_id"] = business_connection_id
    try:
        if sub.media_type == "photo" and sub.media_file_id:
            await bot.send_photo(photo=sub.media_file_id, caption=text, **kw)
        elif sub.media_type == "video" and sub.media_file_id:
            await bot.send_video(video=sub.media_file_id, caption=text, **kw)
        else:
            await bot.send_message(text=text, **kw)
        logger.info(f"[SUB_MSG] تم إرسال رسالة الاشتراك الإجباري في شات البيزنس {chat_id}")
    except Exception as e:
        logger.warning(f"[SUB_MSG] فشل إرسال رسالة الاشتراك الإجباري: {e}")


async def get_or_create_welcome_setting(user_id: int, session: AsyncSession) -> BusinessWelcomeSetting:
    res = await session.execute(select(BusinessWelcomeSetting).where(BusinessWelcomeSetting.user_id == user_id))
    ws = res.scalar_one_or_none()
    if not ws:
        ws = BusinessWelcomeSetting(user_id=user_id, is_enabled=False)
        session.add(ws)
        await session.commit()
    return ws


async def send_welcome_message(
    bot: Bot,
    chat_id: int,
    welcome: BusinessWelcomeSetting,
    customer: TgUser,
    business_connection_id: str
) -> bool:
    formatted_text = format_welcome_text(welcome.welcome_text, customer)
    kw: Dict[str, Any] = {
        "chat_id": chat_id,
        "business_connection_id": business_connection_id,
    }

    try:
        if welcome.media_type == "photo" and welcome.media_file_id:
            try:
                await bot.send_photo(photo=welcome.media_file_id, caption=formatted_text, parse_mode=ParseMode.HTML, **kw)
            except TelegramBadRequest as e:
                if any(x in str(e).lower() for x in ["entity", "parse"]):
                    await bot.send_photo(photo=welcome.media_file_id, caption=formatted_text, **kw)
                else:
                    raise
        elif welcome.media_type == "video" and welcome.media_file_id:
            try:
                await bot.send_video(video=welcome.media_file_id, caption=formatted_text, parse_mode=ParseMode.HTML, **kw)
            except TelegramBadRequest as e:
                if any(x in str(e).lower() for x in ["entity", "parse"]):
                    await bot.send_video(video=welcome.media_file_id, caption=formatted_text, **kw)
                else:
                    raise
        elif formatted_text:
            try:
                await bot.send_message(text=formatted_text, parse_mode=ParseMode.HTML, **kw)
            except TelegramBadRequest as e:
                if any(x in str(e).lower() for x in ["entity", "parse"]):
                    await bot.send_message(text=formatted_text, **kw)
                else:
                    raise
        return True
    except Exception as e:
        logger.error(f"[WELCOME_SEND] Error sending welcome message: {e}")
        return False


_customer_welcome_locks: Dict[Tuple[int, str, int], asyncio.Lock] = {}


def get_customer_welcome_lock(owner_id: int, conn_id: str, customer_id: int) -> asyncio.Lock:
    global _customer_welcome_locks
    if len(_customer_welcome_locks) > 10000:
        _customer_welcome_locks.clear()
    key = (owner_id, conn_id, customer_id)
    if key not in _customer_welcome_locks:
        _customer_welcome_locks[key] = asyncio.Lock()
    return _customer_welcome_locks[key]


async def process_business_welcome(
    bot: Bot,
    message: Message,
    owner_id: int,
    connection_id: str,
    customer: TgUser
) -> bool:
    lock = get_customer_welcome_lock(owner_id, connection_id, customer.id)
    async with lock:
        async with async_session() as session:
            res = await session.execute(
                select(BusinessCustomer).where(
                    BusinessCustomer.owner_user_id == owner_id,
                    BusinessCustomer.business_connection_id == connection_id,
                    BusinessCustomer.customer_user_id == customer.id
                )
            )
            cust = res.scalar_one_or_none()
            should_send = False

            if cust is None:
                cust = BusinessCustomer(
                    owner_user_id=owner_id,
                    business_connection_id=connection_id,
                    customer_user_id=customer.id,
                    first_seen_at=datetime.datetime.utcnow(),
                    last_seen_at=datetime.datetime.utcnow(),
                    welcome_sent=False,
                    welcome_sent_at=None
                )
                session.add(cust)
                try:
                    await session.commit()
                    should_send = True
                    logger.info(f"[NEW_CUSTOMER] Registered new customer {customer.id} for owner {owner_id}")
                except Exception as e:
                    await session.rollback()
                    logger.debug(f"[NEW_CUSTOMER] Concurrent register check: {e}")
                    res = await session.execute(
                        select(BusinessCustomer).where(
                            BusinessCustomer.owner_user_id == owner_id,
                            BusinessCustomer.business_connection_id == connection_id,
                            BusinessCustomer.customer_user_id == customer.id
                        )
                    )
                    cust = res.scalar_one_or_none()
                    if cust and not cust.welcome_sent:
                        should_send = True
            else:
                cust.last_seen_at = datetime.datetime.utcnow()
                if not cust.welcome_sent:
                    should_send = True
                await session.commit()

            if not should_send:
                return False

            res_ws = await session.execute(
                select(BusinessWelcomeSetting).where(BusinessWelcomeSetting.user_id == owner_id)
            )
            ws = res_ws.scalar_one_or_none()
            if not ws or not ws.is_enabled:
                logger.info(f"[WELCOME] Disabled or not set for owner={owner_id}")
                return False

            if not ws.welcome_text and not ws.media_file_id:
                return False

            sent = await send_welcome_message(
                bot=bot,
                chat_id=message.chat.id,
                welcome=ws,
                customer=customer,
                business_connection_id=connection_id
            )

            if sent:
                cust.welcome_sent = True
                cust.welcome_sent_at = datetime.datetime.utcnow()
                try:
                    await session.commit()
                    logger.info(f"[WELCOME] Successfully sent welcome to customer {customer.id}")
                except Exception as e:
                    logger.error(f"[WELCOME] DB commit error after welcome sent: {e}")
                return True
            else:
                logger.warning(f"[WELCOME] Sending failed for customer {customer.id}, leaving welcome_sent=False for retry")
                return False


def detect_hyperlink_spoofing(message: Message):
    if not message.text and not message.caption:
        return None
    full = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    if not entities:
        return None
    url_re = re.compile(r"^https?://|^www\.", re.I)
    trusted = re.compile(r"(^|\.)(t\.me|telegram\.me|telegram\.org|fragment\.com|ton\.org|tondns\.com)$", re.I)
    for e in entities:
        if e.type != "text_link" or not e.url:
            continue
        displayed = full[e.offset:e.offset + e.length].strip()
        real = e.url.strip()
        if url_re.match(displayed):
            dh_m = re.search(r"(?:https?://)?(?:www\.)?([^/\s]+)", displayed)
            rh_m = re.search(r"(?:https?://)?(?:www\.)?([^/\s]+)", real)
            if dh_m and rh_m:
                dh, rh = dh_m.group(1).lower(), rh_m.group(1).lower()
                if dh != rh:
                    return (displayed, real)
        if re.search(r"(fragment|telegram|t\.me)", displayed, re.I) and not trusted.search(real):
            return (displayed, real)
    return None


async def track_business_chat(message: Message, owner_id: int):
    if not message.business_connection_id or not owner_id:
        return
    try:
        async with async_session() as session:
            res = await session.execute(
                select(BusinessChat).where(
                    BusinessChat.business_connection_id == str(message.business_connection_id),
                    BusinessChat.chat_id == message.chat.id))
            ex = res.scalar_one_or_none()
            sender = message.from_user
            if ex:
                ex.last_seen = datetime.datetime.utcnow()
                ex.is_active = True
                if sender:
                    ex.user_id = sender.id
                    ex.username = sender.username
            else:
                session.add(BusinessChat(
                    business_connection_id=str(message.business_connection_id),
                    owner_id=owner_id, chat_id=message.chat.id,
                    user_id=sender.id if sender else None,
                    chat_type=message.chat.type,
                    title=message.chat.title or (sender.full_name if sender else None),
                    username=sender.username if sender else None))
            await session.commit()
    except Exception as e:
        logger.debug(f"[TRACK] {e}")


async def log_incoming_message(message: Message, owner_id: int):
    if not message.from_user or not message.business_connection_id:
        return
    try:
        media_type = None
        media_file_id = None
        if message.photo:
            media_type = "photo"
            media_file_id = message.photo[-1].file_id
        elif message.video:
            media_type = "video"
            media_file_id = message.video.file_id
        elif message.voice:
            media_type = "voice"
            media_file_id = message.voice.file_id
        elif message.audio:
            media_type = "audio"
            media_file_id = message.audio.file_id
        elif message.document:
            media_type = "document"
            media_file_id = message.document.file_id
        elif message.sticker:
            media_type = "sticker"
            media_file_id = message.sticker.file_id
        elif message.video_note:
            media_type = "video_note"
            media_file_id = message.video_note.file_id
        elif message.animation:
            media_type = "animation"
            media_file_id = message.animation.file_id

        async with async_session() as session:
            session.add(UserMessageLog(
                business_connection_id=str(message.business_connection_id),
                chat_id=message.chat.id,
                message_id=message.message_id,
                user_id=message.from_user.id,
                owner_id=owner_id,
                sender_full_name=message.from_user.full_name,
                sender_username=message.from_user.username,
                text_content=message.text or message.caption,
                media_type=media_type,
                media_file_id=media_file_id))
            await session.commit()
    except Exception as e:
        logger.debug(f"[LOG] {e}")


async def delete_safe_message(bot: Bot, message: Message):
    if message.business_connection_id:
        try:
            url = f"https://api.telegram.org/bot{bot.token}/deleteBusinessMessages"
            async with httpx.AsyncClient(timeout=4) as c:
                r = await c.post(url, json={
                    "business_connection_id": str(message.business_connection_id),
                    "message_ids": [message.message_id]})
                if r.status_code == 200:
                    return
                logger.warning(f"del biz error: {r.status_code} {r.text}")
        except Exception as e:
            logger.warning(f"del biz exc: {e}")
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception as e:
        logger.warning(f"del msg exc: {e}")


async def download_and_send_media(bot: Bot, target_chat_id: int, media_obj, media_type: str, caption: str):
    fid = media_obj[-1].file_id if isinstance(media_obj, list) else media_obj.file_id
    try:
        buf = io.BytesIO()
        await bot.download(fid, destination=buf)
        buf.seek(0)
        data = buf.getvalue()
        if data:
            if media_type == "photo":
                return await bot.send_photo(chat_id=target_chat_id,
                                            photo=BufferedInputFile(data, "m.jpg"),
                                            caption=caption, parse_mode=ParseMode.HTML)
            elif media_type == "video":
                return await bot.send_video(chat_id=target_chat_id,
                                            video=BufferedInputFile(data, "m.mp4"),
                                            caption=caption, parse_mode=ParseMode.HTML)
            elif media_type == "voice":
                return await bot.send_voice(chat_id=target_chat_id,
                                            voice=BufferedInputFile(data, "m.ogg"),
                                            caption=caption, parse_mode=ParseMode.HTML)
            elif media_type == "video_note":
                res = await bot.send_video_note(chat_id=target_chat_id,
                                                video_note=BufferedInputFile(data, "m.mp4"))
                if caption:
                    try:
                        await bot.send_message(chat_id=target_chat_id, text=caption, parse_mode=ParseMode.HTML)
                    except Exception:
                        pass
                return res
            elif media_type == "document":
                doc_name = getattr(media_obj, "file_name", "file") or "file"
                return await bot.send_document(chat_id=target_chat_id,
                                               document=BufferedInputFile(data, doc_name),
                                               caption=caption, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.warning(f"dl buf: {e}")
    try:
        if media_type == "voice":
            return await bot.send_voice(chat_id=target_chat_id, voice=fid, caption=caption,
                                        parse_mode=ParseMode.HTML)
        elif media_type == "video_note":
            res = await bot.send_video_note(chat_id=target_chat_id, video_note=fid)
            if caption:
                try:
                    await bot.send_message(chat_id=target_chat_id, text=caption, parse_mode=ParseMode.HTML)
                except Exception:
                    pass
            return res
        return await bot.send_document(chat_id=target_chat_id, document=fid,
                                       caption=caption, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    if media_type == "photo":
        return await bot.send_photo(chat_id=target_chat_id, photo=fid, caption=caption,
                                    parse_mode=ParseMode.HTML)
    elif media_type == "video":
        return await bot.send_video(chat_id=target_chat_id, video=fid, caption=caption,
                                    parse_mode=ParseMode.HTML)
    elif media_type == "voice":
        return await bot.send_voice(chat_id=target_chat_id, voice=fid, caption=caption,
                                    parse_mode=ParseMode.HTML)
    elif media_type == "video_note":
        return await bot.send_video_note(chat_id=target_chat_id, video_note=fid)


async def check_self_destruct_enabled(user_id: Optional[int] = None) -> bool:
    uid = user_id or OWNER_ID
    if not uid:
        return True
    async with async_session() as session:
        res = await session.execute(select(ServiceSetting).where(
            ServiceSetting.user_id == uid,
            ServiceSetting.service_key == "self_destruct"))
        s = res.scalar_one_or_none()
        if s:
            return s.is_enabled
    return True


async def check_auto_save_enabled(user_id: int) -> bool:
    """فحص إذا كان الحفظ التلقائي للذاتية مفعلاً (مفعل دائماً وتلقائياً كخدمة أساسية)"""
    async with async_session() as session:
        res = await session.execute(select(ServiceSetting).where(
            ServiceSetting.user_id == user_id,
            ServiceSetting.service_key == "auto_save_self_destruct"))
        s = res.scalar_one_or_none()
        if s:
            return s.is_enabled
    return True  # مفعل دائماً وافتراضياً بصورة تلقائية


# =============================================================================
# فلتر الكلمات المحظورة - دوال مساعدة
# =============================================================================

async def get_blacklist_words(owner_id: int) -> List[str]:
    """جلب قائمة الكلمات المحظورة للمستخدم"""
    async with async_session() as session:
        res = await session.execute(select(BlacklistWord).where(BlacklistWord.owner_id == owner_id))
        return [r.word.lower() for r in res.scalars().all()]


async def check_blacklist(text: str, owner_id: int) -> Optional[str]:
    """فحص النص ضد الكلمات المحظورة. يُرجع الكلمة المحظورة إن وُجدت أو None"""
    if not text:
        return None
    words = await get_blacklist_words(owner_id)
    text_lower = text.lower()
    for w in words:
        if w in text_lower:
            return w
    return None


# =============================================================================
# تحويل الصوت إلى نص - دوال مساعدة
# =============================================================================

_openai_whisper_quota_exhausted = False


async def transcribe_voice_openai(file_bytes: bytes, filename: str = "voice.ogg") -> Optional[str]:
    """تحويل ملف صوتي إلى نص عبر OpenAI Whisper API"""
    global _openai_whisper_quota_exhausted
    if not OPENAI_API_KEY or _openai_whisper_quota_exhausted:
        return None
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                files={"file": (filename, file_bytes, "audio/ogg")},
                data={"model": "whisper-1", "response_format": "text"})
            if r.status_code == 200:
                return r.text.strip()
            if r.status_code == 429 and ("insufficient" in r.text.lower() or "credits" in r.text.lower()):
                _openai_whisper_quota_exhausted = True
                logger.warning("⚠️ نفد رصيد OpenAI Whisper — سيتم التحويل تلقائياً للاعتماد على Gemini.")
                return None
            logger.warning(f"[WHISPER] HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"[WHISPER] {e}")
    return None


async def transcribe_voice_gemini(file_bytes: bytes) -> Optional[str]:
    """تحويل ملف صوتي إلى نص عبر Gemini"""
    if not GEMINI_API_KEY:
        return None
    try:
        b64 = base64.b64encode(file_bytes).decode()
        payload = {
            "contents": [{
                "parts": [
                    {"text": "Please transcribe the following audio accurately. Return only the spoken text, nothing else."},
                    {"inline_data": {"mime_type": "audio/ogg", "data": b64}}
                ]
            }],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 2048}
        }
        for model in ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-flash-latest", "gemini-3.1-flash-lite"]:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
                async with httpx.AsyncClient(timeout=60.0) as c:
                    r = await c.post(url, json=payload)
                    if r.status_code == 200:
                        cand = r.json().get("candidates", [])
                        if cand:
                            parts = cand[0].get("content", {}).get("parts", [])
                            text_chunks = [p.get("text", "") for p in parts if p.get("text")]
                            if text_chunks:
                                return "".join(text_chunks).strip()
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"[GEMINI_VOICE] {e}")
    return None


async def transcribe_voice(bot: Bot, voice_obj) -> Optional[str]:
    """تحويل الفويس إلى نص - يجرب OpenAI Whisper ثم Gemini"""
    try:
        fid = voice_obj.file_id if hasattr(voice_obj, "file_id") else voice_obj[-1].file_id
        buf = io.BytesIO()
        await bot.download(fid, destination=buf)
        buf.seek(0)
        data = buf.getvalue()
        if not data:
            return None
        # جرب OpenAI Whisper أولاً لدقته العالية
        if OPENAI_API_KEY:
            result = await transcribe_voice_openai(data)
            if result:
                return result
        # جرب Gemini كبديل
        if GEMINI_API_KEY:
            result = await transcribe_voice_gemini(data)
            if result:
                return result
    except Exception as e:
        logger.warning(f"[TRANSCRIBE] {e}")
    return None


# =============================================================================
# التذكير بالرد على العميل - دوال مساعدة
# =============================================================================

_REMINDER_PATTERNS = [
    # فكرني بعد X (دقيقة/ساعة/يوم) [بالرد]
    re.compile(r"فكرني\s+بعد\s+(\d+(?:\.\d+)?)\s*(دقيقة|دقيقه|دقائق|ساعة|ساعه|ساعات|يوم|ايام|أيام)", re.I | re.U),
    # فكرني بكره / فكرني غداً
    re.compile(r"فكرني\s+(بكره|غداً|غدا|النهارده|دلوقتي)", re.I | re.U),
]

def _parse_reminder_time(text: str) -> Optional[datetime.datetime]:
    """استخراج وقت التذكير من النص"""
    m = re.search(r"فكرني\s+بعد\s+(\d+(?:\.\d+)?)\s*(دقيقة|دقيقه|دقائق|ساعة|ساعه|ساعات|يوم|ايام|أيام)", text, re.I | re.U)
    if m:
        amount = float(m.group(1))
        unit = m.group(2)
        now = datetime.datetime.utcnow()
        if any(u in unit for u in ["دقيقة", "دقيقه", "دقائق"]):
            return now + datetime.timedelta(minutes=amount)
        elif any(u in unit for u in ["ساعة", "ساعه", "ساعات"]):
            return now + datetime.timedelta(hours=amount)
        elif any(u in unit for u in ["يوم", "ايام", "أيام"]):
            return now + datetime.timedelta(days=amount)
    m2 = re.search(r"فكرني\s+(بكره|غداً|غدا)", text, re.I | re.U)
    if m2:
        return datetime.datetime.utcnow() + datetime.timedelta(days=1)
    m3 = re.search(r"فكرني\s+(النهارده|دلوقتي)", text, re.I | re.U)
    if m3:
        return datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    return None


async def check_and_send_reminders(bot: Bot):
    """مهمة جدولية: إرسال التذكيرات المستحقة"""
    try:
        now = datetime.datetime.utcnow()
        async with async_session() as session:
            due = (await session.execute(
                select(CustomerReminder).where(
                    CustomerReminder.remind_at <= now,
                    CustomerReminder.is_sent == False
                )
            )).scalars().all()
            for rem in due:
                try:
                    chat_link = f"https://t.me/c/{str(rem.chat_id).replace('-100', '')}/{rem.message_id or 1}" if rem.message_id else None
                    customer_part = f"👤 <b>{html.quote(rem.customer_name or 'العميل')}</b>" if rem.customer_name else ""
                    chat_part = f"💬 {html.quote(rem.chat_title or str(rem.chat_id))}"
                    note_part = f"\n📝 <i>{html.quote(rem.reminder_text)}</i>" if rem.reminder_text else ""
                    kb_rows = []
                    if chat_link:
                        kb_rows.append([make_btn("📨 انتقل للرسالة", url=chat_link, style="primary",
                                                  icon_custom_emoji_id=CUSTOM_EMOJI_IDS.get("chat", ""))])
                    text = (
                        f"<blockquote>⏰ <b>تذكير من البوت</b></blockquote>\n\n"
                        f"{customer_part}\n{chat_part}{note_part}"
                    )
                    await bot.send_message(
                        rem.owner_id, text, parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows) if kb_rows else None)
                    rem.is_sent = True
                except Exception as e:
                    logger.warning(f"[REMINDER_SEND] {e}")
            await session.commit()
    except Exception as e:
        logger.warning(f"[REMINDERS_JOB] {e}")


# =============================================================================
# 12. دوال أسعار الهدايا (مصححة)
# =============================================================================
_gift_lib_instance = None


def _get_gift_lib():
    global _gift_lib_instance
    if _gift_lib_instance is None and _GIFTS_LIB:
        try:
            _gift_lib_instance = _TGifts(cache_mode="http", asset_mode="lazy")
        except Exception as e:
            try:
                _gift_lib_instance = _TGifts()
            except Exception as e2:
                logger.error(f"[GIFT_LIB_INIT] {e} | {e2}")
                return None
    return _gift_lib_instance


ARABIC_GIFT_ALIASES = {
    # الهدايا
    "طوبة": "artisan_brick",
    "طوبه": "artisan_brick",
    "قالب طوب": "artisan_brick",
    "نبيذ": "spiced_wine",
    "خمر": "spiced_wine",
    "ضفدع": "kissing_frog",
    "كيك": "delicious_cake",
    "كيكة": "delicious_cake",
    "كعكة": "delicious_cake",
    "بيبي": "plush_pepe",
    "بيبى": "plush_pepe",
    "دب": "teddy_bear",
    "حصان": "trojan_horse",
    "خاتم": "gem_ring",
    "شجرة": "bonsai_tree",
    "ساعة": "pocket_watch",
    "صقر": "falcon",
    "سيجار": "vintage_cigar",
    "شمعة": "festive_candle",
    "شمعة العيد": "festive_candle",
    "سيارة": "sports_car",
    "بطة": "rubber_duck",
    # الخلفيات (Backdrops)
    "سوداء": "Black",
    "اسود": "Black",
    "أونيكس": "Onyx Black",
    "اونيكس": "Onyx Black",
    "ذهبي": "Pure Gold",
    "ذهبيه": "Pure Gold",
    "ذهبية": "Pure Gold",
    "ازرق": "Blue",
    "زرقاء": "Blue",
    "اخضر": "Green",
    "خضراء": "Green",
    "زمرد": "Emerald",
    "بنفسجي": "Purple",
    "سماوي": "Sky Blue",
    # الرموز (Symbols)
    "قلب": "Heart",
    "القلب": "Heart",
    "وردة": "Rose",
    "ورده": "Rose",
    "الوردة": "Rose",
    "الورده": "Rose",
    "تاج": "Crown",
    "التاج": "Crown",
    "نجمة": "Star",
    "نجمه": "Star",
    "قمر": "Moon",
    "القمر": "Moon",
    "ماس": "Diamond",
    "الماس": "Diamond",
    "الماسة": "Diamond",
    "فلوس": "Cash",
    "دولار": "Cash",
    "عملة": "Coin",
    "نار": "Fire",
    "صاروخ": "Rocket",
    "تنين": "Dragon",
}


def _parse_tme_nft_page_sync(url: str) -> Optional[Dict[str, Any]]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html_doc = resp.read().decode("utf-8", errors="ignore")
        model = re.search(r'Model:\s*([^\n<]+)', html_doc)
        backdrop = re.search(r'Backdrop:\s*([^\n<]+)', html_doc)
        symbol = re.search(r'Symbol:\s*([^\n<]+)', html_doc)
        num = re.search(r'#(\d+)', html_doc)
        return {
            "model": model.group(1).strip() if model else None,
            "backdrop": backdrop.group(1).strip() if backdrop else None,
            "symbol": symbol.group(1).strip() if symbol else None,
            "number": num.group(1) if num else None,
        }
    except Exception:
        return None


def _format_ton_usd_egp(ton_val: float, ton_usd: float, usd_egp: float) -> str:
    usd = ton_val * ton_usd
    egp = usd * usd_egp
    return f"<code>{ton_val:.3f} TON</code> (≈ ${usd:.2f} | {egp:,.0f} EGP)"


def _get_gift_floor_sync(query: str) -> Optional[Dict[str, Any]]:
    gifts = _get_gift_lib()
    if not gifts:
        return None
    try:
        q_orig = query.strip()
        nft_info = None

        # 1. فحص روابط المنصات
        link_m = re.search(r"(?:t\.me/nft|fragment\.com/gift|getgems\.io/nft|tgmrkt\.io/gift|portals\.tg/gift)/([A-Za-z0-9_\-]+)", q_orig)
        if link_m:
            slug = link_m.group(1)
            num_m = re.search(r"-(\d+)$", slug)
            if num_m:
                tme_url = f"https://t.me/nft/{slug}"
                nft_info = _parse_tme_nft_page_sync(tme_url)
                base_slug = re.sub(r"-\d+$", "", slug)
                base_name = re.sub(r"([a-z])([A-Z])", r"\1 \2", base_slug).replace("-", " ").strip()
                q_orig = base_name
            else:
                base_name = re.sub(r"([a-z])([A-Z])", r"\1 \2", slug).replace("-", " ").strip()
                q_orig = base_name

        # فحص وجود رقم القطعة في النص مثل Spiced Wine #67781
        num_in_text = re.search(r"#(\d+)", q_orig)
        if num_in_text and not nft_info:
            raw_slug = re.sub(r"[^A-Za-z0-9]", "", re.sub(r"#\d+", "", q_orig))
            if raw_slug:
                tme_url = f"https://t.me/nft/{raw_slug}-{num_in_text.group(1)}"
                nft_info = _parse_tme_nft_page_sync(tme_url)
            q_orig = re.sub(r"#\d+", "", q_orig).strip()

        # استبدال الكلمات العربية الشائعة
        words = q_orig.split()
        translated = []
        for w in words:
            wl = w.lower().strip(".,/-_")
            if wl in ARABIC_GIFT_ALIASES:
                translated.append(ARABIC_GIFT_ALIASES[wl])
            else:
                translated.append(w)
        search_str = " ".join(translated).strip()
        search_lower = search_str.lower()

        try:
            gifts._load_data()
        except Exception:
            pass

        upgraded_list = getattr(gifts, "_gifts_details", {}).get("upgraded", []) if isinstance(getattr(gifts, "_gifts_details", None), dict) else []
        matched_gift = None

        # مطابقة تامة أو بادئة
        for g in upgraded_list:
            fn = g.get("full_name", "").lower()
            sn = g.get("short_name", "").lower()
            if fn == search_lower or sn == search_lower:
                matched_gift = g
                break
            if search_lower.startswith(fn) or search_lower.startswith(sn):
                matched_gift = g
                break

        if not matched_gift:
            for g in upgraded_list:
                fn = g.get("full_name", "").lower()
                sn = g.get("short_name", "").lower()
                if fn in search_lower or sn in search_lower or fn.replace(" ", "") in search_lower.replace(" ", ""):
                    matched_gift = g
                    break

        if matched_gift:
            short_name = matched_gift["short_name"]
            full_name = matched_gift.get("full_name", short_name)
            prices = matched_gift.get("prices", {}) or {}

            attr_prices = {}
            try:
                attr_prices = gifts.get_attribute_price(short_name) or {}
            except Exception:
                pass
            backdrops = attr_prices.get("backdrops", {})
            symbols = attr_prices.get("symbols", {})
            models = attr_prices.get("models", {})

            m_name = nft_info.get("model") if nft_info else None
            b_name = nft_info.get("backdrop") if nft_info else None
            s_name = nft_info.get("symbol") if nft_info else None
            num_val = nft_info.get("number") if nft_info else (num_in_text.group(1) if num_in_text else None)

            if not nft_info:
                for b in sorted(backdrops.keys(), key=len, reverse=True):
                    if re.search(r'\b' + re.escape(b.lower()) + r'\b', search_lower):
                        b_name = b
                        break
                for s in sorted(symbols.keys(), key=len, reverse=True):
                    if re.search(r'\b' + re.escape(s.lower()) + r'\b', search_lower):
                        s_name = s
                        break
                for m in sorted(models.keys(), key=len, reverse=True):
                    if re.search(r'\b' + re.escape(m.lower()) + r'\b', search_lower):
                        m_name = m
                        break

            mp = models.get(m_name) if m_name else None
            bp = backdrops.get(b_name) if b_name else None
            sp = symbols.get(s_name) if s_name else None

            floor_val = None
            for k in ["floor_price_ton", "tgmrkt_price_ton", "getgems_price_ton", "portal_price_ton", "fragment_price_ton"]:
                v = matched_gift.get(k) or prices.get(k)
                if isinstance(v, (int, float)) and v > 0:
                    floor_val = float(v)
                    break

            candidates = [floor_val or 0]
            top_driver = None
            if mp:
                candidates.append(float(mp))
                if float(mp) >= max(candidates): top_driver = f"Model: {m_name} ({float(mp):.3f} TON)"
            if bp:
                candidates.append(float(bp))
                if float(bp) >= max(candidates): top_driver = f"Backdrop: {b_name} ({float(bp):.3f} TON)"
            if sp:
                candidates.append(float(sp))
                if float(sp) >= max(candidates): top_driver = f"Symbol: {s_name} ({float(sp):.3f} TON)"

            est_val = max(candidates) if candidates else floor_val

            top_b = sorted(backdrops.items(), key=lambda x: x[1], reverse=True)[:3]
            top_s = sorted(symbols.items(), key=lambda x: x[1], reverse=True)[:3]
            top_m = sorted(models.items(), key=lambda x: x[1], reverse=True)[:3]

            display_name = f"{full_name} #{num_val}" if num_val else full_name

            return {
                "is_specific_nft": bool(nft_info or num_val),
                "name": display_name,
                "gift_name": full_name,
                "gift_type": "UPGRADED",
                "custom_emoji_id": matched_gift.get("custom_emoji_id"),
                "collection": matched_gift.get("collection", "Telegram Gifts"),
                "floor_ton": floor_val,
                "tgmrkt_price_ton": matched_gift.get("tgmrkt_price_ton") or prices.get("tgmrkt_price_ton"),
                "getgems_price_ton": matched_gift.get("getgems_price_ton") or prices.get("getgems_price_ton"),
                "portal_price_ton": matched_gift.get("portal_price_ton") or prices.get("portal_price_ton"),
                "fragment_price_ton": matched_gift.get("fragment_price_ton") or prices.get("fragment_price_ton"),
                "average_price_ton": matched_gift.get("average_price_ton") or prices.get("average_price_ton"),
                "model_name": m_name,
                "model_price_ton": float(mp) if mp else None,
                "backdrop_name": b_name,
                "backdrop_price_ton": float(bp) if bp else None,
                "symbol_name": s_name,
                "symbol_price_ton": float(sp) if sp else None,
                "top_attribute_name": top_driver,
                "estimated_value_ton": est_val if (m_name or b_name or s_name) else floor_val,
                "top_backdrops": top_b,
                "top_symbols": top_s,
                "top_models": top_m,
            }

        # 2. فحص الهدايا غير المرقاة (Unupgraded)
        unupgraded_list = getattr(gifts, "_gifts_details", {}).get("unupgraded", []) if isinstance(getattr(gifts, "_gifts_details", None), dict) else []
        for u in unupgraded_list:
            fn = u.get("full_name", "").lower()
            sn = u.get("short_name", "").lower()
            if fn in search_lower or sn in search_lower:
                return {
                    "name": u.get("full_name", fn),
                    "gift_type": "UNUPGRADED",
                    "custom_emoji_id": u.get("custom_emoji_id"),
                    "price_ton": u.get("price_ton", 0),
                }

        # 3. فحص الهدايا العادية (Regular / Stars)
        try:
            reg_list = gifts.get_regular_gifts() or []
        except Exception:
            reg_list = []
        for r in reg_list:
            fn = r.full_name.lower()
            sn = r.short_name.lower()
            if fn in search_lower or sn in search_lower:
                return {
                    "name": r.full_name,
                    "gift_type": "REGULAR",
                    "supply": r.supply,
                    "is_active": r.is_active,
                    "count": r.count,
                }

        # 4. البحث المباشر عن خاصية (خلفية أو رمز) بمفردها
        sample_gift_slugs = ["artisan_brick", "spiced_wine", "plush_pepe", "kissing_frog", "delicious_cake"]
        all_known_backdrops = set()
        for s in sample_gift_slugs[:2]:
            ap = gifts.get_attribute_price(s) or {}
            all_known_backdrops.update(ap.get("backdrops", {}).keys())
        for b in sorted(all_known_backdrops, key=len, reverse=True):
            if b.lower() in search_lower:
                samples = []
                for s in sample_gift_slugs:
                    ap = gifts.get_attribute_price(s) or {}
                    if b in ap.get("backdrops", {}):
                        g_fn = next((x['full_name'] for x in upgraded_list if x['short_name'] == s), s)
                        samples.append((g_fn, ap["backdrops"][b]))
                return {
                    "is_attribute_search": True,
                    "attr_type": "الخلفية (Backdrop)",
                    "attr_name": b,
                    "samples": samples
                }

        all_known_symbols = set()
        for s in sample_gift_slugs[:2]:
            ap = gifts.get_attribute_price(s) or {}
            all_known_symbols.update(ap.get("symbols", {}).keys())
        for sm in sorted(all_known_symbols, key=len, reverse=True):
            if sm.lower() in search_lower:
                samples = []
                for s in sample_gift_slugs:
                    ap = gifts.get_attribute_price(s) or {}
                    if sm in ap.get("symbols", {}):
                        g_fn = next((x['full_name'] for x in upgraded_list if x['short_name'] == s), s)
                        samples.append((g_fn, ap["symbols"][sm]))
                return {
                    "is_attribute_search": True,
                    "attr_type": "الرمز (Symbol)",
                    "attr_name": sm,
                    "samples": samples
                }

        return None
    except Exception as e:
        logger.debug(f"[GIFT_LIB] {query}: {e}")
        return None


async def get_gift_floor(query: str) -> Optional[Dict[str, Any]]:
    q = query.strip()
    if not q:
        return None
    key = q.lower()
    now = datetime.datetime.utcnow()
    cached = _gift_price_cache.get(key)
    if cached and (now - cached[1]).total_seconds() < _GIFT_PRICE_TTL:
        return cached[0]
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _get_gift_floor_sync, q)
    if data:
        _gift_price_cache[key] = (data, now)
    return data


def _build_gift_price_text(data: Dict[str, Any], ton_usd: float, usd_egp: float) -> str:
    name = data.get("name", "هدية")
    floor = data.get("floor_ton")
    custom_emoji = data.get("custom_emoji_id")
    emoji_tag = f'<tg-emoji emoji-id="{custom_emoji}">🎁</tg-emoji> ' if custom_emoji else f"{CE.GIFT} "

    # 1. استعلام عن خاصية محددة فقط (خلفية أو رمز بدون اسم هدية)
    if data.get("is_attribute_search"):
        attr_type = data.get("attr_type", "خاصية")
        attr_name = data.get("attr_name", "")
        lines = [
            f"<blockquote>💎 <b>استعلام أسعار {attr_type}: {html.quote(attr_name)}</b></blockquote>\n",
            f"<blockquote>📊 <b>عينات من أسعار هذه الـ {attr_type} في هدايا Telegram:</b>"
        ]
        samples = data.get("samples", [])
        if samples:
            for g_name, p in samples[:7]:
                usd = p * ton_usd
                lines.append(f"• <b>{html.quote(g_name)}:</b> <code>{p:.3f} TON</code> (≈ ${usd:.2f})")
        else:
            lines.append(f"• <i>لا تتوفر أسعار مباشرة مسجلة حالياً لهذه الخاصية</i>")
        lines.append("</blockquote>")
        return "\n".join(lines)

    # 2. هدية عادية في متجر النجوم
    if data.get("gift_type") == "REGULAR":
        lines = [
            f"<blockquote>{emoji_tag}<b>هدية متجر Telegram: {html.quote(str(name))}</b> ⭐</blockquote>\n",
            f"<blockquote>📦 <b>تفاصيل الهدية:</b>\n"
            f"• <b>النوع:</b> هدية عادية (Regular Gift)\n"
            f"• <b>العدد الكلي (Supply):</b> <code>{data.get('supply', 'غير محدد'):,}</code>\n"
            f"• <b>الحالة:</b> {'✅ نشطة ومتاحة' if data.get('is_active') else '🔒 منتهية'}\n"
            f"• <b>المباع حالياً:</b> <code>{data.get('count', 0):,}</code></blockquote>"
        ]
        return "\n".join(lines)

    # 3. هدية غير مرقاة
    if data.get("gift_type") == "UNUPGRADED":
        p = data.get("price_ton", 0)
        lines = [
            f"<blockquote>{emoji_tag}<b>هدية Telegram غير مرقاة: {html.quote(str(name))}</b> 💎</blockquote>\n"
        ]
        if p and p > 0:
            lines.append(f"<blockquote>💎 <b>السعر المقدر:</b> {_format_ton_usd_egp(p, ton_usd, usd_egp)}</blockquote>\n")
        else:
            lines.append(f"<blockquote>⚠️ <i>لا يتوفر سعر محدد حالياً لهذه الهدية</i></blockquote>\n")
        return "\n".join(lines)

    # 4. هدية NFT مرقاة
    is_specific = data.get("is_specific_nft", False)
    has_specific_attrs = bool(data.get("backdrop_name") or data.get("symbol_name") or data.get("model_name"))

    header_title = f"فحص قطعة NFT: {html.quote(str(name))}" if is_specific else f"سعر هدية Telegram: {html.quote(str(name))}"
    lines = [
        f"<blockquote>{emoji_tag}<b>{header_title}</b> {CE.DIAMOND}</blockquote>\n"
    ]

    # عرض تفاصيل الخصائص المحددة
    if has_specific_attrs:
        attr_lines = []
        if data.get("model_name"):
            mp = data.get("model_price_ton")
            m_str = f" ➔ <code>{mp:.3f} TON</code>" if mp else ""
            attr_lines.append(f"• 🎭 <b>الموديل (Model):</b> <code>{html.quote(data['model_name'])}</code>{m_str}")
        if data.get("backdrop_name"):
            bp = data.get("backdrop_price_ton")
            b_str = f" ➔ <code>{bp:.3f} TON</code>" if bp else ""
            attr_lines.append(f"• 🎨 <b>الخلفية (Backdrop):</b> <code>{html.quote(data['backdrop_name'])}</code>{b_str}")
        if data.get("symbol_name"):
            sp = data.get("symbol_price_ton")
            s_str = f" ➔ <code>{sp:.3f} TON</code>" if sp else ""
            attr_lines.append(f"• ⚜️ <b>الرمز (Symbol):</b> <code>{html.quote(data['symbol_name'])}</code>{s_str}")

        est = data.get("estimated_value_ton")
        if est and est > 0:
            top_attr = data.get("top_attribute_name")
            driver_str = f"\n👑 <b>أعلى خاصية قيمة:</b> <code>{html.quote(top_attr)}</code>" if top_attr else ""
            attr_lines.append(
                f"━━━━━━━━━━━━━━━{driver_str}\n"
                f"💎 <b>القيمة التقديرية للقطعة:</b> {_format_ton_usd_egp(est, ton_usd, usd_egp)}"
            )

        lines.append(f"<blockquote>🏷️ <b>تفاصيل الخصائص المحددة (Attributes):</b>\n" + "\n".join(attr_lines) + "</blockquote>\n")

    # الحد الأدنى لسعر الهدية في السوق
    if floor and float(floor) > 0:
        lines.append(
            f"<blockquote>💎 <b>أقل سعر معروض للهدية عامة (Floor Price):</b>\n"
            f"┗ {_format_ton_usd_egp(float(floor), ton_usd, usd_egp)}</blockquote>\n"
        )
    else:
        lines.append(f"<blockquote>⚠️ <i>لا يتوفر سعر بيع مباشر حالياً على المنصات</i></blockquote>\n")

    # أسعار الأسواق والمنصات
    platforms = [
        ("TGMrkt", "tgmrkt_price_ton"),
        ("GetGems", "getgems_price_ton"),
        ("Portals", "portal_price_ton"),
        ("Fragment", "fragment_price_ton")
    ]
    plat_lines = []
    for label, key in platforms:
        v = data.get(key)
        if isinstance(v, (int, float)) and v > 0:
            plat_lines.append(f"• <b>{label}:</b> <code>{float(v):.3f} TON</code>")
        else:
            plat_lines.append(f"• <b>{label}:</b> <i>غير متوفر</i>")

    avg = data.get("average_price_ton")
    if isinstance(avg, (int, float)) and avg > 0:
        plat_lines.append(f"\n📈 <b>متوسط سعر الصفقات:</b> <code>{float(avg):.3f} TON</code>")

    if plat_lines:
        lines.append(f"<blockquote>📊 <b>الأسعار حسب المنصات والأسواق:</b>\n" + "\n".join(plat_lines) + "</blockquote>\n")

    # إذا لم يحدد المستخدم خصائص، نعرض له نظرة شاملة على أعلى الخلفيات والرموز والموديلات
    if not has_specific_attrs:
        top_b = data.get("top_backdrops", [])
        top_s = data.get("top_symbols", [])
        top_m = data.get("top_models", [])

        overview_lines = []
        if top_b:
            overview_lines.append("🎨 <b>أعلى الخلفيات قيمة (Top Backdrops):</b>")
            for b_name, b_pr in top_b[:3]:
                overview_lines.append(f"• <b>{html.quote(b_name)}:</b> <code>{b_pr:.3f} TON</code>")

        if top_s:
            if overview_lines: overview_lines.append("")
            overview_lines.append("⚜️ <b>أندر وأعلى الرموز قيمة (Top Symbols):</b>")
            for s_name, s_pr in top_s[:3]:
                overview_lines.append(f"• <b>{html.quote(s_name)}:</b> <code>{s_pr:.3f} TON</code>")

        if top_m:
            if overview_lines: overview_lines.append("")
            overview_lines.append("🎭 <b>أعلى الموديلات قيمة (Top Models):</b>")
            for m_name, m_pr in top_m[:3]:
                overview_lines.append(f"• <b>{html.quote(m_name)}:</b> <code>{m_pr:.3f} TON</code>")

        if overview_lines:
            lines.append(f"<blockquote>" + "\n".join(overview_lines) + "</blockquote>\n")

        lines.append(
            f"<blockquote>💡 <i>للاستعلام عن قطعة معينة أو بخلفية/رمز محدد:</i>\n"
            f"• <code>.سعر {html.quote(str(data.get('gift_name', name)))} + اسم الخلفية أو الرمز</code>\n"
            f"• أو أرسل رابط القطعة: <code>t.me/nft/...</code></blockquote>"
        )

    return "\n".join(lines).strip()


# =============================================================================
# 13. لوحات المفاتيح
# =============================================================================
def get_main_dashboard_kb() -> InlineKeyboardMarkup:
    buttons = [
        # المجموعة 1: (1, 2)
        [make_btn("الردود التلقائية", callback_data="sec_replies", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["chat"]),
         make_btn("👋 الترحيب التلقائي", callback_data="sec_welcome", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["sparkles"])],
        [make_btn("التفعيل والتعطيل", callback_data="sec_toggles", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bolt"]),
         make_btn("الذكاء الاصطناعي", callback_data="sec_ai", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["brain"])],

        # المجموعة 2: (1, 2)
        [make_btn("إدارة محافظ TON", callback_data="ton_wallets", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["diamond"])],
        [make_btn("الاشتراك الإجباري", callback_data="sec_mandatory", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["book"]),
         make_btn("تنبيه أمان", callback_data="sec_security", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["shield"])],

        # المجموعة 3: (1, 2)
        [make_btn("كشف الحسابات", callback_data="sec_inspect", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["search"])],
        [make_btn("قائمة المكتومين", callback_data="sec_muted", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["ban"]),
         make_btn("حفظ الذاتية", callback_data="sec_self_destruct", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["camera"])],

        # المجموعة 4: (1, 2)
        [make_btn("محول العملات", callback_data="sec_currency", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["money"])],
        [make_btn("Telegram Stars", callback_data="sec_stars", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["star"]),
         make_btn("Telegram Premium", callback_data="sec_premium", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["crown"])],

        # المجموعة 5: (1, 2)
        [make_btn("أسعار الهدايا", callback_data="sec_gifts", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["gift"])],
        [make_btn("الأكواد والكاش", callback_data="sec_cash", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["coin"]),
         make_btn("تحميل الفيديوهات", callback_data="sec_downloader", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["video"])],

        # المجموعة 6: (1, 2)
        [make_btn("الترجمة الفورية", callback_data="sec_translate", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["globe"])],
        [make_btn("الرسائل المجدولة", callback_data="sec_scheduled", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bell"]),
         make_btn("الآلة الحاسبة", callback_data="sec_calc", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bulb"])],

        # المجموعة 7: (1, 2)
        [make_btn("الألعاب والتسلية", callback_data="sec_plugins_fun", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["game"])],
        [make_btn("الإسلاميات والأدوات", callback_data="sec_plugins_tools", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["pray"]),
         make_btn("شرح ربط البوت", callback_data="sec_how_to_link", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["link"])],

        # مميزات البوت
        [make_btn("مميزات البوت", callback_data="sec_features", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["sparkles"])],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_back_kb(target: str = "main_dashboard") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("رجوع للقائمة الرئيسية", callback_data=target, style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
    ])


def get_admin_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        # إدارة الاشتراكات
        [make_btn("إضافة وتجديد اشتراك", callback_data="adm_add_sub", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["plus"]),
         make_btn("إنهاء وإلغاء اشتراك", callback_data="adm_end_sub", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_red"])],
        [make_btn("عرض المشتركين", callback_data="adm_list_subs", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["doc"]),
         make_btn("فحص اشتراك مستخدم", callback_data="adm_check_sub", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["search"])],
        # إعدادات البوت
        [make_btn("اشتراك إجباري في البوت", callback_data="adm_bot_sub", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["book"]),
         make_btn("تغيير ساعات التجربة", callback_data="adm_trial_hours", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["clock"])],
        # التواصل مع المستخدمين
        [make_btn("إذاعة للمستخدمين", callback_data="dev_broadcast", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["broadcast"]),
         make_btn("إهداء وقت مجاني للكل", callback_data="dev_gift_all", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["gift"])],
        # إعدادات النظام
        [make_btn("إعدادات ومعلومات النظام", callback_data="sec_settings", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["gear"])],
        [make_btn("رجوع للقائمة الرئيسية", callback_data="main_dashboard", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
    ])


def get_mandatory_sub_kb(sub: Optional[MandatorySubscription]) -> InlineKeyboardMarkup:
    is_enabled = bool(sub and sub.is_enabled and sub.channel_id)
    has_channel = bool(sub and sub.channel_id)
    status = "مفعّل" if is_enabled else ("غير مهيأ" if not has_channel else "معطّل")
    toggle_text = "تعطيل الاشتراك" if is_enabled else "تفعيل الاشتراك"
    toggle_cb = "ms_toggle_off" if is_enabled else "ms_toggle_on"
    return InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("إضافة قناة", callback_data="ms_add_channel", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["plus"]),
         make_btn("حذف القناة", callback_data="ms_del_channel", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["trash"])],
        [make_btn("إعداد رسالة الاشتراك", callback_data="ms_set_message", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["chat"])],
        [make_btn(f"{toggle_text} ({status})", callback_data=toggle_cb,
                  style="danger" if is_enabled else "success",
                  icon_custom_emoji_id=CUSTOM_EMOJI_IDS["ban"] if is_enabled else CUSTOM_EMOJI_IDS["check_verified"])],
        [make_btn("رجوع للقائمة الرئيسية", callback_data="main_dashboard", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
    ])


def format_welcome_text(template: Optional[str], user: Optional[TgUser]) -> str:
    if not template:
        return ""
    if not user:
        name = "عميلنا العزيز"
        first_name = "عميلنا"
        username = "غير متوفر"
        user_id_str = "غير متوفر"
    else:
        name = html.quote(user.full_name or user.first_name or "عميلنا العزيز")
        first_name = html.quote(user.first_name or name)
        username = f"@{html.quote(user.username)}" if user.username else "غير متوفر"
        user_id_str = str(user.id)

    text = template
    text = text.replace("{name}", name)
    text = text.replace("{first_name}", first_name)
    text = text.replace("{username}", username)
    text = text.replace("{id}", user_id_str)
    return text


def get_welcome_kb(welcome: Optional[BusinessWelcomeSetting] = None) -> InlineKeyboardMarkup:
    is_enabled = bool(welcome and welcome.is_enabled and (welcome.welcome_text or welcome.media_file_id))
    buttons = [
        [make_btn("تعديل رسالة الترحيب", callback_data="welcome_edit", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["pencil"])],
        [make_btn("تفعيل الترحيب", callback_data="welcome_enable", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["check_verified"]),
         make_btn("تعطيل الترحيب", callback_data="welcome_disable", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_red"])],
        [make_btn("معاينة الترحيب", callback_data="welcome_preview", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["refresh"]),
         make_btn("حذف رسالة الترحيب", callback_data="welcome_del_confirm", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["trash"])],
        [make_btn("إعادة ضبط سجل العملاء", callback_data="welcome_reset_cust_confirm", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["sparkles"])],
        [make_btn("رجوع للقائمة الرئيسية", callback_data="main_dashboard", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_security_kb(security: Optional[UserSecuritySettings]) -> InlineKeyboardMarkup:
    hp = security.hyperlink_protection if security else True
    ad = security.auto_delete_malicious if security else True
    ns = security.notify_security_alerts if security else True
    return InlineKeyboardMarkup(inline_keyboard=[
        [make_btn(f"حماية الروابط: {'مفعلة' if hp else 'معطلة'}", callback_data=f"sec_toggle_hp_{int(not hp)}",
                  style="success" if hp else "danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["shield"])],
        [make_btn(f"الحذف التلقائي: {'مفعل' if ad else 'معطل'}", callback_data=f"sec_toggle_ad_{int(not ad)}",
                  style="success" if ad else "danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["trash"])],
        [make_btn(f"إرسال التنبيهات: {'مفعل' if ns else 'معطل'}", callback_data=f"sec_toggle_ns_{int(not ns)}",
                  style="success" if ns else "danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bell"])],
        [make_btn("رجوع للقائمة الرئيسية", callback_data="main_dashboard", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
    ])


def get_security_alert_kb(owner_id: int, user_id: int, chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("فتح ملف العميل", callback_data=f"sec_open_profile_{user_id}", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["search"]),
         make_btn("كتم العميل فوراً", callback_data=f"sec_mute_user_{user_id}_{chat_id}", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["ban"])],
    ])


def get_broadcast_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("بدء الإذاعة الآن", callback_data="acc_bc_start", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["broadcast"]),
         make_btn("إلغاء الإذاعة", callback_data="acc_bc_cancel", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_red"])]
    ])


def get_translate_languages_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("العربية", callback_data="trlang_ar", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["globe"]),
         make_btn("English", callback_data="trlang_en", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["flag_gb"])],
        [make_btn("Français", callback_data="trlang_fr", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["flag_fr"]),
         make_btn("Deutsch", callback_data="trlang_de", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["flag_de"])],
        [make_btn("Español", callback_data="trlang_es", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["flag_es"])],
        [make_btn("رجوع للقائمة الرئيسية", callback_data="main_dashboard", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
    ])


# =============================================================================
# 14. Business Connection
# =============================================================================
@main_router.business_connection()
async def handle_business_connection(connection: BusinessConnection, bot: Bot):
    owner_id = connection.user.id
    async with async_session() as session:
        res = await session.execute(select(BusinessConnectionRecord).where(
            BusinessConnectionRecord.connection_id == connection.id))
        rec = res.scalar_one_or_none()
        if rec:
            rec.user_id = owner_id
            rec.is_enabled = connection.is_enabled
            rec.updated_at = datetime.datetime.utcnow()
        else:
            session.add(BusinessConnectionRecord(
                connection_id=connection.id, user_id=owner_id,
                is_enabled=connection.is_enabled))
        user_res = await session.execute(select(User).where(User.id == owner_id))
        user = user_res.scalar_one_or_none()
        if not user:
            role = "owner" if owner_id == OWNER_ID else "customer"
            user = User(id=owner_id, username=connection.user.username,
                        full_name=connection.user.full_name or "مستخدم", role=role,
                        created_at=datetime.datetime.utcnow(),
                        trial_expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=FREE_TRIAL_HOURS),
                        is_active=True)
            session.add(user)
        await session.commit()
        active = await is_subscription_active(owner_id, session)
    if connection.is_enabled:
        try:
            msg = (
                f"<blockquote>{CE.CHECK_VERIFIED} <b>تم ربط البوت بحسابك بنجاح!</b></blockquote>\n\n"
                f"<blockquote>{CE.GEAR} <b>لوحة التحكم:</b> <i>يمكنك التحكم الكامل في كافة الإعدادات عبر /start</i>\n"
                f"{CE.LOCK} <b>الحماية:</b> <tg-spoiler>يعمل البوت على حسابك بشكل آمن ومستقل 100%</tg-spoiler></blockquote>"
            ) if active else (
                f"<blockquote>{CE.ALERT} <b>تم ربط البوت بحسابك!</b></blockquote>\n\n"
                f"<blockquote>{CE.LOCK} <b>الحالة:</b> <tg-spoiler>فترة اشتراكك أو تجربتك المجانية منتهية حالياً</tg-spoiler>\n\n"
                f"📞 <i>تواصل مع المطور لتفعيل حسابك:</i> <b>{SUPPORT_USERNAME}</b></blockquote>"
            )
            await bot.send_message(owner_id, msg, parse_mode=ParseMode.HTML)
        except Exception:
            pass
    logger.info(f"🔗 Business connection {connection.id} → {owner_id} (active={active})")


# =============================================================================
# 15. /start
# =============================================================================
@main_router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    async with async_session() as session:
        user, is_new = await get_or_create_user_flagged(message.from_user, session)
        is_sub_valid = await is_subscription_active(user.id, session)
    if is_new and OWNER_ID and message.from_user.id != OWNER_ID:
        try:
            async with async_session() as session:
                total = await session.scalar(select(func.count(User.id))) or 0
            uname = f"@{message.from_user.username}" if message.from_user.username else "—"
            await bot.send_message(
                OWNER_ID,
                f"<blockquote>{CE.USER} <b>مستخدم جديد انضم للبوت بنجاح!</b> {CE.SPARKLES}</blockquote>\n\n"
                f"<blockquote>{CE.USER} <b>الاسم الكريم:</b> <b>{html.quote(message.from_user.full_name)}</b>\n"
                f"{CE.POINT} <b>اسم المستخدم:</b> {html.quote(uname)}\n"
                f"{CE.KEY} <b>معرّف الحساب:</b> <code>{message.from_user.id}</code>\n"
                f"{CE.TIME} <b>توقيت الانضمام:</b> <code>{datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</code>\n\n"
                f"{CE.STATS} <b>إجمالي مستخدمي البوت:</b> <tg-spoiler>{total}</tg-spoiler> مستخدم</blockquote>",
                parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.debug(f"[NEW_USER_NOTIFY] {e}")
    if not is_sub_valid:
        text = (
            f"<blockquote>{CE.ALERT} <b>انتهت صلاحية اشتراكك / تجربتك المجانية!</b> {CE.SIREN}</blockquote>\n\n"
            f"<blockquote>{CE.LOCK} <b>حالة البوت:</b> <tg-spoiler>متوقف حالياً عن معالجة رسائل عملائك</tg-spoiler>\n"
            f"{CE.BOLT} <i>لن يتم إرسال الردود التلقائية أو فحص المحادثات حتى تجديد الاشتراك.</i>\n\n"
            f"{CE.USER} <b>المطور المسؤول:</b> <code>{SUPPORT_USERNAME}</code>\n"
            f"{CE.KEY} <b>معرف حسابك:</b> <code>{message.from_user.id}</code></blockquote>\n\n"
            f"{CE.DOWN} <i>اضغط على الزر بالأسفل للتواصل المباشر وتجديد اشتراكك فوراً:</i>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("تواصل مع المطور للاشتراك", url=get_support_url(), style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["chat"])]
        ])
        return await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    now = datetime.datetime.utcnow()
    # التحقق من الاشتراك الإجباري في البوت (للمستخدمين العاديين فقط)
    if user.role not in ("owner", "admin"):
        bot_sub = await _get_bot_mandatory_sub()
        if bot_sub and bot_sub.is_enabled and bot_sub.channel_id:
            is_subbed = await _check_bot_channel_sub(bot, message.from_user.id, bot_sub)
            if not is_subbed:
                ch = f"@{bot_sub.channel_username}" if bot_sub.channel_username else str(bot_sub.channel_id)
                ch_link = f"https://t.me/{bot_sub.channel_username}" if bot_sub.channel_username else f"https://t.me/c/{str(bot_sub.channel_id).replace('-100', '')}"
                title = bot_sub.channel_title or ch
                sub_text = (
                    f"<blockquote>{CE.BOOK} <b>اشتراك إجباري مطلوب لتفعيل واستخدام البوت</b> {CE.SPARKLES}</blockquote>\n\n"
                    f"<blockquote>{CE.BROADCAST} <b>القناة الرسمية:</b> <b>{html.quote(title)}</b>\n"
                    f"{CE.LINK} <b>رابط القناة:</b> <code>{ch}</code>\n\n"
                    f"{CE.CHECK} <i>يرجى الاشتراك بالقناة ثم النقر على زر 'لقد اشتركت' للتحقق والمتابعة.</i></blockquote>"
                )
                sub_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [make_btn(f"اشترك في {html.quote(title)}", url=ch_link, style="success",
                              icon_custom_emoji_id=CUSTOM_EMOJI_IDS["link"])],
                    [make_btn("لقد اشتركت ✅", callback_data=f"botsubcheck_{message.from_user.id}",
                              style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["check_verified"])]
                ])
                return await message.answer(sub_text, reply_markup=sub_kb, parse_mode=ParseMode.HTML)
    sub_info = f"{CE.INFINITY} <b>دائم (إدارة عليا)</b>" if user.role in ["owner", "admin"] else ""
    if not sub_info:
        if user.subscription_expires_at and user.subscription_expires_at > now:
            days = (user.subscription_expires_at - now).days
            sub_info = f"{CE.CHECK_VERIFIED} <b>نشط ومفعل</b> <i>(متبقي <tg-spoiler>{days} يوم</tg-spoiler>)</i>"
        elif user.trial_expires_at and user.trial_expires_at > now:
            hours = int((user.trial_expires_at - now).total_seconds() // 3600)
            sub_info = f"{CE.CLOCK} <b>فترة تجريبية مجانية</b> <i>(متبقي <tg-spoiler>{hours} ساعة</tg-spoiler>)</i>"
        else:
            sub_info = f"{CE.CROSS} <b>غير نشط / منتهي</b>"
    text = (
        f"<blockquote>{CE.ROBOT} <b>لوحة إدارة الأعمال والذكاء الاصطناعي الفاخرة</b> {CE.SPARKLES}\n"
        f"{CE.WAVE} <i>أهلاً وسهلاً بك:</i> <b>{html.quote(message.from_user.full_name)}</b></blockquote>\n\n"
        f"<blockquote>{CE.STATS} <b>حالة الاشتراك:</b> {sub_info}\n"
        f"{CE.LINK} <b>معرّف البوت:</b> <code>{BOT_USERNAME}</code>\n"
        f"{CE.SHIELD} <b>مستوى الحماية والأمان:</b> <tg-spoiler>مشفر 100% — حماية متقدمة ضد الاحتيال والروابط الخبيثة</tg-spoiler></blockquote>\n\n"
        f"{CE.DOWN} <i>يرجى اختيار الخدمة أو القسم المطلوب من القائمة أدناه:</i>"
    )
    await message.answer(text, reply_markup=get_main_dashboard_kb(), parse_mode=ParseMode.HTML)


@main_router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != OWNER_ID:
        return await message.answer(f"<blockquote>{CE.BAN} <b>هذا الأمر مخصص للمطور فقط!</b></blockquote>")
    await message.answer(
        f"<blockquote>{CE.CROWN} <b>لوحة الإدارة والتحكم العليا للبوت</b> {CE.STAR_GOLD}</blockquote>\n\n"
        f"<blockquote>{CE.CROWN} <i>أهلاً بك يا مالك ومطور البوت الموقر.</i>\n\n"
        f"{CE.GEAR} <b>أبرز الصلاحيات وإدارة النظام:</b>\n"
        f"{CE.POINT} <b>إدارة الاشتراكات:</b> <i>(تفعيل — إلغاء — فحص — قائمة المشتركين)</i>\n"
        f"{CE.POINT} <b>الاشتراك الإجباري:</b> <i>(إلزام المستخدمين بقناة البوت الرسمية)</i>\n"
        f"{CE.POINT} <b>الفترة التجريبية:</b> <i>(تعديل ساعات التجربة المجانية للأعضاء)</i>\n"
        f"{CE.POINT} <b>الإذاعة الجماعية:</b> <i>(إرسال وتوجيه رسائل لجميع المستخدمين)</i>\n"
        f"{CE.POINT} <b>إهداء الوقت المجاني:</b> <i>(منح وقت اشتراك إضافي مجاني للجميع)</i>\n"
        f"{CE.POINT} <b>إعدادات النظام:</b> <i>(معلومات السيرفر، الموارد، وقواعد البيانات)</i></blockquote>",
        reply_markup=get_admin_panel_kb(), parse_mode=ParseMode.HTML)


# =============================================================================
# 16. main_dashboard
# =============================================================================
@main_router.callback_query(F.data == "main_dashboard")
async def cb_main_dashboard(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    async with async_session() as session:
        user = await get_or_create_user(callback.from_user, session)
        active = await is_subscription_active(user.id, session)
    if not active:
        return await callback.answer("⚠️ اشتراكك منتهي.", show_alert=True)
    text = (
        f"<blockquote>{CE.ROBOT} <b>لوحة التحكم الرئيسية لإدارة الأعمال</b> {CE.SPARKLES}</blockquote>\n\n"
        f"<blockquote>{CE.STAR} <i>مرحباً بك في مركز التحكم الذكي المتقدم لحسابك.</i>\n"
        f"{CE.BOLT} <b>حالة النظام:</b> <tg-spoiler>متصل وجاهز لمعالجة الرسائل والردود بدقة فائقة</tg-spoiler></blockquote>\n\n"
        f"{CE.DOWN} <i>اختر الخدمة المطلوبة للمتابعة:</i>"
    )
    kb = get_main_dashboard_kb()
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    finally:
        try:
            await callback.answer()
        except Exception:
            pass


# =============================================================================
# 16.1 مميزات البوت
# =============================================================================
@main_router.callback_query(F.data == "sec_features")
async def cb_sec_features(callback: CallbackQuery):
    text = (
        f"<blockquote>{CE.SPARKLES} <b>مميزات البوت — دليل شامل لكافة الخدمات الاحترافية</b></blockquote>\n\n"

        f"<blockquote>{CE.CHAT} <b>① الردود التلقائية الذكية</b>\n"
        f"<i>برمجة ردود فورية ودقيقة على استفسارات العملاء بناءً على كلمات دلالية.</i>\n"
        f"<tg-spoiler>يدعم: نصوص • وسائط • مطابقة ذكية أو كاملة</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.WAVE} <b>② الترحيب التلقائي بالعملاء</b>\n"
        f"<i>إرسال رسائل ترحيب راقية ومخصصة لكل عميل جديد يتواصل معك لأول مرة.</i>\n"
        f"<tg-spoiler>يدعم متغيرات ديناميكية: {{name}} / {{username}} / {{id}}</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.BOLT} <b>③ مركز التحكم بالتفعيل</b>\n"
        f"<i>تشغيل وإيقاف خدمات البوت المتعددة بضغطة زر واحدة وسهولة تامة.</i>\n"
        f"<tg-spoiler>تخصيص فوري لحالة كل خدمة على حدة في ثوانٍ</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.BRAIN} <b>④ مساعد الذكاء الاصطناعي</b>\n"
        f"<i>محركات Gemini و OpenAI الذكية للرد التلقائي وإدارة الحوار مع عملائك.</i>\n"
        f"<tg-spoiler>يشمل ميزة التفريغ الصوتي الفوري للرسائل الصوتية (Whisper)</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.DIAMOND} <b>⑤ إدارة وتتبع محافظ TON</b>\n"
        f"<i>ربط محافظك المشفرة ومراقبة المعاملات المالية اللحظية الواردة والصادرة.</i>\n"
        f"<tg-spoiler>حماية وتشفير عالي الأمان AES-256 مع إشعارات مباشرة</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.BOOK} <b>⑥ الاشتراك الإجباري الذكي</b>\n"
        f"<i>إلزام العملاء بالانضمام لقناتك الرسمية أولاً قبل فتح إمكانية التواصل معك.</i>\n"
        f"<tg-spoiler>فحص آلي فوري وزر تحقق وتأكيد فوري</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.SHIELD} <b>⑦ درع الأمان ومكافحة التصيد</b>\n"
        f"<i>كشف روابط التصيد والصفحات المزورة المضللة في رسائل عملائك.</i>\n"
        f"<tg-spoiler>حذف تلقائي وفوري للمحتوى الخبيث مع تنبيه المالك</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.SEARCH} <b>⑧ كشف تفاصيل الحسابات</b>\n"
        f"<i>فحص شامل ودقيق لأي حساب تيليجرام وتاريخ إنشائه وسيرفره التابع له.</i>\n"
        f"<tg-spoiler>كشف الحسابات الوهمية والبوتات واشتراكات البريميوم</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.BAN} <b>⑨ إدارة المكتومين والحظر</b>\n"
        f"<i>إدارة قائمة المحجوبين ومنع البوت من الرد عليهم أو تمرير أي رسائل منهم.</i>\n"
        f"<tg-spoiler>إمكانية الكتم وإلغاء الكتم بالأوامر السريعة أو الأزرار</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.CAM} <b>⑩ حفظ الوسائط الذاتية</b>\n"
        f"<i>الاحتفاظ بالصور ومقاطع الفيديو المؤقتة (ذاتية التدمير) فور استلامها.</i>\n"
        f"<tg-spoiler>حفظ يدوي أو تلقائي فوري للملف بجودته الأصلية</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.MONEY} <b>⑪ محول العملات والكريبتو</b>\n"
        f"<i>تحويل مباشر وسريع بين العملات الرقمية والورقية بأسعار حية من منصات عالمية.</i>\n"
        f"<tg-spoiler>يدعم TON • BTC • ETH • USDT • EGP وأكثر من 20 عملة</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.STAR} <b>⑫ أسعار نجوم Telegram Stars</b>\n"
        f"<i>عرض ومتابعة أسعار النجوم الرسمية للشراء والسحب مباشرة من Fragment.</i>\n"
        f"<tg-spoiler>حاسبة ذكية لتكلفة الشراء وعوائد السحب الصافية</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.CROWN} <b>⑬ باقات Telegram Premium</b>\n"
        f"<i>استعراض تكاليف اشتراكات تيليجرام المميز لمدد 3 و 6 و 12 شهراً.</i>\n"
        f"<tg-spoiler>أسعار حية محدثة وتدعم مختلف العملات</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.GIFT} <b>⑭ أسعار هدايا تيليجرام NFT</b>\n"
        f"<i>فحص فوري لقيم الهدايا التذكارية عبر Fragment و GetGems و TGMrkt.</i>\n"
        f"<tg-spoiler>يعرض Floor Price و Last Sale وأعلى العروض مباشرة</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.COIN} <b>⑮ شحن الكاش والأكواد</b>\n"
        f"<i>عرض وإدارة أسعار وباقات شحن المحافظ الإلكترونية والأكواد لعملائك.</i>\n"
        f"<tg-spoiler>نظام متكامل لتنسيق وعرض الخدمات المالية</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.VIDEO} <b>⑯ تحميل الفيديوهات الذكي</b>\n"
        f"<i>تنزيل الفيديوهات من منصات التواصل الاجتماعي بأعلى جودة وبدون علامة مائية.</i>\n"
        f"<tg-spoiler>يدعم: TikTok • Instagram • Facebook والمزيد</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.GLOBE} <b>⑰ الترجمة الفورية المتعددة</b>\n"
        f"<i>ترجمة فورية للرسائل والنصوص بين 7 لغات عالمية معتمدة.</i>\n"
        f"<tg-spoiler>دعم العربية والإنجليزية والفرنسية والألمانية والإسبانية وغيرها</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.CLOCK} <b>⑱ جدولة الرسائل المسبقة</b>\n"
        f"<i>تحديد مواعيد إرسال رسائل آلية ومجدولة للعملاء في تواريخ وأوقات محددة.</i>\n"
        f"<tg-spoiler>إرسال آلي دقيق دون الحاجة لتواجدك متصلاً</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.INFO} <b>⑲ الآلة الحاسبة الذكية</b>\n"
        f"<i>إجراء العمليات الحسابية والمعادلات الرياضية المعقدة مباشرة في المحادثة.</i>\n"
        f"<tg-spoiler>تنفيذ آمن للعمليات الحسابية دون أدنى خطأ</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.GAME} <b>⑳ الألعاب والتسلية</b>\n"
        f"<i>مجموعة ألعاب تفاعلية ممتعة لإضفاء جو ترفيهي مميز في المجموعات والخاص.</i>\n"
        f"<tg-spoiler>كت تويت • لو خيروك • نكت • حكم • صراحة والمزيد</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.PRAY} <b>㉑ الخدمات الإسلامية اليومية</b>\n"
        f"<i>مواقيت الصلاة الدقيقة والأذكار الصباحية والمسائية والسبحة الإلكترونية.</i>\n"
        f"<tg-spoiler>أدعية نبوية ومواقيت لكافة العواصم والمدن العربية</tg-spoiler></blockquote>\n\n"

        f"<blockquote>{CE.BELL} <b>㉒ التذكير الآلي الذكي</b>\n"
        f"<i>تنبيهك وتذكيرك بمتابعة الرد على العملاء بعد انقضاء الوقت المطلوب.</i>\n"
        f"<tg-spoiler>أرسل: فكرني بعد ساعتين أو فكرني بكره للجدولة الفورية</tg-spoiler></blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("رجوع للقائمة الرئيسية", callback_data="main_dashboard",
                  style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    finally:
        try:
            await callback.answer()
        except Exception:
            pass


# =============================================================================
# 16.5 الترحيب التلقائي للعملاء الجدد (Auto Welcome)
# =============================================================================
@main_router.callback_query(F.data == "sec_welcome")
async def cb_sec_welcome(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    async with async_session() as session:
        ws = await get_or_create_welcome_setting(callback.from_user.id, session)
        total_cust = await session.scalar(
            select(func.count(BusinessCustomer.id)).where(BusinessCustomer.owner_user_id == callback.from_user.id)
        ) or 0
        welcomed_cust = await session.scalar(
            select(func.count(BusinessCustomer.id)).where(
                BusinessCustomer.owner_user_id == callback.from_user.id,
                BusinessCustomer.welcome_sent.is_(True)
            )
        ) or 0

    is_on = bool(ws and ws.is_enabled and (ws.welcome_text or ws.media_file_id))
    status_str = f"{CE.CHECK_VERIFIED} <b><tg-spoiler>مفعل ونشط</tg-spoiler></b>" if is_on else f"{CE.CROSS} <b><tg-spoiler>معطل حالياً</tg-spoiler></b>"

    media_info = "لا توجد رسالة محددة"
    if ws:
        if ws.media_type == "photo":
            media_info = "صورة + نص"
        elif ws.media_type == "video":
            media_info = "فيديو + نص"
        elif ws.welcome_text:
            media_info = "نص فقط"

    text = (
        f"<blockquote>{CE.WAVE} <b>نظام الترحيب التلقائي الذكي للعملاء</b> {CE.SPARKLES}</blockquote>\n\n"
        f"<blockquote>{CE.STATS} <b>حالة الخدمة:</b> {status_str}\n"
        f"{CE.DOC} <b>نوع محتوى الترحيب:</b> <i>{media_info}</i>\n"
        f"{CE.USERS} <b>إجمالي العملاء المسجلين:</b> <code>{total_cust}</code>\n"
        f"{CE.CHECK} <b>تم الترحيب بهم بنجاح:</b> <tg-spoiler>{welcomed_cust}</tg-spoiler></blockquote>\n\n"
        f"<blockquote>{CE.INFO} <i>يرسل البوت تلقائياً رسالة الترحيب الأنيقة لكل عميل جديد يراسلك لأول مرة عبر Telegram Business.</i></blockquote>\n\n"
        f"{CE.DOWN} <i>اختر الإجراء المطلوب من الخيارات بالأسفل:</i>"
    )
    kb = get_welcome_kb(ws)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    finally:
        try:
            await callback.answer()
        except Exception:
            pass


@main_router.callback_query(F.data == "welcome_enable")
async def cb_welcome_enable(callback: CallbackQuery, state: FSMContext):
    async with async_session() as session:
        ws = await get_or_create_welcome_setting(callback.from_user.id, session)
        if not ws.welcome_text and not ws.media_file_id:
            return await callback.answer("⚠️ يرجى تحديد رسالة الترحيب أولاً بالضغط على ✏️ تعديل رسالة الترحيب.", show_alert=True)
        ws.is_enabled = True
        await session.commit()
    await callback.answer("🟢 تم تفعيل الترحيب التلقائي.")
    await cb_sec_welcome(callback, state)


@main_router.callback_query(F.data == "welcome_disable")
async def cb_welcome_disable(callback: CallbackQuery, state: FSMContext):
    async with async_session() as session:
        ws = await get_or_create_welcome_setting(callback.from_user.id, session)
        ws.is_enabled = False
        await session.commit()
    await callback.answer("🔴 تم تعطيل الترحيب التلقائي.")
    await cb_sec_welcome(callback, state)


@main_router.callback_query(F.data == "welcome_edit")
async def cb_welcome_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(WelcomeMessageFSM.waiting_for_message)
    text = (
        "<blockquote>👋 <b>إعداد رسالة الترحيب</b></blockquote>\n\n"
        "أرسل الآن الرسالة التي تريد إرسالها تلقائيًا للعميل عند أول تواصل معه.\n\n"
        "<b>يمكنك إرسال:</b>\n"
        "• نص\n"
        "• صورة + نص\n"
        "• فيديو + نص\n\n"
        "<blockquote>💡 <b>المتغيرات المدعومة:</b>\n"
        "• <code>{name}</code> : اسم العميل الكامل\n"
        "• <code>{first_name}</code> : الاسم الأول فقط\n"
        "• <code>{username}</code> : معرف العميل (@username)\n"
        "• <code>{id}</code> : المعرف الرقمي للعميل</blockquote>\n\n"
        "أرسل /cancel للإلغاء."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("❌ إلغاء", callback_data="sec_welcome", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_red"])]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    finally:
        try:
            await callback.answer()
        except Exception:
            pass


@main_router.message(WelcomeMessageFSM.waiting_for_message)
async def fsm_save_welcome_message(message: Message, state: FSMContext):
    if message.text and message.text.strip().lower() in ("/cancel", "الغاء", "إلغاء"):
        await state.clear()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("🔙 رجوع للترحيب التلقائي", callback_data="sec_welcome", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
        ])
        return await message.answer("❌ تم إلغاء تعديل رسالة الترحيب.", reply_markup=kb)

    media_type = None
    media_file_id = None
    welcome_text = None
    raw_html = message.html_text or ""

    if message.photo:
        media_type = "photo"
        media_file_id = message.photo[-1].file_id
        welcome_text = raw_html
    elif message.video:
        media_type = "video"
        media_file_id = message.video.file_id
        welcome_text = raw_html
    elif message.text:
        welcome_text = raw_html
    else:
        return await message.answer("⚠️ نوع الرسالة غير مدعوم. يرجى إرسال نص أو صورة أو فيديو فقط.\nأرسل /cancel للإلغاء.")

    async with async_session() as session:
        ws = await get_or_create_welcome_setting(message.from_user.id, session)
        ws.welcome_text = welcome_text
        ws.media_type = media_type
        ws.media_file_id = media_file_id
        ws.is_enabled = True
        ws.updated_at = datetime.datetime.utcnow()
        await session.commit()

    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("🔄 معاينة الترحيب", callback_data="welcome_preview", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["refresh"])],
        [make_btn("🔙 رجوع لقسم الترحيب", callback_data="sec_welcome", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
    ])
    await message.answer(
        "<blockquote>✅ <b>تم حفظ رسالة الترحيب بنجاح.</b></blockquote>\n\n"
        "🟢 تم تفعيل الترحيب التلقائي تلقائياً للعملاء الجدد.",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )


@main_router.callback_query(F.data == "welcome_preview")
async def cb_welcome_preview(callback: CallbackQuery):
    async with async_session() as session:
        ws = await session.scalar(
            select(BusinessWelcomeSetting).where(BusinessWelcomeSetting.user_id == callback.from_user.id)
        )
    if not ws or (not ws.welcome_text and not ws.media_file_id):
        return await callback.answer("⚠️ لم يتم تحديد رسالة ترحيب بعد.", show_alert=True)

    formatted_text = format_welcome_text(ws.welcome_text or "", callback.from_user)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("🔙 رجوع", callback_data="sec_welcome", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
    ])

    try:
        if ws.media_type == "photo" and ws.media_file_id:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer_photo(
                photo=ws.media_file_id,
                caption=formatted_text,
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )
        elif ws.media_type == "video" and ws.media_file_id:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer_video(
                video=ws.media_file_id,
                caption=formatted_text,
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )
        else:
            await callback.message.edit_text(
                formatted_text,
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )
    except TelegramBadRequest:
        if ws.media_type == "photo" and ws.media_file_id:
            await callback.message.answer_photo(photo=ws.media_file_id, caption=formatted_text, reply_markup=kb)
        elif ws.media_type == "video" and ws.media_file_id:
            await callback.message.answer_video(video=ws.media_file_id, caption=formatted_text, reply_markup=kb)
        else:
            await callback.message.answer(formatted_text, reply_markup=kb)
    finally:
        try:
            await callback.answer()
        except Exception:
            pass


@main_router.callback_query(F.data == "welcome_del_confirm")
async def cb_welcome_del_confirm(callback: CallbackQuery):
    text = (
        "<blockquote>⚠️ <b>هل أنت متأكد من حذف رسالة الترحيب؟</b></blockquote>\n\n"
        "سيتم حذف الرسالة بالكامل وتعطيل الترحيب التلقائي."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("✅ نعم، حذف", callback_data="welcome_do_delete", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["trash"]),
         make_btn("❌ إلغاء", callback_data="sec_welcome", style="secondary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_red"])]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    finally:
        try:
            await callback.answer()
        except Exception:
            pass


@main_router.callback_query(F.data == "welcome_do_delete")
async def cb_welcome_do_delete(callback: CallbackQuery, state: FSMContext):
    async with async_session() as session:
        ws = await session.scalar(
            select(BusinessWelcomeSetting).where(BusinessWelcomeSetting.user_id == callback.from_user.id)
        )
        if ws:
            ws.welcome_text = None
            ws.media_type = None
            ws.media_file_id = None
            ws.is_enabled = False
            await session.commit()
    await callback.answer("✅ تم حذف رسالة الترحيب بنجاح.", show_alert=True)
    await cb_sec_welcome(callback, state)


@main_router.callback_query(F.data == "welcome_reset_cust_confirm")
async def cb_welcome_reset_cust_confirm(callback: CallbackQuery):
    text = (
        "<blockquote>⚠️ <b>إعادة ضبط سجل العملاء</b></blockquote>\n\n"
        "هل تريد بالتأكيد إعادة ضبط سجل الترحيب لجميع عملائك؟\n"
        "عند التأكيد، سيتمكن البوت من إرسال الترحيب مجدداً لعملائك السابقين عند تواصلهم القادم."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("✅ نعم، إعادة الضبط", callback_data="welcome_reset_cust_do", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["sparkles"]),
         make_btn("❌ إلغاء", callback_data="sec_welcome", style="secondary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_red"])]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    finally:
        try:
            await callback.answer()
        except Exception:
            pass


@main_router.callback_query(F.data == "welcome_reset_cust_do")
async def cb_welcome_reset_cust_do(callback: CallbackQuery, state: FSMContext):
    async with async_session() as session:
        await session.execute(
            update(BusinessCustomer)
            .where(BusinessCustomer.owner_user_id == callback.from_user.id)
            .values(welcome_sent=False, welcome_sent_at=None)
        )
        await session.commit()
    await callback.answer("✅ تم إعادة ضبط سجل الترحيب لجميع العملاء.", show_alert=True)
    await cb_sec_welcome(callback, state)


# =============================================================================
# 17. الاشتراك الإجباري
# =============================================================================
@main_router.callback_query(F.data == "sec_mandatory")
async def cb_sec_mandatory(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    async with async_session() as session:
        sub = await get_mandatory_sub(callback.from_user.id, session)
    ch_info = "— لم تُضف قناة —"
    if sub and sub.channel_id:
        ch_info = (f"<b>{html.quote(sub.channel_title or '')}</b>\n"
                   f"🔗 @{sub.channel_username or ''}\n🆔 <code>{sub.channel_id}</code>")
    status = "🟢 مفعّل" if (sub and sub.is_enabled and sub.channel_id) else "🔴 معطّل / غير مهيأ"
    media_line = "نص فقط"
    if sub and sub.media_type == "photo":
        media_line = "🖼 صورة + نص"
    elif sub and sub.media_type == "video":
        media_line = "🎬 فيديو + نص"
    text = (
        f"<blockquote>{CE.BOOK} <b>نظام الاشتراك الإجباري للقنوات</b></blockquote>\n\n"
        f"<blockquote>📊 <b>الحالة:</b> <b>{status}</b>\n\n"
        f"📌 <b>القناة المربوطة:</b>\n{ch_info}\n\n"
        f"📝 <b>نوع الرسالة:</b> <i>{media_line}</i>\n\n"
        f"{CE.LOCK} <b>مستوى الحظر:</b> <tg-spoiler>حظر تلقائي لأي عميل غير مشترك حتى يشترك</tg-spoiler></blockquote>\n\n"
        f"👇 <i>إدارة إعدادات الاشتراك:</i>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=get_mandatory_sub_kb(sub), parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(text, reply_markup=get_mandatory_sub_kb(sub), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data == "ms_add_channel")
async def cb_ms_add_channel(callback: CallbackQuery, state: FSMContext):
    await state.set_state(MandatorySubFSM.waiting_for_channel)
    await callback.message.edit_text(
        f"<blockquote>{CE.PLUS} <b>إضافة قناة للاشتراك الإجباري</b></blockquote>\n\n"
        f"<blockquote>✏️ <i>أرسل معرف القناة أو رابطها أو حول رسالة منها:</i>\n\n"
        f"• <code>@channel_username</code>\n"
        f"• <code>https://t.me/channel_username</code>\n"
        f"• <code>-100xxxxxxxxxx</code>\n\n"
        f"{CE.ALERT} <b>تنبيه:</b> <tg-spoiler>يجب إضافة البوت مشرفاً في القناة أولاً</tg-spoiler></blockquote>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("إلغاء", callback_data="sec_mandatory", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_red"])]
        ]), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.message(MandatorySubFSM.waiting_for_channel)
async def fsm_ms_channel(message: Message, bot: Bot, state: FSMContext):
    ref = None
    if message.forward_from_chat and message.forward_from_chat.type in ("channel", "supergroup"):
        ref = message.forward_from_chat.id
    elif message.text:
        txt = message.text.strip()
        if re.match(r"^-?\d+$", txt):
            ref = int(txt)
        else:
            m = re.search(r"(?:t\.me/|@)([A-Za-z0-9_]{4,})", txt)
            if m:
                ref = f"@{m.group(1)}"
    if not ref:
        return await message.answer("⚠️ لم أتمكن من التعرف على القناة.")
    try:
        chat = await bot.get_chat(ref)
        if chat.type not in ("channel", "supergroup"):
            return await message.answer("⚠️ ليس قناة/مجموعة.")
        try:
            me = await bot.get_me()
            member = await bot.get_chat_member(chat.id, me.id)
            if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
                await message.answer("⚠️ البوت ليس مشرفاً في القناة. أضفه كمشرف أولاً.", parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.warning(f"[MS] {e}")
        async with async_session() as session:
            sub = await get_mandatory_sub(message.from_user.id, session)
            if not sub:
                sub = MandatorySubscription(user_id=message.from_user.id)
                session.add(sub)
            sub.channel_id = chat.id
            sub.channel_username = chat.username
            sub.channel_title = chat.title or chat.full_name
            await session.commit()
        await state.clear()
        await message.answer(
            f"<blockquote>{CE.CHECK_VERIFIED} <b>تم ربط قناة الاشتراك الإجباري بنجاح!</b>\n\n"
            f"📢 <b>القناة:</b> <b>{html.quote(chat.title or '')}</b>\n"
            f"🔗 <b>اليوزر:</b> @{chat.username or '—'}\n"
            f"🆔 <b>المعرف:</b> <code>{chat.id}</code>\n\n"
            f"{CE.LOCK} <b>الحالة:</b> <tg-spoiler>البوت سيتأكد تلقائياً من اشتراك أي عميل قبل التفاعل</tg-spoiler></blockquote>",
            reply_markup=get_back_kb("sec_mandatory"), parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
        await message.answer(f"<blockquote>{CE.ALERT} <b>تعذّر الوصول للقناة:</b> {html.quote(str(e))}</blockquote>", parse_mode=ParseMode.HTML)


@main_router.callback_query(F.data == "ms_del_channel")
async def cb_ms_del_channel(callback: CallbackQuery):
    async with async_session() as session:
        sub = await get_mandatory_sub(callback.from_user.id, session)
        if sub:
            sub.channel_id = None
            sub.channel_username = None
            sub.channel_title = None
            sub.is_enabled = False
            await session.commit()
        sub = await get_mandatory_sub(callback.from_user.id, session)
    await callback.answer("✅ تم حذف القناة.", show_alert=True)
    text = (
        f"<blockquote>{CE.TRASH} <b>إلغاء قناة الاشتراك الإجباري</b></blockquote>\n\n"
        f"<blockquote>📊 <b>الحالة:</b> 🔴 معطّل / غير مهيأ\n"
        f"📌 <b>القناة:</b> <i>— تم حذف القناة بنجاح —</i>\n\n"
        f"{CE.ALERT} <tg-spoiler>تم إيقاف فحص اشتراك العملاء مؤقتاً</tg-spoiler></blockquote>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=get_mandatory_sub_kb(sub), parse_mode=ParseMode.HTML)
    except Exception:
        pass


@main_router.callback_query(F.data.in_(["ms_toggle_on", "ms_toggle_off"]))
async def cb_ms_toggle(callback: CallbackQuery):
    enabled = callback.data == "ms_toggle_on"
    async with async_session() as session:
        sub = await get_mandatory_sub(callback.from_user.id, session)
        if not sub or not sub.channel_id:
            return await callback.answer("⚠️ أضف قناة أولاً.", show_alert=True)
        sub.is_enabled = enabled
        await session.commit()
        sub = await get_mandatory_sub(callback.from_user.id, session)
    await callback.answer("🟢 تم التفعيل" if enabled else "🔴 تم التعطيل", show_alert=True)
    status = "🟢 مفعّل" if sub.is_enabled else "🔴 معطّل"
    text = (
        f"<blockquote>{CE.BOOK} <b>الاشتراك الإجباري للقنوات</b></blockquote>\n\n"
        f"<blockquote>📊 <b>الحالة:</b> <b>{status}</b>\n\n"
        f"📌 <b>القناة:</b> <b>{html.quote(sub.channel_title or '')}</b> (@{sub.channel_username or ''})\n\n"
        f"{CE.SHIELD} <b>وضع الحماية:</b> <tg-spoiler>{'نشط - يمنع غير المشتركين' if sub.is_enabled else 'متوقف مؤقتاً'}</tg-spoiler></blockquote>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=get_mandatory_sub_kb(sub), parse_mode=ParseMode.HTML)
    except Exception:
        pass


@main_router.callback_query(F.data.startswith("subcheck_"))
async def cb_subcheck(callback: CallbackQuery, bot: Bot):
    """يُستدعى لما المستخدم يضغط 'لقد اشتركت' — يمسح الكاش ويتحقق فوراً"""
    try:
        parts = callback.data.split("_")
        target_user_id = int(parts[1])
        channel_id = int(parts[2])
    except (IndexError, ValueError):
        await callback.answer("⚠️ خطأ في البيانات.", show_alert=True)
        return

    cache_key = (channel_id, target_user_id)
    _sub_check_cache.pop(cache_key, None)
    logger.info(f"[SUBCHECK_BTN] مسح كاش user={target_user_id} channel={channel_id}")

    try:
        m = await bot.get_chat_member(chat_id=channel_id, user_id=target_user_id)
        is_subbed = m.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR,
                                 ChatMemberStatus.CREATOR, ChatMemberStatus.RESTRICTED)
    except TelegramAPIError as e:
        logger.warning(f"[SUBCHECK_BTN] {e}")
        await callback.answer("⚠️ تعذّر التحقق، حاول مرة أخرى.", show_alert=True)
        return

    if is_subbed:
        _sub_check_cache[cache_key] = (True, datetime.datetime.utcnow())
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.answer("✅ تم التحقق بنجاح! شكراً لاشتراكك.", show_alert=True)
        logger.info(f"[SUBCHECK_BTN] user={target_user_id} اشترك بنجاح ✅")
    else:
        await callback.answer("❌ لم يتم العثور على اشتراكك بعد، تأكد من الاشتراك في القناة ثم اضغط مرة أخرى.", show_alert=True)


@main_router.callback_query(F.data == "ms_set_message")
async def cb_ms_set_message(callback: CallbackQuery, state: FSMContext):
    await state.set_state(MandatorySubFSM.waiting_for_message_text)
    await callback.message.edit_text(
        f"<blockquote>{CE.CHAT} <b>تخصيص رسالة الاشتراك الإجباري</b></blockquote>\n\n"
        f"<blockquote>✏️ <i>أرسل المحتوى الذي يظهر للعميل عند مطالبته بالاشتراك:</i>\n\n"
        f"• نص مخصص\n"
        f"• صورة + شرح\n"
        f"• فيديو + شرح\n\n"
        f"💡 <i>زر الاشتراك وزر التحقق ستتم إضافتهما تلقائياً.</i></blockquote>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("إلغاء", callback_data="sec_mandatory", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_red"])]
        ]), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.message(MandatorySubFSM.waiting_for_message_text)
async def fsm_ms_set_message(message: Message, state: FSMContext):
    media_type = "text"
    media_file_id = None
    text_content = message.text or message.caption
    if message.photo:
        media_type = "photo"
        media_file_id = message.photo[-1].file_id
    elif message.video:
        media_type = "video"
        media_file_id = message.video.file_id
    async with async_session() as session:
        sub = await get_mandatory_sub(message.from_user.id, session)
        if not sub:
            sub = MandatorySubscription(user_id=message.from_user.id)
            session.add(sub)
        sub.message_text = text_content
        sub.media_type = media_type
        sub.media_file_id = media_file_id
        await session.commit()
    await state.clear()
    await message.answer(
        f"<blockquote>{CE.CHECK_VERIFIED} <b>تم حفظ رسالة الاشتراك الإجباري بنجاح!</b></blockquote>\n\n"
        f"<blockquote>📝 <b>النوع:</b> <code>{media_type}</code>\n"
        f"{CE.SPARKLES} <i>ستظهر الآن لكافة العملاء غير المشتركين بصورة احترافية.</i></blockquote>",
        reply_markup=get_back_kb("sec_mandatory"), parse_mode=ParseMode.HTML)


# =============================================================================
# 18. الأمان
# =============================================================================
@main_router.callback_query(F.data == "sec_security")
async def cb_sec_security(callback: CallbackQuery):
    async with async_session() as session:
        sec = await get_or_create_security(callback.from_user.id, session)
    text = (
        f"<blockquote>{CE.SHIELD} <b>مركز الأمان ومكافحة الاحتيال</b></blockquote>\n\n"
        f"<blockquote>🔍 <b>فحص الروابط الخبيثة (Hyperlink Spoofing):</b>\n"
        f"• كشف الروابط التي تظهر اسماً وهمياً لرابط احتيالي آخر.\n"
        f"• كتم الحسابات المشبوهة وحذف رسائل الصيد فوراً.\n\n"
        f"{CE.LOCK} <b>حماية الحساب:</b> <tg-spoiler>مشفرة وفعالة على مدار الساعة</tg-spoiler></blockquote>\n\n"
        f"👇 <i>اضبط خيارات الحماية لحسابك:</i>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=get_security_kb(sec), parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=get_security_kb(sec), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data.startswith("sec_toggle_"))
async def cb_sec_toggle(callback: CallbackQuery):
    parts = callback.data.split("_")
    if len(parts) < 4 or not parts[3].isdigit():
        return await callback.answer()
    key, val = parts[2], bool(int(parts[3]))
    async with async_session() as session:
        sec = await get_or_create_security(callback.from_user.id, session)
        if key == "hp":
            sec.hyperlink_protection = val
        elif key == "ad":
            sec.auto_delete_malicious = val
        elif key == "ns":
            sec.notify_security_alerts = val
        await session.commit()
        sec = await get_or_create_security(callback.from_user.id, session)
    try:
        await callback.message.edit_reply_markup(reply_markup=get_security_kb(sec))
    except Exception:
        pass
    await callback.answer("✅ تم التحديث")


@main_router.callback_query(F.data.startswith("sec_open_profile_"))
async def cb_sec_open_profile(callback: CallbackQuery, bot: Bot):
    uid = int(callback.data.split("_")[3])
    async with async_session() as session:
        await get_or_create_user(callback.from_user, session)
        msg_count = await session.scalar(select(func.count(UserMessageLog.id)).where(
            UserMessageLog.user_id == uid)) or 0
        last = await session.scalar(select(UserMessageLog).where(
            UserMessageLog.user_id == uid).order_by(UserMessageLog.created_at.desc()).limit(1))
        muted = await session.scalar(select(MutedUser).where(
            MutedUser.owner_id == callback.from_user.id, MutedUser.user_id == uid))
    try:
        chat = await bot.get_chat(uid)
        name = chat.full_name or "غير معروف"
        username = f"@{chat.username}" if chat.username else "—"
    except Exception:
        name, username = "غير معروف", "—"
    last_time = last.created_at.strftime("%Y-%m-%d %H:%M UTC") if last else "—"
    status = "🚫 مكتوم ومحظور" if muted else "🟢 نشط وطبيعي"
    text = (
        f"<blockquote>{CE.USER} <b>الملف التعريفي للعميل</b></blockquote>\n\n"
        f"<blockquote>👤 <b>الاسم:</b> <b>{html.quote(name)}</b>\n"
        f"🔹 <b>اليوزر:</b> {html.quote(username)}\n"
        f"🆔 <b>المعرف:</b> <code>{uid}</code>\n\n"
        f"📨 <b>عدد الرسائل:</b> <code>{msg_count}</code> رسالة\n"
        f"{CE.TIME} <b>آخر ظهور:</b> <code>{last_time}</code>\n"
        f"🛡️ <b>الحالة:</b> <tg-spoiler>{status}</tg-spoiler></blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("كتم العميل", callback_data=f"sec_mute_user_{uid}_0", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["ban"]),
         make_btn("إلغاء الكتم", callback_data=f"sec_unmute_user_{uid}", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["check_verified"])],
        [make_btn("رجوع للقائمة الرئيسية", callback_data="main_dashboard", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data.startswith("sec_mute_user_"))
async def cb_sec_mute_user(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split("_")
    uid = int(parts[3])
    chat_id = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
    owner_id = callback.from_user.id
    async with async_session() as session:
        res = await session.execute(select(MutedUser).where(
            MutedUser.owner_id == owner_id, MutedUser.user_id == uid))
        if not res.scalar_one_or_none():
            session.add(MutedUser(owner_id=owner_id, user_id=uid, chat_id=chat_id))
            await session.commit()
    if chat_id:
        await _apply_restriction(bot, chat_id, uid, restrict=True)
    await callback.answer("✅ تم كتم العميل.", show_alert=True)


@main_router.callback_query(F.data.startswith("sec_unmute_user_"))
async def cb_sec_unmute_user(callback: CallbackQuery, bot: Bot):
    uid = int(callback.data.split("_")[3])
    async with async_session() as session:
        res = await session.execute(select(MutedUser).where(
            MutedUser.owner_id == callback.from_user.id, MutedUser.user_id == uid))
        rec = res.scalar_one_or_none()
        if rec:
            chat_id = rec.chat_id
            await session.delete(rec)
            await session.commit()
        else:
            chat_id = 0
    if chat_id:
        await _apply_restriction(bot, chat_id, uid, restrict=False)
    await callback.answer("🔊 تم إلغاء الكتم.", show_alert=True)


# =============================================================================
# 19. دوال الكتم الفعلي
# =============================================================================
async def _apply_restriction(bot: Bot, chat_id: int, user_id: int, restrict: bool,
                             business_connection_id: Optional[str] = None) -> bool:
    try:
        if restrict:
            perms = ChatPermissions(
                can_send_messages=False,
                can_send_audios=False,
                can_send_documents=False,
                can_send_photos=False,
                can_send_videos=False,
                can_send_video_notes=False,
                can_send_voice_notes=False,
                can_send_polls=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False,
            )
        else:
            perms = ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_invite_users=True,
            )
        kw = {"chat_id": chat_id, "user_id": user_id, "permissions": perms}
        if business_connection_id:
            kw["business_connection_id"] = business_connection_id
        await bot.restrict_chat_member(**kw)
        return True
    except TelegramBadRequest as e:
        err = str(e).lower()
        if "not enough rights" in err or "not enough" in err:
            logger.warning(f"[RESTRICT] البوت لا يملك صلاحيات كافية في {chat_id}")
        elif "user is an administrator" in err:
            logger.warning(f"[RESTRICT] لا يمكن تقييد مشرف")
        elif "method is not available" in err or "not supported" in err:
            logger.debug(f"[RESTRICT] غير مدعوم في هذه المحادثة")
        else:
            logger.warning(f"[RESTRICT] {e}")
        return False
    except Exception as e:
        logger.error(f"[RESTRICT] {e}")
        return False


# =============================================================================
# 20. أقسام ثابتة + حفظ الذاتية
# =============================================================================
@main_router.callback_query(F.data == "sec_how_to_link")
async def cb_how_to_link(callback: CallbackQuery):
    text = (
        f"<blockquote>{CE.LINK} <b>دليل ربط البوت بحساب Telegram Business</b></blockquote>\n\n"
        f"<blockquote>1️⃣ <b>انسخ معرف البوت:</b> <code>{BOT_USERNAME}</code>\n"
        f"2️⃣ افتح <b>إعدادات تليجرام</b> ➔ <b>Telegram Business</b>\n"
        f"3️⃣ اختر <b>برامج المساعدة الآلية (Chatbots)</b>\n"
        f"4️⃣ الصق معرف البوت: <code>{BOT_USERNAME}</code> ثم احفظ\n"
        f"5️⃣ تأكد من تفعيل الصلاحيات المطلوبة للرد على العملاء</blockquote>\n\n"
        f"<blockquote>{CE.SHIELD} <b>الخصوصية والأمان:</b> <tg-spoiler>يعمل البوت فقط على الرسائل الواردة ولا يصل لبياناتك الشخصية إطلاقاً</tg-spoiler></blockquote>"
    )
    await callback.message.edit_text(text, reply_markup=get_back_kb(), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data == "sec_plugins_fun")
async def cb_sec_plugins_fun(callback: CallbackQuery):
    text = (
        f"<blockquote>{CE.GAME} <b>قسم الألعاب والترفيه التفاعلي</b></blockquote>\n\n"
        f"<blockquote>🎮 <b>الأوامر المتاحة في المحادثات:</b>\n\n"
        f"• <code>.كت</code> <i>أو</i> <code>كت تويت</code> — أسئلة وتغريدات تفاعلية\n"
        f"• <code>.لو خيروك</code> — مقارنات وخيارات صعبة\n"
        f"• <code>.صراحة</code> — أسئلة محرجة وصريحة\n"
        f"• <code>.نكتة</code> | <code>.حكمة</code> — ترفيه وثقافة\n\n"
        f"🎲 <b>ألعاب النرد والحظ:</b>\n"
        f"• <code>.نرد</code> | <code>.سلة</code> | <code>.كرة</code> | <code>.سهم</code> | <code>.بولينج</code> | <code>.حظ</code>\n\n"
        f"❤️ <b>العلاقات والتسلية (بالرد):</b>\n"
        f"• <code>.نسبة الحب</code> | <code>.زواج</code> | <code>.طلاق</code></blockquote>\n\n"
        f"⚡ <i>تعمل بكتابة النقطة أو بدونها مباشرة في أي شات.</i>"
    )
    await callback.message.edit_text(text, reply_markup=get_back_kb(), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data == "sec_plugins_tools")
async def cb_sec_plugins_tools(callback: CallbackQuery):
    text = (
        f"<blockquote>{CE.PRAY} <b>قسم الإسلاميات والأدوات الذكية</b></blockquote>\n\n"
        f"<blockquote>🕋 <b>الأدوات الإسلامية:</b>\n"
        f"• <code>.صلاة القاهرة</code> — مواقيت الصلاة لأي مدينة\n"
        f"• <code>.اذكار الصباح</code> | <code>.اذكار المساء</code> — الأذكار الصحيحة\n"
        f"• <code>.دعاء</code> — أدعية مأثورة مختارة\n"
        f"• <code>.سبحة</code> — سبحة إلكترونية تفاعلية بالعداد\n\n"
        f"🛠️ <b>الأدوات الذكية:</b>\n"
        f"• <code>.زخرفة كلمة</code> — زخرفة احترافية فورية للنصوص\n"
        f"• <code>.عمر 2000/5/14</code> — حساب دقيق للعمر بالأيام والساعات\n"
        f"• <code>.bin 457173</code> — فحص بنوك البطاقات الائتمانية\n"
        f"• <code>.تشفير نص</code> | <code>.فك تشفير</code> | <code>.عكس</code></blockquote>"
    )
    await callback.message.edit_text(text, reply_markup=get_back_kb(), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data == "sec_self_destruct")
async def cb_sec_self_destruct(callback: CallbackQuery):
    text = (
        f"<blockquote>{CE.CAM} <b>حفظ الوسائط والرسائل المؤقتة (الذاتية)</b></blockquote>\n\n"
        f"<blockquote>💡 <b>الحفظ اليدوي:</b>\n"
        f"• الرد على الميديا الذاتية بـ <code>ذ</code> أو <code>.ذ</code> أو <code>حفظ</code>\n"
        f"• يدعم: صور، فيديوهات، فويس، ملفات\n\n"
        f"🔒 <b>الحفظ التلقائي:</b>\n"
        f"• مفعّل تلقائياً ودائماً على مدار الساعة 24/7\n"
        f"• يحفظ البوت كل صورة/فيديو/فويس ذاتي فور وصوله بدون أي تدخل\n\n"
        f"🎙️ <b>تحويل الصوت إلى نص:</b>\n"
        f"• الرد على فويس نوت بـ <code>.صوت</code> أو <code>.نص</code>\n"
        f"• أو فعّله تلقائياً من مركز التحكم\n\n"
        f"📋 <b>التلخيص:</b>\n"
        f"• الرد على أي رسالة طويلة بـ <code>.لخص</code>\n\n"
        f"⏰ <b>التذكير بالرد:</b>\n"
        f"• الرد على رسالة عميل بـ <code>فكرني بعد 30 دقيقة</code>\n"
        f"• أو: <code>فكرني بعد 2 ساعة</code> / <code>فكرني بكره</code>\n\n"
        f"🚫 <b>فلتر الكلمات المحظورة:</b>\n"
        f"• <code>حظر كلمة [الكلمة]</code> — إضافة كلمة للفلتر\n"
        f"• <code>الغاء حظر [الكلمة]</code> — رفع الحظر\n"
        f"• <code>قائمة المحظورة</code> — عرض الكلمات المحظورة</blockquote>"
    )
    await callback.message.edit_text(text, reply_markup=get_back_kb(), parse_mode=ParseMode.HTML)
    await callback.answer()


# =============================================================================
# 21. الردود التلقائية
# =============================================================================
@main_router.callback_query(F.data == "sec_replies")
async def cb_sec_replies(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("إضافة رد تلقائي", callback_data="reply_add", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["plus"])],
        [make_btn("عرض الردود المحفوظة", callback_data="reply_list", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["doc"])],
        [make_btn("رجوع للقائمة الرئيسية", callback_data="main_dashboard", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
    ])
    text = (
        f"<blockquote>{CE.CHAT} <b>نظام الردود التلقائية الذكية</b></blockquote>\n\n"
        f"<blockquote>⚡ <i>برمج البوت ليرد نيابة عنك على استفسارات عملائك المكررة بنصوص أو صور أو فيديوهات.</i>\n\n"
        f"{CE.LOCK} <b>الاستجابة:</b> <tg-spoiler>رد فوري في أجزاء من الثانية 24/7</tg-spoiler></blockquote>\n\n"
        f"👇 <i>اختر العملية المطلوبة:</i>"
    )
    await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data == "reply_add")
async def cb_reply_add(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddReplyFSM.waiting_for_keyword)
    await callback.message.edit_text(
        f"<blockquote>{CE.PLUS} <b>إضافة رد تلقائي جديد</b></blockquote>\n\n"
        f"<blockquote>✏️ <b>الخطوة 1:</b> <i>أرسل الكلمة المفتاحية التي يكتبها العميل</i>\n"
        f"💡 <i>مثال:</i> <code>الأسعار</code> أو <code>العنوان</code> أو <code>طريقة الدفع</code></blockquote>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("إلغاء", callback_data="sec_replies", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_red"])]
        ]), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.message(AddReplyFSM.waiting_for_keyword)
async def fsm_reply_keyword(message: Message, state: FSMContext):
    await state.update_data(keyword=message.text.strip())
    await state.set_state(AddReplyFSM.waiting_for_match_type)
    text = (
        f"<blockquote>{CE.GEAR} <b>نوع مطابقة الكلمة:</b> <code>{html.quote(message.text.strip())}</code></blockquote>\n\n"
        f"<blockquote>🎯 <i>حدد متى يتم إرسال الرد للعميل:</i>\n\n"
        f"🔹 <b>تطابق كامل:</b> <tg-spoiler>عندما تكون الرسالة هي الكلمة المفتاحية فقط</tg-spoiler>\n"
        f"🔹 <b>يحتوي:</b> <tg-spoiler>إذا احتوت رسالة العميل على الكلمة في أي سياق</tg-spoiler>\n"
        f"🔹 <b>تجاهل الحالة:</b> <tg-spoiler>مطابقة مرنة دون قيود الأحرف</tg-spoiler></blockquote>"
    )
    await message.answer(text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("تطابق كامل", callback_data="match_exact", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["check_verified"])],
            [make_btn("يحتوي على الكلمة", callback_data="match_contains", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["search"])],
            [make_btn("تجاهل الأحرف والتشكيل", callback_data="match_ignore_case", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["refresh"])],
            [make_btn("إلغاء", callback_data="sec_replies", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_red"])]]),
        parse_mode=ParseMode.HTML)


@main_router.callback_query(F.data.startswith("match_"), AddReplyFSM.waiting_for_match_type)
async def cb_reply_match_type(callback: CallbackQuery, state: FSMContext):
    await state.update_data(match_type=callback.data.replace("match_", ""))
    await state.set_state(AddReplyFSM.waiting_for_content)
    await callback.message.edit_text(
        f"<blockquote>{CE.CHAT} <b>محتوى الرد التلقائي:</b></blockquote>\n\n"
        f"<blockquote>📤 <i>أرسل المحتوى الذي سيتم إرساله للعميل:</i>\n"
        f"• رسالة نصية مزخرفة\n"
        f"• صورة مع شرح\n"
        f"• مقطع فيديو مع شرح</blockquote>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("إلغاء", callback_data="sec_replies", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_red"])]]),
        parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.message(AddReplyFSM.waiting_for_content)
async def fsm_reply_content(message: Message, state: FSMContext):
    data = await state.get_data()
    keyword = data["keyword"]
    match_type = data["match_type"]
    reply_type = "text"
    media_id = None
    text_content = message.html_text if (message.text or message.caption) else None
    if message.photo:
        reply_type = "photo"; media_id = message.photo[-1].file_id
    elif message.video:
        reply_type = "video"; media_id = message.video.file_id
    elif message.animation:
        reply_type = "animation"; media_id = message.animation.file_id
    elif message.document:
        reply_type = "document"; media_id = message.document.file_id
    elif message.voice:
        reply_type = "voice"; media_id = message.voice.file_id
    elif message.audio:
        reply_type = "audio"; media_id = message.audio.file_id
    elif message.sticker:
        reply_type = "sticker"; media_id = message.sticker.file_id
    uid = message.from_user.id
    async with async_session() as session:
        ex = await session.execute(select(AutoReply).where(
            AutoReply.user_id == uid, AutoReply.keyword == keyword))
        if ex.scalar_one_or_none():
            await state.clear()
            return await message.answer(f"<blockquote>{CE.ALERT} <b>يوجد رد مسبق بنفس الكلمة!</b></blockquote>")
        session.add(AutoReply(user_id=uid, keyword=keyword, reply_text=text_content,
                              reply_type=reply_type, media_file_id=media_id,
                              match_type=match_type, created_by=uid))
        await session.commit()
    await state.clear()
    await message.answer(
        f"<blockquote>{CE.CHECK_VERIFIED} <b>تم حفظ الرد التلقائي بنجاح!</b></blockquote>\n\n"
        f"<blockquote>📌 <b>الكلمة:</b> <code>{html.quote(keyword)}</code>\n"
        f"🎯 <b>النوع:</b> <code>{reply_type}</code> | <b>المطابقة:</b> <code>{match_type}</code>\n"
        f"{CE.BOLT} <b>الحالة:</b> <tg-spoiler>جاهز للرد الفوري على عملائك</tg-spoiler></blockquote>",
        reply_markup=get_back_kb("sec_replies"), parse_mode=ParseMode.HTML)


@main_router.callback_query(F.data == "reply_list")
async def cb_reply_list(callback: CallbackQuery):
    async with async_session() as session:
        res = await session.execute(select(AutoReply).where(AutoReply.user_id == callback.from_user.id))
        replies = res.scalars().all()
    if not replies:
        return await callback.message.edit_text(
            f"<blockquote>{CE.DOC} <b>قائمة الردود التلقائية</b></blockquote>\n\n"
            f"<blockquote><i>لا توجد ردود محفوظة حالياً. اضغط على إضافة رد لإنشاء أول رد.</i></blockquote>",
            reply_markup=get_back_kb("sec_replies"), parse_mode=ParseMode.HTML)
    kb = []
    for r in replies[:15]:
        kb.append([make_btn(f"{r.keyword} ({r.reply_type})", callback_data=f"rep_info_{r.id}", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["chat"]),
                   make_btn("حذف", callback_data=f"del_rep_{r.id}", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["trash"])])
    kb.append([make_btn("رجوع للردود", callback_data="sec_replies", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])])
    await callback.message.edit_text(
        f"<blockquote>{CE.DOC} <b>قائمة الردود التلقائية النشطة</b></blockquote>\n\n"
        f"<blockquote>📊 <b>إجمالي الردود:</b> <code>{len(replies)}</code> رد\n"
        f"⚡ <i>اضغط على أي رد للتفاصيل أو الحذف:</i></blockquote>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data.startswith("rep_info_"))
async def cb_rep_info(callback: CallbackQuery):
    rid = int(callback.data.split("_")[2])
    async with async_session() as session:
        rep = (await session.execute(select(AutoReply).where(AutoReply.id == rid))).scalar_one_or_none()
        if not rep or (rep.user_id != callback.from_user.id and callback.from_user.id != OWNER_ID):
            return await callback.answer("❌ غير مصرح.", show_alert=True)
    text = (
        f"<blockquote>{CE.DOC} <b>تفاصيل الرد التلقائي:</b></blockquote>\n\n"
        f"<blockquote>📌 <b>الكلمة المفتاحية:</b> <code>{html.quote(rep.keyword)}</code>\n"
        f"📁 <b>نوع المحتوى:</b> <code>{rep.reply_type}</code>\n\n"
        f"📝 <b>النص المسجل:</b>\n<i>{html.quote(rep.reply_text or '—')}</i></blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("حذف هذا الرد", callback_data=f"del_rep_{rep.id}", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["trash"])],
        [make_btn("رجوع لقائمة الردود", callback_data="reply_list", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data.startswith("del_rep_"))
async def cb_del_rep(callback: CallbackQuery):
    rid = int(callback.data.split("_")[2])
    async with async_session() as session:
        rep = (await session.execute(select(AutoReply).where(AutoReply.id == rid))).scalar_one_or_none()
        if not rep or (rep.user_id != callback.from_user.id and callback.from_user.id != OWNER_ID):
            return await callback.answer("❌ غير مصرح.", show_alert=True)
        await session.delete(rep)
        await session.commit()
    await callback.answer("✅ تم حذف الرد بنجاح.", show_alert=True)
    await cb_reply_list(callback)


# =============================================================================
# 22. باقي الأقسام
# =============================================================================


@main_router.callback_query(F.data == "sched_add")
async def cb_sched_add(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ScheduleMessageFSM.waiting_for_target)
    await callback.message.edit_text(
        f"<blockquote>{CE.TIME} <b>جدولة رسالة جديدة</b></blockquote>\n\n"
        f"<blockquote>🎯 <i>أرسل معرف المحادثة (Chat ID / User ID):</i></blockquote>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("إلغاء", callback_data="sec_scheduled", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_red"])]]),
        parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.message(ScheduleMessageFSM.waiting_for_target)
async def fsm_sched_target(message: Message, state: FSMContext):
    v = message.text.strip()
    if not (v.isdigit() or (v.startswith("-") and v[1:].isdigit())):
        return await message.answer(f"<blockquote>{CE.ALERT} <b>معرف محادثة غير صحيح!</b></blockquote>")
    await state.update_data(target_id=int(v))
    await state.set_state(ScheduleMessageFSM.waiting_for_text)
    await message.answer(f"<blockquote>{CE.CHAT} <i>أرسل نص الرسالة المطلوب جدولتها:</i></blockquote>", parse_mode=ParseMode.HTML)


@main_router.message(ScheduleMessageFSM.waiting_for_text)
async def fsm_sched_text(message: Message, state: FSMContext):
    await state.update_data(msg_text=message.text.strip())
    await state.set_state(ScheduleMessageFSM.waiting_for_minutes)
    await message.answer(f"<blockquote>{CE.TIME} <i>بعد كم دقيقة يتم إرسال الرسالة تلقائياً؟</i></blockquote>", parse_mode=ParseMode.HTML)


@main_router.message(ScheduleMessageFSM.waiting_for_minutes)
async def fsm_sched_minutes(message: Message, state: FSMContext):
    v = message.text.strip()
    if not v.isdigit() or int(v) <= 0:
        return await message.answer(f"<blockquote>{CE.ALERT} <b>يرجى إرسال رقم صحيح بالدقائق!</b></blockquote>")
    m = int(v)
    data = await state.get_data()
    await state.clear()
    send_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=m)
    biz_conn_id = None
    async with async_session() as session:
        conn = await session.scalar(select(BusinessConnectionRecord).where(
            BusinessConnectionRecord.user_id == message.from_user.id,
            BusinessConnectionRecord.is_enabled == True
        ))
        if conn:
            biz_conn_id = conn.connection_id

        session.add(ScheduledMessage(created_by_user_id=message.from_user.id,
                                     business_connection_id=biz_conn_id,
                                     target_chat_id=data["target_id"],
                                     text_content=data["msg_text"],
                                     scheduled_time=send_at, is_sent=False, is_active=True))
        await session.commit()

    from_account_label = "حسابك الشخصي (Business) 👤" if biz_conn_id else "البوت 🤖"
    await message.answer(
        f"<blockquote>{CE.CHECK_VERIFIED} <b>تمت جدولة الرسالة بنجاح!</b></blockquote>\n\n"
        f"<blockquote>⏰ <b>موعد الإرسال:</b> بعد <code>{m}</code> دقيقة\n"
        f"🎯 <b>الوجهة:</b> <code>{data['target_id']}</code>\n"
        f"📤 <b>الإرسال من:</b> <b>{from_account_label}</b></blockquote>",
        reply_markup=get_back_kb("sec_scheduled"), parse_mode=ParseMode.HTML)


@main_router.callback_query(F.data == "sec_toggles")
async def cb_sec_toggles(callback: CallbackQuery):
    async with async_session() as session:
        res = await session.execute(select(ServiceSetting).where(ServiceSetting.user_id == callback.from_user.id))
        st = {s.service_key: s.is_enabled for s in res.scalars().all()}
        sub = await get_mandatory_sub(callback.from_user.id, session)
        st["mandatory"] = bool(sub and sub.is_enabled and sub.channel_id)
        ws = await get_or_create_welcome_setting(callback.from_user.id, session)
        st["welcome"] = bool(ws and ws.is_enabled and (ws.welcome_text or ws.media_file_id))
    services = [("welcome", "الترحيب التلقائي", CUSTOM_EMOJI_IDS["sparkles"]),
                ("auto_reply", "الردود التلقائية", CUSTOM_EMOJI_IDS["chat"]),
                ("mandatory", "الاشتراك الإجباري", CUSTOM_EMOJI_IDS["book"]),
                ("self_destruct", "حفظ الميديا والذاتية", CUSTOM_EMOJI_IDS["camera"]),
                ("voice_to_text", "تحويل الصوت إلى نص تلقائياً", CUSTOM_EMOJI_IDS["chat"]),
                ("currency", "محول العملات المباشر", CUSTOM_EMOJI_IDS["money"]),
                ("ai", "الذكاء الاصطناعي (روك)", CUSTOM_EMOJI_IDS["brain"]),
                ("calc", "الآلة الحاسبة الآمنة", CUSTOM_EMOJI_IDS["bulb"]),
                ("translate", "الترجمة الفورية", CUSTOM_EMOJI_IDS["globe"]),
                ("downloader", "تحميل الفيديوهات", CUSTOM_EMOJI_IDS["video"]),
                ("scheduled", "الرسائل المجدولة", CUSTOM_EMOJI_IDS["bell"])]
    kb = []
    for k, n, icon in services:
        en = st.get(k, True) if k not in ("mandatory", "welcome") else st.get(k, False)
        status_sym = "مفعّل" if en else "معطّل"
        kb.append([make_btn(f"{n} ({status_sym})", callback_data=f"tog_{k}_{int(not en)}",
                            style="success" if en else "danger", icon_custom_emoji_id=icon)])
    kb.append([make_btn("رجوع للقائمة الرئيسية", callback_data="main_dashboard", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])])
    text = (
        f"<blockquote>{CE.BOLT} <b>مركز التحكم بالتفعيل والتعطيل</b></blockquote>\n\n"
        f"<blockquote>⚡ <i>تحكم بالخدمات المفعلة على حسابك في محادثات Telegram Business:</i>\n\n"
        f"🟢 <b>مفعّل:</b> <tg-spoiler>الخدمة نشطة وتستجيب للعملاء مباشرة</tg-spoiler>\n"
        f"🔴 <b>معطّل:</b> <tg-spoiler>الخدمة متوقفة مؤقتاً عن الرد</tg-spoiler></blockquote>"
    )
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data.startswith("tog_"))
async def cb_toggle_service(callback: CallbackQuery):
    raw = callback.data[4:]
    key, _, st_str = raw.rpartition("_")
    ns = bool(int(st_str)) if st_str.isdigit() else False
    uid = callback.from_user.id
    if key == "welcome":
        async with async_session() as session:
            ws = await get_or_create_welcome_setting(uid, session)
            if ns and not ws.welcome_text and not ws.media_file_id:
                return await callback.answer("⚠️ يرجى إعداد رسالة الترحيب أولاً من قسم الترحيب التلقائي!", show_alert=True)
            ws.is_enabled = ns
            await session.commit()
        await callback.answer("🟢 تم تفعيل الترحيب التلقائي" if ns else "🔴 تم تعطيل الترحيب التلقائي", show_alert=True)
        return await cb_sec_toggles(callback)

    if key == "mandatory":
        async with async_session() as session:
            sub = await get_mandatory_sub(uid, session)
            if not sub or not sub.channel_id:
                return await callback.answer("⚠️ أضف قناة أولاً من قسم الاشتراك الإجباري!", show_alert=True)
            sub.is_enabled = ns
            await session.commit()
        await callback.answer("🟢 تم تفعيل الاشتراك الإجباري" if ns else "🔴 تم تعطيل الاشتراك الإجباري", show_alert=True)
        return await cb_sec_toggles(callback)

    async with async_session() as session:
        res = await session.execute(select(ServiceSetting).where(
            ServiceSetting.user_id == uid, ServiceSetting.service_key == key))
        s = res.scalar_one_or_none()
        if s:
            s.is_enabled = ns
        else:
            session.add(ServiceSetting(user_id=uid, chat_id=callback.message.chat.id,
                                       service_key=key, is_enabled=ns))
        await session.commit()
    await cb_sec_toggles(callback)


# =============================================================================
# 23. أقسام بسيطة
# =============================================================================
@main_router.callback_query(F.data == "sec_ai")
async def cb_sec_ai(callback: CallbackQuery):
    parts = []
    if GEMINI_API_KEY and GEMINI_API_KEY.startswith("AIzaSy"):
        parts.append("Gemini 🟢")
    elif GEMINI_API_KEY:
        parts.append("Gemini ⚠️")
    else:
        parts.append("Gemini 🔴")
    parts.append("OpenAI 🟢" if OPENAI_API_KEY else "OpenAI 🔴")
    status = " | ".join(parts)
    text = (
        f"<blockquote>{CE.BRAIN} <b>مساعد الذكاء الاصطناعي الخارق</b></blockquote>\n\n"
        f"<blockquote>📊 <b>حالة النماذج:</b> <b>{status}</b>\n\n"
        f"⚡ <b>طريقة الاستخدام في الشاتات:</b>\n"
        f"• اكتب <code>.روك</code> متبوعاً بسؤالك (مثال: <code>.روك لخص الرسالة</code>)\n"
        f"• أو قم بالرد على أي رسالة واكتب <code>.روك</code>\n\n"
        f"{CE.LOCK} <b>التشغيل:</b> <tg-spoiler>يعمل في الخاص ومحادثات البيزنس والمجموعات</tg-spoiler></blockquote>"
    )
    await callback.message.edit_text(text, reply_markup=get_back_kb(), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data == "sec_muted")
async def cb_sec_muted(callback: CallbackQuery):
    async with async_session() as session:
        res = await session.execute(select(MutedUser).where(MutedUser.owner_id == callback.from_user.id))
        ml = res.scalars().all()
    kb = []
    for m in ml[:10]:
        kb.append([make_btn(f"مستخدم: {m.user_id}", callback_data=f"info_mute_{m.user_id}", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["user"]),
                   make_btn("إلغاء الكتم", callback_data=f"unmute_btn_{m.user_id}", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["check_verified"])])
    kb.append([make_btn("رجوع للقائمة الرئيسية", callback_data="main_dashboard", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])])
    text = (
        f"<blockquote>{CE.MUTE} <b>قائمة المستخدمين المكتومين</b></blockquote>\n\n"
        f"<blockquote>📊 <b>العدد الإجمالي:</b> <code>{len(ml)}</code> مكتوم\n\n"
        f"🛡️ <b>طريقة الكتم السريع:</b> بالرد بـ <code>.كتم</code> أو <code>.الغاء كتم</code>\n"
        f"⚡ <b>في المجموعات:</b> <tg-spoiler>تقييد صلاحيات الإرسال عبر البوت المشرف</tg-spoiler>\n"
        f"⚡ <b>في الخاص:</b> <tg-spoiler>حذف تلقائي فوري لأي رسالة يرسلها المكتوم</tg-spoiler></blockquote>"
    )
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data.startswith("unmute_btn_"))
async def cb_unmute_btn(callback: CallbackQuery, bot: Bot):
    uid = int(callback.data.split("_")[2])
    async with async_session() as session:
        res = await session.execute(select(MutedUser).where(
            MutedUser.owner_id == callback.from_user.id, MutedUser.user_id == uid))
        rec = res.scalar_one_or_none()
        chat_id = rec.chat_id if rec else 0
        if rec:
            await session.delete(rec)
            await session.commit()
    if chat_id:
        await _apply_restriction(bot, chat_id, uid, restrict=False)
    await callback.answer("✅ تم إلغاء الكتم.", show_alert=True)
    await cb_sec_muted(callback)


@main_router.callback_query(F.data == "sec_calc")
async def cb_sec_calc(callback: CallbackQuery):
    text = (
        f"<blockquote>{CE.INFO} <b>الآلة الحاسبة الرياضية الفورية</b></blockquote>\n\n"
        f"<blockquote>💡 <b>طريقة الاستخدام:</b>\n"
        f"• أرسل أي معادلة حسابية مباشرة في الشات أو محادثات البيزنس:\n\n"
        f"• مثال: <code>50 + 50</code>\n"
        f"• مثال: <code>(250 * 4) / 2</code>\n"
        f"• مثال: <code>100 * 14%</code>\n\n"
        f"{CE.SHIELD} <b>الأمان:</b> <tg-spoiler>حساب آمن بنظام AST المعزول ضد الثغرات</tg-spoiler></blockquote>"
    )
    await callback.message.edit_text(text, reply_markup=get_back_kb(), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data == "sec_currency")
async def cb_sec_currency(callback: CallbackQuery):
    d = await CurrencyCache.get_rates()
    all_usd = d.get("all_usd", {})
    init_text, _ = get_converter_message_content("TON", 1.0, target_sym=None, all_usd=all_usd)
    kb = get_converter_keyboard("TON", 1.0, include_back=True)
    await callback.message.edit_text(init_text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data.startswith("ccnv:"))
async def cb_currency_convert(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 4:
        return await callback.answer()
    _, base_sym, amt_str, target_sym = parts
    try:
        amount = float(amt_str)
    except ValueError:
        amount = 1.0

    d = await CurrencyCache.get_rates()
    all_usd = d.get("all_usd", {})

    text, toast = get_converter_message_content(base_sym, amount, target_sym, all_usd)
    has_back = False
    if callback.message.reply_markup and callback.message.reply_markup.inline_keyboard:
        last_row = callback.message.reply_markup.inline_keyboard[-1]
        if last_row and any(btn.callback_data == "main_dashboard" for btn in last_row):
            has_back = True

    # الأزرار فقط للاختيار؛ بعد الضغط تظهر النتيجة مباشرة بدون أزرار
    kb = InlineKeyboardMarkup(inline_keyboard=[[make_btn("رجوع", callback_data="main_dashboard", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]]) if has_back else None
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.debug(f"cb_currency_convert error: {e}")

    await callback.answer(toast[:200] if toast else None)


async def get_stars_overview_text() -> str:
    sp = await _fetch_stars_price_live()
    ton_usd = sp["ton_usd"]
    usd_egp = sp["usd_egp"]
    buy_1000 = sp["buy"]
    buy_inapp_1000 = sp["buy_inapp"]
    wd_1000 = sp["withdraw"]

    p100_usd = (100 / 1000) * buy_1000
    p100_egp = p100_usd * usd_egp
    p100_ton = p100_usd / ton_usd if ton_usd else 0

    p500_usd = (500 / 1000) * buy_1000
    p500_egp = p500_usd * usd_egp
    p500_ton = p500_usd / ton_usd if ton_usd else 0

    p1000_usd = buy_1000
    p1000_egp = p1000_usd * usd_egp
    p1000_ton = p1000_usd / ton_usd if ton_usd else 0

    inapp_egp = buy_inapp_1000 * usd_egp

    wd_usd = wd_1000
    wd_egp = wd_usd * usd_egp
    wd_ton = wd_usd / ton_usd if ton_usd else 0

    src_icon = "🟢" if sp["source"] not in ("config", "fallback") else "🟡"

    return (
        f"<blockquote>⭐ <b>أسعار نجوم تيليجرام (Telegram Stars)</b>\n\n"
        f"🛒 <b>شراء مباشر (Fragment / TON):</b>\n"
        f"• 100 ⭐ = <b>${p100_usd:.2f}</b> (~{p100_egp:.0f} EGP | {p100_ton:.2f} TON)\n"
        f"• 500 ⭐ = <b>${p500_usd:.2f}</b> (~{p500_egp:.0f} EGP | {p500_ton:.2f} TON)\n"
        f"• 1,000 ⭐ = <b>${p1000_usd:.2f}</b> (~{p1000_egp:.0f} EGP | {p1000_ton:.2f} TON)\n\n"
        f"📱 <b>شراء من التطبيق (Store / بطاقة):</b>\n"
        f"• 1,000 ⭐ = <b>${buy_inapp_1000:.2f}</b> (~{inapp_egp:.0f} EGP)\n\n"
        f"💸 <b>سحب أرباح النجوم للمنشئين (Withdrawal):</b>\n"
        f"• 1,000 ⭐ = <b>${wd_usd:.2f}</b> (~{wd_egp:.0f} EGP | {wd_ton:.2f} TON)\n\n"
        f"💱 <b>أسعار الصرف الحية:</b>\n"
        f"• 1 USD = <b>{usd_egp:.2f} EGP</b> | 1 TON = <b>${ton_usd:.2f}</b>\n"
        f"{src_icon} <i>محدّث لحظياً</i> | 🕐 {sp['updated_at']}\n\n"
        f"💡 <i>أرسل أي رقم لحسابه، مثال: <code>500 نجمة</code> أو <code>2500 نجمة</code></i></blockquote>"
    )


async def get_stars_calc_text(q: int) -> str:
    sp = await _fetch_stars_price_live()
    ton_usd = sp["ton_usd"]
    usd_egp = sp["usd_egp"]

    buy_usd = (q / 1000) * sp["buy"]
    buy_egp = buy_usd * usd_egp
    buy_ton = buy_usd / ton_usd if ton_usd else 0

    inapp_usd = (q / 1000) * sp["buy_inapp"]
    inapp_egp = inapp_usd * usd_egp

    wd_usd = (q / 1000) * sp["withdraw"]
    wd_egp = wd_usd * usd_egp
    wd_ton = wd_usd / ton_usd if ton_usd else 0

    return (
        f"<blockquote>⭐ <b>سعر {q:,} نجمة تيليجرام:</b>\n\n"
        f"🛒 <b>شراء مباشر (Fragment / TON):</b>\n"
        f"💵 <b>${buy_usd:.2f}</b> | 🇪🇬 <b>{buy_egp:.2f} EGP</b> | 💎 <b>{buy_ton:.2f} TON</b>\n\n"
        f"📱 <b>شراء من التطبيق (Store):</b>\n"
        f"💵 <b>${inapp_usd:.2f}</b> | 🇪🇬 <b>{inapp_egp:.2f} EGP</b>\n\n"
        f"💸 <b>سحب أرباح المنشئين (Withdrawal):</b>\n"
        f"💵 <b>${wd_usd:.2f}</b> | 🇪🇬 <b>{wd_egp:.2f} EGP</b> | 💎 <b>{wd_ton:.2f} TON</b>\n\n"
        f"💱 1 USD = {usd_egp:.2f} EGP | 1 TON = ${ton_usd:.2f}</blockquote>"
    )


def get_stars_overview_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("شراء 1000 نجمة", callback_data="stars_buy_1000", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["diamond"]),
         make_btn("سحب 1000 نجمة", callback_data="stars_withdraw_1000", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bolt"])],
        [make_btn("100 نجمة", callback_data="stars_calc_100", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["star"]),
         make_btn("500 نجمة", callback_data="stars_calc_500", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["star"]),
         make_btn("2,500 نجمة", callback_data="stars_calc_2500", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["star"]),
         make_btn("5,000 نجمة", callback_data="stars_calc_5000", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["star"])],
        [make_btn("فتح منصة Fragment", url="https://fragment.com/stars", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["link"]),
         make_btn("تحديث الأسعار", callback_data="stars_refresh", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bolt"])],
        [make_btn("رجوع للقائمة الرئيسية", callback_data="main_dashboard", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
    ])


def get_stars_calc_kb(q: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [make_btn(f"تفاصيل شراء {q:,}", callback_data=f"stars_buy_{q}", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["diamond"]),
         make_btn(f"تفاصيل سحب {q:,}", callback_data=f"stars_withdraw_{q}", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bolt"])],
        [make_btn("فتح Fragment", url="https://fragment.com/stars", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["link"]),
         make_btn("كل الأسعار", callback_data="sec_stars", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
    ])


@main_router.callback_query(F.data == "sec_stars")
async def cb_sec_stars(callback: CallbackQuery):
    text = await get_stars_overview_text()
    kb = get_stars_overview_kb()
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise
    await callback.answer()


@main_router.callback_query(F.data.startswith("stars_calc_"))
async def cb_stars_calc(callback: CallbackQuery):
    await callback.answer()
    try:
        q = int(callback.data.split("_")[2])
    except Exception:
        q = 1000
    text = await get_stars_calc_text(q)
    kb = get_stars_calc_kb(q)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


@main_router.callback_query(F.data.startswith("stars_buy_"))
async def cb_stars_buy(callback: CallbackQuery):
    await callback.answer()
    try:
        q = int(callback.data.split("_")[2])
    except Exception:
        q = 1000
    sp = await _fetch_stars_price_live()
    ton_usd = sp["ton_usd"]
    usd_egp = sp["usd_egp"]
    pu_usd = (q / 1000) * sp["buy"]
    pu_egp = pu_usd * usd_egp
    pu_ton = pu_usd / ton_usd if ton_usd else 0
    inapp_usd = (q / 1000) * sp["buy_inapp"]
    inapp_egp = inapp_usd * usd_egp

    text = (
        f"<blockquote>{CE.DIAMOND} <b>شراء {q:,} نجمة تيليجرام ⭐</b></blockquote>\n\n"
        f"<blockquote>💎 <b>عبر منصة Fragment الرسمية:</b>\n"
        f"💵 <b>${pu_usd:.2f}</b>\n"
        f"🇪🇬 <b>{pu_egp:.2f} EGP</b>\n"
        f"💎 <b>{pu_ton:.2f} TON</b>\n\n"
        f"📱 <b>من داخل التطبيق (App Store / Play):</b>\n"
        f"💵 <b>${inapp_usd:.2f}</b> (~{inapp_egp:.0f} EGP)\n\n"
        f"🔗 <i>اضغط على الزر لشراء النجوم مباشرة عبر محفظتك</i></blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("شراء الآن من Fragment", url="https://fragment.com/stars", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["diamond"])],
        [make_btn("رجوع لقائمة النجوم", callback_data="sec_stars", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


@main_router.callback_query(F.data.startswith("stars_withdraw_"))
async def cb_stars_withdraw(callback: CallbackQuery):
    await callback.answer()
    try:
        q = int(callback.data.split("_")[2])
    except Exception:
        q = 1000
    sp = await _fetch_stars_price_live()
    ton_usd = sp["ton_usd"]
    usd_egp = sp["usd_egp"]
    wd_usd = (q / 1000) * sp["withdraw"]
    wd_egp = wd_usd * usd_egp
    wd_ton = wd_usd / ton_usd if ton_usd else 0

    text = (
        f"<blockquote>{CE.BOLT} <b>سحب أرباح {q:,} نجمة للمنشئين 💸</b></blockquote>\n\n"
        f"<blockquote>💵 القيمة بالدولار: <b>${wd_usd:.2f}</b>\n"
        f"🇪🇬 القيمة بالجنيه: <b>{wd_egp:.2f} EGP</b>\n"
        f"💎 ما يعادله بالـ TON: <b>{wd_ton:.2f} TON</b>\n\n"
        f"📌 <i>يتم سحب النجوم عبر منصة Fragment بعد <b>21 يوماً</b> من استلامها</i>\n"
        f"<tg-spoiler>الحد الأدنى للسحب: 1,000 نجمة</tg-spoiler></blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("سحب عبر Fragment", url="https://fragment.com/stars", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["link"])],
        [make_btn("رجوع لقائمة النجوم", callback_data="sec_stars", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


@main_router.callback_query(F.data == "stars_refresh")
async def cb_stars_refresh(callback: CallbackQuery):
    """تحديث سعر النجوم بالإجبار — يمسح الكاش"""
    global _stars_live_cache
    _stars_live_cache = {}
    CurrencyCache._last = None
    await callback.answer("🔄 يتم تحديث الأسعار لحظياً...", show_alert=False)
    text = await get_stars_overview_text()
    kb = get_stars_overview_kb()
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


@main_router.callback_query(F.data.startswith("stars_back_"))
async def cb_stars_back(callback: CallbackQuery):
    await callback.answer()
    try:
        q = int(callback.data.split("_")[2])
    except Exception:
        q = 1000
    text = await get_stars_calc_text(q)
    kb = get_stars_calc_kb(q)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


async def get_telegram_premium_overview() -> str:
    usd_egp = 52.0
    ton_usd = 1.44
    try:
        d = await CurrencyCache.get_rates()
        if isinstance(d, dict):
            rates = d.get("rates", {})
            ton_usd = float(rates.get("ton_usd", 1.44))
            usd_egp = float(rates.get("usd_egp", 52.0))
    except Exception:
        pass

    p3_usd = 11.99
    p3_ton = 8.32
    p3_egp = round(p3_usd * usd_egp)

    p6_usd = 15.99
    p6_ton = 11.10
    p6_egp = round(p6_usd * usd_egp)

    p12_usd = 28.99
    p12_ton = 20.13
    p12_egp = round(p12_usd * usd_egp)

    p1_usd = 4.99
    p1_egp = round(p1_usd * usd_egp)

    text = (
        f"<blockquote>{CE.DIAMOND} <b>أسعار اشتراكات تيليجرام بريميوم الرسمية (Telegram Premium)</b></blockquote>\n\n"
        f"<blockquote>⚡ <b>الأسعار عبر منصة Fragment (رسمية ومخفضة):</b>\n\n"
        f"🌟 <b>اشتراك 3 شهور:</b>\n"
        f"• السعر: <code>{p3_usd}$</code> (<b>{p3_ton} TON</b>)\n"
        f"• بالجنيه المصري: <code>{p3_egp:,} EGP</code> تقريباً\n\n"
        f"🌟 <b>اشتراك 6 شهور (خصم 47%):</b>\n"
        f"• السعر: <code>{p6_usd}$</code> (<b>{p6_ton} TON</b>)\n"
        f"• بالجنيه المصري: <code>{p6_egp:,} EGP</code> تقريباً\n\n"
        f"🌟 <b>اشتراك سنة كاملة 12 شهر (خصم 52%):</b>\n"
        f"• السعر: <code>{p12_usd}$</code> (<b>{p12_ton} TON</b>)\n"
        f"• بالجنيه المصري: <code>{p12_egp:,} EGP</code> تقريباً\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"📱 <i>اشتراك شهر واحد (عبر التطبيق Google Play / App Store):</i> <code>{p1_usd}$</code> (~<code>{p1_egp:,} EGP</code>)\n"
        f"🔗 <i>شراء مباشر:</i> <a href='https://fragment.com/premium/gift'>fragment.com/premium/gift</a></blockquote>"
    )
    return text


def get_premium_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("شراء بريميوم عبر Fragment", url="https://fragment.com/premium/gift", style="primary",
                  icon_custom_emoji_id=CUSTOM_EMOJI_IDS["diamond"])],
        [make_btn("رجوع للقائمة الرئيسية", callback_data="sec_security", style="danger",
                  icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
    ])


@main_router.callback_query(F.data == "sec_premium")
async def cb_sec_premium(callback: CallbackQuery):
    text = await get_telegram_premium_overview()
    await callback.message.edit_text(text, reply_markup=get_premium_kb(), parse_mode=ParseMode.HTML)
    await callback.answer()


def extract_cash_transfer_data(text: str):
    if not text:
        return None
    text = text.strip()
    ar = "٠١٢٣٤٥٦٧٨٩"
    en = "0123456789"
    text = text.translate(str.maketrans({ar[i]: en[i] for i in range(10)}))
    tokens = re.sub(r"[,;/_|]+", " ", text).split()
    phone = None
    pi = -1
    for i, t in enumerate(tokens):
        ct = re.sub(r"[^\d+]", "", t)
        m = re.search(r"(?:(?:\+|00)?20)?(01[0125]\d{8})$", ct)
        if m:
            phone = m.group(1); pi = i; break
    if not phone:
        return None
    amount = None
    for i, t in enumerate(tokens):
        if i == pi:
            continue
        cv = re.sub(r"[^\d.]", "", t)
        if not cv:
            continue
        try:
            v = float(cv)
            if 0 < v <= 1000000 and len(cv) <= 7 and not (len(cv) >= 10 and cv.startswith("01")):
                amount = str(int(v)) if v.is_integer() else str(v)
                break
        except Exception:
            continue
    if not amount:
        return None
    return phone, amount


def _cash_codes(p, a):
    return (f"*9*7*{p}*{a}#", f"#7115*1*1*1*{p}*{a}#", f"*777*1*{p}*{a}#")


def get_cash_codes_kb(p, a):
    v, o, e = _cash_codes(p, a)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="فودافون كاش", copy_text=CopyTextButton(text=v),
                              style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["shield"])],
        [InlineKeyboardButton(text="أورنج كاش", copy_text=CopyTextButton(text=o),
                              style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["diamond"])],
        [InlineKeyboardButton(text="إتصالات كاش", copy_text=CopyTextButton(text=e),
                              style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["check_verified"])]])


def get_cash_intro_text(p, a):
    return (f"<blockquote>{CE.BOLT} <b>أكواد التحويل للرقم 📱</b>\n<code>{html.quote(p)}</code></blockquote>\n\n"
            f"<blockquote>💵 <b>المبلغ: {html.quote(a)} جنيه مصري</b>\n\n"
            f"⬇️ <i>اضغط على الزر لنسخ الكود المناسب لشبكتك</i></blockquote>")


@main_router.callback_query(F.data == "sec_cash")
async def cb_sec_cash(callback: CallbackQuery):
    async with async_session() as session:
        res = await session.execute(select(CashService))
        w = res.scalars().all()
    text = (f"<blockquote>{CE.BOLT} <b>أكواد الكاش الفورية 💳</b></blockquote>\n\n"
            f"<blockquote>📤 <b>طريقة الاستخدام:</b>\n"
            f"أرسل رقم الهاتف والمبلغ معاً\n"
            f"• مثال: <code>01010101010 100</code>\n\n"
            f"🔴 فودافون كاش · 🟠 أورنج كاش · 🟢 إتصالات كاش\n\n"
            f"<tg-spoiler>الأكواد تنتهي بعد فترة — تأكد من إدخال البيانات الصحيحة</tg-spoiler></blockquote>")
    if w:
        text += "\n\n<b>📂 المحافظ المسجلة:</b>\n"
        for x in w:
            text += f"• {html.quote(x.name)}: <code>{x.wallet_number}</code>\n"
    await callback.message.edit_text(text, reply_markup=get_back_kb(), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data == "sec_downloader")
async def cb_sec_downloader(callback: CallbackQuery):
    text = (
        f"<blockquote>{CE.DOWNLOAD} <b>أداة تحميل الفيديوهات والوسائط</b></blockquote>\n\n"
        f"<blockquote>⚡ <b>المنصات المدعومة:</b>\n"
        f"• <b>TikTok</b> · <b>Instagram</b> · <b>Facebook</b> · <b>YouTube & Shorts</b> · <b>Twitter (X)</b> · <b>Pinterest</b> · <b>Snapchat</b> · <b>Reddit</b>\n\n"
        f"📥 <b>طرق الاستخدام المدعومة:</b>\n"
        f"• إرسال رابط الفيديو مباشرة في المحادثة\n"
        f"• كتابة الأمر مع الرابط: <code>.تحميل https://...</code>\n"
        f"• أو الرد على أي رسالة بها رابط بـ <code>.تحميل</code> أو <code>.تنزيل</code>\n\n"
        f"{CE.SHIELD} <b>الجودة:</b> <tg-spoiler>يتم جلب الفيديو بأعلى دقة متوفرة وبدون علامة مائية (حتى 50MB)</tg-spoiler></blockquote>"
    )
    await callback.message.edit_text(text, reply_markup=get_back_kb(), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data == "sec_inspect")
async def cb_sec_inspect(callback: CallbackQuery):
    text = (
        f"<blockquote>{CE.USER} <b>أداة كشف الحسابات والمعلومات</b></blockquote>\n\n"
        f"<blockquote>🔍 <b>طريقة الاستخدام:</b>\n"
        f"• أرسل يوزر الحساب: <code>@username</code>\n"
        f"• أو أرسل آيدي الحساب مباشرة: <code>123456789</code>\n"
        f"• أو قم بالرد على رسالة المستخدم بـ <code>.كشف</code> أو <code>.ايدي</code>\n\n"
        f"📊 <b>البيانات المستخرجة:</b>\n"
        f"• الاسم واليوزر والآيدي ونوع الحساب وتاريخ الإنشاء التقريبي والصور والروابط\n\n"
        f"{CE.LOCK} <b>الحماية:</b> <tg-spoiler>فحص أمني متقدم ومطابقة القيود</tg-spoiler></blockquote>"
    )
    await callback.message.edit_text(text, reply_markup=get_back_kb(), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data == "sec_translate")
async def cb_sec_translate(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text = (
        f"<blockquote>{CE.GLOBE} <b>نظام الترجمة الفورية الذكي</b></blockquote>\n\n"
        f"<blockquote>⚡ <b>الترجمة السريعة في المحادثات:</b>\n"
        f"• قم بالرد على أي رسالة واكتب كود اللغة:\n"
        f"  - <code>ar</code> 🇸🇦 للعربية\n"
        f"  - <code>en</code> 🇺🇸 للإنجليزية\n"
        f"  - <code>fr</code> 🇫🇷 للفرنسية\n"
        f"  - <code>de</code> 🇩🇪 للألمانية\n"
        f"  - <code>es</code> 🇪🇸 للإسبانية\n"
        f"  - <code>tr</code> 🇹🇷 للتركية\n"
        f"  - <code>ru</code> 🇷🇺 للروسية\n\n"
        f"🔘 <i>أو اختر اللغة المستهدفة من القائمة أدناه للترجمة المباشرة:</i>\n\n"
        f"{CE.SHIELD} <b>الخصوصية:</b> <tg-spoiler>الترجمة مدعومة بمحرك ترجمة فوري ومعزول</tg-spoiler></blockquote>"
    )
    await callback.message.edit_text(text, reply_markup=get_translate_languages_kb(), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data.startswith("trlang_"))
async def cb_pick_tr_lang(callback: CallbackQuery, state: FSMContext):
    lc = callback.data.split("_")[1]
    _user_translate_lang[callback.from_user.id] = lc
    await state.set_state(TranslateFSM.waiting_for_text)
    await state.update_data(target_lang=lc)
    lt = TRANSLATE_LANGUAGES.get(f"tr_{lc}", (lc, lc))[0]
    await callback.message.edit_text(
        f"<blockquote>{CE.GLOBE} <b>اللغة المختارة:</b> <b>{lt}</b>\n\n"
        f"✏️ <i>أرسل النص المطلوب ترجمته الآن مباشرة:</i></blockquote>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("تغيير اللغة", callback_data="sec_translate", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["gear"])],
            [make_btn("رجوع", callback_data="main_dashboard", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
        ]), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.message(TranslateFSM.waiting_for_text, F.text)
async def fsm_translate_text(message: Message, state: FSMContext):
    if message.text.startswith("/"):
        await state.clear()
        return
    data = await state.get_data()
    tl = data.get("target_lang") or _user_translate_lang.get(message.from_user.id, "ar")
    lt = TRANSLATE_LANGUAGES.get(f"tr_{tl}", (tl, tl))[0]
    tr = await translate_text_async(message.text, tl)
    if tr:
        await message.reply(
            f"<blockquote>{CE.GLOBE} <b>الترجمة إلى ({lt}):</b></blockquote>\n\n"
            f"<blockquote>{html.quote(tr)}</blockquote>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [make_btn("ترجمة نص آخر", callback_data=f"trlang_{tl}", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["pencil"])],
                [make_btn("رجوع للقائمة الرئيسية", callback_data="main_dashboard", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
            ]),
            parse_mode=ParseMode.HTML)
    else:
        await message.reply(
            f"<blockquote>{CE.CROSS_BAN} <b>تعذرت الترجمة حالياً، يرجى المحاولة لاحقاً.</b></blockquote>",
            parse_mode=ParseMode.HTML)


@main_router.callback_query(F.data == "sec_scheduled")
async def cb_sec_scheduled(callback: CallbackQuery):
    async with async_session() as session:
        res = await session.execute(select(ScheduledMessage).where(
            ScheduledMessage.created_by_user_id == callback.from_user.id,
            ScheduledMessage.is_sent == False,
            ScheduledMessage.is_active == True))
        t = res.scalars().all()
    kb = [[make_btn("إضافة رسالة مجدولة", callback_data="sched_add", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["plus"])],
          [make_btn("رجوع للقائمة الرئيسية", callback_data="main_dashboard", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]]
    await callback.message.edit_text(
        f"<blockquote>{CE.CLOCK} <b>إدارة الرسائل المجدولة والتذكيرات</b></blockquote>\n\n"
        f"<blockquote>📊 <b>الرسائل النشطة حالياً:</b> <code>{len(t)}</code> رسالة\n\n"
        f"⏰ يمكنك جدولة إرسال أي نص أو وسائط في وقت وتاريخ محدد بدقة.\n\n"
        f"{CE.SHIELD} <b>الدقة:</b> <tg-spoiler>إرسال تلقائي فوري فور حلول الموعد المحدد</tg-spoiler></blockquote>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data == "sec_gifts")
async def cb_sec_gifts(callback: CallbackQuery, bot: Bot):
    text = (
        f"<blockquote>{CE.STAR} <b>أسعار وقيم هدايا تيليجرام (Telegram Gifts)</b></blockquote>\n\n"
        f"<blockquote>⚡ <b>طريقة الاستخدام:</b>\n"
        f"• <code>.سعر + رابط أو اسم الهدية</code>\n"
        f"• مثال: <code>.سعر t.me/nft/Artisan-Brick</code>\n"
        f"• مثال: <code>.سعر Artisan Brick</code>\n\n"
        f"📊 <b>مصادر الأسعار الحية:</b>\n"
        f"• TGMrkt · GetGems · Fragment Market\n\n"
        f"📦 <b>حالة نظام الفحص:</b> {'✅ جاهز ويعمل بكفاءة' if _GIFTS_LIB else '⚠️ يعمل بالمحرك الاحتياطي'}\n\n"
        f"{CE.DIAMOND} <b>المعلومات:</b> <tg-spoiler>يعرض Floor Price و Last Sale وأعلى عرض شراء لحظياً</tg-spoiler></blockquote>"
    )
    await callback.message.edit_text(text, reply_markup=get_back_kb(), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data == "sec_pin")
async def cb_sec_pin(callback: CallbackQuery):
    text = (
        f"<blockquote>{CE.PIN} <b>تثبيت وإلغاء تثبيت الرسائل السريع</b></blockquote>\n\n"
        f"<blockquote>📌 <b>الأوامر المتاحة:</b>\n"
        f"• قم بالرد على أي رسالة واكتب: <code>.ث</code> أو <code>تثبيت</code> لتثبيتها فوراً\n"
        f"• قم بالرد على أي رسالة واكتب: <code>.غ ث</code> أو <code>الغاء التثبيت</code> لإلغاء التثبيت\n\n"
        f"{CE.SHIELD} <b>الصلاحيات:</b> <tg-spoiler>يتطلب أن يكون البوت مشرفاً بصلاحية تثبيت الرسائل في المجموعة</tg-spoiler></blockquote>"
    )
    await callback.message.edit_text(text, reply_markup=get_back_kb(), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data == "sec_settings")
async def cb_sec_settings(callback: CallbackQuery):
    ai_status = []
    if GEMINI_API_KEY:
        ai_status.append("Gemini 🟢" if GEMINI_API_KEY.startswith("AIzaSy") else "Gemini ⚠️")
    if OPENAI_API_KEY:
        ai_status.append("OpenAI 🟢")
    if not ai_status:
        ai_status.append("غير مفعل 🔴")
    text = (
        f"<blockquote>{CE.GEAR} <b>إعدادات ومعلومات النظام</b></blockquote>\n\n"
        f"<blockquote>🤖 <b>معرف البوت:</b> <code>{BOT_USERNAME}</code>\n"
        f"👑 <b>معرف المطور:</b> <code>{OWNER_ID}</code>\n"
        f"⏳ <b>الفترة التجريبية:</b> <code>{FREE_TRIAL_HOURS} ساعة</code>\n"
        f"🧠 <b>محركات الذكاء الاصطناعي:</b> {' | '.join(ai_status)}\n"
        f"🎁 <b>مكتبة أسعار الهدايا:</b> {'✅ متصلة' if _GIFTS_LIB else '⚠️ محرك احتياطي'}\n"
        f"📞 <b>حساب الدعم الفني:</b> <b>{SUPPORT_USERNAME}</b>\n\n"
        f"{CE.LOCK} <b>الأمان:</b> <tg-spoiler>جميع البيانات والتوكنات مشفرة بنظام Fernet 256-bit</tg-spoiler></blockquote>"
    )
    await callback.message.edit_text(text, reply_markup=get_back_kb(), parse_mode=ParseMode.HTML)
    await callback.answer()


# =============================================================================
# 24. لوحة إدارة الاشتراكات + الإذاعة
# =============================================================================
@main_router.callback_query(F.data == "admin_sub_panel")
async def cb_admin_sub_panel(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("⛔ للمطور فقط.", show_alert=True)
    text = (
        f"<blockquote>{CE.CROWN} <b>لوحة إدارة البوت الكاملة</b></blockquote>\n\n"
        f"<blockquote>👑 <i>أهلاً بك يا مالك البوت.</i>\n"
        f"{CE.GEAR} <b>من هنا تتحكم في كل شيء:</b>\n"
        f"• إدارة الاشتراكات (تفعيل / إنهاء / عرض)\n"
        f"• الاشتراك الإجباري في البوت\n"
        f"• تغيير ساعات الفترة التجريبية\n"
        f"• إذاعة رسائل لجميع المستخدمين\n"
        f"• إهداء وقت اشتراك مجاني للكل\n"
        f"• إعدادات ومعلومات النظام</blockquote>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=get_admin_panel_kb(), parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await callback.answer()


@main_router.callback_query(F.data == "adm_add_sub")
async def cb_adm_add_sub(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("⛔", show_alert=True)
    await state.set_state(AddSubscriptionFSM.waiting_for_user_id)
    text = (
        f"<blockquote>{CE.PLUS} <b>تفعيل اشتراك مستخدم جديد</b></blockquote>\n\n"
        f"<blockquote>👤 <b>أرسل الآيدي الرقمي (User ID) الخاص بالمستخدم:</b>\n"
        f"• مثال: <code>123456789</code></blockquote>"
    )
    await callback.message.edit_text(text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("إلغاء العملية", callback_data="admin_sub_panel", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_ban"])]]),
        parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.message(AddSubscriptionFSM.waiting_for_user_id)
async def fsm_sub_user_id(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    v = message.text.strip()
    if not v.isdigit():
        return await message.answer(f"<blockquote>{CE.CROSS_BAN} <b>خطأ: الآيدي يجب أن يتكون من أرقام فقط!</b></blockquote>", parse_mode=ParseMode.HTML)
    await state.update_data(target_id=int(v))
    await state.set_state(AddSubscriptionFSM.waiting_for_days)
    await message.answer(
        f"<blockquote>{CE.CLOCK} <b>تحديد مدة الاشتراك</b></blockquote>\n\n"
        f"<blockquote>📅 <b>أرسل عدد الأيام المطلوبة:</b>\n"
        f"• مثال: <code>30</code> لشهر واحد\n"
        f"• مثال: <code>365</code> لسنة كاملة</blockquote>",
        parse_mode=ParseMode.HTML)


@main_router.message(AddSubscriptionFSM.waiting_for_days)
async def fsm_sub_days(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id != OWNER_ID:
        return
    v = message.text.strip()
    if not v.isdigit() or int(v) <= 0:
        return await message.answer(f"<blockquote>{CE.CROSS_BAN} <b>خطأ: يرجى إدخال عدد صحيح موجب من الأيام.</b></blockquote>", parse_mode=ParseMode.HTML)
    days = int(v)
    data = await state.get_data()
    tid = data["target_id"]
    await state.clear()
    now = datetime.datetime.utcnow()
    async with async_session() as session:
        user = await session.get(User, tid)
        if not user:
            user = User(id=tid, full_name="مشترك", role="customer",
                        trial_expires_at=now,
                        subscription_expires_at=now + datetime.timedelta(days=days),
                        is_active=True)
            session.add(user)
        else:
            cur = user.subscription_expires_at if (user.subscription_expires_at and user.subscription_expires_at > now) else now
            user.subscription_expires_at = cur + datetime.timedelta(days=days)
            user.is_active = True
        await session.commit()
        await session.execute(delete(ServiceSetting).where(
            ServiceSetting.user_id == tid, ServiceSetting.service_key == "expiry_notified"))
        await session.commit()
        end = user.subscription_expires_at.strftime("%Y-%m-%d %H:%M UTC")
    try:
        user_notify_text = (
            f"<blockquote>{CE.CHECK_VERIFIED} <b>تهانينا! تم تفعيل اشتراكك بنجاح</b></blockquote>\n\n"
            f"<blockquote>📅 <b>صالح حتى:</b> <code>{end}</code>\n"
            f"⚡ <b>المدة المضافة:</b> <code>{days} يوم</code>\n\n"
            f"✅ البوت يعمل الآن بكامل طاقته وصلاحياته على حسابك!</blockquote>"
        )
        await bot.send_message(tid, user_notify_text, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    confirm_text = (
        f"<blockquote>{CE.CHECK_VERIFIED} <b>تم تفعيل الاشتراك بنجاح</b></blockquote>\n\n"
        f"<blockquote>👤 <b>المستخدم:</b> <code>{tid}</code>\n"
        f"📅 <b>صالح حتى:</b> <code>{end}</code>\n"
        f"⏳ <b>المدة:</b> <code>{days} يوم</code></blockquote>"
    )
    await message.answer(confirm_text, reply_markup=get_admin_panel_kb(), parse_mode=ParseMode.HTML)


@main_router.callback_query(F.data == "adm_end_sub")
async def cb_adm_end_sub(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("⛔", show_alert=True)
    await state.set_state(EndSubscriptionFSM.waiting_for_user_id)
    text = (
        f"<blockquote>{CE.CROSS_BAN} <b>إنهاء اشتراك مستخدم</b></blockquote>\n\n"
        f"<blockquote>⚠️ <b>أرسل الآيدي الرقمي (User ID) للمستخدم المطلوب إنهاء اشتراكه:</b></blockquote>"
    )
    await callback.message.edit_text(text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("إلغاء العملية", callback_data="admin_sub_panel", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]]),
        parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.message(EndSubscriptionFSM.waiting_for_user_id)
async def fsm_end_sub_user_id(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    v = message.text.strip()
    if not v.isdigit():
        return await message.answer(f"<blockquote>{CE.CROSS_BAN} <b>خطأ: يرجى إدخال آيدي صحيح.</b></blockquote>", parse_mode=ParseMode.HTML)
    tid = int(v)
    await state.clear()
    now = datetime.datetime.utcnow()
    async with async_session() as session:
        u = await session.get(User, tid)
        if not u:
            return await message.answer(f"<blockquote>{CE.CROSS_BAN} <b>المستخدم غير موجود في قاعدة البيانات.</b></blockquote>", parse_mode=ParseMode.HTML)
        u.subscription_expires_at = now - datetime.timedelta(minutes=1)
        u.trial_expires_at = now - datetime.timedelta(minutes=1)
        await session.commit()
    await message.answer(
        f"<blockquote>{CE.CROSS_BAN} <b>تم إنهاء الاشتراك بنجاح</b></blockquote>\n\n"
        f"<blockquote>🛑 تم إيقاف الاشتراك والحساب للمستخدم: <code>{tid}</code></blockquote>",
        reply_markup=get_admin_panel_kb(), parse_mode=ParseMode.HTML)


@main_router.callback_query(F.data == "adm_list_subs")
async def cb_adm_list_subs(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("⛔", show_alert=True)
    now = datetime.datetime.utcnow()
    async with async_session() as session:
        res = await session.execute(select(User).order_by(User.created_at.desc()).limit(20))
        us = res.scalars().all()
    text = f"<blockquote>{CE.INFO} <b>قائمة آخر 20 مستخدم مسجل</b></blockquote>\n\n<blockquote>"
    for u in us:
        st = "🔴 منتهي"
        if u.role in ["owner", "admin"]:
            st = "👑 مالك/مشرف"
        elif u.subscription_expires_at and u.subscription_expires_at > now:
            st = f"🟢 متبقي {(u.subscription_expires_at-now).days}ي"
        elif u.trial_expires_at and u.trial_expires_at > now:
            st = f"⏳ تجريبي {int((u.trial_expires_at-now).total_seconds()//3600)}س"
        text += f"• <code>{u.id}</code> | {html.quote(u.full_name[:15])} | <b>{st}</b>\n"
    text += "</blockquote>"
    await callback.message.edit_text(text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("رجوع للوحة الإدارة", callback_data="admin_sub_panel", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]]), parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data == "adm_check_sub")
async def cb_adm_check_sub(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("⛔", show_alert=True)
    await state.set_state(EndSubscriptionFSM.waiting_for_user_id)
    text = (
        f"<blockquote>{CE.INFO} <b>فحص اشتراك مستخدم</b></blockquote>\n\n"
        f"<blockquote>🔎 <b>أرسل الآيدي الرقمي (User ID) لفحص حالته:</b></blockquote>"
    )
    await callback.message.edit_text(text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("إلغاء", callback_data="admin_sub_panel", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_ban"])]]),
        parse_mode=ParseMode.HTML)
    await callback.answer()


# =============================================================================
# تغيير ساعات الفترة التجريبية المجانية
# =============================================================================
@main_router.callback_query(F.data == "adm_trial_hours")
async def cb_adm_trial_hours(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("⛔", show_alert=True)
    await state.set_state(TrialHoursFSM.waiting_for_hours)
    text = (
        f"<blockquote>{CE.CLOCK} <b>تغيير ساعات الفترة التجريبية المجانية</b></blockquote>\n\n"
        f"<blockquote>⏳ <b>الإعداد الحالي:</b> <code>{FREE_TRIAL_HOURS} ساعة</code>\n\n"
        f"📝 <b>أرسل العدد الجديد للساعات:</b>\n"
        f"• مثال: <code>48</code> لتجربة مجانية مدتها يومان\n"
        f"• مثال: <code>0</code> لإلغاء الفترة التجريبية تماماً\n"
        f"• أقصى قيمة: <code>8760</code> (سنة كاملة)</blockquote>"
    )
    await callback.message.edit_text(text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("إلغاء العملية", callback_data="admin_sub_panel", style="danger",
                      icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_ban"])]]),
        parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.message(TrialHoursFSM.waiting_for_hours)
async def fsm_trial_hours(message: Message, state: FSMContext):
    global FREE_TRIAL_HOURS
    if message.from_user.id != OWNER_ID:
        return
    v = message.text.strip()
    if not v.isdigit() or int(v) > 8760:
        return await message.answer(
            f"<blockquote>{CE.CROSS_BAN} <b>خطأ: أرسل رقماً صحيحاً بين 0 و 8760.</b></blockquote>",
            parse_mode=ParseMode.HTML)
    hours = int(v)
    old = FREE_TRIAL_HOURS
    FREE_TRIAL_HOURS = hours
    await state.clear()
    # تحديث الـ .env
    try:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                content = f.read()
            import re as _re
            new_content = _re.sub(
                r"(FREE_TRIAL_HOURS\s*=\s*)\d+",
                f"FREE_TRIAL_HOURS={hours}",
                content
            )
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(new_content)
    except Exception as e:
        logger.warning(f"[TRIAL_HOURS] Failed to update .env: {e}")
    await message.answer(
        f"<blockquote>{CE.CHECK_VERIFIED} <b>تم تحديث الفترة التجريبية بنجاح</b></blockquote>\n\n"
        f"<blockquote>⏳ <b>القيمة القديمة:</b> <code>{old} ساعة</code>\n"
        f"✅ <b>القيمة الجديدة:</b> <code>{hours} ساعة</code>\n\n"
        f"⚡ <i>التغيير سيُطبق على المستخدمين الجدد فقط عند تسجيلهم لأول مرة.</i></blockquote>",
        reply_markup=get_admin_panel_kb(), parse_mode=ParseMode.HTML)


# =============================================================================
# الاشتراك الإجباري في البوت نفسه (يُطبق على /start لكل المستخدمين)
# =============================================================================
async def _get_bot_mandatory_sub() -> Optional["BotMandatorySubscription"]:
    """جلب إعدادات الاشتراك الإجباري في البوت من قاعدة البيانات مع كاش"""
    global _bot_mandatory_sub_cache
    now = datetime.datetime.utcnow()
    if _bot_mandatory_sub_cache:
        ts = _bot_mandatory_sub_cache.get("_ts")
        if ts and (now - ts).total_seconds() < _BOT_SUB_CACHE_TTL:
            return _bot_mandatory_sub_cache.get("obj")
    async with async_session() as session:
        res = await session.execute(select(BotMandatorySubscription).limit(1))
        obj = res.scalar_one_or_none()
    _bot_mandatory_sub_cache = {"obj": obj, "_ts": now}
    return obj


def _invalidate_bot_sub_cache():
    global _bot_mandatory_sub_cache
    _bot_mandatory_sub_cache = None


async def _check_bot_channel_sub(bot: Bot, user_id: int, sub) -> bool:
    """التحقق من اشتراك المستخدم في قناة البوت"""
    if not sub or not sub.channel_id or not sub.is_enabled:
        return True
    try:
        m = await bot.get_chat_member(chat_id=sub.channel_id, user_id=user_id)
        return m.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR,
                            ChatMemberStatus.CREATOR, ChatMemberStatus.RESTRICTED)
    except Exception:
        return True


@main_router.callback_query(F.data == "adm_bot_sub")
async def cb_adm_bot_sub(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("⛔", show_alert=True)
    sub = await _get_bot_mandatory_sub()
    is_enabled = bool(sub and sub.is_enabled and sub.channel_id)
    channel_info = "لم يُحدد بعد"
    if sub and sub.channel_id:
        ch = f"@{sub.channel_username}" if sub.channel_username else str(sub.channel_id)
        title = sub.channel_title or ch
        channel_info = f"{title} ({ch})"
    status_str = "🟢 مفعّل" if is_enabled else "🔴 معطّل"
    text = (
        f"<blockquote>{CE.BOOK} <b>الاشتراك الإجباري في البوت</b></blockquote>\n\n"
        f"<blockquote>📢 <b>الحالة:</b> {status_str}\n"
        f"📌 <b>القناة المحددة:</b> <code>{channel_info}</code>\n\n"
        f"💡 <i>عند التفعيل — أي مستخدم يفتح البوت (/start) سيُطلب منه الاشتراك في القناة أولاً قبل الاستخدام.</i></blockquote>"
    )
    toggle_text = "🔴 تعطيل الاشتراك الإجباري" if is_enabled else "🟢 تفعيل الاشتراك الإجباري"
    toggle_cb = "adm_bot_sub_off" if is_enabled else "adm_bot_sub_on"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("تحديد / تغيير القناة", callback_data="adm_bot_sub_set", style="primary",
                  icon_custom_emoji_id=CUSTOM_EMOJI_IDS["link"])],
        [make_btn(toggle_text, callback_data=toggle_cb,
                  style="danger" if is_enabled else "success",
                  icon_custom_emoji_id=CUSTOM_EMOJI_IDS["ban"] if is_enabled else CUSTOM_EMOJI_IDS["check_verified"])],
        [make_btn("حذف إعداد القناة", callback_data="adm_bot_sub_del", style="danger",
                  icon_custom_emoji_id=CUSTOM_EMOJI_IDS["trash"])],
        [make_btn("رجوع للوحة الإدارة", callback_data="admin_sub_panel", style="danger",
                  icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])],
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await callback.answer()


@main_router.callback_query(F.data == "adm_bot_sub_set")
async def cb_adm_bot_sub_set(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("⛔", show_alert=True)
    await state.set_state(BotMandatorySubFSM.waiting_for_channel)
    text = (
        f"<blockquote>{CE.LINK} <b>تحديد قناة الاشتراك الإجباري للبوت</b></blockquote>\n\n"
        f"<blockquote>📢 <b>أرسل معرف القناة بأحد الأشكال التالية:</b>\n"
        f"• <code>@username</code> — مثال: <code>@mychannel</code>\n"
        f"• ID رقمي — مثال: <code>-1001234567890</code>\n\n"
        f"⚠️ <i>تأكد أن البوت مضاف كمشرف في القناة حتى يقدر يتحقق من الاشتراك.</i></blockquote>"
    )
    await callback.message.edit_text(text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("إلغاء", callback_data="adm_bot_sub", style="danger",
                      icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_ban"])]]),
        parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.message(BotMandatorySubFSM.waiting_for_channel)
async def fsm_bot_mandatory_sub_channel(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id != OWNER_ID:
        return
    raw = message.text.strip()
    await state.clear()
    channel_id = None
    channel_username = None
    channel_title = None
    if raw.lstrip("-").isdigit():
        channel_id = int(raw)
    elif raw.startswith("@"):
        channel_username = raw.lstrip("@")
    else:
        return await message.answer(
            f"<blockquote>{CE.CROSS_BAN} <b>خطأ: أرسل @username أو ID رقمي للقناة.</b></blockquote>",
            parse_mode=ParseMode.HTML)
    try:
        chat_obj = await bot.get_chat(raw)
        channel_id = chat_obj.id
        channel_username = chat_obj.username
        channel_title = chat_obj.title or chat_obj.username or str(channel_id)
    except Exception as e:
        if channel_id is None:
            return await message.answer(
                f"<blockquote>{CE.ALERT} <b>تعذّر الوصول للقناة:</b> <code>{html.quote(str(e))}</code>\n"
                f"تأكد أن البوت مشرف فيها وأن المعرف صحيح.</blockquote>",
                parse_mode=ParseMode.HTML)
    async with async_session() as session:
        res = await session.execute(select(BotMandatorySubscription).limit(1))
        obj = res.scalar_one_or_none()
        if obj:
            obj.channel_id = channel_id
            obj.channel_username = channel_username
            obj.channel_title = channel_title
            obj.updated_at = datetime.datetime.utcnow()
        else:
            obj = BotMandatorySubscription(
                channel_id=channel_id,
                channel_username=channel_username,
                channel_title=channel_title,
                is_enabled=False)
            session.add(obj)
        await session.commit()
    _invalidate_bot_sub_cache()
    ch_display = f"@{channel_username}" if channel_username else str(channel_id)
    await message.answer(
        f"<blockquote>{CE.CHECK_VERIFIED} <b>تم حفظ القناة بنجاح</b></blockquote>\n\n"
        f"<blockquote>📌 <b>القناة:</b> <code>{html.quote(channel_title or ch_display)}</code>\n"
        f"🔗 <b>المعرف:</b> <code>{ch_display}</code>\n\n"
        f"⚡ <i>اضغط على 'تفعيل الاشتراك الإجباري' من لوحة الأدمن لتشغيل الميزة.</i></blockquote>",
        reply_markup=get_admin_panel_kb(), parse_mode=ParseMode.HTML)


@main_router.callback_query(F.data == "adm_bot_sub_on")
async def cb_adm_bot_sub_on(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("⛔", show_alert=True)
    async with async_session() as session:
        res = await session.execute(select(BotMandatorySubscription).limit(1))
        obj = res.scalar_one_or_none()
        if not obj or not obj.channel_id:
            return await callback.answer("⚠️ حدد القناة أولاً قبل التفعيل!", show_alert=True)
        obj.is_enabled = True
        obj.updated_at = datetime.datetime.utcnow()
        await session.commit()
    _invalidate_bot_sub_cache()
    await callback.answer("✅ تم تفعيل الاشتراك الإجباري في البوت!", show_alert=True)
    await cb_adm_bot_sub(callback)


@main_router.callback_query(F.data == "adm_bot_sub_off")
async def cb_adm_bot_sub_off(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("⛔", show_alert=True)
    async with async_session() as session:
        res = await session.execute(select(BotMandatorySubscription).limit(1))
        obj = res.scalar_one_or_none()
        if obj:
            obj.is_enabled = False
            obj.updated_at = datetime.datetime.utcnow()
            await session.commit()
    _invalidate_bot_sub_cache()
    await callback.answer("🔴 تم تعطيل الاشتراك الإجباري في البوت.", show_alert=True)
    await cb_adm_bot_sub(callback)


@main_router.callback_query(F.data == "adm_bot_sub_del")
async def cb_adm_bot_sub_del(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("⛔", show_alert=True)
    async with async_session() as session:
        res = await session.execute(select(BotMandatorySubscription).limit(1))
        obj = res.scalar_one_or_none()
        if obj:
            await session.delete(obj)
            await session.commit()
    _invalidate_bot_sub_cache()
    await callback.answer("🗑 تم حذف إعداد الاشتراك الإجباري.", show_alert=True)
    await cb_adm_bot_sub(callback)


@main_router.callback_query(F.data.startswith("botsubcheck_"))
async def cb_bot_sub_check(callback: CallbackQuery, bot: Bot):
    """تحقق من اشتراك المستخدم في القناة بعد ضغط زر 'لقد اشتركت'"""
    parts = callback.data.split("_")
    if len(parts) < 2:
        return await callback.answer("خطأ.", show_alert=True)
    user_id = callback.from_user.id
    sub = await _get_bot_mandatory_sub()
    if not sub or not sub.is_enabled:
        return await callback.answer("✅ لا يوجد اشتراك إجباري حالياً.", show_alert=True)
    is_subbed = await _check_bot_channel_sub(bot, user_id, sub)
    if is_subbed:
        await callback.answer("✅ تم التحقق! يمكنك الآن استخدام البوت.", show_alert=True)
        try:
            await callback.message.delete()
        except Exception:
            pass
    else:
        ch = f"@{sub.channel_username}" if sub.channel_username else str(sub.channel_id)
        await callback.answer(f"❌ لم يتم الاشتراك بعد في {ch}. اشترك أولاً ثم اضغط مرة أخرى.", show_alert=True)


@main_router.callback_query(F.data == "dev_gift_all")
async def cb_dev_gift_all(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("❌ هذه الميزة خاصة بالمطور.", show_alert=True)
    await state.set_state(GiftAllFSM.waiting_for_hours)
    text = (
        f"<blockquote>{CE.STAR} <b>إهداء وقت اشتراك لجميع المشتركين 🎁</b></blockquote>\n\n"
        f"<blockquote>⏰ <b>أرسل عدد الساعات المطلوب إضافتها للجميع:</b>\n"
        f"• مثال: <code>24</code> لإضافة يوم كامل لكل المشتركين</blockquote>"
    )
    await callback.message.edit_text(text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("إلغاء", callback_data="admin_sub_panel", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_ban"])]]),
        parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.message(GiftAllFSM.waiting_for_hours)
async def fsm_gift_hours(message: Message, bot: Bot, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    v = message.text.strip()
    if not v.isdigit() or int(v) <= 0:
        return await message.answer(f"<blockquote>{CE.CROSS_BAN} <b>يرجى إدخال عدد صحيح موجب من الساعات.</b></blockquote>", parse_mode=ParseMode.HTML)
    hours = int(v)
    await state.clear()
    await message.answer(f"<blockquote>⏳ <b>جاري إهداء ({hours} ساعة) لجميع المشتركين في الخلفية...</b></blockquote>", parse_mode=ParseMode.HTML)
    now = datetime.datetime.utcnow()
    delta = datetime.timedelta(hours=hours)
    async with async_session() as session:
        res = await session.execute(select(User))
        all_u = res.scalars().all()
        total = len(all_u)
        for u in all_u:
            if u.role in ("owner", "admin"):
                continue
            if u.subscription_expires_at and u.subscription_expires_at > now:
                u.subscription_expires_at += delta
            else:
                u.subscription_expires_at = now + delta
            await session.execute(delete(ServiceSetting).where(
                ServiceSetting.user_id == u.id, ServiceSetting.service_key == "expiry_notified"))
        await session.commit()
    ok, fail = 0, 0
    gift_msg_text = (
        f"<blockquote>{CE.STAR} <b>هدية خاصة من إدارة البوت! 🎁</b></blockquote>\n\n"
        f"<blockquote>🎉 تم إهداؤك <b>{hours} ساعة إضافية</b> اشتراك مجاناً!\n\n"
        f"⚡ نتمنى لك تجربة ممتعة ومفيدة مع خدماتنا.</blockquote>"
    )
    for u in all_u:
        if u.role in ("owner", "admin"):
            continue
        try:
            await bot.send_message(u.id, gift_msg_text, parse_mode=ParseMode.HTML)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    async with async_session() as session:
        session.add(BroadcastTask(created_by=OWNER_ID, task_type="gift",
                                  total_count=total, success_count=ok, fail_count=fail, status="done"))
        await session.commit()
    await message.answer(
        f"<blockquote>{CE.CHECK_VERIFIED} <b>تقرير الإهداء الجماعي</b></blockquote>\n\n"
        f"<blockquote>👥 <b>إجمالي المستخدمين:</b> <code>{total}</code>\n"
        f"✅ <b>تم الإرسال بنجاح:</b> <code>{ok}</code>\n"
        f"❌ <b>فشل / محظور:</b> <code>{fail}</code></blockquote>",
        reply_markup=get_back_kb("admin_sub_panel"), parse_mode=ParseMode.HTML)


@main_router.callback_query(F.data == "dev_broadcast")
async def cb_dev_broadcast(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("❌ خاصة بالمطور.", show_alert=True)
    await state.set_state(BroadcastFSM.waiting_for_message)
    text = (
        f"<blockquote>{CE.BROADCAST} <b>الإذاعة العامة لمستخدمي البوت</b></blockquote>\n\n"
        f"<blockquote>📢 <b>أرسل الآن الرسالة المطلوب إذاعتها للجميع:</b>\n"
        f"• تدعم: (نص · صورة · فيديو · مستند · ملصق · بصمة صوتية)</blockquote>"
    )
    await callback.message.edit_text(text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("إلغاء الإذاعة", callback_data="admin_sub_panel", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_ban"])]]),
        parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.message(BroadcastFSM.waiting_for_message)
async def fsm_broadcast(message: Message, bot: Bot, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    await state.clear()
    async with async_session() as session:
        ids = [r[0] for r in (await session.execute(select(User.id))).all()]
    total = len(ids)
    await message.answer(f"<blockquote>⏳ <b>جاري بدء الإذاعة لـ <code>{total}</code> مستخدم...</b></blockquote>", parse_mode=ParseMode.HTML)
    ok, fail, blocked = 0, 0, 0
    t0 = time.time()
    for uid in ids:
        try:
            if message.photo:
                await bot.send_photo(uid, message.photo[-1].file_id, caption=message.caption)
            elif message.video:
                await bot.send_video(uid, message.video.file_id, caption=message.caption)
            elif message.document:
                await bot.send_document(uid, message.document.file_id, caption=message.caption)
            elif message.text:
                await bot.send_message(uid, message.text)
            else:
                continue
            ok += 1
        except TelegramBadRequest as e:
            err = str(e).lower()
            if any(k in err for k in ["chat not found", "blocked", "user is deactivated"]):
                blocked += 1
            else:
                fail += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    el = int(time.time() - t0)
    async with async_session() as session:
        session.add(BroadcastTask(created_by=OWNER_ID, task_type="broadcast",
                                  total_count=total, success_count=ok,
                                  fail_count=fail + blocked, status="done"))
        await session.commit()
    await message.answer(
        f"<blockquote>{CE.CHECK_VERIFIED} <b>تقرير الإذاعة العامة المكتمل</b></blockquote>\n\n"
        f"<blockquote>👥 <b>الإجمالي:</b> <code>{total}</code>\n"
        f"✅ <b>وصلت بنجاح:</b> <code>{ok}</code>\n"
        f"❌ <b>فشل الإرسال:</b> <code>{fail}</code>\n"
        f"🚫 <b>محظور / معطل:</b> <code>{blocked}</code>\n"
        f"⏱️ <b>الوقت المستغرق:</b> <code>{el} ثانية</code></blockquote>",
        reply_markup=get_back_kb("admin_sub_panel"), parse_mode=ParseMode.HTML)


@main_router.message(F.text.in_([".ذيع", ".اذاعه", "ذيع", "اذاعه"]))
async def cmd_account_broadcast(message: Message, bot: Bot, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return await message.answer(f"<blockquote>{CE.CROSS_BAN} <b>هذه الميزة مخصصة للمطور فقط.</b></blockquote>", parse_mode=ParseMode.HTML)
    replied = message.reply_to_message
    if not replied:
        return await message.answer(f"<blockquote>⚠️ <b>استخدم الأمر بالرد (Reply) على الرسالة المطلوب إذاعتها.</b></blockquote>", parse_mode=ParseMode.HTML)
    async with async_session() as session:
        res = await session.execute(select(BusinessChat).where(
            BusinessChat.owner_id == OWNER_ID, BusinessChat.is_active == True))
        chats = res.scalars().all()
    if not chats:
        return await message.answer(f"<blockquote>⚠️ <b>لا توجد محادثات بيزنس مسجلة بعد.</b></blockquote>", parse_mode=ParseMode.HTML)
    biz_id = str(message.business_connection_id) if message.business_connection_id else None
    _business_broadcast_pending[OWNER_ID] = {
        "src_chat_id": replied.chat.id, "src_message_id": replied.message_id, "biz_id": biz_id}
    kw = {"chat_id": message.chat.id, "reply_markup": get_broadcast_confirm_kb(), "parse_mode": ParseMode.HTML}
    if biz_id:
        kw["business_connection_id"] = biz_id
    await bot.send_message(
        text=(
            f"<blockquote>{CE.BROADCAST} <b>تأكيد الإذاعة عبر حساب البيزنس</b></blockquote>\n\n"
            f"<blockquote>👥 <b>الوجهة:</b> <code>{len(chats)}</code> محادثة بيزنس نشطة\n\n"
            f"⚠️ هل تريد بدء عملية النشر فوراً؟</blockquote>"
        ), **kw)


@main_router.callback_query(F.data == "acc_bc_cancel")
async def cb_acc_bc_cancel(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("❌", show_alert=True)
    await state.clear()
    await callback.message.edit_text(f"<blockquote>{CE.CROSS_BAN} <b>تم إلغاء عملية الإذاعة بنجاح.</b></blockquote>", parse_mode=ParseMode.HTML)
    await callback.answer()


@main_router.callback_query(F.data == "acc_bc_start")
async def cb_acc_bc_start(callback: CallbackQuery, bot: Bot, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("❌", show_alert=True)
    data = _business_broadcast_pending.pop(OWNER_ID, None)
    if not data:
        return await callback.answer("⚠️ انتهت صلاحية العملية.", show_alert=True)
    src_chat_id = data["src_chat_id"]
    src_msg_id = data["src_message_id"]
    async with async_session() as session:
        res = await session.execute(select(BusinessChat).where(
            BusinessChat.owner_id == OWNER_ID, BusinessChat.is_active == True))
        chats = res.scalars().all()
    await callback.message.edit_text(f"<blockquote>⏳ <b>جاري الإذاعة لـ <code>{len(chats)}</code> محادثة...</b></blockquote>", parse_mode=ParseMode.HTML)
    ok, fail, skipped = 0, 0, 0
    t0 = time.time()
    for c in chats:
        try:
            await bot.copy_message(chat_id=c.chat_id, from_chat_id=src_chat_id,
                                   message_id=src_msg_id, business_connection_id=c.business_connection_id)
            ok += 1
        except TelegramBadRequest as e:
            err = str(e).lower()
            if any(k in err for k in ["not enough rights", "chat not found", "blocked", "forbidden"]):
                skipped += 1
                async with async_session() as session:
                    await session.execute(update(BusinessChat).where(BusinessChat.id == c.id).values(is_active=False))
                    await session.commit()
            else:
                fail += 1
        except Exception:
            fail += 1
        await asyncio.sleep(1.2)
    el = int(time.time() - t0)
    total = len(chats)
    pct = round((ok / total * 100), 1) if total else 0
    async with async_session() as session:
        session.add(BroadcastTask(created_by=OWNER_ID, task_type="account_broadcast",
                                  total_count=total, success_count=ok, fail_count=fail + skipped, status="done"))
        await session.commit()
    await callback.message.edit_text(
        f"<blockquote>{CE.CHECK_VERIFIED} <b>تقرير إذاعة البيزنس المكتمل</b></blockquote>\n\n"
        f"<blockquote>📨 <b>إجمالي المحادثات:</b> <code>{total}</code>\n"
        f"✅ <b>تم الإرسال بنجاح:</b> <code>{ok}</code>\n"
        f"❌ <b>فشل:</b> <code>{fail}</code>\n"
        f"🚫 <b>معطل أو محظور:</b> <code>{skipped}</code>\n"
        f"⏱️ <b>الوقت:</b> <code>{el} ثانية</code>\n"
        f"📊 <b>نسبة النجاح:</b> <b>{pct}%</b></blockquote>",
        parse_mode=ParseMode.HTML)
    await callback.answer()


# =============================================================================
# 25. الألعاب
# =============================================================================
CUT_TWEETS = [
    "‏إذا كان بإمكانك العودة بالزمن وإصلاح خطأ واحد، ماذا سيكون؟",
    "صف حياتك الحالية بعنوان فيلم سينمائي؟",
    "ما هي العادة التي تود التخلص منها ولكنك لا تستطيع؟",
    "أكثر صفة تجذبك في الشخص الذي تتعامل معه لأول مرة؟",
    "لو أتيحت لك فرصة الهجرة لأي دولة مجاناً، أين ستذهب؟",
    "هل تؤمن بالفرصة الثانية في العلاقات؟ ولماذا؟",
    "أغرب حلم حلمت به وما زلت تتذكره حتى اليوم؟",
    "ما هو الشيء الذي اشتريته وندمت على شرائه لاحقاً؟",
    "إذا أعطيت مليون دولار بشرط عدم استخدام الهاتف لمدة سنة، هل توافق؟",
    "كلمة واحدة تصف بها شعورك في هذه اللحظة؟",
    "أكبر درس تعلمته من خيبة أمل مررت بها؟",
    "هل تفضل العمل الذي تحبه براتب متوسط، أو عمل تكرهه براتب عالي جداً؟",
    "ما هي الحقيقة المؤلمة التي أدركتها متأخراً؟",
    "شخص في حياتك لا يمكنك تخيل يومك بدونه؟"]

WOULD_YOU_RATHER = [
    ("تعيش في جزيرة معزولة ولديك كل وسائل الراحة", "أو تعيش في مدينة مزدحمة جداً بدون إنترنت؟"),
    ("تقرأ أفكار الناس من حولك", "أو تصبح خفياً متى ما أردت؟"),
    ("تسافر للمستقبل 50 سنة", "أو تسافر للماضي 100 سنة؟"),
    ("تفقد كل ذكرياتك الماضية", "أو لا تستطيع تكوين ذكريات جديدة أبداً؟"),
    ("تكون فائق الذكاء ولكن وحيد دائماً", "أو متوسط الذكاء ولديك أصدقاء مخلصين كثر؟"),
    ("تتحكم بالوقت (إيقافه وإعادته)", "أو تتحكم بالجاذبية والطيران؟"),
    ("تعرف موعد وفاتك", "أو تعرف سبب وفاتك؟"),
    ("تأكل طعامك المفضل طوال حياتك فقط", "أو تجربه مرة واحدة كل 5 سنوات؟")]

TRUTH_QUESTIONS = [
    "ما هو أكثر شيء تخاف أن يعرفه الناس عنك؟",
    "هل ندمت يوماً على مساعدة شخص ما؟ من هو ولماذا؟",
    "ما هو أكبر سر أخفيته عن أقرب الناس إليك؟",
    "هل تمنيت يوماً زوال نعمة من شخص آخر؟ بصدق.",
    "متى كانت آخر مرة بكيت فيها من قلبك ولماذا؟",
    "لو طلب منك الاعتذار لشخص واحد في حياتك، من سيكون؟",
    "هل خنت ثقة شخص وثق بك يوماً ما؟",
    "ما هو القرار الأسوأ الذي اتخذته في حياتك وتتمنى محوه؟"]

JOKES = [
    "واحد غبي راح للدكتور، قاله: يا دكتور كل ما أشرب شاي عيني اليمين توجعني! قاله الدكتور: شيل المعلقة من الكوباية يا فالح! 😂",
    "مرة واحد كسلان جداً اتجوز واحدة كسلانة.. خلفوا ولد سمّوه 'أجلينها لبكرة'! 🤣",
    "مدرس بيسأل تلميذ: الثعلب بيولد ولا بيبيض؟ قاله: والله يا أستاذ الثعلب مكار توقع منه أي حاجة! 🦊😂",
    "واحد بخيل ابنه نجح في الامتحان جاب 98%، قاله: وضيعت الـ 2% فين يا مسرف! 💸🤣",
    "عجوزة دخلت محو الأمية، المدرس رسم شجرة وقال دي إيه؟ قالت: شجرة! قال: برافو.. وتاني يوم رسم نخلة.. قالت: سبحان الله الشجرة كبرت وحلقت شعرها! 🌴😂",
    "واحد محشش سألوه: إيه هو الشبه بين الحمار والأرنب؟ قالهم: الحمار بياكل الجزر بالبطيء، والأرنب بياكله بسرعة! 🥕😆"]

WISDOM_QUOTES = [
    "💎 'لا تكن أصعب ما في الحياة، ولا تكن أسهل ما فيها، بل كن أنت كما أنت.'",
    "🌱 'كلما زاد نضجك، قل عدد الأشخاص الذين ترغب في الجلوس معهم.'",
    "⏳ 'الوقت كالسيف إن لم تقطعه قطعك، فاستغل كل دقيقة في بناء مستقبلك.'",
    "🦁 'كن شجاعاً، فالخوف لا يمنع الموت، بل يمنع الحياة.'",
    "🕊️ 'راحة البال تبدأ عندما تتوقف عن تبرير تصرفاتك لمن لا يستحق.'",
    "💡 'النجاح ليس عدم ارتكاب الأخطاء، بل عدم تكرار نفس الخطأ مرتين.'"]

LOVE_RESPONSES = [
    "❤️ نسبة التوافق: **98%** - حب حقيقي وأسطوري زي قيس وليلى!",
    "💖 نسبة التوافق: **85%** - علاقة جميلة جداً ومتفاهمة!",
    "💛 نسبة التوافق: **65%** - محتاجين شوية تفاهم وصبر وتظبط.",
    "💔 نسبة التوافق: **30%** - كلاكيع وخناقات.. فكر كويس قبل ما تدبس!",
    "🖤 نسبة التوافق: **5%** - اهرب فوراً ومتبصش وراك! 😂"]

@main_router.message(F.text.regexp(r"^(\.)?(?:سعر النجوم|اسعار النجوم|النجوم|نجوم|سعر النجمه|stars)$", flags=re.I))
async def cmd_stars_overview_chat(message: Message):
    text = await get_stars_overview_text()
    kb = get_stars_overview_kb()
    await message.reply(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@main_router.message(F.text.regexp(r"^(?:(\.)?(?:سعر|كام سعر|كم سعر|سعر ال|احسب)\s*)?(\d+)\s*(?:نجمة|نجمه|نجوم|stars?)$", flags=re.I))
async def cmd_stars_quantity_chat(message: Message):
    m = re.search(r"(\d+)\s*(?:نجمة|نجمه|نجوم|stars?)", message.text, re.I)
    if m:
        q = int(m.group(1))
        if q > 0:
            text = await get_stars_calc_text(q)
            kb = get_stars_calc_kb(q)
            return await message.reply(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@main_router.message(F.text.regexp(r"^(\.)?(كت|كت تويت)$"))
async def cmd_cut_tweet(message: Message):
    text = (
        f"<blockquote>{CE.CHAT} <b>فقرة كت تويت — مساحة نقاش وصراحة</b> {CE.SPARKLES}</blockquote>\n\n"
        f"<blockquote>{CE.DOVE} <b>{random.choice(CUT_TWEETS)}</b>\n\n"
        f"{CE.WRITE} <i>شاركنا إجابتك وصراحتك بكل أريحية!</i></blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("سؤال آخر", callback_data="fun_next_cut", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bolt"])]
    ])
    await message.reply(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@main_router.callback_query(F.data == "fun_next_cut")
async def cb_next_cut(callback: CallbackQuery):
    text = (
        f"<blockquote>{CE.CHAT} <b>فقرة كت تويت — مساحة نقاش وصراحة</b> {CE.SPARKLES}</blockquote>\n\n"
        f"<blockquote>{CE.DOVE} <b>{random.choice(CUT_TWEETS)}</b>\n\n"
        f"{CE.WRITE} <i>شاركنا إجابتك وصراحتك بكل أريحية!</i></blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("سؤال آخر", callback_data="fun_next_cut", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bolt"])]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await callback.answer()


@main_router.message(F.text.regexp(r"^(\.)?(لو خيروك|خيروك)$"))
async def cmd_wyr(message: Message):
    o1, o2 = random.choice(WOULD_YOU_RATHER)
    text = (
        f"<blockquote>{CE.BRAIN} <b>لعبة لو خيروك — قرارك الحاسم</b> {CE.FIRE}</blockquote>\n\n"
        f"<blockquote>{CE.NUM_1} <b>{o1}</b>\n\n"
        f"{CE.NUM_2} <b>{o2}</b>\n\n"
        f"{CE.SPARKLES} <i>أيهما تختار ولماذا؟</i> <tg-spoiler>فكّر جيداً قبل اتخاذ القرار!</tg-spoiler></blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("الاختيار الأول", callback_data="wyr_1", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["check_verified"]),
         make_btn("الاختيار الثاني", callback_data="wyr_2", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["check_verified"])],
        [make_btn("سؤال جديد", callback_data="fun_next_wyr", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bolt"])]
    ])
    await message.reply(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@main_router.callback_query(F.data == "fun_next_wyr")
async def cb_next_wyr(callback: CallbackQuery):
    o1, o2 = random.choice(WOULD_YOU_RATHER)
    text = (
        f"<blockquote>{CE.BRAIN} <b>لعبة لو خيروك — قرارك الحاسم</b> {CE.FIRE}</blockquote>\n\n"
        f"<blockquote>{CE.NUM_1} <b>{o1}</b>\n\n"
        f"{CE.NUM_2} <b>{o2}</b>\n\n"
        f"{CE.SPARKLES} <i>أيهما تختار ولماذا؟</i> <tg-spoiler>فكّر جيداً قبل اتخاذ القرار!</tg-spoiler></blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("الاختيار الأول", callback_data="wyr_1", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["check_verified"]),
         make_btn("الاختيار الثاني", callback_data="wyr_2", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["check_verified"])],
        [make_btn("سؤال جديد", callback_data="fun_next_wyr", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bolt"])]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await callback.answer()


@main_router.callback_query(F.data.in_(["wyr_1", "wyr_2"]))
async def cb_wyr_choice(callback: CallbackQuery):
    choice_num = "الأول" if callback.data == "wyr_1" else "الثاني"
    await callback.answer(f"اخترت الخيار {choice_num}.. قرار شجاع! 💪", show_alert=True)


@main_router.message(F.text.regexp(r"^(\.)?(صراحة|صراحه)$"))
async def cmd_truth(message: Message):
    text = (
        f"<blockquote>{CE.LOCK} <b>كرسي الصراحة والاعتراف الجريء</b> {CE.SPARKLES}</blockquote>\n\n"
        f"<blockquote>{CE.INFO} <i>{random.choice(TRUTH_QUESTIONS)}</i>\n\n"
        f"{CE.CHAT} <tg-spoiler>جاوب بكل شفافية وصدق تام دون تردد!</tg-spoiler></blockquote>"
    )
    await message.reply(text, parse_mode=ParseMode.HTML)


@main_router.message(F.text.regexp(r"^(\.)?(نكتة|نكته)$"))
async def cmd_joke(message: Message):
    text = (
        f"<blockquote>{CE.ROBOT} <b>فقرة نكتة وفرفشة اليوم</b> {CE.SPARKLES}</blockquote>\n\n"
        f"<blockquote>{CE.SPARKLES} <i>{random.choice(JOKES)}</i></blockquote>"
    )
    await message.reply(text, parse_mode=ParseMode.HTML)


@main_router.message(F.text.regexp(r"^(\.)?(حكمة|حكمه)$"))
async def cmd_wisdom(message: Message):
    text = (
        f"<blockquote>{CE.DIAMOND} <b>حكمة وموعظة اليوم الذهبية</b> {CE.STAR_GOLD}</blockquote>\n\n"
        f"<blockquote>{CE.STAR} <b>{random.choice(WISDOM_QUOTES)}</b></blockquote>"
    )
    await message.reply(text, parse_mode=ParseMode.HTML)


@main_router.message(F.text.regexp(r"^(\.)?(نرد|زهر)$"))
async def cmd_dice(message: Message):
    await message.answer_dice(emoji="🎲")


@main_router.message(F.text.regexp(r"^(\.)?(سلة|سله)$"))
async def cmd_basket(message: Message):
    await message.answer_dice(emoji="🏀")


@main_router.message(F.text.regexp(r"^(\.)?(كرة|كورة)$"))
async def cmd_football(message: Message):
    await message.answer_dice(emoji="⚽")


@main_router.message(F.text.regexp(r"^(\.)?(سهم|دارت)$"))
async def cmd_dart(message: Message):
    await message.answer_dice(emoji="🎯")


@main_router.message(F.text.regexp(r"^(\.)?(بولينج|بولينغ)$"))
async def cmd_bowling(message: Message):
    await message.answer_dice(emoji="🎳")


@main_router.message(F.text.regexp(r"^(\.)?(حظ|كازينو)$"))
async def cmd_slot(message: Message):
    await message.answer_dice(emoji="🎰")


@main_router.message(F.text.regexp(r"^(\.)?(نسبة الحب|نسبه الحب)$"))
async def cmd_love(message: Message):
    r = message.reply_to_message
    if not r or not r.from_user:
        return await message.reply(f"<blockquote>⚠️ <b>يرجى الرد على رسالة الشخص لحساب نسبة الحب!</b></blockquote>", parse_mode=ParseMode.HTML)
    name1 = html.quote(message.from_user.first_name)
    name2 = html.quote(r.from_user.first_name)
    text = (
        f"<blockquote>{CE.HEART_RED} <b>مقياس التوافق ونسبة الحب</b> {CE.HEART_PURPLE}</blockquote>\n\n"
        f"<blockquote>{CE.USERS} بين <b>{name1}</b> و <b>{name2}</b>:\n\n"
        f"{CE.SPARKLES} <tg-spoiler>{random.choice(LOVE_RESPONSES)}</tg-spoiler></blockquote>"
    )
    await message.reply(text, parse_mode=ParseMode.HTML)


@main_router.message(F.text.regexp(r"^(\.)?(زواج)$"))
async def cmd_marriage(message: Message):
    r = message.reply_to_message
    if not r or not r.from_user:
        return await message.reply(f"<blockquote>⚠️ <b>يرجى الرد على رسالة الشخص لإتمام الزواج!</b></blockquote>", parse_mode=ParseMode.HTML)
    name1 = html.quote(message.from_user.first_name)
    name2 = html.quote(r.from_user.first_name)
    text = (
        f"<blockquote>{CE.CROWN} <b>وثيقة عقد قران ومباركة زواج رسمي</b> {CE.STAR}</blockquote>\n\n"
        f"<blockquote>{CE.SPARKLES} <i>بارك الله لكما وبارك عليكما وجمع بينكما في خير!</i>\n\n"
        f"{CE.HEART_RED} مبروك للعروسين <b>{name1}</b> {CE.HEART_RED} <b>{name2}</b> {CE.FIRE}\n"
        f"<tg-spoiler>ألف ترليون مبروك ونتمنى لكما حياة زوجية مليئة بالحب والسعادة الدائمة!</tg-spoiler></blockquote>"
    )
    await message.reply(text, parse_mode=ParseMode.HTML)


@main_router.message(F.text.regexp(r"^(\.)?(طلاق)$"))
async def cmd_divorce(message: Message):
    r = message.reply_to_message
    if not r or not r.from_user:
        return await message.reply(f"<blockquote>⚠️ <b>يرجى الرد على رسالة الشخص لإيقاع الطلاق!</b></blockquote>", parse_mode=ParseMode.HTML)
    name1 = html.quote(message.from_user.first_name)
    name2 = html.quote(r.from_user.first_name)
    text = (
        f"<blockquote>{CE.CROSS_BAN} <b>وثيقة إنهاء علاقة وانفصال رسمي</b> {CE.ALERT}</blockquote>\n\n"
        f"<blockquote>{CE.BOLT} <b>تم إنهاء العلاقة رسمياً بين {name1} و {name2}!</b>\n\n"
        f"{CE.BACK} <tg-spoiler>«وعسى أن تكرهوا شيئاً وهو خير لكم».. نسأل الله التوفيق والخير لكل طرف في مساره الجديد.</tg-spoiler></blockquote>"
    )
    await message.reply(text, parse_mode=ParseMode.HTML)


# =============================================================================
# 26. الإسلاميات
# =============================================================================
CITIES_MAP = {
    "القاهرة": ("Cairo", "Egypt"), "مصر": ("Cairo", "Egypt"), "الاسكندرية": ("Alexandria", "Egypt"),
    "الرياض": ("Riyadh", "Saudi Arabia"), "السعودية": ("Riyadh", "Saudi Arabia"),
    "مكة": ("Makkah", "Saudi Arabia"), "المدينة": ("Medina", "Saudi Arabia"),
    "دبي": ("Dubai", "United Arab Emirates"), "أبوظبي": ("Abu Dhabi", "United Arab Emirates"),
    "بغداد": ("Baghdad", "Iraq"), "عمان": ("Amman", "Jordan"), "بيروت": ("Beirut", "Lebanon"),
    "الكويت": ("Kuwait City", "Kuwait"), "الدوحة": ("Doha", "Qatar"), "القدس": ("Jerusalem", "Palestine")}

AZKAR_S = ["☀️ «أَصْبَحْنَا وَأَصْبَحَ المُلْكُ لِلَّهِ»", "☀️ «اللَّهُمَّ بِكَ أَصْبَحْنَا»"]
AZKAR_M = ["🌙 «أَمْسَيْنَا وَأَمْسَى المُلْكُ لِلَّهِ»", "🌙 «اللَّهُمَّ بِكَ أَمْسَيْنَا»"]
DOAA = ["🤲 «رَبَّنَا آتِنَا فِي الدُّنْيَا حَسَنَةً»", "🤲 «اللَّهُمَّ إِنِّي أَسْأَلُكَ العَفْوَ»"]


async def get_prayer_times(city_query: str = "القاهرة") -> Optional[Dict[str, str]]:
    today = datetime.datetime.now().strftime("%d-%m-%Y")
    clean_city = city_query.replace(".", "").strip()
    if not clean_city:
        clean_city = "القاهرة"
    ce, co = CITIES_MAP.get(clean_city, (clean_city, ""))

    urls = [
        f"https://api.aladhan.com/v1/timingsByAddress/{today}?address={clean_city}",
        f"https://api.aladhan.com/v1/timingsByCity/{today}?city={ce}&country={co}&method=5",
        f"https://api.aladhan.com/v1/timingsByAddress/{today}?address={ce}",
    ]

    async with httpx.AsyncClient(timeout=9.0, follow_redirects=True) as c:
        for u in urls:
            try:
                r = await c.get(u)
                if r.status_code == 200:
                    d = r.json().get("data", {})
                    t = d.get("timings", {})
                    if t and "Fajr" in t:
                        return {k: t[k].split(" ")[0] for k in ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]}
            except Exception:
                pass
    return None


@main_router.message(F.text.regexp(r"^(\.)?(صلاة|الصلاة)(\s+.+)?$"))
async def cmd_prayer(message: Message):
    p = message.text.strip().split(maxsplit=1)
    city = p[1].strip() if len(p) > 1 else "القاهرة"
    city = city.replace(".", "").strip() or "القاهرة"
    timings = await get_prayer_times(city)
    if timings:
        prayer_txt = (
            f"<blockquote>{CE.PRAY} <b>مواقيت الصلاة الرسمية في {html.quote(city)}</b> {CE.SPARKLES}</blockquote>\n\n"
            f"<blockquote>{CE.SUN} <b>الفجر:</b> <code>{timings['Fajr']}</code>\n"
            f"{CE.SUN} <b>الظهر:</b> <code>{timings['Dhuhr']}</code>\n"
            f"{CE.SUN} <b>العصر:</b> <code>{timings['Asr']}</code>\n"
            f"{CE.MOON} <b>المغرب:</b> <code>{timings['Maghrib']}</code>\n"
            f"{CE.MOON} <b>العشاء:</b> <code>{timings['Isha']}</code>\n\n"
            f"{CE.PRAY} <i>تقبل الله منا ومنكم صالح الأعمال والطاعات بمزيد من الأجر والثواب</i></blockquote>"
        )
        return await message.reply(prayer_txt, parse_mode=ParseMode.HTML)
    await message.reply(f"<blockquote>⚠️ <b>تعذر جلب مواقيت الصلاة لمدينة {html.quote(city)} حالياً.</b></blockquote>", parse_mode=ParseMode.HTML)


@main_router.message(F.text.regexp(r"^(\.)?(اذكار الصباح|أذكار الصباح)$"))
async def cmd_azk_s(message: Message):
    text = (
        f"<blockquote>{CE.SUN} <b>أذكار الصباح المباركة وحصن المسلم</b> {CE.SPARKLES}</blockquote>\n\n"
        f"<blockquote>{CE.STAR} <b>{random.choice(AZKAR_S)}</b></blockquote>"
    )
    await message.reply(text, parse_mode=ParseMode.HTML)


@main_router.message(F.text.regexp(r"^(\.)?(اذكار المساء|أذكار المساء)$"))
async def cmd_azk_m(message: Message):
    text = (
        f"<blockquote>{CE.MOON} <b>أذكار المساء المباركة وحفظ الرحمن</b> {CE.SPARKLES}</blockquote>\n\n"
        f"<blockquote>{CE.STAR} <b>{random.choice(AZKAR_M)}</b></blockquote>"
    )
    await message.reply(text, parse_mode=ParseMode.HTML)


@main_router.message(F.text.regexp(r"^(\.)?(دعاء|ادعية)$"))
async def cmd_doaa(message: Message):
    text = (
        f"<blockquote>{CE.PRAY} <b>دعاء ومناجاة طيبة</b> {CE.SPARKLES}</blockquote>\n\n"
        f"<blockquote>{CE.STAR} <b>{random.choice(DOAA)}</b></blockquote>"
    )
    await message.reply(text, parse_mode=ParseMode.HTML)


@main_router.message(F.text.regexp(r"^(\.)?(سبحة|تسبيح)$"))
async def cmd_tasbeeh(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("سبّح (0)", callback_data="tsb_click_0_1", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["check_verified"])],
        [make_btn("تصفير العداد", callback_data="tsb_reset", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bolt"])]
    ])
    text = (
        f"<blockquote>{CE.PRAY} <b>السبحة الإلكترونية المباركة</b> {CE.SPARKLES}</blockquote>\n\n"
        f"<blockquote>{CE.STATS} <b>العدد الحالي:</b> <tg-spoiler>0</tg-spoiler>\n\n"
        f"{CE.STAR} <i>«سُبْحَانَ اللَّهِ وَبِحَمْدِهِ ، سُبْحَانَ اللَّهِ الْعَظِيمِ»</i></blockquote>"
    )
    await message.reply(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@main_router.callback_query(F.data.startswith("tsb_click_"))
async def cb_tsb(callback: CallbackQuery):
    cnt = int(callback.data.split("_")[2]) + 1
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn(f"سبّح ({cnt})", callback_data=f"tsb_click_{cnt}_1", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["check_verified"])],
        [make_btn("تصفير العداد", callback_data="tsb_reset", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bolt"])]
    ])
    text = (
        f"<blockquote>📿 <b>السبحة الإلكترونية المباركة</b></blockquote>\n\n"
        f"<blockquote>🔢 <b>العدد الحالي:</b> <code>{cnt}</code>\n\n"
        f"✨ <i>«سُبْحَانَ اللَّهِ وَبِحَمْدِهِ ، سُبْحَانَ اللَّهِ الْعَظِيمِ»</i></blockquote>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await callback.answer("تقبل الله طاعتكم 🤲")


@main_router.callback_query(F.data == "tsb_reset")
async def cb_tsb_reset(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("سبّح (0)", callback_data="tsb_click_0_1", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["check_verified"])],
        [make_btn("تصفير العداد", callback_data="tsb_reset", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bolt"])]
    ])
    text = (
        f"<blockquote>{CE.PRAY} <b>السبحة الإلكترونية المباركة</b> {CE.SPARKLES}</blockquote>\n\n"
        f"<blockquote>{CE.STATS} <b>العدد الحالي:</b> <tg-spoiler>0</tg-spoiler>\n\n"
        f"{CE.STAR} <i>«سُبْحَانَ اللَّهِ وَبِحَمْدِهِ ، سُبْحَانَ اللَّهِ الْعَظِيمِ»</i></blockquote>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await callback.answer("تم تصفير عداد السبحة بنجاح 🔄")


# =============================================================================
# 27. الملصقات + الأدوات الذكية
# =============================================================================
@main_router.message(F.text.regexp(r"^(\.)?(ملصق لصورة|تحويل لصورة)$"))
async def cmd_stk_to_img(message: Message, bot: Bot):
    r = message.reply_to_message
    if not r or not r.sticker:
        return await message.reply(f"<blockquote>{CE.ALERT} <b>تنبيه!</b>\n<i>يرجى الرد على ملصق ثابت لتحويله إلى صورة.</i></blockquote>", parse_mode=ParseMode.HTML)
    if r.sticker.is_animated or r.sticker.is_video:
        return await message.reply(f"<blockquote>{CE.ALERT} <b>تنبيه!</b>\n<i>هذه الميزة تدعم الملصقات الثابتة فقط حالياً.</i></blockquote>", parse_mode=ParseMode.HTML)
    w = await message.reply("⏳ ...")
    try:
        os.makedirs("downloads", exist_ok=True)
        fi = await bot.get_file(r.sticker.file_id)
        out = f"downloads/stk_{r.sticker.file_unique_id}.png"
        await bot.download_file(fi.file_path, destination=out)
        if os.path.exists(out):
            await message.reply_photo(photo=FSInputFile(out), caption="🖼️")
            try:
                await w.delete()
            except Exception:
                pass
            try:
                os.remove(out)
            except Exception:
                pass
    except Exception as e:
        await w.edit_text(f"⚠️ {e}")


@main_router.message(F.text.regexp(r"^(\.)?(معلومات الملصق)$"))
async def cmd_stk_info(message: Message):
    r = message.reply_to_message
    if not r or not r.sticker:
        return await message.reply(f"<blockquote>{CE.ALERT} <b>تنبيه!</b>\n<i>يرجى الرد على ملصق لمعاينة بياناته وحزمته.</i></blockquote>", parse_mode=ParseMode.HTML)
    s = r.sticker
    await message.reply(
        f"🎨 Emoji: {s.emoji or '—'}\n📦 Set: `{s.set_name or '—'}`\n📏 {s.width}x{s.height}",
        parse_mode=ParseMode.MARKDOWN)


EN_FONTS = {
    "Bold": lambda t: "".join(chr(ord(c) + 120205) if 'a' <= c <= 'z' else (chr(ord(c) + 120211) if 'A' <= c <= 'Z' else c) for c in t),
    "Italic": lambda t: "".join(chr(ord(c) + 120237) if 'a' <= c <= 'z' else (chr(ord(c) + 120243) if 'A' <= c <= 'Z' else c) for c in t),
    "Circled": lambda t: "".join(chr(ord(c) + 9327) if 'a' <= c <= 'z' else (chr(ord(c) + 9333) if 'A' <= c <= 'Z' else c) for c in t),
    "Mono": lambda t: "".join(chr(ord(c) + 120361) if 'a' <= c <= 'z' else (chr(ord(c) + 120367) if 'A' <= c <= 'Z' else c) for c in t),
    "Gothic": lambda t: "".join(chr(ord(c) + 120153) if 'a' <= c <= 'z' else (chr(ord(c) + 120159) if 'A' <= c <= 'Z' else c) for c in t),
    "Squared": lambda t: "".join(chr(ord(c) + 127183) if 'a' <= c <= 'z' else (chr(ord(c) + 127189) if 'A' <= c <= 'Z' else c) for c in t)}

AR_STYLES = [
    lambda t: f"『 {t} 』", lambda t: f"★彡 {t} 彡★", lambda t: f"⎝⎝ {t} ⎠⎠",
    lambda t: f"•.¸♡ {t} ♡¸.•", lambda t: f"༒☬ {t} ☬༒", lambda t: f"░▒▓█ {t} █▓▒░",
    lambda t: f"【 {t} 】", lambda t: f"ღ {t} ღ"]


@main_router.message(F.text.regexp(r"^(\.)?(زخرفة|زخرفه)(\s+.+)?$"))
async def cmd_deco(message: Message):
    p = message.text.strip().split(maxsplit=1)
    if len(p) < 2:
        return await message.reply("⚠️ `.زخرفة كلمة`")
    t = p[1].strip()
    res = f"✨ `{t}`\n━━━━━━━\n\n"
    if re.search(r"[a-zA-Z]", t):
        for n, f in EN_FONTS.items():
            try:
                res += f"• {n}: `{f(t)}`\n\n"
            except Exception:
                pass
    else:
        for i, f in enumerate(AR_STYLES, 1):
            res += f"• {i}: `{f(t)}`\n\n"
    await message.reply(res, parse_mode=ParseMode.MARKDOWN)


@main_router.message(F.text.regexp(r"^(\.)?(عمر|العمر)(\s+.+)?$"))
async def cmd_age(message: Message):
    p = message.text.strip().split(maxsplit=1)
    if len(p) < 2:
        return await message.reply("⚠️ `.عمر 2000/5/14`")
    ds = p[1].strip().replace("-", "/").replace(".", "/")
    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", ds)
    if not m:
        return await message.reply("❌ صيغة غير صحيحة.")
    try:
        bd = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        t = datetime.date.today()
        if bd > t:
            return await message.reply("😅 لم تولد بعد!")
        y = t.year - bd.year
        mo = t.month - bd.month
        d = t.day - bd.day
        if d < 0:
            mo -= 1
            d += 30
        if mo < 0:
            y -= 1
            mo += 12
        td = (t - bd).days
        await message.reply(
            f"<blockquote>{CE.CAL} <b>حساب العمر الدقيق وتفاصيله</b> {CE.SPARKLES}</blockquote>\n\n"
            f"<blockquote>{CE.POINT} <b>العمر:</b> <b>{y} سنة و {mo} شهر و {d} يوم</b>\n"
            f"{CE.STATS} <b>إجمالي الأيام المعاشة:</b> <tg-spoiler>{td:,} يوم</tg-spoiler></blockquote>",
            parse_mode=ParseMode.HTML)
    except Exception as e:
        await message.reply(f"⚠️ {e}")


@main_router.message(F.text.regexp(r"^(\.)?(bin)(\s+.+)?$"))
async def cmd_bin(message: Message):
    p = message.text.strip().split(maxsplit=1)
    if len(p) < 2:
        return await message.reply("⚠️ `.bin 457173`")
    bn = re.sub(r"\D", "", p[1])[:8]
    if len(bn) < 6:
        return await message.reply("❌ 6 أرقام على الأقل.")
    w = await message.reply("⏳ ...")
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"https://data.handyapi.com/bin/{bn}")
            if r.status_code == 200:
                d = r.json()
                if d.get("Status") == "SUCCESS":
                    return await w.edit_text(
                        f"💳 **{bn}**\n🔹 {d.get('Scheme','-')} | {d.get('Type','-')}\n"
                        f"🏛️ {d.get('Issuer','-')}\n🌍 {d.get('Country',{}).get('Name','-')}",
                        parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass
    await w.edit_text(f"⚠️ لا بيانات لـ {bn}.")


@main_router.message(F.text.regexp(r"^(\.)?(تشفير)(\s+.+)?$"))
async def cmd_enc(message: Message):
    p = message.text.strip().split(maxsplit=1)
    if len(p) < 2:
        return await message.reply("⚠️ `.تشفير نص`")
    await message.reply(
        f"<blockquote>{CE.LOCK} <b>النص بعد التشفير (Base64):</b>\n\n"
        f"<code>{base64.b64encode(p[1].encode()).decode()}</code></blockquote>",
        parse_mode=ParseMode.HTML)


@main_router.message(F.text.regexp(r"^(\.)?(فك تشفير)(\s+.+)?$"))
async def cmd_dec(message: Message):
    p = message.text.strip().split(maxsplit=1)
    if len(p) < 2:
        return await message.reply("⚠️ `.فك تشفير نص`")
    try:
        decoded_text = base64.b64decode(p[1].encode()).decode()
        await message.reply(
            f"<blockquote>{CE.KEY} <b>النص بعد فك التشفير:</b>\n\n"
            f"<b>{html.quote(decoded_text)}</b></blockquote>",
            parse_mode=ParseMode.HTML)
    except Exception:
        await message.reply(f"<blockquote>{CE.CROSS} <b>النص المدخل غير صالح أو غير مشفر بنظام Base64!</b></blockquote>", parse_mode=ParseMode.HTML)


@main_router.message(F.text.regexp(r"^(\.)?(عكس)(\s+.+)?$"))
async def cmd_rev(message: Message):
    p = message.text.strip().split(maxsplit=1)
    if len(p) < 2:
        return await message.reply("⚠️ `.عكس نص`")
    await message.reply(
        f"<blockquote>{CE.REFRESH} <b>النص بعد العكس:</b>\n\n"
        f"<code>{html.quote(p[1][::-1])}</code></blockquote>",
        parse_mode=ParseMode.HTML)


# =============================================================================
# 28. أوامر الكتم/التثبيت
# =============================================================================
async def _find_reply_or_target(message: Message, bot: Bot, owner_id: Optional[int] = None) -> Tuple[Optional[int], Optional[str], str]:
    """يعيد (target_id, target_username, target_name) بدقة"""
    # 1. الرد على رسالة
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        return u.id, u.username, u.full_name or u.first_name or str(u.id)

    # 2. استخراج المعرّف أو اليوزر من نص الأمر
    parts = (message.text or "").strip().split()
    for p in parts[1:]:
        p_clean = p.strip()
        if p_clean.isdigit():
            return int(p_clean), None, f"مستخدم ({p_clean})"
        if p_clean.startswith("@"):
            un = p_clean.lstrip("@")
            async with async_session() as session:
                found = await session.scalar(select(User).where(func.lower(User.username) == un.lower()))
                if found:
                    return found.id, found.username, found.full_name or f"@{un}"
            return None, un, f"@{un}"

    # 3. محادثة خاصة (private)
    if message.chat.type == "private":
        c = message.chat
        c_name = c.full_name or c.first_name or (f"@{c.username}" if c.username else str(c.id))
        return c.id, c.username, c_name

    return None, None, "المستخدم"


@main_router.message(F.text.regexp(r"^(\.|\/)?(كتم|الغاء كتم|الغاء الكتم|فك كتم|فك الكتم)(\s+.+)?$"))
async def handle_mute_ops(message: Message, bot: Bot):
    if message.business_connection_id:
        async with async_session() as session:
            owner_id = await resolve_business_owner(str(message.business_connection_id), session, bot=bot)
        if message.from_user and message.from_user.id != owner_id:
            return
    else:
        owner_id = message.from_user.id
    if not owner_id:
        return

    text_clean = message.text.strip().lstrip("./")
    is_mute = text_clean.startswith("كتم")
    is_unmute = any(text_clean.startswith(prefix) for prefix in ["الغاء كتم", "الغاء الكتم", "فك كتم", "فك الكتم"])

    target_id, target_username, target_name = await _find_reply_or_target(message, bot, owner_id)
    await delete_safe_message(bot, message)

    chat_id = message.chat.id
    biz_id = str(message.business_connection_id) if message.business_connection_id else None

    if not target_id:
        return await send_safe_reply(
            message,
            f"<blockquote>{CE.ALERT} <b>تحديد العضو المطلوب</b></blockquote>\n\n"
            f"<blockquote>⚠️ يرجى الرد على رسالة العضو أو تحديد معرّفه بعد الأمر:\n"
            f"• <code>.كتم @username</code>\n"
            f"• <code>.كتم 123456789</code>\n"
            f"• أو كتابة الأمر داخل شات العضو الخاص مباشرة.</blockquote>",
            business_connection_id=biz_id
        )

    if target_id == owner_id or target_id == OWNER_ID:
        return await send_safe_reply(message, f"<blockquote>{CE.ALERT} <b>لا يمكنك كتم حسابك الخاص!</b></blockquote>", business_connection_id=biz_id)

    async with async_session() as session:
        if is_mute:
            ex = await session.scalar(select(MutedUser).where(
                or_(MutedUser.owner_id == owner_id, MutedUser.owner_id.is_(None)),
                MutedUser.user_id == target_id
            ))
            if not ex:
                session.add(MutedUser(owner_id=owner_id, user_id=target_id,
                                      chat_id=chat_id, username=target_username))
                await session.commit()
            if message.chat.type in ("group", "supergroup"):
                await _apply_restriction(bot, chat_id, target_id, restrict=True, business_connection_id=biz_id)

            alert_text = (
                f"<blockquote>{CE.BAN} <b>تم كتم المستخدم بنجاح</b></blockquote>\n\n"
                f"<blockquote>👤 <b>المستخدم:</b> <b>{html.quote(str(target_name))}</b>\n"
                f"🆔 <b>المعرّف:</b> <code>{target_id}</code>\n"
                f"🔒 <i>تم تفعيل الكتم ولن يتم الرد عليه أو تمرير رسائله.</i></blockquote>"
            )
            try:
                if biz_id:
                    await bot.send_message(chat_id=chat_id, text=alert_text,
                                           business_connection_id=biz_id, parse_mode=ParseMode.HTML)
                else:
                    await message.answer(alert_text, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.warning(f"send mute alert: {e}")

        elif is_unmute:
            await session.execute(delete(MutedUser).where(
                or_(MutedUser.owner_id == owner_id, MutedUser.owner_id.is_(None)),
                MutedUser.user_id == target_id
            ))
            await session.commit()
            if message.chat.type in ("group", "supergroup"):
                await _apply_restriction(bot, chat_id, target_id, restrict=False, business_connection_id=biz_id)

            alert_text = (
                f"<blockquote>{CE.CHECK_VERIFIED} <b>تم إلغاء كتم المستخدم بنجاح</b></blockquote>\n\n"
                f"<blockquote>👤 <b>المستخدم:</b> <b>{html.quote(str(target_name))}</b>\n"
                f"🆔 <b>المعرّف:</b> <code>{target_id}</code>\n"
                f"🔊 <i>تم رفع التقييد ويمكنه التواصل مجدداً.</i></blockquote>"
            )
            try:
                if biz_id:
                    await bot.send_message(chat_id=chat_id, text=alert_text,
                                           business_connection_id=biz_id, parse_mode=ParseMode.HTML)
                else:
                    await message.answer(alert_text, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.warning(f"send unmute alert: {e}")


@main_router.message(F.text.in_(["ث", "تثبيت", "غ ث", "الغاء تثبيت",
                                  ".ث", ".تثبيت", ".غ ث", ".الغاء تثبيت"]))
async def handle_pin_ops(message: Message, bot: Bot):
    if message.business_connection_id:
        async with async_session() as session:
            owner_id = await resolve_business_owner(str(message.business_connection_id), session, bot=bot)
        if message.from_user and message.from_user.id != owner_id:
            return
    elif message.from_user.id != OWNER_ID:
        async with async_session() as session:
            u = await get_or_create_user(message.from_user, session)
            if u.role not in ("owner", "admin"):
                return
    cmd = message.text.strip()
    r = message.reply_to_message
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception:
        pass
    if not r:
        return
    biz = str(message.business_connection_id) if message.business_connection_id else None
    try:
        kw = {"chat_id": message.chat.id, "message_id": r.message_id}
        if biz:
            kw["business_connection_id"] = biz
        if cmd in ["ث", "تثبيت", ".ث", ".تثبيت"]:
            await bot.pin_chat_message(**kw)
        elif cmd in ["غ ث", "الغاء تثبيت", ".غ ث", ".الغاء تثبيت"]:
            await bot.unpin_chat_message(**kw)
    except Exception as e:
        logger.warning(f"pin: {e}")


def estimate_account_creation_year(user_id: int) -> str:
    if user_id < 0:
        return "قناة / مجموعة"
    if user_id < 100_000_000:
        return "2013 - 2014 (قديم جداً 👑)"
    elif user_id < 300_000_000:
        return "2015 - 2016"
    elif user_id < 600_000_000:
        return "2017 - 2018"
    elif user_id < 1_100_000_000:
        return "2019 - 2020"
    elif user_id < 2_100_000_000:
        return "2021 - 2022"
    elif user_id < 5_500_000_000:
        return "2022 - 2023"
    elif user_id < 7_200_000_000:
        return "2023 - 2024"
    elif user_id < 8_200_000_000:
        return "2024 - 2025"
    else:
        return "2025 - 2026 (حساب حديث جداً ⚡)"


async def check_user_scam_status(user_id: int, full_name: str, username: Optional[str], bio: str) -> bool:
    try:
        async with async_session() as session:
            alerts_cnt = await session.scalar(select(func.count(SecurityAlertLog.id)).where(SecurityAlertLog.user_id == user_id)) or 0
            if alerts_cnt > 0:
                return True
            muted = await session.scalar(select(MutedUser).where(MutedUser.user_id == user_id))
            if muted:
                return True

        combined = f"{full_name} {username or ''} {bio or ''}".lower()
        suspicious_keywords = [
            "telegram support", "telegram admin", "telegram security", "telegram service",
            "fragment support", "wallet support", "ton support", "official support",
            "دعم تيليجرام", "ادارة تيليجرام", "فريق تيليجرام", "خدمة عملاء تيليجرام",
            "مشرف تيليجرام", "بوت أمان"
        ]
        for kw in suspicious_keywords:
            if kw in combined:
                return True

        fake_badges = ["✓", "✔", "☑️"]
        for fb in fake_badges:
            if fb in full_name:
                return True

        if any(p in (bio or "").lower() for p in ["t.me/claim", "t.me/airdrop", "gift-telegram", "free-telegram"]):
            return True
    except Exception as e:
        logger.debug(f"[SCAM_CHECK] {e}")

    return False


async def is_reveal_locked(chat_id: int) -> bool:
    try:
        async with async_session() as session:
            st = await session.scalar(
                select(ServiceSetting).where(
                    ServiceSetting.chat_id == chat_id,
                    ServiceSetting.service_key.in_(["id", "kashf", "reveal", "lock_id", "lock_kashf", "lock_reveal"])
                )
            )
            if st and not st.is_enabled:
                return True
    except Exception:
        pass
    return False


async def get_chat_user_messages_count(chat_id: int, user_id: int) -> int:
    try:
        async with async_session() as session:
            count = await session.scalar(
                select(func.count(UserMessageLog.id)).where(
                    UserMessageLog.chat_id == chat_id,
                    UserMessageLog.user_id == user_id
                )
            )
            return count or 0
    except Exception:
        return 0


async def get_chat_user_warnings_count(chat_id: int, user_id: int) -> int:
    try:
        async with async_session() as session:
            count = await session.scalar(
                select(func.count(SecurityAlertLog.id)).where(
                    SecurityAlertLog.chat_id == chat_id,
                    SecurityAlertLog.user_id == user_id
                )
            )
            return count or 0
    except Exception:
        return 0


async def get_chat_user_rank_and_title(bot: Bot, chat_id: int, user_id: int) -> Tuple[str, str]:
    try:
        member = await asyncio.wait_for(bot.get_chat_member(chat_id, user_id), timeout=2.5)
        status = member.status
        custom_title = getattr(member, "custom_title", None)
        if status == ChatMemberStatus.CREATOR:
            rank = "مالك المجموعة 👑"
            title = custom_title or "المالك الأساسي"
        elif status == ChatMemberStatus.ADMINISTRATOR:
            rank = "مشرف 👮‍♂️"
            title = custom_title or "مشرف المجموعة"
        elif status == ChatMemberStatus.MEMBER:
            rank = "عضو 👤"
            title = custom_title or "عضو مميز"
        elif status == ChatMemberStatus.RESTRICTED:
            rank = "مقيد ⚠️"
            title = custom_title or "عضو مقيد"
        elif status == ChatMemberStatus.LEFT:
            rank = "غادر 🚪"
            title = "عضو مغادر"
        elif status == ChatMemberStatus.KICKED:
            rank = "محظور 🚫"
            title = "عضو محظور"
        else:
            rank = "عضو 👤"
            title = custom_title or "عضو"
        return rank, title
    except Exception:
        return "عضو 👤", "عضو"


async def do_reveal_user_info(message: Message, bot: Bot, biz_id: Optional[str] = None):
    chat = message.chat
    chat_id = chat.id

    # ── فحص التفعيل/التعطيل (مربوط بقفل "ايدي" + توافق مع "كشف" القديم) ──
    if await is_reveal_locked(chat_id):
        return await send_safe_reply(message, "هذا الأمر معطل", business_connection_id=biz_id)

    # ── تحديد الهدف: من الرد أو المرسل نفسه أو المعرف ──
    target_user = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user = message.reply_to_message.from_user
    elif message.text:
        parts = message.text.strip().split()
        if len(parts) > 1:
            arg = parts[1].strip()
            if arg.isdigit():
                try:
                    c = await asyncio.wait_for(bot.get_chat(int(arg)), timeout=2.5)
                    target_user = c
                except Exception:
                    pass
            elif arg.startswith("@"):
                try:
                    c = await asyncio.wait_for(bot.get_chat(arg), timeout=2.5)
                    target_user = c
                except Exception:
                    pass

    if not target_user:
        target_user = message.from_user

    if not target_user:
        return await send_safe_reply(message, "❌ تعذر تحديد المستخدم المستهدف", business_connection_id=biz_id)

    try:
        user_id = target_user.id
        username = f"@{target_user.username}" if getattr(target_user, "username", None) else "لا يوجد"
        profile_link = f"https://t.me/{target_user.username}" if getattr(target_user, "username", None) else f"tg://user?id={user_id}"

        # جلب معلومات الشات والبايو
        bio = "لا يوجد"
        user_info = None
        try:
            user_info = await asyncio.wait_for(bot.get_chat(user_id), timeout=2.5)
            if user_info and getattr(user_info, "bio", None):
                bio = user_info.bio
        except Exception:
            pass

        # معلومات تقنية
        lang = getattr(target_user, "language_code", None) or "غير متوفر"
        dc_id = getattr(target_user, "dc_id", None) or "غير متوفر"

        # فحص بريميوم الحقيقي المباشر فقط من تيليجرام
        is_prem_val = bool(getattr(target_user, "is_premium", False))
        if not is_prem_val and message.from_user and message.from_user.id == user_id:
            is_prem_val = bool(getattr(message.from_user, "is_premium", False))
        if not is_prem_val and message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == user_id:
            is_prem_val = bool(getattr(message.reply_to_message.from_user, "is_premium", False))

        is_premium = f"{CE.CHECK_VERIFIED} <b><tg-spoiler>حساب بريميوم 💎</tg-spoiler></b>" if is_prem_val else f"{CE.CROSS} <i>عادي</i>"
        is_bot = f"{CE.ALERT} <b><tg-spoiler>حساب بوت 🤖</tg-spoiler></b>" if getattr(target_user, "is_bot", False) else f"{CE.CHECK} <i>حساب بشري</i>"

        # صورة البروفايل
        has_photo = f"{CE.CROSS} <i>غير متوفرة</i>"
        photo_file_id = None
        try:
            user_photos = await asyncio.wait_for(bot.get_user_profile_photos(user_id, limit=1), timeout=2.5)
            if user_photos and user_photos.photos and len(user_photos.photos) > 0:
                has_photo = f"{CE.CHECK_VERIFIED} <i>متوفرة</i>"
                photo_file_id = user_photos.photos[0][-1].file_id
        except Exception:
            pass

        if not photo_file_id and user_info and getattr(user_info, "photo", None):
            has_photo = f"{CE.CHECK_VERIFIED} <i>متوفرة</i>"
            photo_file_id = getattr(user_info.photo, "big_file_id", None) or getattr(user_info.photo, "small_file_id", None)

        # زر الملف الشخصي الملون مع الإيموجي المميز لجميع الشاتات
        first_name_btn = getattr(target_user, "first_name", None) or getattr(target_user, "title", None) or "المستخدم"
        button = InlineKeyboardMarkup(inline_keyboard=[[
            make_btn(
                f"{first_name_btn}",
                url=profile_link,
                style="primary",
                icon_custom_emoji_id=CUSTOM_EMOJI_IDS.get("user")
            )
        ]])

        # الحالة العشوائية
        random_phrase = random.choice([
            "تعبان 😴", "عنده برد 🤧", "سخن ومتحمس 🔥", "هبوط 📉",
            "بيتكوهن 💸", "بيتعلق 🫠", "بيتمحن 🤕", "بيستهبل 🤪",
            "عيان 😷", "في النازل ⬇️"
        ])

        if hasattr(target_user, "mention_html"):
            user_mention = target_user.mention_html()
        else:
            user_mention = f'<a href="{profile_link}"><b>{html.escape(first_name_btn)}</b></a>'

        is_group = chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

        if is_group:
            # جلب إحصائيات العضو
            messages_count = await get_chat_user_messages_count(chat_id, user_id)
            warnings_count = await get_chat_user_warnings_count(chat_id, user_id)
            rank, rank_title = await get_chat_user_rank_and_title(bot, chat_id, user_id)

            caption = (
                f"<blockquote>{CE.USER} <b>بطاقة كشف هوية المستخدم</b></blockquote>\n"
                f"┌ {CE.USER} <b>الاسم:</b> {user_mention}\n"
                f"├ {CE.KEY} <b>اليوزر:</b> <i>{username}</i>\n"
                f"├ {CE.DOC} <b>الآيدي:</b> <code>{user_id}</code>\n"
                f"├ {CE.GLOBE} <b>السيرفر (DC):</b> <tg-spoiler>DC {dc_id}</tg-spoiler>\n"
                f"├ {CE.INFO} <b>اللغة:</b> <i>{lang}</i>\n"
                f"└ {CE.CHAT} <b>البايو:</b> <i>{bio}</i>\n\n"
                f"<blockquote>{CE.STATS} <b>إحصائيات ورتبة المجموعة</b></blockquote>\n"
                f"┌ {CE.CROWN} <b>الرتبة:</b> <b>{rank}</b> (<i>{rank_title}</i>)\n"
                f"├ {CE.CHAT} <b>إجمالي الرسائل:</b> <code>{messages_count}</code> رسالة\n"
                f"├ {CE.ALERT} <b>سجل الإنذارات:</b> <tg-spoiler>{warnings_count} / 3</tg-spoiler>\n"
                f"└ {CE.SPARKLES} <b>المزاج الحالي:</b> <i>{random_phrase}</i>\n\n"
                f"<blockquote>{CE.SEARCH} <b>بيانات الحساب التقنية</b></blockquote>\n"
                f"┌ {CE.DIAMOND} <b>حالة البريميوم:</b> {is_premium}\n"
                f"├ {CE.ROBOT} <b>نوع الحساب:</b> {is_bot}\n"
                f"└ {CE.CAM} <b>صورة الحساب:</b> {has_photo}\n\n"
                f"<blockquote>{CE.FIRE} <b>معلومات المجموعة الحالية</b></blockquote>\n"
                f"┌ {CE.PIN} <b>الاسم:</b> <b>{html.escape(chat.title or '')}</b>\n"
                f"├ {CE.KEY} <b>الآيدي:</b> <code>{chat.id}</code>\n"
                f"└ {CE.GLOBE} <b>المعرف:</b> <i>@{chat.username if chat.username else 'لا يوجد'}</i>"
            )
        else:
            caption = (
                f"<blockquote>{CE.USER} <b>بطاقة كشف هوية المستخدم</b></blockquote>\n"
                f"┌ {CE.USER} <b>الاسم:</b> {user_mention}\n"
                f"├ {CE.KEY} <b>اليوزر:</b> <i>{username}</i>\n"
                f"├ {CE.DOC} <b>الآيدي:</b> <code>{user_id}</code>\n"
                f"├ {CE.GLOBE} <b>السيرفر (DC):</b> <tg-spoiler>DC {dc_id}</tg-spoiler>\n"
                f"├ {CE.INFO} <b>اللغة:</b> <i>{lang}</i>\n"
                f"├ {CE.SPARKLES} <b>المزاج الحالي:</b> <i>{random_phrase}</i>\n"
                f"└ {CE.CHAT} <b>البايو:</b> <i>{bio}</i>\n\n"
                f"<blockquote>{CE.SEARCH} <b>بيانات الحساب التقنية</b></blockquote>\n"
                f"┌ {CE.DIAMOND} <b>حالة البريميوم:</b> {is_premium}\n"
                f"├ {CE.ROBOT} <b>نوع الحساب:</b> {is_bot}\n"
                f"└ {CE.CAM} <b>صورة الحساب:</b> {has_photo}"
            )

        if photo_file_id:
            try:
                if biz_id:
                    return await bot.send_photo(
                        chat_id=message.chat.id,
                        photo=photo_file_id,
                        caption=caption,
                        reply_markup=button,
                        has_spoiler=True,
                        parse_mode=ParseMode.HTML,
                        business_connection_id=biz_id,
                        reply_parameters=ReplyParameters(message_id=message.message_id)
                    )
                else:
                    return await message.reply_photo(
                        photo=photo_file_id,
                        caption=caption,
                        reply_markup=button,
                        has_spoiler=True,
                        parse_mode=ParseMode.HTML
                    )
            except Exception as e:
                logger.warning(f"send_photo with spoiler and button error: {e}")
                try:
                    if biz_id:
                        return await bot.send_photo(
                            chat_id=message.chat.id,
                            photo=photo_file_id,
                            caption=caption,
                            reply_markup=button,
                            has_spoiler=True,
                            parse_mode=ParseMode.HTML,
                            business_connection_id=biz_id
                        )
                    else:
                        return await bot.send_photo(
                            chat_id=message.chat.id,
                            photo=photo_file_id,
                            caption=caption,
                            reply_markup=button,
                            has_spoiler=True,
                            parse_mode=ParseMode.HTML
                        )
                except Exception as e2:
                    logger.warning(f"send_photo fallback error: {e2}")

        return await send_safe_reply(message, caption, reply_markup=button, business_connection_id=biz_id, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"Error in reveal_user_info: {e}")
        await send_safe_reply(message, "❌ حدث خطأ أثناء جلب المعلومات", business_connection_id=biz_id)


@main_router.message(
    F.text.in_(["كشف", "الكشف", "ايدي", "الأيدي", "الايدي", "ID", "id", "ا"]) |
    F.text.regexp(r"^(\.|\/)?(كشف|الكشف|ايدي|الأيدي|الايدي|id|ID|ا)(\s+.+)?$")
)
async def cmd_reveal_user_info_handler(message: Message, bot: Bot):
    biz_id = str(message.business_connection_id) if message.business_connection_id else None
    if biz_id:
        async with async_session() as session:
            owner_id = await resolve_business_owner(biz_id, session, bot=bot)
        if message.from_user and message.from_user.id != owner_id:
            return
    await do_reveal_user_info(message, bot, biz_id=biz_id)


# =============================================================================
# 30.5  ميزات جديدة: تحويل صوت + لخص + فكرني + بلاك ليست
# =============================================================================

async def _auto_transcribe_voice(bot: Bot, message: Message, owner_id: int):
    """مهمة خلفية: تحويل الفويس إلى نص وإرسال النتيجة للأونر"""
    try:
        biz_id = str(message.business_connection_id) if message.business_connection_id else None
        sender_name = html.quote(message.from_user.full_name if message.from_user else "مجهول")
        w = await send_safe_reply(message, "🎙️ <i>جاري تحويل الصوت إلى نص...</i>",
                                   business_connection_id=biz_id)
        result = await transcribe_voice(bot, message.voice)
        if result:
            reply_text = (
                f"<blockquote>🎙️ <b>تحويل صوت إلى نص</b>\n"
                f"👤 {sender_name}</blockquote>\n\n"
                f"<blockquote>{html.quote(result)}</blockquote>"
            )
        else:
            reply_text = f"<blockquote>❌ <b>تعذر تحويل الصوت إلى نص</b></blockquote>"
        if w:
            try:
                await w.edit_text(reply_text, parse_mode=ParseMode.HTML)
            except Exception:
                await send_safe_reply(message, reply_text, business_connection_id=biz_id)
        else:
            await send_safe_reply(message, reply_text, business_connection_id=biz_id)
    except Exception as e:
        logger.warning(f"[AUTO_TRANSCRIBE] {e}")


# ─── أمر .صوت / .نص - تحويل فويس بالرد ───
@main_router.message(F.text.regexp(r"^(\.)?(?:صوت|نص الصوت|فرغ الصوت|نص)$"))
async def cmd_voice_to_text(message: Message, bot: Bot):
    biz_id = str(message.business_connection_id) if message.business_connection_id else None
    if biz_id:
        async with async_session() as session:
            owner_id = await resolve_business_owner(biz_id, session, bot=bot)
        if message.from_user and message.from_user.id != owner_id:
            return
    replied = message.reply_to_message
    if not replied or not replied.voice:
        return await send_safe_reply(message,
            "<blockquote>🎙️ <b>تحويل الصوت إلى نص</b></blockquote>\n\n"
            "<blockquote>⚠️ الرجاء الرد على رسالة صوتية (فويس نوت) بهذا الأمر.</blockquote>",
            business_connection_id=biz_id)
    await delete_safe_message(bot, message)
    w = await send_safe_reply(replied, "🎙️ <i>جاري تحويل الصوت إلى نص...</i>",
                               business_connection_id=biz_id)
    if not (OPENAI_API_KEY or GEMINI_API_KEY):
        if w:
            await w.edit_text("<blockquote>⚠️ <b>لا يوجد مفتاح AI مُفعّل</b></blockquote>", parse_mode=ParseMode.HTML)
        return
    result = await transcribe_voice(bot, replied.voice)
    text = (f"<blockquote>🎙️ <b>نص الرسالة الصوتية</b></blockquote>\n\n"
            f"<blockquote>{html.quote(result)}</blockquote>") if result else \
           "<blockquote>❌ <b>تعذر تحويل الصوت إلى نص</b></blockquote>"
    if w:
        try:
            await w.edit_text(text, parse_mode=ParseMode.HTML)
            return
        except Exception:
            pass
    await send_safe_reply(replied, text, business_connection_id=biz_id)


# ─── أمر .لخص - تلخيص رسالة أو نص ───
@main_router.message(F.text.regexp(r"^(\.)?(?:لخص|تلخيص|خلاصة|خلاصه)(.*)$", flags=re.I | re.DOTALL))
async def cmd_summarize(message: Message, bot: Bot):
    biz_id = str(message.business_connection_id) if message.business_connection_id else None
    if biz_id:
        async with async_session() as session:
            owner_id = await resolve_business_owner(biz_id, session, bot=bot)
        if message.from_user and message.from_user.id != owner_id:
            return
    if not (OPENAI_API_KEY or GEMINI_API_KEY):
        return await send_safe_reply(message,
            "<blockquote>⚠️ <b>التلخيص يتطلب تفعيل مفتاح AI</b></blockquote>",
            business_connection_id=biz_id)
    # المحتوى الذي سيُلخَّص
    raw_cmd = message.text.strip()
    m = re.match(r"^\.?(?:لخص|تلخيص|خلاصة|خلاصه)\s+(.+)$", raw_cmd, re.I | re.DOTALL)
    to_summarize = m.group(1).strip() if m else ""
    if not to_summarize and message.reply_to_message:
        to_summarize = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()
    if not to_summarize:
        return await send_safe_reply(message,
            "<blockquote>📋 <b>أمر التلخيص</b></blockquote>\n\n"
            "<blockquote>📝 <b>الاستخدام:</b>\n"
            "• الرد على أي رسالة طويلة بـ <code>.لخص</code>\n"
            "• أو <code>.لخص النص المراد تلخيصه</code></blockquote>",
            business_connection_id=biz_id)
    await delete_safe_message(bot, message)
    w = await send_safe_reply(message.reply_to_message or message,
                               "📋 <i>جاري التلخيص...</i>", business_connection_id=biz_id)
    prompt = f"لخص النص التالي باختصار شديد في نقاط رئيسية واضحة (3-5 نقاط كحد أقصى):\n\n{to_summarize}"
    ai_key = f"sum_{biz_id}_{message.chat.id}" if biz_id else f"sum_{message.from_user.id if message.from_user else 0}"
    result = await AIAssistant.reply(ai_key, prompt)
    text = (f"<blockquote>📋 <b>الخلاصة</b></blockquote>\n\n"
            f"<blockquote>{html.quote(result)}</blockquote>") if result else \
           "<blockquote>❌ <b>تعذر التلخيص حالياً</b></blockquote>"
    if w:
        try:
            await w.edit_text(text, parse_mode=ParseMode.HTML)
            return
        except Exception:
            pass
    await send_safe_reply(message, text, business_connection_id=biz_id)


# ─── أمر فكرني - التذكير بالرد على عميل ───
@main_router.message(F.text.regexp(r"فكرني\s+بعد|فكرني\s+بكره|فكرني\s+غد|فكرني\s+النهارده", flags=re.I | re.U))
async def cmd_remind_me(message: Message, bot: Bot):
    biz_id = str(message.business_connection_id) if message.business_connection_id else None
    if biz_id:
        async with async_session() as session:
            owner_id = await resolve_business_owner(biz_id, session, bot=bot)
        if message.from_user and message.from_user.id != owner_id:
            return
    else:
        owner_id = message.from_user.id if message.from_user else None
    if not owner_id:
        return
    raw = message.text.strip()
    remind_at = _parse_reminder_time(raw)
    if not remind_at:
        return await send_safe_reply(message,
            "<blockquote>⏰ <b>أمر التذكير</b></blockquote>\n\n"
            "<blockquote>📝 <b>الاستخدام:</b>\n"
            "• <code>فكرني بعد 30 دقيقة</code>\n"
            "• <code>فكرني بعد 2 ساعة</code>\n"
            "• <code>فكرني بعد 1 يوم</code>\n"
            "• <code>فكرني بكره</code></blockquote>",
            business_connection_id=biz_id)
    replied = message.reply_to_message
    customer_id = replied.from_user.id if replied and replied.from_user else None
    customer_name = replied.from_user.full_name if replied and replied.from_user else None
    msg_id = replied.message_id if replied else None
    chat_title = message.chat.title or None
    note_match = re.search(r"فكرني\s+بعد\s+\d+(?:\.\d+)?\s*\w+\s+(.*)", raw, re.I | re.U | re.DOTALL)
    note_match2 = re.search(r"فكرني\s+\w+\s+(.*)", raw, re.I | re.U | re.DOTALL)
    note = (note_match or note_match2)
    note = note.group(1).strip() if note else ""
    async with async_session() as session:
        session.add(CustomerReminder(
            owner_id=owner_id, chat_id=message.chat.id, chat_title=chat_title,
            customer_id=customer_id, customer_name=customer_name,
            message_id=msg_id, reminder_text=note, remind_at=remind_at))
        await session.commit()
    await delete_safe_message(bot, message)
    delta = remind_at - datetime.datetime.utcnow()
    total_mins = int(delta.total_seconds() / 60)
    if total_mins >= 1440:
        time_str = f"{total_mins // 1440} يوم"
    elif total_mins >= 60:
        time_str = f"{total_mins // 60} ساعة"
    else:
        time_str = f"{total_mins} دقيقة"
    customer_line = f"\n👤 سيُذكرك بالرد على <b>{html.quote(customer_name)}</b>" if customer_name else ""
    confirm = (f"<blockquote>✅ <b>تم ضبط التذكير</b></blockquote>\n\n"
               f"<blockquote>⏰ بعد <b>{time_str}</b>{customer_line}"
               f"{chr(10) + '📝 ' + html.quote(note) if note else ''}</blockquote>")
    try:
        notif = await bot.send_message(owner_id, confirm, parse_mode=ParseMode.HTML)
        await asyncio.sleep(6)
        try:
            await bot.delete_message(owner_id, notif.message_id)
        except Exception:
            pass
    except Exception:
        pass


# ─── إدارة قائمة الكلمات المحظورة ───
@main_router.message(F.text.regexp(r"^(\.)?(?:حظر كلمة|اضف محظور|بلاك ليست|اضف كلمة محظورة)\s+(.+)$", flags=re.I | re.U))
async def cmd_add_blacklist(message: Message, bot: Bot):
    biz_id = str(message.business_connection_id) if message.business_connection_id else None
    if biz_id:
        async with async_session() as session:
            owner_id = await resolve_business_owner(biz_id, session, bot=bot)
        if message.from_user and message.from_user.id != owner_id:
            return
    else:
        owner_id = message.from_user.id if message.from_user else None
    if not owner_id:
        return
    raw = message.text.strip()
    m = re.search(r"(?:حظر كلمة|اضف محظور|بلاك ليست|اضف كلمة محظورة)\s+(.+)$", raw, re.I | re.U)
    if not m:
        return
    word = m.group(1).strip().lower()
    if not word:
        return
    await delete_safe_message(bot, message)
    async with async_session() as session:
        existing = await session.scalar(select(BlacklistWord).where(
            BlacklistWord.owner_id == owner_id, BlacklistWord.word == word))
        if existing:
            return await bot.send_message(owner_id,
                f"<blockquote>⚠️ الكلمة <code>{html.quote(word)}</code> موجودة بالفعل في القائمة.</blockquote>",
                parse_mode=ParseMode.HTML)
        session.add(BlacklistWord(owner_id=owner_id, word=word))
        await session.commit()
    await bot.send_message(owner_id,
        f"<blockquote>✅ <b>تمت إضافة الكلمة المحظورة</b></blockquote>\n\n"
        f"🔤 <code>{html.quote(word)}</code>\n"
        f"<i>سيتم حذف أي رسالة تحتوي عليها تلقائياً.</i>",
        parse_mode=ParseMode.HTML)


@main_router.message(F.text.regexp(r"^(\.)?(?:الغاء حظر|ازل محظور|فك حظر كلمة)\s+(.+)$", flags=re.I | re.U))
async def cmd_remove_blacklist(message: Message, bot: Bot):
    biz_id = str(message.business_connection_id) if message.business_connection_id else None
    if biz_id:
        async with async_session() as session:
            owner_id = await resolve_business_owner(biz_id, session, bot=bot)
        if message.from_user and message.from_user.id != owner_id:
            return
    else:
        owner_id = message.from_user.id if message.from_user else None
    if not owner_id:
        return
    raw = message.text.strip()
    m = re.search(r"(?:الغاء حظر|ازل محظور|فك حظر كلمة)\s+(.+)$", raw, re.I | re.U)
    if not m:
        return
    word = m.group(1).strip().lower()
    await delete_safe_message(bot, message)
    async with async_session() as session:
        result = await session.execute(delete(BlacklistWord).where(
            BlacklistWord.owner_id == owner_id, BlacklistWord.word == word))
        await session.commit()
        deleted = result.rowcount
    if deleted:
        await bot.send_message(owner_id,
            f"<blockquote>✅ <b>تم رفع الحظر عن الكلمة</b></blockquote>\n🔤 <code>{html.quote(word)}</code>",
            parse_mode=ParseMode.HTML)
    else:
        await bot.send_message(owner_id,
            f"<blockquote>⚠️ الكلمة <code>{html.quote(word)}</code> غير موجودة في القائمة.</blockquote>",
            parse_mode=ParseMode.HTML)


@main_router.message(F.text.regexp(r"^(\.)?(?:قائمة المحظورة|الكلمات المحظورة|بلاك ليست)$", flags=re.I | re.U))
async def cmd_list_blacklist(message: Message, bot: Bot):
    biz_id = str(message.business_connection_id) if message.business_connection_id else None
    if biz_id:
        async with async_session() as session:
            owner_id = await resolve_business_owner(biz_id, session, bot=bot)
        if message.from_user and message.from_user.id != owner_id:
            return
    else:
        owner_id = message.from_user.id if message.from_user else None
    if not owner_id:
        return
    await delete_safe_message(bot, message)
    words = await get_blacklist_words(owner_id)
    if not words:
        return await bot.send_message(owner_id,
            "<blockquote>📋 <b>قائمة الكلمات المحظورة فارغة</b></blockquote>\n\n"
            "<blockquote>💡 أضف كلمة بـ: <code>حظر كلمة [الكلمة]</code></blockquote>",
            parse_mode=ParseMode.HTML)
    words_text = "\n".join(f"• <code>{html.quote(w)}</code>" for w in words)
    await bot.send_message(owner_id,
        f"<blockquote>🚫 <b>الكلمات المحظورة ({len(words)})</b></blockquote>\n\n"
        f"<blockquote>{words_text}</blockquote>\n\n"
        f"<blockquote>💡 لرفع الحظر: <code>الغاء حظر [الكلمة]</code></blockquote>",
        parse_mode=ParseMode.HTML)


async def auto_save_business_media(bot: Bot, message: Message, owner_id: int, is_from_owner: bool):
    """
    حفظ وسائط الرسائل الذاتية والمؤقتة فوراً للحساب
    """
    try:
        media_obj = None
        media_type = None
        if message.photo:
            media_obj = message.photo
            media_type = "photo"
        elif message.video:
            media_obj = message.video
            media_type = "video"
        elif message.voice:
            media_obj = message.voice
            media_type = "voice"
        elif message.video_note:
            media_obj = message.video_note
            media_type = "video_note"
        elif message.document:
            mime = (message.document.mime_type or "").lower()
            if mime.startswith("image/") or mime.startswith("video/") or mime.startswith("audio/"):
                media_obj = message.document
                media_type = "document"

        if not media_obj:
            return

        has_spoiler = getattr(message, "has_media_spoiler", False)

        si = html.quote(message.from_user.full_name if message.from_user else "مجهول")
        sid = message.from_user.id if message.from_user else 0
        sender_tag = "حسابك الشخصي (تجربة)" if is_from_owner else "العميل"

        tag_type = "وسائط مخفية (حرق / سبويلر)" if has_spoiler else "ميديا ذاتية / مؤقتة"

        cap = (
            f"<blockquote>{CE.CAM} <b>حفظ تلقائي للرسائل والوسائط الذاتية</b></blockquote>\n\n"
            f"┌ 👤 <b>{sender_tag}:</b> <b>{si}</b> (<code>{sid}</code>)\n"
            f"├ 🏷️ <b>الحالة:</b> <i>{tag_type}</i>\n"
            f"└ 💬 <b>المحادثة:</b> <i>{html.quote(message.chat.title or 'خاص')}</i>"
        )
        if message.caption:
            cap += f"\n\n<blockquote>📝 <b>الوصف المرفق:</b>\n<i>{html.quote(message.caption)}</i></blockquote>"

        logger.info(f"🔒 [AUTO-SAVE] جاري حفظ ميديا ({media_type}) تلقائياً للحساب {owner_id} من {sid}")
        await download_and_send_media(bot, owner_id, media_obj, media_type, cap)
        logger.info(f"✅ [AUTO-SAVE] تم حفظ وإرسال الميديا الذاتية بنجاح للحساب {owner_id}")
    except Exception as e:
        logger.warning(f"❌ [AUTO-SAVE] خطأ أثناء الحفظ التلقائي: {e}")


# =============================================================================
# 29. معالج Business الرئيسي
# =============================================================================
@main_router.business_message()
async def handle_business_message(message: Message, bot: Bot):
    if not message.business_connection_id:
        return
    async with async_session() as session:
        owner_id = await resolve_business_owner(str(message.business_connection_id), session, bot=bot)
    if not owner_id:
        logger.warning(f"⚠️ Business connection غير معروف: {message.business_connection_id}")
        return
    await track_business_chat(message, owner_id)
    async with async_session() as session:
        active = await is_subscription_active(owner_id, session)
    if not active:
        logger.info(f"⏸️ اشتراك {owner_id} منتهي — تجاهل رسالة")
        return
    is_from_owner = bool(message.from_user and message.from_user.id == owner_id)

    # ─── الحفظ التلقائي للوسائط والرسائل الذاتية ───
    if await check_auto_save_enabled(owner_id):
        asyncio.create_task(auto_save_business_media(bot, message, owner_id, is_from_owner))

    if not is_from_owner:
        await log_incoming_message(message, owner_id)
    if message.from_user and not is_from_owner:
        async with async_session() as session:
            muted = await session.scalar(select(MutedUser).where(
                or_(MutedUser.owner_id == owner_id, MutedUser.owner_id.is_(None)),
                or_(MutedUser.user_id == message.from_user.id, MutedUser.user_id == message.chat.id)
            ))
            if muted:
                if message.chat.type in ("group", "supergroup"):
                    await _apply_restriction(bot, message.chat.id, message.from_user.id, restrict=True)
                await delete_safe_message(bot, message)
                return
    async with async_session() as session:
        sub = await get_mandatory_sub(owner_id, session)
        sec = await get_or_create_security(owner_id, session)
    logger.info(f"[BIZ_MSG] owner={owner_id} sub={bool(sub)} enabled={getattr(sub,'is_enabled',None)} channel={getattr(sub,'channel_id',None)} from_user={message.from_user.id if message.from_user else None} is_owner={is_from_owner}")
    if sub and sub.is_enabled and sub.channel_id and message.from_user and not is_from_owner:
        is_subbed = await check_channel_subscription(
            bot, message.from_user.id, sub)
        if not is_subbed:
            await delete_safe_message(bot, message)
            # نرسل رسالة الاشتراك الإجباري في شات البيزنس (أكونتك مع العميل)
            await send_mandatory_sub_message(
                bot, message.chat.id, sub,
                business_connection_id=str(message.business_connection_id))
            return
    if sec.hyperlink_protection and message.from_user and not is_from_owner:
        spoof = detect_hyperlink_spoofing(message)
        if spoof:
            displayed, real_url = spoof
            if sec.auto_delete_malicious:
                await delete_safe_message(bot, message)
            async with async_session() as session:
                session.add(SecurityAlertLog(
                    owner_id=owner_id, user_id=message.from_user.id,
                    chat_id=message.chat.id, alert_type="HyperlinkSpoofing",
                    displayed_text=displayed, real_url=real_url))
                await session.commit()
            if sec.notify_security_alerts:
                s = message.from_user
                un = f"@{s.username}" if s.username else "—"
                alert = (
                    f"<blockquote>{CE.ALERT} <b>تنبيه أمان: رصد محاولة تصيد واحتيال!</b></blockquote>\n\n"
                    f"┌ 👤 <b>العميل:</b> <b>{html.quote(s.full_name)}</b> (<code>{s.id}</code>)\n"
                    f"└ 🔖 <b>اليوزر:</b> <i>{html.quote(un)}</i>\n\n"
                    f"<blockquote>{CE.SHIELD} <b>انتحال الروابط (Hyperlink Spoofing)</b>\n\n"
                    f"🔗 <b>النص الظاهر:</b>\n<code>{html.quote(displayed)}</code>\n\n"
                    f"⚠️ <b>الرابط الخفي الحقيقي:</b>\n<tg-spoiler>{html.quote(real_url)}</tg-spoiler></blockquote>\n\n"
                    f"<i>{CE.CHECK_VERIFIED} تم حذف الرسالة تلقائياً لحماية حسابك وعملائك.</i>")
                try:
                    await bot.send_message(owner_id, alert,
                                           reply_markup=get_security_alert_kb(owner_id, s.id, message.chat.id),
                                           parse_mode=ParseMode.HTML)
                except Exception:
                    pass
            try:
                await bot.send_message(chat_id=message.chat.id,
                                       text=f"<blockquote>{CE.SHIELD} <b>تنبيه أمان:</b> <i>تم حظر الرسالة لاحتوائها على روابط غير آمنة أو مشبوهة.</i></blockquote>",
                                       business_connection_id=str(message.business_connection_id),
                                       parse_mode=ParseMode.HTML)
            except Exception:
                pass
            return
    if not is_from_owner:
        if message.from_user:
            welcome_sent = await process_business_welcome(
                bot=bot,
                message=message,
                owner_id=owner_id,
                connection_id=str(message.business_connection_id),
                customer=message.from_user
            )
            if welcome_sent:
                await asyncio.sleep(0.5)

        # ─── فلتر الكلمات المحظورة ───
        msg_text = message.text or message.caption or ""
        if msg_text:
            banned_word = await check_blacklist(msg_text, owner_id)
            if banned_word:
                await delete_safe_message(bot, message)
                try:
                    await bot.send_message(
                        owner_id,
                        f"<blockquote>{CE.CROSS_BAN} <b>تنبيه: رسالة محتوية على كلمة محظورة</b></blockquote>\n\n"
                        f"┌ 👤 <b>المرسل:</b> <b>{html.quote(message.from_user.full_name if message.from_user else 'مجهول')}</b>\n"
                        f"├ 🔤 <b>الكلمة:</b> <tg-spoiler>{html.quote(banned_word)}</tg-spoiler>\n"
                        f"└ 💬 <b>المحادثة:</b> <i>{html.quote(message.chat.title or 'خاص')}</i>\n\n"
                        f"<i>{CE.TRASH} تم حذف الرسالة تلقائياً.</i>",
                        parse_mode=ParseMode.HTML)
                except Exception:
                    pass
                return

        # ─── تحويل الفويس إلى نص تلقائياً ───
        if message.voice and message.from_user:
            async with async_session() as _s:
                _vt_set = await _s.scalar(select(ServiceSetting).where(
                    ServiceSetting.user_id == owner_id,
                    ServiceSetting.service_key == "voice_to_text"))
            if _vt_set and _vt_set.is_enabled:
                asyncio.create_task(_auto_transcribe_voice(bot, message, owner_id))

    if message.text:
        await handle_general_text_messages(message, bot, owner_id=owner_id)


@main_router.edited_business_message()
async def handle_edited_business_message(message: Message, bot: Bot):
    if not message.business_connection_id or not message.from_user:
        return
    async with async_session() as session:
        owner_id = await resolve_business_owner(str(message.business_connection_id), session, bot=bot)
        if not owner_id or message.from_user.id == owner_id:
            return

        muted = await session.scalar(select(MutedUser).where(
            or_(MutedUser.owner_id == owner_id, MutedUser.owner_id.is_(None)),
            or_(MutedUser.user_id == message.from_user.id, MutedUser.user_id == message.chat.id)
        ))
        if muted:
            await delete_safe_message(bot, message)
            return

        res = await session.execute(select(UserMessageLog).where(
            UserMessageLog.chat_id == message.chat.id,
            UserMessageLog.message_id == message.message_id))
        old = res.scalar_one_or_none()
        old_text = old.text_content if old and old.text_content else "<i>(غير متوفر)</i>"
        new_text = message.text or message.caption or "<i>(وسائط)</i>"
        if old:
            old.text_content = message.text or message.caption
            if message.from_user:
                old.sender_full_name = message.from_user.full_name
                old.sender_username = message.from_user.username
            await session.commit()
        else:
            session.add(UserMessageLog(
                business_connection_id=str(message.business_connection_id),
                chat_id=message.chat.id,
                message_id=message.message_id,
                user_id=message.from_user.id,
                owner_id=owner_id,
                sender_full_name=message.from_user.full_name,
                sender_username=message.from_user.username,
                text_content=message.text or message.caption
            ))
            await session.commit()
    s = message.from_user
    un = f"@{s.username}" if s.username else "—"
    alert = (
        "<blockquote>✏️ <b>تنبيه: العميل عدّل رسالة</b></blockquote>\n\n"
        f"👤 {html.quote(s.full_name)} (<code>{s.id}</code>)\n"
        f"👤 {html.quote(un)}\n\n"
        f"📝 <b>قبل:</b>\n<i>{html.quote(old_text[:1500])}</i>\n\n"
        f"📝 <b>بعد:</b>\n<i>{html.quote(new_text[:1500])}</i>")
    try:
        await bot.send_message(owner_id, alert, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.warning(f"[EDIT] {e}")


@main_router.deleted_business_messages()
async def handle_business_messages_deleted(event: BusinessMessagesDeleted, bot: Bot):
    async with async_session() as session:
        owner_id = await resolve_business_owner(str(event.business_connection_id), session, bot=bot)
    if not owner_id:
        return
    chat_id = event.chat.id

    media_labels = {
        "photo": "📷 صورة",
        "video": "🎥 فيديو",
        "voice": "🎙️ رسالة صوتية",
        "audio": "🎵 مقطع صوتي",
        "document": "📄 مستند",
        "sticker": "🎭 ملصق",
        "video_note": "🔘 فيديو دائري",
        "animation": "🎬 صورة متحركة (GIF)",
    }

    for msg_id in event.message_ids:
        sender_id = None
        sender_name = None
        sender_username = None
        text_content = None
        media_type = None
        media_file_id = None
        log_id = None

        async with async_session() as session:
            res = await session.execute(select(UserMessageLog).where(
                UserMessageLog.chat_id == chat_id,
                UserMessageLog.message_id == msg_id))
            log = res.scalar_one_or_none()
            if log:
                log_id = log.id
                sender_id = log.user_id
                sender_name = log.sender_full_name
                sender_username = log.sender_username
                text_content = log.text_content
                media_type = log.media_type
                media_file_id = log.media_file_id

        # إذا كانت الرسالة المحذوفة مرسلة من صاحب الحساب نفسه، أو لا يوجد لها سجل، يتم تجاهلها
        if not log or sender_id == owner_id:
            if log_id:
                try:
                    async with async_session() as session:
                        await session.execute(delete(UserMessageLog).where(UserMessageLog.id == log_id))
                        await session.commit()
                except Exception:
                    pass
            continue

        if not sender_id or not sender_name:
            async with async_session() as session:
                bchat = await session.scalar(select(BusinessChat).where(
                    BusinessChat.chat_id == chat_id,
                    BusinessChat.owner_id == owner_id))
                if bchat:
                    sender_id = sender_id or bchat.user_id or chat_id
                    sender_name = sender_name or bchat.title or "عميل"
                    sender_username = sender_username or bchat.username

        if sender_id == owner_id:
            continue

        if not sender_id:
            sender_id = chat_id
        if not sender_name:
            try:
                sc = await bot.get_chat(sender_id)
                sender_name = sc.full_name or "عميل"
                sender_username = sender_username or sc.username
            except Exception:
                sender_name = "عميل"

        un = f"@{sender_username}" if sender_username else "—"

        if text_content and media_type:
            media_label = media_labels.get(media_type, f"[{media_type}]")
            content_desc = f"{media_label}\n{text_content}"
        elif text_content:
            content_desc = text_content
        elif media_type:
            content_desc = media_labels.get(media_type, f"<i>({media_type})</i>")
        else:
            content_desc = "<i>(غير متوفر في السجل)</i>"

        alert = (
            "<blockquote>🗑️ <b>تنبيه: العميل حذف رسالة</b></blockquote>\n\n"
            f"👤 {html.quote(sender_name)} (<code>{sender_id}</code>)\n"
            f"👤 {html.quote(un)}\n\n"
            f"📝 <b>الرسالة المحذوفة:</b>\n"
            f"<i>{html.quote(content_desc[:1500])}</i>"
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("فتح ملف", callback_data=f"sec_open_profile_{sender_id}",
                      style="primary", icon_custom_emoji_id="6039397383148674517"),
             make_btn("كتم", callback_data=f"sec_mute_user_{sender_id}_{chat_id}",
                      style="danger", icon_custom_emoji_id="6039798533094128324")]
        ])

        sent = False
        if media_file_id:
            try:
                if media_type == "photo":
                    await bot.send_photo(owner_id, photo=media_file_id, caption=alert, reply_markup=kb, parse_mode=ParseMode.HTML)
                    sent = True
                elif media_type == "video":
                    await bot.send_video(owner_id, video=media_file_id, caption=alert, reply_markup=kb, parse_mode=ParseMode.HTML)
                    sent = True
                elif media_type == "voice":
                    await bot.send_message(owner_id, alert, reply_markup=kb, parse_mode=ParseMode.HTML)
                    await bot.send_voice(owner_id, voice=media_file_id)
                    sent = True
                elif media_type == "audio":
                    await bot.send_audio(owner_id, audio=media_file_id, caption=alert, reply_markup=kb, parse_mode=ParseMode.HTML)
                    sent = True
                elif media_type == "document":
                    await bot.send_document(owner_id, document=media_file_id, caption=alert, reply_markup=kb, parse_mode=ParseMode.HTML)
                    sent = True
            except Exception as e:
                logger.debug(f"[DEL_MEDIA_FALLBACK] {e}")

        if not sent:
            try:
                await bot.send_message(owner_id, alert, reply_markup=kb, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.warning(f"[DEL_ALERT] {e}")

        if log_id:
            try:
                async with async_session() as session:
                    await session.execute(delete(UserMessageLog).where(UserMessageLog.id == log_id))
                    await session.commit()
            except Exception as e:
                logger.debug(f"[DEL_CLEANUP] {e}")


# =============================================================================
# 31. المعالج العام للنصوص
# =============================================================================
async def send_safe_reply(message: Message, text: str, reply_markup=None,
                          business_connection_id=None, parse_mode=ParseMode.HTML):
    text = format_custom_emojis(text)
    if business_connection_id:
        try:
            return await message.bot.send_message(
                chat_id=message.chat.id, text=text,
                reply_markup=reply_markup,
                business_connection_id=business_connection_id,
                parse_mode=parse_mode)
        except Exception:
            try:
                return await message.bot.send_message(
                    chat_id=message.chat.id, text=text,
                    business_connection_id=business_connection_id,
                    parse_mode=parse_mode)
            except Exception:
                try:
                    return await message.bot.send_message(
                        chat_id=message.chat.id, text=text,
                        business_connection_id=business_connection_id)
                except Exception:
                    pass
        return None

    kw = {"parse_mode": parse_mode, "reply_markup": reply_markup}
    try:
        return await message.reply(text, **kw)
    except Exception:
        try:
            kw_no_parse = dict(kw)
            kw_no_parse.pop("parse_mode", None)
            return await message.reply(text, **kw_no_parse)
        except Exception:
            try:
                return await message.answer(text, **kw)
            except Exception:
                return await message.answer(text, reply_markup=reply_markup)


async def send_safe_dice(message: Message, emoji: str = "🎲", business_connection_id=None):
    if business_connection_id:
        try:
            return await message.bot.send_dice(
                chat_id=message.chat.id, emoji=emoji,
                business_connection_id=business_connection_id)
        except Exception:
            pass
    try:
        return await message.answer_dice(emoji=emoji)
    except Exception:
        pass


async def send_safe_photo(message: Message, photo, caption=None, business_connection_id=None, parse_mode=ParseMode.HTML):
    if caption:
        caption = format_custom_emojis(caption)
    kw = {"caption": caption, "parse_mode": parse_mode}
    if business_connection_id:
        try:
            return await message.bot.send_photo(
                chat_id=message.chat.id, photo=photo,
                business_connection_id=business_connection_id, **kw)
        except Exception:
            try:
                kw_no_parse = dict(kw)
                kw_no_parse.pop("parse_mode", None)
                return await message.bot.send_photo(
                    chat_id=message.chat.id, photo=photo,
                    business_connection_id=business_connection_id, **kw_no_parse)
            except Exception:
                pass
    try:
        return await message.reply_photo(photo, **kw)
    except Exception:
        try:
            return await message.answer_photo(photo, **kw)
        except Exception:
            pass


async def _process_auto_replies(message: Message, bot: Bot, owner_id: int, text_content: str, biz_id: Optional[str] = None) -> bool:
    async with async_session() as session:
        ars = (await session.execute(select(AutoReply).where(AutoReply.user_id == owner_id))).scalars().all()
        for ar in ars:
            matched = False
            kw = ar.keyword.strip()
            if ar.match_type == "exact" and text_content == kw:
                matched = True
            elif ar.match_type == "ignore_case" and text_content.lower() == kw.lower():
                matched = True
            elif ar.match_type == "contains" and kw.lower() in text_content.lower():
                matched = True
            if matched:
                try:
                    caption_val = ar.reply_text if ar.reply_text else None
                    if ar.reply_type == "photo" and ar.media_file_id:
                        if biz_id:
                            try:
                                await bot.send_photo(chat_id=message.chat.id, photo=ar.media_file_id,
                                                     caption=caption_val, business_connection_id=biz_id,
                                                     parse_mode=ParseMode.HTML)
                            except Exception:
                                await bot.send_photo(chat_id=message.chat.id, photo=ar.media_file_id,
                                                     caption=caption_val, business_connection_id=biz_id)
                        else:
                            try:
                                await message.reply_photo(photo=ar.media_file_id, caption=caption_val,
                                                          parse_mode=ParseMode.HTML)
                            except Exception:
                                await message.reply_photo(photo=ar.media_file_id, caption=caption_val)
                    elif ar.reply_type == "video" and ar.media_file_id:
                        if biz_id:
                            try:
                                await bot.send_video(chat_id=message.chat.id, video=ar.media_file_id,
                                                     caption=caption_val, business_connection_id=biz_id,
                                                     parse_mode=ParseMode.HTML)
                            except Exception:
                                await bot.send_video(chat_id=message.chat.id, video=ar.media_file_id,
                                                     caption=caption_val, business_connection_id=biz_id)
                        else:
                            try:
                                await message.reply_video(video=ar.media_file_id, caption=caption_val,
                                                          parse_mode=ParseMode.HTML)
                            except Exception:
                                await message.reply_video(video=ar.media_file_id, caption=caption_val)
                    elif ar.reply_type == "animation" and ar.media_file_id:
                        if biz_id:
                            try:
                                await bot.send_animation(chat_id=message.chat.id, animation=ar.media_file_id,
                                                         caption=caption_val, business_connection_id=biz_id,
                                                         parse_mode=ParseMode.HTML)
                            except Exception:
                                await bot.send_animation(chat_id=message.chat.id, animation=ar.media_file_id,
                                                         caption=caption_val, business_connection_id=biz_id)
                        else:
                            try:
                                await message.reply_animation(animation=ar.media_file_id, caption=caption_val,
                                                              parse_mode=ParseMode.HTML)
                            except Exception:
                                await message.reply_animation(animation=ar.media_file_id, caption=caption_val)
                    elif ar.reply_type == "document" and ar.media_file_id:
                        if biz_id:
                            try:
                                await bot.send_document(chat_id=message.chat.id, document=ar.media_file_id,
                                                        caption=caption_val, business_connection_id=biz_id,
                                                        parse_mode=ParseMode.HTML)
                            except Exception:
                                await bot.send_document(chat_id=message.chat.id, document=ar.media_file_id,
                                                        caption=caption_val, business_connection_id=biz_id)
                        else:
                            try:
                                await message.reply_document(document=ar.media_file_id, caption=caption_val,
                                                             parse_mode=ParseMode.HTML)
                            except Exception:
                                await message.reply_document(document=ar.media_file_id, caption=caption_val)
                    elif ar.reply_type == "voice" and ar.media_file_id:
                        if biz_id:
                            try:
                                await bot.send_voice(chat_id=message.chat.id, voice=ar.media_file_id,
                                                     caption=caption_val, business_connection_id=biz_id,
                                                     parse_mode=ParseMode.HTML)
                            except Exception:
                                await bot.send_voice(chat_id=message.chat.id, voice=ar.media_file_id,
                                                     caption=caption_val, business_connection_id=biz_id)
                        else:
                            try:
                                await message.reply_voice(voice=ar.media_file_id, caption=caption_val,
                                                          parse_mode=ParseMode.HTML)
                            except Exception:
                                await message.reply_voice(voice=ar.media_file_id, caption=caption_val)
                    elif ar.reply_type == "audio" and ar.media_file_id:
                        if biz_id:
                            try:
                                await bot.send_audio(chat_id=message.chat.id, audio=ar.media_file_id,
                                                     caption=caption_val, business_connection_id=biz_id,
                                                     parse_mode=ParseMode.HTML)
                            except Exception:
                                await bot.send_audio(chat_id=message.chat.id, audio=ar.media_file_id,
                                                     caption=caption_val, business_connection_id=biz_id)
                        else:
                            try:
                                await message.reply_audio(audio=ar.media_file_id, caption=caption_val,
                                                          parse_mode=ParseMode.HTML)
                            except Exception:
                                await message.reply_audio(audio=ar.media_file_id, caption=caption_val)
                    elif ar.reply_type == "sticker" and ar.media_file_id:
                        if biz_id:
                            await bot.send_sticker(chat_id=message.chat.id, sticker=ar.media_file_id,
                                                   business_connection_id=biz_id)
                        else:
                            await message.reply_sticker(sticker=ar.media_file_id)
                    else:
                        await send_safe_reply(message, ar.reply_text or "", business_connection_id=biz_id,
                                              parse_mode=ParseMode.HTML)
                except Exception as e:
                    logger.warning(f"[AR] {e}")
                return True
    return False


@main_router.message(F.text, StateFilter(None))
async def handle_general_text_messages(message: Message, bot: Bot, owner_id: Optional[int] = None):
    raw = message.text.strip()
    is_business = bool(message.business_connection_id)
    biz_id = str(message.business_connection_id) if message.business_connection_id else None
    if is_business:
        if owner_id is None:
            async with async_session() as session:
                owner_id = await resolve_business_owner(biz_id, session, bot=bot)
        if not owner_id:
            return
    else:
        owner_id = message.from_user.id if message.from_user else None
        if not owner_id:
            return

    is_prefix = raw.startswith((".", "/"))
    text_content = raw[1:].strip() if is_prefix else raw

    if is_business and message.from_user and message.from_user.id != owner_id:
        async with async_session() as session:
            m = await session.scalar(select(MutedUser).where(
                or_(MutedUser.owner_id == owner_id, MutedUser.owner_id.is_(None)),
                or_(MutedUser.user_id == message.from_user.id, MutedUser.user_id == message.chat.id)
            ))
            if m:
                if message.chat.type in ("group", "supergroup"):
                    await _apply_restriction(bot, message.chat.id, message.from_user.id, restrict=True)
                await delete_safe_message(bot, message)
                return

        # فحص إذا كان العميل أرسل رابط فيديو وخدمة التحميل مفعلة
        if await is_downloader_enabled(owner_id):
            client_vid_url = _detect_video_url(raw)
            if client_vid_url:
                await process_video_download(message, bot, client_vid_url, biz_id=biz_id)
                return

        # في محادثات البيزنس: إذا لم يكن كاتب الرسالة هو صاحب حساب البيزنس نفسه (بل طرف آخر / عميل)،
        # لا يتم تنفيذ أوامر البوت لصالحه إطلاقاً لمنع تداخل الأوامر وحفظ الذاتية والردود المكررة إذا كان كلاهما يستخدم البوت.
        # ويُفحص فقط نظام الردود التلقائية (AutoReply) المخصص للعملاء.
        await _process_auto_replies(message, bot, owner_id, text_content, biz_id)
        return
    if not is_business and message.chat.type == "private":
        async with async_session() as session:
            active = await is_subscription_active(owner_id, session)
        if not active:
            text = ("<blockquote>⚠️ <b>اشتراكك منتهي</b></blockquote>\n\n"
                    "🔒 البوت متوقف عن العمل على حسابك.")
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [make_btn("تواصل مع المطور", url=get_support_url(), style="success",
                          icon_custom_emoji_id="6041783877431729209")]])
            return await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    # 0) تحميل الفيديوهات والوسائط (.تحميل / .تنزيل أو إرسال الرابط مباشرة)
    is_download_cmd = (
        text_content in ["تحميل", "تنزيل", "download", "dl", "تحميل فيديو", "تنزيل فيديو"] or
        any(text_content.startswith(cmd + " ") for cmd in ["تحميل", "تنزيل", "download", "dl"])
    )
    direct_vid_url = _detect_video_url(raw)

    if is_download_cmd:
        target_url = None
        for cmd in ["تحميل", "تنزيل", "download", "dl"]:
            if text_content.startswith(cmd + " "):
                cand = text_content[len(cmd):].strip()
                m = re.search(r"https?://[^\s]+", cand)
                if m:
                    target_url = m.group(0).rstrip(".,)>]\"'")
                break

        if not target_url and message.reply_to_message:
            rep = message.reply_to_message
            rep_text = (rep.text or rep.caption or "").strip()
            target_url = _detect_video_url(rep_text)
            if not target_url:
                m = re.search(r"https?://[^\s]+", rep_text)
                if m:
                    target_url = m.group(0).rstrip(".,)>]\"'")

        if target_url:
            return await process_video_download(message, bot, target_url, biz_id=biz_id)
        else:
            return await send_safe_reply(
                message,
                f"<blockquote>{CE.DOWNLOAD} <b>أداة تحميل الفيديوهات والوسائط</b></blockquote>\n\n"
                f"<blockquote>📥 <b>كيفية الاستخدام:</b>\n"
                f"• اكتب الأمر ومعه الرابط: <code>.تحميل https://...</code>\n"
                f"• أو قم بالرد على أي رسالة تحتوي رابطاً واكتب <code>.تحميل</code> أو <code>.تنزيل</code>\n"
                f"• أو أرسل رابط الفيديو مباشرة وسيقوم البوت بتحميله!</blockquote>",
                business_connection_id=biz_id
            )

    elif direct_vid_url and await is_downloader_enabled(owner_id):
        return await process_video_download(message, bot, direct_vid_url, biz_id=biz_id)

    # 1) .روك
    if text_content == "روك" or text_content.startswith("روك ") or text_content.startswith("روك\n"):
        ai_question = ""
        if message.reply_to_message:
            ai_question = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()
        else:
            ai_question = text_content[3:].strip()
        if not ai_question:
            return await send_safe_reply(message,
                "<blockquote>🤖 <b>الذكاء الاصطناعي</b>\n\n"
                "📝 <b>الاستخدام:</b>\n• <code>.روك سؤالك</code>\n• أو الرد بـ <code>.روك</code></blockquote>",
                business_connection_id=biz_id)
        if not OPENAI_API_KEY and not GEMINI_API_KEY:
            return await send_safe_reply(message,
                "<blockquote>⚠️ <b>AI غير مفعّل</b></blockquote>", business_connection_id=biz_id)
        wait_msg = await send_safe_reply(message, "🤖 يفكّر...", business_connection_id=biz_id)
        ai_key = f"{biz_id}_{message.chat.id}" if is_business else str(message.from_user.id)
        ai_resp = await AIAssistant.reply(ai_key, ai_question)
        if wait_msg:
            try:
                if biz_id:
                    await bot.delete_message(chat_id=message.chat.id, message_id=wait_msg.message_id,
                                             business_connection_id=biz_id)
                else:
                    await bot.delete_message(chat_id=wait_msg.chat.id, message_id=wait_msg.message_id)
            except Exception:
                pass
        if ai_resp:
            quoted_body = html.quote(ai_resp)
            if len(quoted_body) > 3800:
                quoted_body = quoted_body[:3800] + "\n\n<i>... (تم اختصار ما تبقى لتجاوز حد تيليجرام)</i>"
            reply_text = f"<blockquote>{CE.ROBOT} <b>رد الذكاء الاصطناعي:</b></blockquote>\n\n{quoted_body}"
        else:
            reply_text = f"<blockquote>{CE.ALERT} <b>تعذر الحصول على رد حالياً.</b></blockquote>"
        try:
            if message.reply_to_message:
                if biz_id:
                    return await bot.send_message(chat_id=message.chat.id, text=reply_text,
                                                  business_connection_id=biz_id, parse_mode=ParseMode.HTML,
                                                  reply_parameters=ReplyParameters(message_id=message.reply_to_message.message_id))
                else:
                    return await message.reply(reply_text, parse_mode=ParseMode.HTML)
        except Exception:
            pass
        return await send_safe_reply(message, reply_text, business_connection_id=biz_id)

    # 2) الترجمة بالرد بكود اللغة
    if message.reply_to_message and re.match(r"^[a-zA-Z]{2}$", text_content):
        lang = text_content.lower()
        if lang in _LOCALES:
            src = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()
            if src:
                tr = await translate_text_async(src, lang)
                if tr:
                    quoted_tr = html.quote(tr)
                    if len(quoted_tr) > 3800:
                        quoted_tr = quoted_tr[:3800] + "\n\n<i>... (تم اختصار الجزء الزائد لتجاوز حد تيليجرام)</i>"
                    reply_text = f"<blockquote>🌐 <b>الترجمة ({lang.upper()}):</b></blockquote>\n\n{quoted_tr}"
                    try:
                        if biz_id:
                            return await bot.send_message(chat_id=message.chat.id, text=reply_text,
                                                          business_connection_id=biz_id, parse_mode=ParseMode.HTML,
                                                          reply_parameters=ReplyParameters(message_id=message.reply_to_message.message_id))
                        else:
                            return await message.reply(reply_text, parse_mode=ParseMode.HTML)
                    except Exception:
                        return await send_safe_reply(message, reply_text, business_connection_id=biz_id)
                else:
                    return await send_safe_reply(message, "❌ تعذرت الترجمة.", business_connection_id=biz_id)

    # 2.5) كشف الحساب والآيدي
    is_reveal = (
        text_content in ["كشف", "الكشف", "ايدي", "الأيدي", "الايدي", "id", "ID", "ا"] or
        any(text_content.startswith(cmd + " ") for cmd in ["كشف", "الكشف", "ايدي", "الأيدي", "الايدي", "id", "ID", "ا"])
    )
    if is_reveal:
        return await do_reveal_user_info(message, bot, biz_id=biz_id)

    # 3) .سعر الهدية الشامل (بالاسم، الخلفية، الرمز، الموديل، والرابط)
    is_gift_query = (
        text_content.startswith("سعر ") or text_content == "سعر" or
        text_content.startswith("هدية ") or text_content == "هدية" or
        text_content.startswith("سعر_الهدية") or
        text_content.startswith("خلفية ") or text_content.startswith("خلفيه ") or
        text_content.startswith("رمز ") or text_content.startswith("سيمبل ") or
        "t.me/nft/" in raw or "fragment.com/gift/" in raw or "getgems.io/nft/" in raw or "mrkt/" in raw or "portals/" in raw
    )
    if is_gift_query:
        target = ""
        if "t.me/nft/" in raw or "fragment.com/gift/" in raw or "getgems.io/nft/" in raw or "mrkt/" in raw or "portals/" in raw:
            target = raw.strip()
        elif text_content.startswith("سعر "):
            target = text_content[4:].strip()
        elif text_content.startswith("هدية "):
            target = text_content[5:].strip()
        elif text_content.startswith("خلفية ") or text_content.startswith("خلفيه "):
            target = text_content[6:].strip()
        elif text_content.startswith("رمز ") or text_content.startswith("سيمبل "):
            target = text_content[4:].strip()
        elif text_content.startswith("سعر_الهدية"):
            target = text_content[10:].strip()

        if not target and message.reply_to_message:
            rep_txt = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()
            if rep_txt.startswith("سعر "):
                target = rep_txt[4:].strip()
            elif rep_txt.startswith("هدية "):
                target = rep_txt[5:].strip()
            elif rep_txt.startswith("خلفية ") or rep_txt.startswith("خلفيه "):
                target = rep_txt[6:].strip()
            elif rep_txt.startswith("رمز ") or rep_txt.startswith("سيمبل "):
                target = rep_txt[4:].strip()
            else:
                target = rep_txt

        if not target:
            return await send_safe_reply(message,
                f"<blockquote>{CE.MONEY} <b>استعلام وفحص أسعار هدايا Telegram الشامل</b> {CE.DIAMOND}</blockquote>\n\n"
                f"<blockquote>📝 <b>طرق الاستعلام والبحث المدعومة:</b>\n"
                f"• <b>رابط القطعة المباشر:</b> <code>t.me/nft/SpicedWine-67781</code>\n"
                f"• <b>اسم الهدية فقط:</b> <code>.سعر Artisan Brick</code>\n"
                f"• <b>الهدية مع الخلفية:</b> <code>.سعر Artisan Brick Onyx Black</code>\n"
                f"• <b>الهدية مع الرمز:</b> <code>.سعر Artisan Brick Heart</code>\n"
                f"• <b>بحث مباشر بالخلفية:</b> <code>.خلفية Onyx Black</code> أو <code>.خلفية سوداء</code>\n"
                f"• <b>بحث مباشر بالرمز:</b> <code>.رمز Heart</code> أو <code>.رمز الوردة</code>\n"
                f"• <b>بحث بالعربية:</b> <code>.سعر طوبة خلفية سوداء</code>\n"
                f"• <b>أو الرد على أي رابط بالأمر:</b> <code>.سعر</code></blockquote>",
                business_connection_id=biz_id)

        if not _GIFTS_LIB:
            return await send_safe_reply(message,
                f"<blockquote>{CE.ALERT} <b>مكتبة الهدايا غير متوفرة حالياً</b></blockquote>",
                business_connection_id=biz_id)

        w = await send_safe_reply(message, f"<blockquote>⏳ <i>جاري فحص أسعار وخصائص <b>{html.quote(target[:40])}</b> عبر الأسواق...</i></blockquote>", business_connection_id=biz_id)
        try:
            data = await get_gift_floor(target)
            if w:
                try:
                    if data:
                        d = await CurrencyCache.get_rates()
                        rates = d.get("rates", {}) if isinstance(d, dict) else {}
                        txt = _build_gift_price_text(data, float(rates.get("ton_usd", 5.4)), float(rates.get("usd_egp", 49.5)))
                        await w.edit_text(txt, parse_mode=ParseMode.HTML)
                    else:
                        await w.edit_text(
                            f"<blockquote>{CE.ALERT} <b>لم يتم العثور على نتائج للهدية أو الخاصية</b></blockquote>\n\n"
                            f"<blockquote>⚠️ يرجى التأكد من كتابة اسم الهدية أو الخاصية (مثل: <code>Artisan Brick</code> أو <code>Onyx Black</code>) أو إرسال رابط القطعة:\n"
                            f"<code>{html.quote(target[:100])}</code></blockquote>",
                            parse_mode=ParseMode.HTML)
                except Exception as ex:
                    logger.warning(f"Error editing gift price: {ex}")
            return
        except Exception as e:
            logger.warning(f"[GIFT_PRICE] {e}")
            if w:
                try:
                    await w.edit_text(f"<blockquote>{CE.ALERT} <i>تعذر جلب السعر حالياً: {html.quote(str(e)[:100])}</i></blockquote>", parse_mode=ParseMode.HTML)
                except Exception:
                    pass
            return

    # حفظ ذاتية يدوي
    if text_content in ["ذ", "ذاتية", "ذاتيه", "حفظ الذاتية", "جلب الوقتيه", "حفظ", "جلب", "مم", "وقتية", "وقتيه", "ذاتي", "حفظ ذاتية", "حفظ ذاتيه"]:
        if not message.from_user:
            return
        # التحقق من أن مرسل الأمر هو نفسه صاحب حساب البيزنس
        # لمنع حفظ الذاتية على الحسابين إذا كان الطرفان مفعلين للبوت في نفس المحادثة
        if is_business and message.from_user.id != owner_id:
            return

        await delete_safe_message(bot, message)
        replied = message.reply_to_message
        if not replied or (not replied.photo and not replied.video and not replied.voice and not replied.video_note and not replied.document):
            return
        try:
            si = html.quote(replied.from_user.full_name if replied.from_user else "مجهول")
            sid = replied.from_user.id if replied.from_user else 0
            cap = (f"<blockquote>📸 <b>حفظ الميديا الذاتية</b>\n👤 {si} (<code>{sid}</code>)\n"
                   f"💬 {html.quote(message.chat.title or 'خاص')}</blockquote>")
            if replied.caption:
                cap += f"\n\n<blockquote>📝 {html.quote(replied.caption)}</blockquote>"
            if replied.photo:
                await download_and_send_media(bot, owner_id, replied.photo, "photo", cap)
            elif replied.video:
                await download_and_send_media(bot, owner_id, replied.video, "video", cap)
            elif replied.voice:
                await download_and_send_media(bot, owner_id, replied.voice, "voice", cap)
            elif replied.video_note:
                await download_and_send_media(bot, owner_id, replied.video_note, "video_note", cap)
            elif replied.document:
                await download_and_send_media(bot, owner_id, replied.document, "document", cap)
            logger.info(f"📸 تم حفظ ميديا ذاتية للحساب {owner_id} (المرسل: {message.from_user.id})")
        except Exception as e:
            logger.warning(f"save: {e}")
        return


    tm = re.match(r"^([a-zA-Z]{2})\s+(.+)$", text_content, re.DOTALL)
    if tm and tm.group(1).lower() in _LOCALES:
        tl = tm.group(1).lower()
        body = tm.group(2).strip()
        tr = await translate_text_async(body, tl)
        if tr:
            return await send_safe_reply(message,
                f"<blockquote>🌐 <b>{tl.upper()}:</b>\n\n{html.quote(tr)}</blockquote>",
                business_connection_id=biz_id)

    cr = SafeCalculator.evaluate(text_content)
    if cr is not None:
        return await send_safe_reply(message, f"<blockquote>🧮 <b>{cr}</b></blockquote>",
                                     business_connection_id=biz_id)

    # محول العملات الاحترافي (5 تون، 100 جنيه، 50 دولار، 0.5 btc، إلخ)
    c_parsed = parse_currency_query(text_content)
    if not c_parsed and message.reply_to_message and message.reply_to_message.text:
        clean_rep = message.reply_to_message.text.strip().replace(",", "")
        num_m = re.match(r"^([\d]+(?:\.\d+)?)$", clean_rep)
        if num_m and text_content.strip().lower() in CURRENCY_ALIASES:
            try:
                amt = float(num_m.group(1))
                if amt > 0:
                    c_parsed = (CURRENCY_ALIASES[text_content.strip().lower()], amt)
            except ValueError:
                pass

    if c_parsed:
        cur_sym, cur_amt = c_parsed
        d = await CurrencyCache.get_rates()
        all_usd = d.get("all_usd", {})
        init_text, _ = get_converter_message_content(cur_sym, cur_amt, target_sym=None, all_usd=all_usd)
        kb = get_converter_keyboard(cur_sym, cur_amt)
        return await send_safe_reply(message, init_text, reply_markup=kb, business_connection_id=biz_id)

    # استعلام عن سعر النجوم العام
    if re.match(r"^(\.)?(?:سعر النجوم|اسعار النجوم|النجوم|نجوم|سعر النجمه|stars)$", text_content.strip(), re.I):
        text = await get_stars_overview_text()
        kb = get_stars_overview_kb()
        return await send_safe_reply(message, text, reply_markup=kb, business_connection_id=biz_id)

    # حساب عدد مخصص من النجوم (مثال: 500 نجمة، سعر 1000 نجمة)
    sm = re.search(r"^(?:(\.)?(?:سعر|كام سعر|كم سعر|سعر ال|احسب)\s*)?(\d+)\s*(?:نجمة|نجمه|نجوم|stars?)$", text_content.strip(), re.I)
    if sm:
        q = int(sm.group(2))
        if q > 0:
            text = await get_stars_calc_text(q)
            kb = get_stars_calc_kb(q)
            return await send_safe_reply(message, text, reply_markup=kb, business_connection_id=biz_id)

    # استعلام عن أسعار تيليجرام بريميوم (بريميوم، سعر بريميوم، 3 شهور، 6 شهور، 12 شهر، إلخ)
    is_premium_query = (
        re.match(r"^(\.)?(?:بريميوم|البريميوم|سعر بريميوم|اسعار بريميوم|أسعار بريميوم|اشتراك بريميوم|اشتراكات بريميوم|telegram premium|premium)$", text_content.strip(), re.I) or
        text_content.strip() in ["3 شهور", "6 شهور", "12 شهر", "سنة", "سنه", "اشتراك سنة", "اشتراك سنه"]
    )
    if is_premium_query:
        text = await get_telegram_premium_overview()
        kb = get_premium_kb()
        return await send_safe_reply(message, text, reply_markup=kb, business_connection_id=biz_id)


    cd = extract_cash_transfer_data(text_content)
    if cd:
        p, a = cd
        return await send_safe_reply(message, get_cash_intro_text(p, a),
                                     reply_markup=get_cash_codes_kb(p, a), business_connection_id=biz_id)

    # --- الألعاب والتسلية (تعمل بـ . أو بدون . في كل الشاتات) ---
    if text_content in ["كت", "كت تويت"]:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("سؤال آخر", callback_data="fun_next_cut", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bolt"])]])
        text = (
            f"<blockquote>{CE.CHAT} <b>فقرة كت تويت (سؤال للنقاش)</b></blockquote>\n\n"
            f"<blockquote>🕊️ <b>{random.choice(CUT_TWEETS)}</b>\n\n"
            f"💬 <i>شاركنا إجابتك وصراحتك!</i></blockquote>"
        )
        return await send_safe_reply(message, text, reply_markup=kb, business_connection_id=biz_id)

    if text_content in ["لو خيروك", "خيروك"]:
        o1, o2 = random.choice(WOULD_YOU_RATHER)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("الاختيار الأول", callback_data="wyr_1", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["check_verified"]),
             make_btn("الاختيار الثاني", callback_data="wyr_2", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["check_verified"])],
            [make_btn("سؤال جديد", callback_data="fun_next_wyr", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bolt"])]])
        text = (
            f"<blockquote>{CE.BRAIN} <b>لعبة لو خيروك — اختر بحكمة</b></blockquote>\n\n"
            f"<blockquote>1️⃣ <b>{o1}</b>\n\n"
            f"2️⃣ <b>{o2}</b>\n\n"
            f"🤔 <tg-spoiler>أيهما تختار ولماذا؟</tg-spoiler></blockquote>"
        )
        return await send_safe_reply(message, text, reply_markup=kb, business_connection_id=biz_id)

    if text_content in ["صراحة", "صراحه"]:
        text = (
            f"<blockquote>{CE.LOCK} <b>سؤال صراحة واعتراف 🎭</b></blockquote>\n\n"
            f"<blockquote>❓ <i>{random.choice(TRUTH_QUESTIONS)}</i>\n\n"
            f"💬 <tg-spoiler>جاوب بكل صدق بدون لف ودوران!</tg-spoiler></blockquote>"
        )
        return await send_safe_reply(message, text, business_connection_id=biz_id)

    if text_content in ["نكتة", "نكته"]:
        text = (
            f"<blockquote>{CE.ROBOT} <b>نكتة وفرفشة 😂</b></blockquote>\n\n"
            f"<blockquote>{random.choice(JOKES)}</blockquote>"
        )
        return await send_safe_reply(message, text, business_connection_id=biz_id)

    if text_content in ["حكمة", "حكمه"]:
        text = (
            f"<blockquote>{CE.DIAMOND} <b>حكمة وموعظة اليوم 📜</b></blockquote>\n\n"
            f"<blockquote>{random.choice(WISDOM_QUOTES)}</blockquote>"
        )
        return await send_safe_reply(message, text, business_connection_id=biz_id)

    _dices = {
        "نرد": "🎲", "زهر": "🎲",
        "سلة": "🏀", "سله": "🏀",
        "كرة": "⚽", "كورة": "⚽",
        "سهم": "🎯", "دارت": "🎯",
        "بولينج": "🎳", "بولينغ": "🎳",
        "حظ": "🎰", "كازينو": "🎰",
    }
    if text_content in _dices:
        return await send_safe_dice(message, emoji=_dices[text_content], business_connection_id=biz_id)

    if text_content in ["نسبة الحب", "نسبه الحب"]:
        r = message.reply_to_message
        if not r or not r.from_user:
            return await send_safe_reply(message, f"<blockquote>⚠️ <b>يرجى الرد على رسالة الشخص لحساب نسبة الحب!</b></blockquote>", business_connection_id=biz_id)
        from_name = html.quote(message.from_user.first_name if message.from_user else "أنت")
        to_name = html.quote(r.from_user.first_name)
        resp = random.choice(LOVE_RESPONSES)
        text = (
            f"<blockquote>{CE.HEART_RED} <b>مقياس التوافق ونسبة الحب 💘</b></blockquote>\n\n"
            f"<blockquote>👩‍❤️‍👨 بين <b>{from_name}</b> و <b>{to_name}</b>:\n\n"
            f"{resp}</blockquote>"
        )
        return await send_safe_reply(message, text, business_connection_id=biz_id)

    if text_content == "زواج":
        r = message.reply_to_message
        if not r or not r.from_user:
            return await send_safe_reply(message, f"<blockquote>⚠️ <b>يرجى الرد على رسالة الشخص لإتمام الزواج!</b></blockquote>", business_connection_id=biz_id)
        from_name = html.quote(message.from_user.first_name if message.from_user else "أنت")
        to_name = html.quote(r.from_user.first_name)
        text = (
            f"<blockquote>{CE.STAR} <b>عقد قران ومباركة زواج 💍</b></blockquote>\n\n"
            f"<blockquote>🎉 بارك الله لكما وبارك عليكما وجمع بينكما في خير!\n\n"
            f"👰🤵 مبروك للعروسين <b>{from_name}</b> ❤️ <b>{to_name}</b> 🥳\n"
            f"<tg-spoiler>ألف ترليون مبروك وحياة سعيدة إن شاء الله!</tg-spoiler></blockquote>"
        )
        return await send_safe_reply(message, text, business_connection_id=biz_id)

    if text_content == "طلاق":
        r = message.reply_to_message
        if not r or not r.from_user:
            return await send_safe_reply(message, f"<blockquote>⚠️ <b>يرجى الرد على رسالة الشخص لإيقاع الطلاق!</b></blockquote>", business_connection_id=biz_id)
        from_name = html.quote(message.from_user.first_name if message.from_user else "أنت")
        to_name = html.quote(r.from_user.first_name)
        text = (
            f"<blockquote>{CE.CROSS_BAN} <b>وثيقة انفصال وطلاق 💔</b></blockquote>\n\n"
            f"<blockquote>⚡ تم إنهاء العلاقة بين <b>{from_name}</b> و <b>{to_name}</b> رسمياً!\n\n"
            f"🚪 <tg-spoiler>وعسى أن تكرهوا شيئاً وهو خير لكم.. كل واحد يشوف طريقه!</tg-spoiler></blockquote>"
        )
        return await send_safe_reply(message, text, business_connection_id=biz_id)

    # --- الإسلاميات ---
    if text_content == "صلاة" or text_content == "الصلاة" or text_content.startswith("صلاة ") or text_content.startswith("الصلاة "):
        parts = text_content.split(maxsplit=1)
        city = parts[1].strip() if len(parts) > 1 else "القاهرة"
        city = city.replace(".", "").strip() or "القاهرة"
        timings = await get_prayer_times(city)
        if timings:
            prayer_txt = (
                f"<blockquote>🕋 <b>مواقيت الصلاة في {html.quote(city)}</b></blockquote>\n\n"
                f"<blockquote>🌅 الفجر: <code>{timings['Fajr']}</code>\n"
                f"☀️ الظهر: <code>{timings['Dhuhr']}</code>\n"
                f"🌤️ العصر: <code>{timings['Asr']}</code>\n"
                f"🌇 المغرب: <code>{timings['Maghrib']}</code>\n"
                f"🌙 العشاء: <code>{timings['Isha']}</code>\n\n"
                f"🤲 <i>تقبل الله منا ومنكم صالح الأعمال</i></blockquote>"
            )
            return await send_safe_reply(message, prayer_txt, business_connection_id=biz_id)
        return await send_safe_reply(message, f"<blockquote>⚠️ <b>تعذر جلب مواقيت الصلاة لمدينة {html.quote(city)} حالياً.</b></blockquote>", business_connection_id=biz_id)

    if text_content in ["اذكار الصباح", "أذكار الصباح"]:
        text = (
            f"<blockquote>☀️ <b>أذكار الصباح المباركة</b></blockquote>\n\n"
            f"<blockquote>{random.choice(AZKAR_S)}</blockquote>"
        )
        return await send_safe_reply(message, text, business_connection_id=biz_id)

    if text_content in ["اذكار المساء", "أذكار المساء"]:
        text = (
            f"<blockquote>🌙 <b>أذكار المساء المباركة</b></blockquote>\n\n"
            f"<blockquote>{random.choice(AZKAR_M)}</blockquote>"
        )
        return await send_safe_reply(message, text, business_connection_id=biz_id)

    if text_content in ["دعاء", "ادعية", "أدعية"]:
        text = (
            f"<blockquote>🤲 <b>دعاء ومناجاة</b></blockquote>\n\n"
            f"<blockquote>{random.choice(DOAA)}</blockquote>"
        )
        return await send_safe_reply(message, text, business_connection_id=biz_id)

    if text_content in ["سبحة", "تسبيح"]:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("سبّح (0)", callback_data="tsb_click_0_1", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["check_verified"])],
            [make_btn("تصفير العداد", callback_data="tsb_reset", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bolt"])]
        ])
        text = (
            f"<blockquote>📿 <b>السبحة الإلكترونية المباركة</b></blockquote>\n\n"
            f"<blockquote>🔢 <b>العدد الحالي:</b> <code>0</code>\n\n"
            f"✨ <i>«سُبْحَانَ اللَّهِ وَبِحَمْدِهِ ، سُبْحَانَ اللَّهِ الْعَظِيمِ»</i></blockquote>"
        )
        return await send_safe_reply(message, text, reply_markup=kb, business_connection_id=biz_id)

    # --- الأدوات والملصقات ---
    if text_content in ["ملصق لصورة", "تحويل لصورة"]:
        r = message.reply_to_message
        if not r or not r.sticker:
            return await send_safe_reply(message, "⚠️ رد على ملصق ثابت.", business_connection_id=biz_id)
        if r.sticker.is_animated or r.sticker.is_video:
            return await send_safe_reply(message, "⚠️ الملصقات الثابتة فقط المدعومة.", business_connection_id=biz_id)
        w = await send_safe_reply(message, "⏳ جاري التحويل...", business_connection_id=biz_id)
        try:
            os.makedirs("downloads", exist_ok=True)
            fi = await bot.get_file(r.sticker.file_id)
            out = f"downloads/stk_{r.sticker.file_unique_id}.png"
            await bot.download_file(fi.file_path, destination=out)
            if os.path.exists(out):
                await send_safe_photo(message, photo=FSInputFile(out), caption="🖼️ تم تحويل الملصق لصورة", business_connection_id=biz_id)
                if w:
                    try:
                        if biz_id:
                            await bot.delete_message(chat_id=message.chat.id, message_id=w.message_id, business_connection_id=biz_id)
                        else:
                            await w.delete()
                    except Exception:
                        pass
                try:
                    os.remove(out)
                except Exception:
                    pass
                return
        except Exception as e:
            if w:
                try:
                    await w.edit_text(f"⚠️ خطأ: {e}")
                except Exception:
                    pass
            return

    if text_content == "معلومات الملصق":
        r = message.reply_to_message
        if not r or not r.sticker:
            return await send_safe_reply(message, "⚠️ رد على الملصق.", business_connection_id=biz_id)
        s = r.sticker
        stk_txt = f"🎨 <b>Emoji:</b> {s.emoji or '—'}\n📦 <b>Set:</b> <code>{s.set_name or '—'}</code>\n📏 <b>الأبعاد:</b> <code>{s.width}x{s.height}</code>"
        return await send_safe_reply(message, stk_txt, business_connection_id=biz_id)

    if text_content.startswith("زخرفة ") or text_content.startswith("زخرفه ") or text_content in ["زخرفة", "زخرفه"]:
        p = text_content.split(maxsplit=1)
        if len(p) < 2:
            return await send_safe_reply(message, "⚠️ <b>الاستخدام:</b> <code>زخرفة كلمة</code> أو <code>.زخرفة كلمة</code>", business_connection_id=biz_id)
        t = p[1].strip()
        res = f"✨ <code>{html.quote(t)}</code>\n━━━━━━━━━━━━━━\n\n"
        if re.search(r"[a-zA-Z]", t):
            for n, f in EN_FONTS.items():
                try:
                    res += f"• {n}: <code>{html.quote(f(t))}</code>\n\n"
                except Exception:
                    pass
        else:
            for i, f in enumerate(AR_STYLES, 1):
                res += f"• {i}: <code>{html.quote(f(t))}</code>\n\n"
        return await send_safe_reply(message, res, business_connection_id=biz_id)

    if text_content.startswith("عمر ") or text_content.startswith("العمر ") or text_content in ["عمر", "العمر"]:
        p = text_content.split(maxsplit=1)
        if len(p) < 2:
            return await send_safe_reply(message, "⚠️ <b>الاستخدام:</b> <code>عمر 2000/5/14</code> أو <code>.عمر 2000/5/14</code>", business_connection_id=biz_id)
        ds = p[1].strip().replace("-", "/").replace(".", "/")
        m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", ds)
        if not m:
            return await send_safe_reply(message, "❌ صيغة التاريخ غير صحيحة. مثال: <code>عمر 2000/5/14</code>", business_connection_id=biz_id)
        try:
            bd = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            t = datetime.date.today()
            if bd > t:
                return await send_safe_reply(message, "😅 التاريخ في المستقبل!", business_connection_id=biz_id)
            y = t.year - bd.year
            mo = t.month - bd.month
            d = t.day - bd.day
            if d < 0:
                mo -= 1
                d += 30
            if mo < 0:
                y -= 1
                mo += 12
            td = (t - bd).days
            age_txt = f"🎂 <b>العمر:</b> {y} سنة، {mo} شهر، {d} يوم\n📊 <b>الأيام:</b> {td:,} يوم"
            return await send_safe_reply(message, age_txt, business_connection_id=biz_id)
        except Exception as e:
            return await send_safe_reply(message, f"⚠️ خطأ: {e}", business_connection_id=biz_id)

    if text_content.startswith("bin ") or text_content == "bin":
        p = text_content.split(maxsplit=1)
        if len(p) < 2:
            return await send_safe_reply(message, "⚠️ <b>الاستخدام:</b> <code>bin 457173</code>", business_connection_id=biz_id)
        bn = re.sub(r"\D", "", p[1])[:8]
        if len(bn) < 6:
            return await send_safe_reply(message, "❌ أدخل 6 أرقام على الأقل.", business_connection_id=biz_id)
        w = await send_safe_reply(message, "⏳ فحص...", business_connection_id=biz_id)
        try:
            async with httpx.AsyncClient(timeout=8.0) as c:
                r = await c.get(f"https://data.handyapi.com/bin/{bn}")
                if r.status_code == 200:
                    d = r.json()
                    if d.get("Status") == "SUCCESS":
                        bin_txt = (
                            f"💳 <b>{bn}</b>\n"
                            f"🔹 {d.get('Scheme','-')} | {d.get('Type','-')}\n"
                            f"🏛️ {d.get('Issuer','-')}\n"
                            f"🌍 {d.get('Country',{}).get('Name','-')}"
                        )
                        if w:
                            try:
                                await w.edit_text(bin_txt, parse_mode=ParseMode.HTML)
                            except Exception:
                                pass
                        return
        except Exception:
            pass
        if w:
            try:
                await w.edit_text(f"⚠️ لا توجد بيانات للـ BIN {bn}.", parse_mode=ParseMode.HTML)
            except Exception:
                pass
        return

    if text_content.startswith("تشفير ") or text_content == "تشفير":
        p = text_content.split(maxsplit=1)
        if len(p) < 2:
            return await send_safe_reply(message, "⚠️ <b>الاستخدام:</b> <code>تشفير نص</code>", business_connection_id=biz_id)
        encoded = base64.b64encode(p[1].encode()).decode()
        return await send_safe_reply(message, f"🔒 <code>{encoded}</code>", business_connection_id=biz_id)

    if text_content.startswith("فك تشفير ") or text_content == "فك تشفير":
        p = text_content.split(maxsplit=1)
        if len(p) < 2:
            return await send_safe_reply(message, "⚠️ <b>الاستخدام:</b> <code>فك تشفير نص</code>", business_connection_id=biz_id)
        try:
            decoded = base64.b64decode(p[1].encode()).decode()
            return await send_safe_reply(message, f"🔓 <code>{html.quote(decoded)}</code>", business_connection_id=biz_id)
        except Exception:
            return await send_safe_reply(message, "❌ النص المشفر غير صالح.", business_connection_id=biz_id)

    if text_content.startswith("عكس ") or text_content == "عكس":
        p = text_content.split(maxsplit=1)
        if len(p) < 2:
            return await send_safe_reply(message, "⚠️ <b>الاستخدام:</b> <code>عكس نص</code>", business_connection_id=biz_id)
        rev_txt = p[1][::-1]
        return await send_safe_reply(message, f"🔄 <code>{html.quote(rev_txt)}</code>", business_connection_id=biz_id)

    # --- الكتم وإلغاء الكتم ---
    first_word = text_content.split()[0] if text_content else ""
    first_two = " ".join(text_content.split()[:2]) if len(text_content.split()) >= 2 else first_word
    is_mute_cmd = first_word in ["كتم"] or text_content.startswith("كتم ")
    is_unmute_cmd = (
        first_two in ["الغاء كتم", "الغاء الكتم", "فك كتم", "فك الكتم"] or
        first_word in ["الغاء_كتم", "فك_كتم", "الغاءكتم", "فك_الكتم", "الغاء_الكتم"] or
        text_content.startswith("الغاء كتم ") or text_content.startswith("فك كتم ") or
        text_content.startswith("الغاء الكتم ") or text_content.startswith("فك الكتم ")
    )

    if is_mute_cmd or is_unmute_cmd:
        is_sender_authorized = (
            (message.from_user and message.from_user.id == owner_id) or
            (message.from_user and message.from_user.id == OWNER_ID) or
            (not is_business)
        )
        if not is_sender_authorized:
            return

        target_id, target_username, target_name = await _find_reply_or_target(message, bot, owner_id)
        await delete_safe_message(bot, message)

        if not target_id:
            return await send_safe_reply(
                message,
                f"<blockquote>{CE.ALERT} <b>تحديد العضو المطلوب</b></blockquote>\n\n"
                f"<blockquote>⚠️ يرجى الرد على رسالة العضو أو كتابة معرّفه بعد الأمر:\n"
                f"• <code>.كتم @username</code>\n"
                f"• <code>.كتم 123456789</code>\n"
                f"• أو كتابة الأمر داخل شات العضو الخاص مباشرة.</blockquote>",
                business_connection_id=biz_id
            )

        if target_id == owner_id or target_id == OWNER_ID:
            return await send_safe_reply(message, f"<blockquote>{CE.ALERT} <b>لا يمكنك كتم حسابك الخاص!</b></blockquote>", business_connection_id=biz_id)

        async with async_session() as session:
            if is_mute_cmd:
                ex = await session.scalar(select(MutedUser).where(
                    or_(MutedUser.owner_id == owner_id, MutedUser.owner_id.is_(None)),
                    MutedUser.user_id == target_id
                ))
                if not ex:
                    session.add(MutedUser(
                        owner_id=owner_id,
                        user_id=target_id,
                        chat_id=message.chat.id,
                        username=target_username
                    ))
                    await session.commit()

                if message.chat.type in ("group", "supergroup"):
                    await _apply_restriction(bot, message.chat.id, target_id, restrict=True, business_connection_id=biz_id)

                alert_text = (
                    f"<blockquote>{CE.BAN} <b>تم كتم المستخدم بنجاح</b></blockquote>\n\n"
                    f"<blockquote>👤 <b>المستخدم:</b> <b>{html.quote(str(target_name))}</b>\n"
                    f"🆔 <b>المعرّف:</b> <code>{target_id}</code>\n"
                    f"🔒 <i>تم تفعيل الكتم ولن يتم الرد عليه أو استقبال رسائله.</i></blockquote>"
                )
                try:
                    if biz_id:
                        await bot.send_message(chat_id=message.chat.id, text=alert_text,
                                               business_connection_id=biz_id, parse_mode=ParseMode.HTML)
                    else:
                        await message.answer(alert_text, parse_mode=ParseMode.HTML)
                except Exception as e:
                    logger.warning(f"send mute alert: {e}")
                return

            elif is_unmute_cmd:
                await session.execute(delete(MutedUser).where(
                    or_(MutedUser.owner_id == owner_id, MutedUser.owner_id.is_(None)),
                    MutedUser.user_id == target_id
                ))
                await session.commit()

                if message.chat.type in ("group", "supergroup"):
                    await _apply_restriction(bot, message.chat.id, target_id, restrict=False, business_connection_id=biz_id)

                alert_text = (
                    f"<blockquote>{CE.CHECK_VERIFIED} <b>تم إلغاء كتم المستخدم بنجاح</b></blockquote>\n\n"
                    f"<blockquote>👤 <b>المستخدم:</b> <b>{html.quote(str(target_name))}</b>\n"
                    f"🆔 <b>المعرّف:</b> <code>{target_id}</code>\n"
                    f"🔊 <i>تم رفع التقييد ويمكنه التواصل واستقبال الردود مجدداً.</i></blockquote>"
                )
                try:
                    if biz_id:
                        await bot.send_message(chat_id=message.chat.id, text=alert_text,
                                               business_connection_id=biz_id, parse_mode=ParseMode.HTML)
                    else:
                        await message.answer(alert_text, parse_mode=ParseMode.HTML)
                except Exception as e:
                    logger.warning(f"send unmute alert: {e}")
                return

    if text_content in ["ث", "تثبيت", "غ ث", "الغاء تثبيت"]:
        cmd = text_content
        r = message.reply_to_message
        try:
            await delete_safe_message(bot, message)
        except Exception:
            pass
        if r:
            try:
                if cmd in ("ث", "تثبيت"):
                    if biz_id:
                        await bot.pin_chat_message(chat_id=message.chat.id, message_id=r.message_id,
                                                   business_connection_id=biz_id)
                    else:
                        await bot.pin_chat_message(chat_id=message.chat.id, message_id=r.message_id)
                elif cmd in ("غ ث", "الغاء تثبيت"):
                    if biz_id:
                        await bot.unpin_chat_message(chat_id=message.chat.id, message_id=r.message_id,
                                                     business_connection_id=biz_id)
                    else:
                        await bot.unpin_chat_message(chat_id=message.chat.id, message_id=r.message_id)
            except Exception as e:
                logger.warning(f"[PIN_OPS] {e}")
        return


    await _process_auto_replies(message, bot, owner_id, text_content, biz_id)
    return




# =============================================================================
# 32. إدارة محافظ TON (TON Wallets Management)
# =============================================================================
class TonWalletEncryption:
    _fernet: Optional[Fernet] = None

    @classmethod
    def _get_fernet(cls) -> Fernet:
        if cls._fernet is not None:
            return cls._fernet

        key = (os.getenv("WALLET_TOKEN_ENCRYPTION_KEY") or WALLET_TOKEN_ENCRYPTION_KEY or "OtmiofjMJNC5tyYH6RQ-p4D2is8pP3ANfqldLSHhXIY=").strip()
        if not key:
            new_key = Fernet.generate_key().decode()
            os.environ["WALLET_TOKEN_ENCRYPTION_KEY"] = new_key
            cls._save_key_to_env(new_key)
            key = new_key
            logger.info("🔑 تم إنشاء مفتاح تشفير جديد لمحفظة TON وحفظه في البيئة.")

        try:
            cls._fernet = Fernet(key.encode())
        except Exception:
            import base64
            import hashlib
            derived = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
            cls._fernet = Fernet(derived)

        return cls._fernet

    @classmethod
    def _save_key_to_env(cls, key: str):
        try:
            env_path = os.path.join(os.getcwd(), ".env")
            content = ""
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    content = f.read()
            if "WALLET_TOKEN_ENCRYPTION_KEY" in content:
                content = re.sub(r"WALLET_TOKEN_ENCRYPTION_KEY=.*", f"WALLET_TOKEN_ENCRYPTION_KEY={key}", content)
            else:
                content += f"\n# مفتاح تشفير توكنات محافظ TON\nWALLET_TOKEN_ENCRYPTION_KEY={key}\n"
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            logger.warning(f"تعذر حفظ مفتاح التشفير في .env: {e}")

    @classmethod
    def encrypt(cls, plain_token: str) -> str:
        if not plain_token:
            return ""
        fernet = cls._get_fernet()
        return fernet.encrypt(plain_token.encode()).decode()

    @classmethod
    def decrypt(cls, cipher_token: str) -> str:
        if not cipher_token:
            return ""
        fernet = cls._get_fernet()
        try:
            return fernet.decrypt(cipher_token.encode()).decode()
        except Exception as e:
            logger.error("فشل فك تشفير التوكن: مفتاح غير مطابق أو بيانات تالفة.")
            return ""

    @classmethod
    def mask(cls, token: str) -> str:
        if not token:
            return "••••••••••••"
        clean = token.strip()
        if len(clean) <= 6:
            return "••••••••" + clean[-2:]
        return "••••••••••••" + clean[-4:]


class TonWalletProvider:
    BASE_URL = "https://tonapi.io/v2"
    _address_cache: Dict[str, str] = {}  # cache لتفادي 429 من استدعاءات /parse المتكررة

    @classmethod
    def _is_wallet_address(cls, token: str) -> bool:
        """تحقق إذا كانت القيمة عنوان محفظة وليس API Key"""
        if not token:
            return False
        t = token.strip()
        return (t.startswith("EQ") or t.startswith("UQ") or t.startswith("0:"))

    @classmethod
    def _get_headers(cls, api_key: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "BusinessBot-TonModule/1.0",
        }
        # استخدم المفتاح فقط إذا كان API Key حقيقي وليس عنوان محفظة
        key = api_key or os.getenv("TONAPI_KEY", "").strip()
        if key and not cls._is_wallet_address(key):
            headers["Authorization"] = f"Bearer {key}"
        return headers

    @classmethod
    async def parse_and_normalize_address(cls, address: str) -> Optional[str]:
        """تحويل العنوان إلى الصيغة القياسية Bounceable (EQ...) عبر TonAPI مع cache"""
        if not address:
            return address
        # إذا العنوان مخزن في الـ cache نرجعه مباشرة
        if address in cls._address_cache:
            return cls._address_cache[address]
        # إذا العنوان بدأ بـ EQ فهو مُطبَّع بالفعل - لا نحتاج API call
        if address.startswith("EQ"):
            cls._address_cache[address] = address
            return address
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(f"{cls.BASE_URL}/address/{address}/parse")
                if resp.status_code == 200:
                    data = resp.json()
                    result = data.get("bounceable", {}).get("b64url") or data.get("raw_form") or address
                    cls._address_cache[address] = result
                    return result
        except Exception as e:
            logger.debug(f"parse_and_normalize_address error: {e}")
        return address


    @classmethod
    async def validate_token_or_address(cls, raw_input: str) -> Tuple[bool, Optional[str], Optional[str], Dict[str, Any]]:
        """
        التحقق من التوكن أو العنوان عبر TonAPI الحقيقي.
        يرجع: (is_valid, normalized_address, effective_token, raw_account_info)
        """
        raw_input = raw_input.strip()
        if not raw_input:
            return False, None, None, {}

        candidate_addr = ""
        candidate_key = ""

        # فحص إذا كانت المدخلات بصيغة token:address أو address:token
        if ":" in raw_input and not raw_input.startswith("0:"):
            parts = raw_input.split(":", 1)
            p0, p1 = parts[0].strip(), parts[1].strip()
            if any(p0.startswith(prefix) for prefix in ["EQ", "UQ", "0:"]):
                candidate_addr, candidate_key = p0, p1
            elif any(p1.startswith(prefix) for prefix in ["EQ", "UQ", "0:"]):
                candidate_key, candidate_addr = p0, p1
            else:
                candidate_key, candidate_addr = p0, p1
        elif any(raw_input.startswith(prefix) for prefix in ["EQ", "UQ", "0:"]):
            candidate_addr = raw_input
            candidate_key = os.getenv("TONAPI_KEY", "")
        else:
            candidate_key = raw_input

        async with httpx.AsyncClient(timeout=10.0) as client:
            if candidate_addr:
                headers = cls._get_headers(candidate_key)
                try:
                    resp = await client.get(f"{cls.BASE_URL}/accounts/{candidate_addr}", headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        norm_addr = await cls.parse_and_normalize_address(candidate_addr)
                        return True, (norm_addr or candidate_addr), (candidate_key or candidate_addr), data
                except Exception as e:
                    logger.debug(f"Validate candidate addr error: {e}")

            if candidate_key:
                headers = cls._get_headers(candidate_key)
                try:
                    resp = await client.get(f"{cls.BASE_URL}/rates?tokens=ton&currencies=usd", headers=headers)
                    if resp.status_code == 200:
                        if candidate_addr:
                            norm_addr = await cls.parse_and_normalize_address(candidate_addr)
                            return True, (norm_addr or candidate_addr), candidate_key, {}
                        return False, None, None, {"error": "need_address"}
                except Exception as e:
                    logger.debug(f"Validate key error: {e}")

        return False, None, None, {}

    @classmethod
    async def get_wallet_assets(cls, address: str, api_token: Optional[str] = None) -> Dict[str, Any]:
        """جلب بيانات الأرصدة و Jettons و NFTs الحقيقية لمحفظة"""
        headers = cls._get_headers(api_token)
        assets: Dict[str, Any] = {
            "ton_balance": 0.0,
            "status": "unknown",
            "jettons": [],
            "nfts": [],
            "raw_name": "",
        }

        async with httpx.AsyncClient(timeout=12.0) as client:
            try:
                r_acc = await client.get(f"{cls.BASE_URL}/accounts/{address}", headers=headers)
                if r_acc.status_code == 200:
                    acc_data = r_acc.json()
                    nano_balance = int(acc_data.get("balance", 0))
                    assets["ton_balance"] = nano_balance / 1e9
                    assets["status"] = acc_data.get("status", "active")
                    assets["raw_name"] = acc_data.get("name", "")
            except Exception as e:
                logger.error(f"Error fetching TON balance for {address}: {e}")

            try:
                r_jettons = await client.get(f"{cls.BASE_URL}/accounts/{address}/jettons", headers=headers)
                if r_jettons.status_code == 200:
                    j_data = r_jettons.json().get("balances", [])
                    for j in j_data:
                        meta = j.get("jetton", {}).get("metadata", {})
                        symbol = meta.get("symbol") or "Unknown"
                        decimals = int(meta.get("decimals", 9))
                        raw_bal = int(j.get("balance", 0))
                        balance = raw_bal / (10 ** decimals)
                        if balance > 0:
                            assets["jettons"].append({
                                "symbol": symbol,
                                "name": meta.get("name", symbol),
                                "balance": balance,
                                "jetton_address": j.get("jetton", {}).get("address", ""),
                            })
            except Exception as e:
                logger.debug(f"Error fetching Jettons for {address}: {e}")

            try:
                # جلب كل الـ NFTs (حتى 100) مع بيانات الروابط
                r_nfts = await client.get(
                    f"{cls.BASE_URL}/accounts/{address}/nfts?limit=100",
                    headers=headers
                )
                if r_nfts.status_code == 200:
                    nft_items = r_nfts.json().get("nft_items", [])
                    for nft in nft_items:
                        meta = nft.get("metadata", {})
                        col = nft.get("collection", {})
                        nft_addr = nft.get("address", "")
                        name = meta.get("name") or f"NFT #{nft.get('index', '')}"
                        collection_name = col.get("name", "")
                        img = meta.get("image", "")
                        desc = meta.get("description", "")

                        # تحديد نوع الـ NFT وبناء رابط تيليجرام الرسمي
                        col_desc = str(col.get("description", "")).lower()
                        is_gift = (
                            "fragment.com/gift" in img
                            or "telegram. owners can showcase these unique nfts in the gifts" in col_desc
                            or "nft collection by telegram" in col_desc
                        )
                        is_username = (
                            "fragment.com/username" in img
                            or col.get("name") == "Telegram Usernames"
                            or ("fragment.com" in img and ("t.me/" in desc or (name.startswith("@") and "fragment" in img)))
                        )

                        if is_gift:
                            # رابط الهدية المباشر على تيليجرام
                            slug_match = re.search(r'/gift/([^/.]+)', img) or re.search(r'/gift/([^/.]+)', meta.get("lottie", ""))
                            if slug_match:
                                gift_slug = slug_match.group(1)
                            else:
                                gift_slug = re.sub(r'[^a-zA-Z0-9#-]', '', name).lower().replace('#', '-')
                            link = f"https://t.me/nft/{gift_slug}"
                        elif is_username:
                            uname = name.lstrip("@").strip()
                            link = f"https://t.me/{uname}"
                        elif nft_addr:
                            link = f"https://getgems.io/nft/{nft_addr}"
                        else:
                            link = ""

                        assets["nfts"].append({
                            "name": name,
                            "collection": collection_name,
                            "address": nft_addr,
                            "link": link,
                            "is_gift": is_gift,
                            "is_username": is_username,
                        })
            except Exception as e:
                logger.debug(f"Error fetching NFTs for {address}: {e}")

        return assets

    @classmethod
    async def get_recent_transactions(cls, address: str, api_token: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """جلب أحدث المعاملات الحقيقية عبر TonAPI Events"""
        headers = cls._get_headers(api_token)
        transactions: List[Dict[str, Any]] = []

        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.get(f"{cls.BASE_URL}/accounts/{address}/events?limit={limit}", headers=headers)
                if resp.status_code != 200:
                    return []

                events = resp.json().get("events", [])
                norm_target = await cls.parse_and_normalize_address(address) or address

                for ev in events:
                    event_id = ev.get("event_id")
                    timestamp = ev.get("timestamp", 0)
                    actions = ev.get("actions", [])

                    for act in actions:
                        act_type = act.get("type")
                        if act_type == "TonTransfer":
                            tt = act.get("TonTransfer", {})
                            amount = int(tt.get("amount", 0)) / 1e9
                            sender_obj = tt.get("sender", {})
                            recipient_obj = tt.get("recipient", {})

                            sender_raw = sender_obj.get("address", "")
                            recipient_raw = recipient_obj.get("address", "")

                            sender = sender_obj.get("name") or await cls.parse_and_normalize_address(sender_raw) or sender_raw
                            recipient = recipient_obj.get("name") or await cls.parse_and_normalize_address(recipient_raw) or recipient_raw

                            direction = "out" if (sender_raw == address or sender == norm_target) else "in"

                            transactions.append({
                                "tx_hash": event_id,
                                "timestamp": timestamp,
                                "direction": direction,
                                "amount": amount,
                                "sender": sender,
                                "recipient": recipient,
                            })
                            break
        except Exception as e:
            logger.error(f"Error fetching transactions for {address}: {e}")

        transactions.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return transactions[:limit]


class TonWalletRepository:
    @classmethod
    async def get_user_wallets(cls, session: AsyncSession, user_id: int, TonWalletModel: Any) -> List[Any]:
        stmt = select(TonWalletModel).where(TonWalletModel.user_id == user_id).order_by(TonWalletModel.id.desc())
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def get_wallet_by_id_and_owner(cls, session: AsyncSession, wallet_id: int, user_id: int, TonWalletModel: Any) -> Optional[Any]:
        stmt = select(TonWalletModel).where(
            TonWalletModel.id == wallet_id,
            TonWalletModel.user_id == user_id
        )
        res = await session.execute(stmt)
        return res.scalars().first()

    @classmethod
    async def get_wallet_by_address_and_owner(cls, session: AsyncSession, user_id: int, address: str, TonWalletModel: Any) -> Optional[Any]:
        stmt = select(TonWalletModel).where(
            TonWalletModel.user_id == user_id,
            TonWalletModel.wallet_address == address
        )
        res = await session.execute(stmt)
        return res.scalars().first()

    @classmethod
    async def get_all_active_wallets(cls, session: AsyncSession, TonWalletModel: Any) -> List[Any]:
        stmt = select(TonWalletModel).where(TonWalletModel.is_active == True)
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def create_wallet(cls, session: AsyncSession, TonWalletModel: Any, user_id: int, name: str,
                            token: str, address: str) -> Any:
        enc_token = TonWalletEncryption.encrypt(token)
        wallet = TonWalletModel(
            user_id=user_id,
            name=name,
            token=enc_token,
            wallet_address=address,
            is_active=True,
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow()
        )
        session.add(wallet)
        await session.commit()
        await session.refresh(wallet)
        return wallet

    @classmethod
    async def update_wallet_token(cls, session: AsyncSession, wallet: Any, new_token: str, new_address: Optional[str] = None):
        wallet.token = TonWalletEncryption.encrypt(new_token)
        if new_address:
            wallet.wallet_address = new_address
        wallet.updated_at = datetime.datetime.utcnow()
        await session.commit()

    @classmethod
    async def set_wallet_active(cls, session: AsyncSession, wallet: Any, is_active: bool):
        wallet.is_active = is_active
        wallet.updated_at = datetime.datetime.utcnow()
        await session.commit()

    @classmethod
    async def delete_wallet_and_transactions(cls, session: AsyncSession, wallet: Any, TonTxModel: Any):
        await session.execute(delete(TonTxModel).where(TonTxModel.wallet_id == wallet.id))
        await session.delete(wallet)
        await session.commit()


class TonTransactionService:
    @classmethod
    async def is_tx_recorded(cls, session: AsyncSession, TonTxModel: Any, wallet_id: int, tx_hash: str) -> bool:
        stmt = select(TonTxModel.id).where(
            TonTxModel.wallet_id == wallet_id,
            TonTxModel.tx_hash == tx_hash
        )
        res = await session.execute(stmt)
        return res.scalars().first() is not None

    @classmethod
    async def record_transaction(cls, session: AsyncSession, TonTxModel: Any, wallet_id: int,
                                 tx_hash: str, direction: str, amount: float,
                                 sender: Optional[str], recipient: Optional[str]) -> Any:
        tx = TonTxModel(
            wallet_id=wallet_id,
            tx_hash=tx_hash,
            direction=direction,
            amount=amount,
            sender=sender,
            recipient=recipient,
            detected_at=datetime.datetime.utcnow()
        )
        session.add(tx)
        await session.commit()
        return tx

    @classmethod
    async def seed_initial_transactions(cls, session: AsyncSession, TonTxModel: Any, wallet_id: int,
                                        transactions: List[Dict[str, Any]]):
        for tx in transactions:
            h = tx.get("tx_hash")
            if not h:
                continue
            exists = await cls.is_tx_recorded(session, TonTxModel, wallet_id, h)
            if not exists:
                rec = TonTxModel(
                    wallet_id=wallet_id,
                    tx_hash=h,
                    direction=tx.get("direction", "in"),
                    amount=tx.get("amount", 0.0),
                    sender=tx.get("sender"),
                    recipient=tx.get("recipient"),
                    detected_at=datetime.datetime.utcnow()
                )
                session.add(rec)
        await session.commit()


# -----------------------------------------------------------------------------
# لوحات مفاتيح محافظ TON
# -----------------------------------------------------------------------------
def get_ton_wallets_list_kb(wallets: List[Any]) -> InlineKeyboardMarkup:
    buttons = []
    for w in wallets:
        buttons.append([make_btn(f"{w.name}", callback_data=f"ton_view_{w.id}", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["diamond"])])
    buttons.append([make_btn("إضافة محفظة جديدة", callback_data="ton_add_wallet", style="success", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["plus"])])
    buttons.append([make_btn("رجوع للقائمة الرئيسية", callback_data="main_dashboard", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_ton_wallet_details_kb(wallet: Any) -> InlineKeyboardMarkup:
    toggle_text = "تعطيل المراقبة" if wallet.is_active else "تفعيل المراقبة"
    toggle_style = "danger" if wallet.is_active else "success"
    toggle_icon = CUSTOM_EMOJI_IDS["cross_ban"] if wallet.is_active else CUSTOM_EMOJI_IDS["check_verified"]
    toggle_cb = f"ton_toggle_{wallet.id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            make_btn("ممتلكات المحفظة", callback_data=f"ton_assets_{wallet.id}", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["diamond"]),
            make_btn("سجل المحفظة", callback_data=f"ton_history_{wallet.id}", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["clock"]),
        ],
        [
            make_btn("إعادة التحقق", callback_data=f"ton_reverify_{wallet.id}", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bolt"]),
            make_btn(toggle_text, callback_data=toggle_cb, style=toggle_style, icon_custom_emoji_id=toggle_icon),
        ],
        [
            make_btn("حذف المحفظة", callback_data=f"ton_del_conf_{wallet.id}", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["trash"]),
        ],
        [
            make_btn("رجوع لمحافظ TON", callback_data="ton_wallets", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"]),
        ]
    ])


# -----------------------------------------------------------------------------
# معالجات راوتر محافظ TON (TON Router Handlers)
# -----------------------------------------------------------------------------

@ton_router.callback_query(F.data == "ton_wallets")
async def cb_ton_wallets(callback: CallbackQuery, state: FSMContext):
    if state:
        await state.clear()
    user_id = callback.from_user.id
    async with async_session() as session:
        wallets = await TonWalletRepository.get_user_wallets(session, user_id, TonWallet)

    text = (
        f"<blockquote>{CE.DIAMOND} <b>إدارة محافظ شبكة TON الذكية</b></blockquote>\n\n"
        f"<blockquote>📊 <b>المحافظ المسجلة:</b> <code>{len(wallets)}</code> محفظة\n\n"
        f"⚡ من هنا يمكنك ربط وإدارة محافظ TON ومتابعة المعاملات الفورية وإشعارات التحويل لحظياً.\n\n"
        f"{CE.SHIELD} <b>الأمان:</b> <tg-spoiler>تشفير عالي المستوى بنظام AES-CBC معزول</tg-spoiler></blockquote>"
    )
    kb = get_ton_wallets_list_kb(wallets)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    finally:
        try:
            await callback.answer()
        except Exception:
            pass

@ton_router.callback_query(F.data == "ton_add_wallet")
async def cb_ton_add_wallet(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddTonWalletFSM.waiting_for_name)
    text = (
        f"<blockquote>{CE.PLUS} <b>إضافة محفظة TON جديدة</b></blockquote>\n\n"
        f"<blockquote>✏️ <b>أرسل اسماً مخصصاً للمحفظة:</b>\n\n"
        f"• <i>مثال:</i> <code>محفظة المبيعات</code>\n"
        f"• <i>مثال:</i> <code>محفظتي الأساسية</code></blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("إلغاء العملية", callback_data="ton_cancel_fsm", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_ban"])]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    finally:
        try:
            await callback.answer()
        except Exception:
            pass

@ton_router.callback_query(F.data == "ton_cancel_fsm")
async def cb_ton_cancel_fsm(callback: CallbackQuery, state: FSMContext):
    if state:
        await state.clear()
    await cb_ton_wallets(callback, state)

@ton_router.message(AddTonWalletFSM.waiting_for_name)
async def msg_ton_wallet_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name or len(name) > 64:
        return await message.answer(f"<blockquote>{CE.CROSS_BAN} <b>يرجى إرسال اسم مناسب للمحفظة (1 إلى 64 حرفاً):</b></blockquote>", parse_mode=ParseMode.HTML)

    await state.update_data(wallet_name=name)
    await state.set_state(AddTonWalletFSM.waiting_for_token)

    text = (
        f"<blockquote>{CE.LOCK} <b>إدخال عنوان أو Token المحفظة</b></blockquote>\n\n"
        f"<blockquote>🔐 <b>أرسل الآن العنوان أو التوكن الخاص بالمحفظة:</b>\n\n"
        f"💡 <i>الخيارات المدعومة:</i>\n"
        f"• عنوان المحفظة المباشر (مثل: <code>EQ...</code> أو <code>UQ...</code>)\n"
        f"• أو API Token من خدمة TonAPI\n"
        f"• أو بالصيغة: <code>Token:Address</code>\n\n"
        f"{CE.SHIELD} <b>الخصوصية:</b> <tg-spoiler>يتم تشفير البيانات فوراً وحذف الرسالة للمحافظة على أمانك</tg-spoiler></blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("إلغاء العملية", callback_data="ton_cancel_fsm", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_ban"])]
    ])
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@ton_router.message(AddTonWalletFSM.waiting_for_token)
async def msg_ton_wallet_token(message: Message, state: FSMContext):
    raw_token = (message.text or "").strip()
    try:
        await message.delete()
    except Exception:
        pass

    wait_msg = await message.answer(f"<blockquote>⏳ <i>جاري التحقق من صحة المحفظة والاتصال بـ TonAPI...</i></blockquote>", parse_mode=ParseMode.HTML)

    is_valid, address, effective_token, _ = await TonWalletProvider.validate_token_or_address(raw_token)

    if not is_valid or not address:
        try:
            await wait_msg.delete()
        except Exception:
            pass
        text = (
            f"<blockquote>{CE.CROSS_BAN} <b>فشل التحقق من المحفظة</b></blockquote>\n\n"
            f"<blockquote>❌ لم أتمكن من التحقق من صحة التوكن أو العنوان على الـ Blockchain.\n\n"
            f"يرجى التأكد من صحة العنوان وإعادة المحاولة.</blockquote>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("إلغاء العملية", callback_data="ton_cancel_fsm", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_ban"])]
        ])
        return await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    data = await state.get_data()
    name = data.get("wallet_name", "محفظة TON")
    user_id = message.from_user.id

    async with async_session() as session:
        existing_wallet = await TonWalletRepository.get_wallet_by_address_and_owner(
            session, user_id, address, TonWallet
        )
        if existing_wallet:
            try:
                await wait_msg.delete()
            except Exception:
                pass
            await state.clear()
            text = (
                f"<blockquote>{CE.ALERT} <b>تنبيه: المحفظة مضافة مسبقاً!</b></blockquote>\n\n"
                f"<blockquote>⚠️ <b>لقد قمت بإضافة هذه المحفظة بالفعل لحسابك:</b>\n\n"
                f"🏷 <b>الاسم:</b> <b>{html.quote(existing_wallet.name)}</b>\n"
                f"💎 <b>العنوان:</b> <code>{html.quote(address)}</code>\n\n"
                f"<i>لا يمكن إضافة نفس المحفظة أكثر من مرة. يمكنك إدارتها مباشرة من قائمة المحافظ.</i></blockquote>"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [make_btn("تفاصيل المحفظة", callback_data=f"ton_view_{existing_wallet.id}", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["diamond"])],
                [make_btn("إدارة محافظ TON", callback_data="ton_wallets", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
            ])
            return await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

        wallet = await TonWalletRepository.create_wallet(
            session,
            TonWallet,
            user_id=user_id,
            name=name,
            token=effective_token or address,
            address=address
        )
        init_txs = await TonWalletProvider.get_recent_transactions(address, api_token=effective_token, limit=10)
        await TonTransactionService.seed_initial_transactions(session, TonTransaction, wallet.id, init_txs)

    await state.clear()
    try:
        await wait_msg.delete()
    except Exception:
        pass

    text = (
        f"<blockquote>{CE.CHECK_VERIFIED} <b>تمت إضافة المحفظة بنجاح!</b></blockquote>\n\n"
        f"<blockquote>📛 <b>اسم المحفظة:</b> <b>{html.quote(name)}</b>\n"
        f"💎 <b>العنوان:</b> <code>{html.quote(address)}</code>\n"
        f"🟢 <b>حالة المراقبة:</b> نشطة ومراقبة لحظياً\n\n"
        f"⚡ <i>سيتم إشعارك تلقائياً بأي معاملة واردة أو صادرة!</i></blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("تفاصيل المحفظة", callback_data=f"ton_view_{wallet.id}", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["diamond"])],
        [make_btn("إدارة محافظ TON", callback_data="ton_wallets", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
    ])
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@ton_router.callback_query(F.data.startswith("ton_view_"))
async def cb_ton_view_wallet(callback: CallbackQuery, state: FSMContext = None):
    if state:
        await state.clear()
    try:
        wallet_id = int(callback.data.split("_")[2])
    except Exception:
        return await callback.answer("خطأ في المعرّف.", show_alert=True)

    user_id = callback.from_user.id
    async with async_session() as session:
        wallet = await TonWalletRepository.get_wallet_by_id_and_owner(session, wallet_id, user_id, TonWallet)

    if not wallet:
        return await callback.answer("❌ هذه المحفظة لا تخص حسابك.", show_alert=True)

    status_str = "🟢 متصلة (المراقبة مفعّلة)" if wallet.is_active else "🔴 معطّلة (المراقبة متوقفة)"
    created_str = wallet.created_at.strftime("%Y-%m-%d %H:%M UTC") if wallet.created_at else "—"

    decrypted = TonWalletEncryption.decrypt(wallet.token)
    masked_tok = TonWalletEncryption.mask(decrypted)

    text = (
        f"<blockquote>{CE.DIAMOND} <b>تفاصيل محفظة TON</b></blockquote>\n\n"
        f"<blockquote>📛 <b>الاسم:</b> <b>{html.quote(wallet.name)}</b>\n"
        f"💎 <b>العنوان:</b> <code>{html.quote(wallet.wallet_address)}</code>\n"
        f"🔐 <b>الرمز/التوكن:</b> <code>{masked_tok}</code>\n"
        f"📊 <b>الحالة:</b> <b>{status_str}</b>\n"
        f"🕐 <b>تاريخ الإضافة:</b> <code>{created_str}</code></blockquote>\n\n"
        f"⚡ <i>اختر العملية المطلوبة من الخيارات أدناه:</i>"
    )
    kb = get_ton_wallet_details_kb(wallet)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    finally:
        try:
            await callback.answer()
        except Exception:
            pass

@ton_router.callback_query(F.data.startswith("ton_assets_"))
async def cb_ton_wallet_assets(callback: CallbackQuery):
    try:
        wallet_id = int(callback.data.split("_")[2])
    except Exception:
        return await callback.answer("خطأ في المعرّف.", show_alert=True)

    user_id = callback.from_user.id
    async with async_session() as session:
        wallet = await TonWalletRepository.get_wallet_by_id_and_owner(session, wallet_id, user_id, TonWallet)

    if not wallet:
        return await callback.answer("❌ غير مسموح لك بالوصول إلى هذه المحفظة.", show_alert=True)

    await callback.answer("⏳ جاري جلب الممتلكات...")

    decrypted_token = TonWalletEncryption.decrypt(wallet.token)
    assets = await TonWalletProvider.get_wallet_assets(wallet.wallet_address, api_token=decrypted_token)
    rates_info = await CurrencyCache.get_rates()
    rates = rates_info.get("rates", {}) if isinstance(rates_info, dict) else {}
    ton_usd = float(rates.get("ton_usd", 1.366))
    usd_egp = float(rates.get("usd_egp", 52.14))

    ton_bal = assets["ton_balance"]
    approx_usd = ton_bal * ton_usd
    approx_egp = approx_usd * usd_egp

    lines = [
        f"<blockquote>{CE.MONEY} <b>كشف ممتلكات وأصول المحفظة</b> {CE.DIAMOND}</blockquote>\n",
        f"<blockquote>🏷 <b>اسم المحفظة:</b> <b>{html.quote(wallet.name)}</b>\n"
        f"📍 <b>العنوان:</b> <code>{html.quote(wallet.wallet_address)}</code></blockquote>\n",
        f"<blockquote>💎 <b>رصيد TON المتاح:</b> <code>{ton_bal:.4f} TON</code>\n"
        f"💵 <b>القيمة التقديرية:</b> <code>≈ ${approx_usd:.2f}</code> | <code>{approx_egp:.2f} EGP</code></blockquote>\n",
    ]

    # ──── Jettons ────
    jettons = assets.get("jettons", [])
    if jettons:
        lines.append(f"<blockquote expandable>{CE.COIN} <b>العملات الرقمية (Jettons) [{len(jettons)}]:</b>")
        for j in jettons:
            sym = html.quote(j.get("symbol") or "?")
            bal = j["balance"]
            bal_str = f"{bal:,.4f}".rstrip("0").rstrip(".")
            lines.append(f"• <b>{sym}</b> ⇠ <code>{bal_str}</code>")
        lines.append("</blockquote>\n")
    else:
        lines.append(f"<blockquote>{CE.COIN} <b>العملات الرقمية (Jettons):</b> <i>لا يوجد رصيد</i></blockquote>\n")

    # ──── NFTs / Gifts ────
    nfts = assets.get("nfts", [])
    gifts = [n for n in nfts if n.get("is_gift")]
    usernames = [n for n in nfts if n.get("is_username")]
    others = [n for n in nfts if not n.get("is_gift") and not n.get("is_username")]

    if gifts:
        lines.append(f"<blockquote expandable>{CE.GIFT} <b>هدايا Telegram المميزة [{len(gifts)}]:</b>")
        for nft in gifts:
            name = html.quote(nft["name"])
            link = nft.get("link", "")
            if link:
                lines.append(f'▫️ <a href="{link}"><b>{name}</b></a>')
            else:
                lines.append(f"▫️ <b>{name}</b>")
        lines.append("</blockquote>\n")

    if usernames:
        lines.append(f"<blockquote expandable>{CE.STAR} <b>معرّفات Telegram المحجوزة [{len(usernames)}]:</b>")
        for nft in usernames:
            name = html.quote(nft["name"])
            link = nft.get("link", "")
            if link:
                lines.append(f'▫️ <a href="{link}"><b>{name}</b></a>')
            else:
                lines.append(f"▫️ <code>{name}</code>")
        lines.append("</blockquote>\n")

    if others:
        lines.append(f"<blockquote expandable>{CE.SPARKLES} <b>NFTs ومقتنيات أخرى [{len(others)}]:</b>")
        for nft in others:
            name = html.quote(nft["name"])
            col = nft.get("collection", "")
            col_str = f" <i>({html.quote(col)})</i>" if col else ""
            link = nft.get("link", "")
            if link:
                lines.append(f'▫️ <a href="{link}"><b>{name}</b></a>{col_str}')
            else:
                lines.append(f"▫️ <b>{name}</b>{col_str}")
        lines.append("</blockquote>\n")

    if not nfts:
        lines.append(f"<blockquote>{CE.GIFT} <b>NFTs / الهدايا:</b> <i>لا توجد أصول مقتناة</i></blockquote>\n")

    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    lines.append(f"<blockquote>{CE.CLOCK} <i>آخر تحديث للشبكة:</i> <code>{now_str}</code></blockquote>")

    text = "\n".join(lines)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("تحديث الأرصدة", callback_data=f"ton_assets_{wallet.id}", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bolt"])],
        [make_btn("رجوع لتفاصيل المحفظة", callback_data=f"ton_view_{wallet.id}", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
    ])
    try:
        await callback.message.edit_text(
            text,
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
            link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
    except TelegramBadRequest:
        pass

@ton_router.callback_query(F.data.startswith("ton_history_"))
async def cb_ton_wallet_history(callback: CallbackQuery):
    try:
        wallet_id = int(callback.data.split("_")[2])
    except Exception:
        return await callback.answer("خطأ في المعرّف.", show_alert=True)

    user_id = callback.from_user.id
    async with async_session() as session:
        wallet = await TonWalletRepository.get_wallet_by_id_and_owner(session, wallet_id, user_id, TonWallet)

    if not wallet:
        return await callback.answer("❌ غير مسموح لك بالوصول إلى هذه المحفظة.", show_alert=True)

    decrypted_token = TonWalletEncryption.decrypt(wallet.token)
    txs = await TonWalletProvider.get_recent_transactions(wallet.wallet_address, api_token=decrypted_token, limit=10)

    lines = [
        f"<blockquote>{CE.CLOCK} <b>سجل معاملات المحفظة (آخر 10 معاملات)</b> {CE.DIAMOND}</blockquote>\n",
        f"<blockquote>🏷 <b>المحفظة:</b> <b>{html.quote(wallet.name)}</b>\n"
        f"📍 <b>العنوان:</b> <code>{html.quote(wallet.wallet_address)}</code></blockquote>\n"
    ]

    if not txs:
        lines.append(f"<blockquote><i>{CE.ALERT} لا توجد معاملات مسجلة حتى الآن لهذه المحفظة على Blockchain.</i></blockquote>")
    else:
        numbers = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        for idx, tx in enumerate(txs[:10]):
            num = numbers[idx] if idx < len(numbers) else f"{idx+1}️⃣"
            direction = tx.get("direction", "in")
            amount = tx.get("amount", 0.0)
            ts = tx.get("timestamp", 0)
            dt_str = datetime.datetime.utcfromtimestamp(ts).strftime("%d %b %Y — %H:%M") if ts else "—"

            if direction == "in":
                sender = html.quote(str(tx.get('sender') or '—'))
                lines.append(
                    f"<blockquote>{num} 🟢 <b>معاملة واردة (إيداع)</b>\n"
                    f"💎 <b>المبلغ:</b> <code>+{amount:.4f} TON</code>\n"
                    f"👤 <b>من:</b> <code>{sender}</code>\n"
                    f"{CE.CLOCK} <b>التاريخ:</b> <code>{dt_str}</code></blockquote>\n"
                )
            else:
                recipient = html.quote(str(tx.get('recipient') or '—'))
                lines.append(
                    f"<blockquote>{num} 🔴 <b>معاملة صادرة (تحويل)</b>\n"
                    f"💎 <b>المبلغ:</b> <code>-{amount:.4f} TON</code>\n"
                    f"📤 <b>إلى:</b> <code>{recipient}</code>\n"
                    f"{CE.CLOCK} <b>التاريخ:</b> <code>{dt_str}</code></blockquote>\n"
                )

    lines.append(f"<blockquote>⚡ <i>مراقبة حية وتحديث مباشر للشبكة</i></blockquote>")

    text = "\n".join(lines)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("تحديث السجل", callback_data=f"ton_history_{wallet.id}", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["bolt"])],
        [make_btn("رجوع لتفاصيل المحفظة", callback_data=f"ton_view_{wallet.id}", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        pass
    finally:
        try:
            await callback.answer()
        except Exception:
            pass

@ton_router.callback_query(F.data.startswith("ton_toggle_"))
async def cb_ton_toggle_monitoring(callback: CallbackQuery):
    try:
        wallet_id = int(callback.data.split("_")[2])
    except Exception:
        return await callback.answer("خطأ في المعرّف.", show_alert=True)

    user_id = callback.from_user.id
    async with async_session() as session:
        wallet = await TonWalletRepository.get_wallet_by_id_and_owner(session, wallet_id, user_id, TonWallet)
        if not wallet:
            return await callback.answer("❌ غير مسموح لك بالوصول إلى هذه المحفظة.", show_alert=True)

        new_status = not wallet.is_active
        await TonWalletRepository.set_wallet_active(session, wallet, new_status)

    msg = "🟢 تم تفعيل مراقبة المحفظة بنجاح." if new_status else "🔴 تم تعطيل مراقبة المحفظة."
    await callback.answer(msg, show_alert=True)
    await cb_ton_view_wallet(callback, None)

@ton_router.callback_query(F.data.startswith("ton_del_conf_"))
async def cb_ton_del_confirm(callback: CallbackQuery):
    try:
        wallet_id = int(callback.data.split("_")[3])
    except Exception:
        return await callback.answer("خطأ في المعرّف.", show_alert=True)

    user_id = callback.from_user.id
    async with async_session() as session:
        wallet = await TonWalletRepository.get_wallet_by_id_and_owner(session, wallet_id, user_id, TonWallet)

    if not wallet:
        return await callback.answer("❌ غير مسموح لك بالوصول إلى هذه المحفظة.", show_alert=True)

    text = (
        f"<blockquote>{CE.CROSS_BAN} <b>تأكيد حذف محفظة TON</b></blockquote>\n\n"
        f"<blockquote>⚠️ <b>هل أنت متأكد من رغبتك في حذف هذه المحفظة نهائياً؟</b>\n\n"
        f"📛 <b>المحفظة:</b> <b>{html.quote(wallet.name)}</b>\n"
        f"💎 <b>العنوان:</b> <code>{html.quote(wallet.wallet_address)}</code>\n\n"
        f"<i>سيتم إيقاف المراقبة وحذف سجل معاملاتها بالكامل من حسابك.</i></blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            make_btn("نعم، تأكيد الحذف", callback_data=f"ton_do_del_{wallet.id}", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["trash"]),
            make_btn("تراجع", callback_data=f"ton_view_{wallet.id}", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"]),
        ]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        pass
    finally:
        try:
            await callback.answer()
        except Exception:
            pass

@ton_router.callback_query(F.data.startswith("ton_do_del_"))
async def cb_ton_do_delete(callback: CallbackQuery, state: FSMContext):
    try:
        wallet_id = int(callback.data.split("_")[3])
    except Exception:
        return await callback.answer("خطأ في المعرّف.", show_alert=True)

    user_id = callback.from_user.id
    async with async_session() as session:
        wallet = await TonWalletRepository.get_wallet_by_id_and_owner(session, wallet_id, user_id, TonWallet)
        if not wallet:
            return await callback.answer("❌ غير مسموح لك بالوصول إلى هذه المحفظة.", show_alert=True)

        await TonWalletRepository.delete_wallet_and_transactions(session, wallet, TonTransaction)

    await callback.answer("✅ تم حذف المحفظة بنجاح.", show_alert=True)
    await cb_ton_wallets(callback, state)

@ton_router.callback_query(F.data.startswith("ton_reverify_"))
async def cb_ton_reverify(callback: CallbackQuery, state: FSMContext):
    try:
        wallet_id = int(callback.data.split("_")[2])
    except Exception:
        return await callback.answer("خطأ في المعرّف.", show_alert=True)

    user_id = callback.from_user.id
    async with async_session() as session:
        wallet = await TonWalletRepository.get_wallet_by_id_and_owner(session, wallet_id, user_id, TonWallet)

    if not wallet:
        return await callback.answer("❌ غير مسموح لك بالوصول إلى هذه المحفظة.", show_alert=True)

    await state.update_data(reverify_wallet_id=wallet.id)
    await state.set_state(ReverifyTonWalletFSM.waiting_for_token)

    text = (
        f"<blockquote>{CE.BOLT} <b>إعادة التحقق من Token المحفظة</b></blockquote>\n\n"
        f"<blockquote>📛 <b>المحفظة:</b> <b>{html.quote(wallet.name)}</b>\n\n"
        f"🔐 <b>أرسل التوكن الجديد أو عنوان المحفظة:</b>\n"
        f"⚠️ <i>سيتم فحص الاتصال وتحديث الربط فوراً.</i></blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("إلغاء", callback_data=f"ton_view_{wallet.id}", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_ban"])]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    finally:
        try:
            await callback.answer()
        except Exception:
            pass

@ton_router.message(ReverifyTonWalletFSM.waiting_for_token)
async def msg_ton_reverify_token(message: Message, state: FSMContext):
    raw_token = (message.text or "").strip()
    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()
    wallet_id = data.get("reverify_wallet_id")
    user_id = message.from_user.id

    async with async_session() as session:
        wallet = await TonWalletRepository.get_wallet_by_id_and_owner(session, wallet_id, user_id, TonWallet)

    if not wallet:
        await state.clear()
        return await message.answer(f"<blockquote>{CE.CROSS_BAN} <b>تعذر العثور على المحفظة الخاصة بك.</b></blockquote>", parse_mode=ParseMode.HTML)

    wait_msg = await message.answer(f"<blockquote>⏳ <i>جاري التحقق من التوكن الجديد والاتصال بالشبكة...</i></blockquote>", parse_mode=ParseMode.HTML)
    is_valid, address, effective_token, _ = await TonWalletProvider.validate_token_or_address(raw_token)

    if not is_valid or not address:
        try:
            await wait_msg.delete()
        except Exception:
            pass
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("إلغاء", callback_data=f"ton_view_{wallet.id}", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["cross_ban"])]
        ])
        return await message.answer(f"<blockquote>{CE.CROSS_BAN} <b>التوكن غير صالح! يرجى التأكد وإعادة الإرسال:</b></blockquote>", reply_markup=kb, parse_mode=ParseMode.HTML)

    async with async_session() as session:
        wallet_to_update = await TonWalletRepository.get_wallet_by_id_and_owner(session, wallet_id, user_id, TonWallet)
        if not wallet_to_update:
            await state.clear()
            return await message.answer(f"<blockquote>{CE.CROSS_BAN} <b>تعذر العثور على المحفظة الخاصة بك.</b></blockquote>", parse_mode=ParseMode.HTML)

        other_wallet = await session.scalar(
            select(TonWallet).where(
                TonWallet.user_id == user_id,
                TonWallet.wallet_address == address,
                TonWallet.id != wallet_id
            )
        )
        if other_wallet:
            try:
                await wait_msg.delete()
            except Exception:
                pass
            await state.clear()
            text = (
                f"<blockquote>{CE.ALERT} <b>العنوان مستخدم في محفظة أخرى!</b></blockquote>\n\n"
                f"<blockquote>⚠️ هذا العنوان مسجل مسبقاً لمحفظة أخرى لديك باسم:\n"
                f"🏷 <b>{html.quote(other_wallet.name)}</b>\n\n"
                f"يرجى التأكد من العنوان وعدم تكراره.</blockquote>"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [make_btn("رجوع لتفاصيل المحفظة", callback_data=f"ton_view_{wallet.id}", style="danger", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["back"])]
            ])
            return await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)

        await TonWalletRepository.update_wallet_token(session, wallet_to_update, effective_token or address, address)

    await state.clear()
    try:
        await wait_msg.delete()
    except Exception:
        pass

    text = (
        f"<blockquote>{CE.CHECK_VERIFIED} <b>تمت إعادة الاتصال بالمحفظة بنجاح!</b></blockquote>\n\n"
        f"<blockquote>📛 <b>المحفظة:</b> <b>{html.quote(wallet.name)}</b>\n"
        f"💎 <b>العنوان:</b> <code>{html.quote(address)}</code>\n\n"
        f"<i>تم تحديث التوكن وتأكيد الاتصال بالـ Blockchain بنجاح.</i></blockquote>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("تفاصيل المحفظة", callback_data=f"ton_view_{wallet.id}", style="primary", icon_custom_emoji_id=CUSTOM_EMOJI_IDS["diamond"])]
    ])
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)



# -----------------------------------------------------------------------------
# مراقبة معاملات محافظ TON في الخلفية
# -----------------------------------------------------------------------------
async def ton_wallet_monitor_job(bot: Bot, async_session: Any, TonWalletModel: Any, TonTxModel: Any):
    try:
        async with async_session() as session:
            wallets = await TonWalletRepository.get_all_active_wallets(session, TonWalletModel)

        if not wallets:
            return

        for wallet in wallets:
            try:
                decrypted_token = TonWalletEncryption.decrypt(wallet.token)
                recent_txs = await TonWalletProvider.get_recent_transactions(
                    wallet.wallet_address,
                    api_token=decrypted_token,
                    limit=5
                )

                for tx in recent_txs:
                    tx_hash = tx.get("tx_hash")
                    if not tx_hash:
                        continue

                    async with async_session() as session:
                        is_seen = await TonTransactionService.is_tx_recorded(session, TonTxModel, wallet.id, tx_hash)
                        if is_seen:
                            continue

                        await TonTransactionService.record_transaction(
                            session,
                            TonTxModel,
                            wallet.id,
                            tx_hash,
                            tx.get("direction", "in"),
                            tx.get("amount", 0.0),
                            tx.get("sender"),
                            tx.get("recipient")
                        )

                    direction = tx.get("direction", "in")
                    amount = tx.get("amount", 0.0)
                    ts = tx.get("timestamp", int(time.time()))
                    time_str = datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M UTC")

                    if direction == "in":
                        text = (
                            f"<blockquote>{CE.DIAMOND} <b>معاملة واردة جديدة (Deposit)</b></blockquote>\n\n"
                            f"<blockquote>💎 <b>المحفظة:</b> <b>{html.quote(wallet.name)}</b>\n"
                            f"📥 <b>المبلغ المستلم:</b> <code>+{amount:.4f} TON</code>\n"
                            f"👤 <b>من:</b> <code>{html.quote(str(tx.get('sender') or '—'))}</code>\n"
                            f"🕐 <b>الوقت:</b> <code>{time_str}</code>\n"
                            f"🔗 <b>المعاملة:</b> <code>{tx_hash}</code></blockquote>"
                        )
                    else:
                        text = (
                            f"<blockquote>{CE.BOLT} <b>معاملة صادرة جديدة (Withdraw)</b></blockquote>\n\n"
                            f"<blockquote>💎 <b>المحفظة:</b> <b>{html.quote(wallet.name)}</b>\n"
                            f"📤 <b>المبلغ المرسل:</b> <code>-{amount:.4f} TON</code>\n"
                            f"👤 <b>إلى:</b> <code>{html.quote(str(tx.get('recipient') or '—'))}</code>\n"
                            f"🕐 <b>الوقت:</b> <code>{time_str}</code>\n"
                            f"🔗 <b>المعاملة:</b> <code>{tx_hash}</code></blockquote>"
                        )

                    try:
                        await bot.send_message(wallet.user_id, text, parse_mode=ParseMode.HTML)
                    except Exception as e:
                        logger.warning(f"Failed to notify user {wallet.user_id} for tx {tx_hash}: {e}")

                await asyncio.sleep(0.3)
            except Exception as e:
                logger.debug(f"Monitor error for wallet {wallet.id}: {e}")

    except Exception as e:
        logger.error(f"Error in ton_wallet_monitor_job: {e}")


# =============================================================================
# 33. المهام المجدولة
# =============================================================================
async def check_and_send_scheduled_messages(bot: Bot):
    now = datetime.datetime.utcnow()
    async with async_session() as session:
        tasks = (await session.execute(select(ScheduledMessage).where(
            ScheduledMessage.is_sent == False,
            ScheduledMessage.is_active == True,
            ScheduledMessage.scheduled_time <= now))).scalars().all()
        for t in tasks:
            biz_conn_id = getattr(t, "business_connection_id", None)
            if not biz_conn_id and t.created_by_user_id:
                bconn = await session.scalar(select(BusinessConnectionRecord).where(
                    BusinessConnectionRecord.user_id == t.created_by_user_id,
                    BusinessConnectionRecord.is_enabled == True
                ))
                if bconn:
                    biz_conn_id = bconn.connection_id

            sent = False
            # 1. إرسال الرسالة باسم حساب صاحب العمل (Business Account)
            if biz_conn_id:
                try:
                    if t.media_type == "photo" and t.media_file_id:
                        await bot.send_photo(
                            chat_id=t.target_chat_id,
                            photo=t.media_file_id,
                            caption=t.text_content,
                            business_connection_id=biz_conn_id
                        )
                    elif t.media_type == "video" and t.media_file_id:
                        await bot.send_video(
                            chat_id=t.target_chat_id,
                            video=t.media_file_id,
                            caption=t.text_content,
                            business_connection_id=biz_conn_id
                        )
                    else:
                        await bot.send_message(
                            chat_id=t.target_chat_id,
                            text=t.text_content,
                            business_connection_id=biz_conn_id
                        )
                    sent = True
                    t.is_sent = True
                    logger.info(f"✅ [SCHED] تم إرسال الرسالة المجدولة #{t.id} من حساب المستخدم ({t.created_by_user_id})")
                except Exception as e:
                    logger.warning(f"[SCHED_BIZ_FAIL] {e} - جاري المحاولة عبر البوت...")

            # 2. إرسال احتياطي من البوت في حال تعذر البيزنس
            if not sent:
                try:
                    if t.media_type == "photo" and t.media_file_id:
                        await bot.send_photo(t.target_chat_id, photo=t.media_file_id, caption=t.text_content)
                    elif t.media_type == "video" and t.media_file_id:
                        await bot.send_video(t.target_chat_id, video=t.media_file_id, caption=t.text_content)
                    else:
                        await bot.send_message(t.target_chat_id, text=t.text_content)
                    t.is_sent = True
                    logger.info(f"✅ [SCHED] تم إرسال الرسالة المجدولة #{t.id} عبر البوت")
                except Exception as e2:
                    logger.error(f"[SCHED_FAIL] {t.id}: {e2}")
                    t.is_active = False

        await session.commit()


async def check_expired_subscriptions(bot: Bot):
    now = datetime.datetime.utcnow()
    async with async_session() as session:
        users = (await session.execute(select(User).where(User.role == "customer"))).scalars().all()
        for u in users:
            trial_ok = u.trial_expires_at and u.trial_expires_at > now
            sub_ok = u.subscription_expires_at and u.subscription_expires_at > now
            if trial_ok or sub_ok:
                await session.execute(delete(ServiceSetting).where(
                    ServiceSetting.user_id == u.id,
                    ServiceSetting.service_key == "expiry_notified"))
                continue
            notified = (await session.execute(select(ServiceSetting).where(
                ServiceSetting.user_id == u.id,
                ServiceSetting.service_key == "expiry_notified"))).scalar_one_or_none()
            if notified and notified.is_enabled:
                continue
            try:
                text = (
                    f"<blockquote>{CE.CROSS_BAN} <b>تنبيه انتهاء الاشتراك</b></blockquote>\n\n"
                    f"<blockquote>🔒 <b>عذراً، انتهت صلاحية اشتراكك في البوت.</b>\n\n"
                    f"⚠️ لقد توقفت جميع خدمات وأدوات البوت على حسابك حالياً.\n"
                    f"📞 لتجديد الاشتراك والتفعيل الفوري، تواصل مع المطور: <b>{SUPPORT_USERNAME}</b>\n\n"
                    f"{CE.SHIELD} <b>ملاحظة:</b> <tg-spoiler>بياناتك وإعداداتك محفوظة وجاهزة للاستئناف فور التجديد</tg-spoiler></blockquote>"
                )
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [make_btn("تجديد الاشتراك الآن", url=get_support_url(), style="success",
                              icon_custom_emoji_id=CUSTOM_EMOJI_IDS["diamond"])],
                    [make_btn("التواصل مع الدعم", url=get_support_url(), style="primary",
                              icon_custom_emoji_id=CUSTOM_EMOJI_IDS["chat"])]
                ])
                await bot.send_message(u.id, text, reply_markup=kb, parse_mode=ParseMode.HTML)
                if notified:
                    notified.is_enabled = True
                else:
                    session.add(ServiceSetting(user_id=u.id, chat_id=u.id,
                                               service_key="expiry_notified", is_enabled=True))
                await session.commit()
            except Exception as e:
                logger.debug(f"expiry notify {u.id}: {e}")


# =============================================================================
# 34. main
# =============================================================================
async def main():
    global BOT_USERNAME, SUPPORT_USERNAME

    if not BOT_TOKEN:
        logger.critical("❌ لا يمكن بدء التشغيل بدون BOT_TOKEN!")
        return

    logger.info("🚀 تهيئة قاعدة البيانات...")
    await init_database()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session.middleware(RichFormattingMiddleware())

    try:
        bi = await bot.get_me()
        BOT_USERNAME = f"@{bi.username}" if bi.username else f"@Bot_{bi.id}"
    except Exception as e:
        logger.warning(f"get_me: {e}")

    if OWNER_ID:
        try:
            oi = await bot.get_chat(OWNER_ID)
            SUPPORT_USERNAME = f"@{oi.username}" if oi.username else f"tg://user?id={OWNER_ID}"
        except Exception:
            SUPPORT_USERNAME = f"tg://user?id={OWNER_ID}"

    logger.info(f"✅ البوت جاهز | {BOT_USERNAME} | {SUPPORT_USERNAME}")
    logger.info(f"🤖 AI: OpenAI={'✅' if OPENAI_API_KEY else '❌'} | Gemini={'✅' if GEMINI_API_KEY else '❌'}")
    logger.info(f"🎁 Gifts Library: {'✅' if _GIFTS_LIB else '❌ (pip install TelegramGifts)'}")

    scheduler.add_job(check_and_send_scheduled_messages, "interval", seconds=30,
                      args=[bot], id="sched_msg_job", replace_existing=True)
    scheduler.add_job(check_expired_subscriptions, "interval", minutes=10,
                      args=[bot], id="expiry_job", replace_existing=True)
    scheduler.add_job(ton_wallet_monitor_job, "interval", seconds=60,
                      args=[bot, async_session, TonWallet, TonTransaction],
                      id="ton_monitor_job", replace_existing=True)
    scheduler.add_job(check_and_send_reminders, "interval", seconds=30,
                      args=[bot], id="reminders_job", replace_existing=True)
    scheduler.start()

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(
            bot,
            allowed_updates=["message", "callback_query", "business_connection",
                            "business_message", "edited_business_message",
                            "deleted_business_messages"])
    except Exception as e:
        logger.error(f"Polling: {e}")
    finally:
        await bot.session.close()
        await engine.dispose()
        scheduler.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 تم الإيقاف يدوياً.")