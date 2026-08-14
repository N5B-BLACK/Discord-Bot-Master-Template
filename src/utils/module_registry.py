"""
Module Registry - single source of truth for every feature "module" in the bot
(built or planned), whether it's on by default, and which category it belongs to.

Why this exists (Phase 0 of the roadmap):
Up to now every feature (moderation, tickets, music, ...) was wired independently
into cogs/setup.py, dashboard.py, and db.py by hand. That was fine at ~10 features,
but the plan is to add 10+ more (security suite, leveling, reaction roles, groups,
giveaways, starboard...) - hand-wiring each one the same way would make dashboard.py
and setup.py grow unbounded again (the exact problem that caused the "orphaned
function" bug 5 times per PROJECT_HANDOFF.md).

This registry does NOT replace SETTINGS_PAGES / SETTINGS_GROUPS / DEFAULT_SETTINGS
(those still define the actual setting *fields*). It adds a layer above them:
which whole *modules* are enabled for a given guild. A module can be toggled off
entirely (hides its dashboard page + disables its cog logic) without touching its
individual settings.

Also doubles as the foundation for a future licensing/tier system: every module
already carries a `tier` field (currently unused - always "core"). When a
subscription-plan decision is made, filtering enabled modules by the guild's plan
becomes a one-line change here instead of a redesign.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Module:
    key: str                 # stable id, used as dict key in DB + dashboard routes
    name_ar: str              # Arabic display name (dashboard sidebar / /setup)
    category: str             # groups related modules together in nav
    description_ar: str
    default_enabled: bool     # whether new guilds get this on by default
    built: bool                # False = planned/not implemented yet (roadmap placeholder)
    tier: str = "core"        # reserved for future licensing (core/pro/premium) - not enforced yet


# ---------------------------------------------------------------------------
# The full catalog: what exists today (built=True) + what the roadmap adds.
# Categories match the phases in PROFESSIONAL_ROADMAP.md so the dashboard nav
# and /setup can be grouped the same way the roadmap is planned.
# ---------------------------------------------------------------------------
MODULES: list[Module] = [
    # --- Core / already built ---
    Module("moderation", "الإدارة", "core", "Kick / Ban / Mute / Warn", True, True),
    Module("welcome", "الترحيب", "core", "رسالة ترحيب + رول تلقائي", True, True),
    Module("tickets", "التذاكر", "core", "نظام تذاكر دعم كامل", True, True),
    Module("ai_chat", "الدردشة الذكية", "core", "دردشة AI عبر OpenRouter", True, True),
    Module("logs", "اللوقز", "core", "لوقز شاملة (34 نوع حدث)", True, True),
    Module("music", "الموسيقى", "core", "تشغيل موسيقى بلوحة تحكم", True, True),
    Module("trap_channel", "قناة الفخ", "core", "حظر تلقائي لمن يكتب بقناة محددة", False, True),
    Module("auto_divider", "الفاصل التلقائي", "core", "صورة فاصلة تلقائية بعد كل رسالة", False, True),
    Module("embed_builder", "بناء Embeds", "core", "محرر embeds مخصص بالكامل", True, True),

    # --- Phase 1: Security suite ---
    Module("anti_nuke", "الحماية من التخريب", "security", "كشف حذف قنوات/رولات جماعي وتجميد الصلاحيات", False, True),
    Module("anti_spam", "منع السبام", "security", "كشف وإيقاف السبام تلقائياً", False, True),
    Module("anti_link", "منع الروابط", "security", "حظر روابط غير مسموحة مع استثناءات", False, True),
    Module("word_filter", "فلتر الكلمات", "security", "قائمة كلمات محظورة قابلة للتخصيص", False, True),
    Module("anti_webhook", "حماية الويبهوكس", "security", "منع إنشاء ويبهوكس غير مصرح", False, True),
    Module("raid_mode", "وضع الهجوم", "security", "قفل مؤقت للسيرفر عند هجوم أعضاء وهميين", False, True),
    Module("server_backup", "نسخ احتياطي", "security", "نسخ/استعادة إعدادات السيرفر", False, False),

    # --- Phase 2: Engagement ---
    Module("leveling", "نظام المستويات", "engagement", "XP + رانك كارد بصورة", False, True),
    Module("reaction_roles", "الرولات بالتفاعل", "engagement", "رول تلقائي عبر ايموجي", False, True),
    Module("starboard", "ستاربورد", "engagement", "أرشفة أفضل الرسائل بنجمة", False, False),
    Module("giveaways", "السحوبات", "engagement", "سحوبات جوائز بزر", False, False),
    Module("autoresponder", "الردود التلقائية", "engagement", "رد تلقائي على كلمات محددة", False, False),

    # --- Phase 3: Community / voice ---
    Module("voice_rooms", "الرومات الخاصة", "community", "رومات صوتية خاصة يتحكم فيها العضو", False, False),
    Module("invite_tracking", "تتبع الدعوات", "community", "من دعا مين + إحصائيات", False, False),
]


def get_module(key: str) -> Module | None:
    return next((m for m in MODULES if m.key == key), None)


def modules_by_category() -> dict[str, list[Module]]:
    out: dict[str, list[Module]] = {}
    for m in MODULES:
        out.setdefault(m.category, []).append(m)
    return out


def built_modules() -> list[Module]:
    """Modules that actually exist in code today - safe to show/toggle in the dashboard."""
    return [m for m in MODULES if m.built]


def default_enabled_modules() -> dict[str, bool]:
    """The `enabled_modules` dict to seed into DEFAULT_SETTINGS in db.py."""
    return {m.key: m.default_enabled for m in MODULES}


def is_enabled(guild_settings: dict, module_key: str) -> bool:
    """Whether a module is on for a guild. Unbuilt modules are always treated as off."""
    mod = get_module(module_key)
    if mod is None or not mod.built:
        return False
    enabled_map = guild_settings.get("enabled_modules", {})
    return enabled_map.get(module_key, mod.default_enabled)
