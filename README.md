# Discord Bot Master Template

قالب أساسي لبناء بوتات ديسكورد بسرعة لعملاء مختلفين: Moderation + Welcome + AI Chat + Utility.

## طريقة التشغيل (أول مرة)

1. ثبّت Python 3.10+ إذا مو موجود
2. أنشئ بيئة افتراضية (اختياري بس موصى به):
   ```
   python -m venv venv
   source venv/bin/activate   # ماك/لينكس
   venv\Scripts\activate      # ويندوز
   ```
3. ثبّت المكتبات:
   ```
   pip install -r requirements.txt
   ```
4. انسخ `.env.example` باسم `.env` واملأ القيم (Token، Guild ID، ...)
5. شغّل البوت:
   ```
   cd src
   python main.py
   ```

## كيف تجيب المعرفات (IDs)

1. بديسكورد: Settings → Advanced → فعّل "Developer Mode"
2. Right-click على السيرفر/القناة/الرول → Copy ID

## خطوات التخصيص لكل عميل جديد (Checklist)

- [ ] استخدم "Use this template" على GitHub لإنشاء ريبو جديد نظيف
- [ ] أنشئ Application جديد بـ Discord Developer Portal، خذ Token
- [ ] املأ `.env` بمعرفات سيرفر العميل (Guild ID, Channel IDs, Role IDs)
- [ ] عدّل `BOT_NAME` و`WELCOME_MESSAGE` و`EMBED_COLOR` حسب هوية العميل
- [ ] فعّل/عطّل الـ Cogs المطلوبة بقائمة `COGS` في `main.py` حسب طلب العميل
- [ ] اختبر كل أمر على سيرفر تجريبي قبل التسليم النهائي
- [ ] اختر طريقة الاستضافة (self-hosted أو hosted عندك) وجهز التوكين المناسب

## هيكلية المشروع

```
src/
├── main.py              # نقطة البداية
├── config.py            # كل الإعدادات القابلة للتخصيص
├── cogs/
│   ├── moderation.py     # kick, ban, mute, warn
│   ├── welcome.py        # ترحيب + رول تلقائي
│   ├── ai_chat.py        # أمر !ask متصل بـ Claude API
│   └── utility.py        # ping, info
└── utils/
    ├── checks.py          # فحوصات قناة/رول قابلة لإعادة الاستخدام
    └── error_handler.py    # معالجة موحدة للأخطاء
```

## الأوامر المتاحة افتراضياً

| الأمر | الوصف | الصلاحية المطلوبة |
|---|---|---|
| `!kick @member [سبب]` | طرد عضو | رول المشرفين |
| `!ban @member [سبب]` | حظر عضو | رول المشرفين |
| `!mute @member [دقائق] [سبب]` | إسكات مؤقت | رول المشرفين |
| `!warn @member [سبب]` | تحذير عضو | رول المشرفين |
| `!warnings @member` | عرض تحذيرات عضو | رول المشرفين |
| `!ask [سؤال]` | سؤال الذكاء الاصطناعي | يعمل بقناة محددة فقط |
| `!ping` | فحص استجابة البوت | الجميع |
| `!info` | معلومات عن البوت | الجميع |

## ملاحظات أمان مهمة

- لا ترفع ملف `.env` أبداً على GitHub (محمي بـ `.gitignore`)
- فعّل بس الـ Intents اللي فعلاً محتاجها (مبدأ أقل صلاحيات ممكنة)
- إذا العميل هو اللي سجل الـ Application وأعطاك التوكين، اطلب منه إعادة توليده (Regenerate) بعد انتهاء شغلك إذا ما في اتفاق صيانة مستمر
