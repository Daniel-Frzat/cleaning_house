# رد الـ Backend — الإشعارات (Push Notifications)

**التاريخ:** 2026-09-28
**إلى:** فريق الموبايل (Qcleano)
**رداً على:** «المطلوب من الـ Backend — الإشعارات» (2026-09-28)

---

## 1. ما أكّدناه من طلبكم ✅

| البند | النتيجة |
|---|---|
| تسجيل الجهاز `POST /api/devices` | ✅ كما وصفتم. |
| إلغاء الجهاز عند الخروج (`device_token` في logout) | ✅ كما وصفتم. |
| قنوات Android: `offers` للأولوية العالية، و`general` للعادية | ✅ هذه هي **القيم الافتراضية** في الكود، فلا حاجة لضبط `NOTIFICATION_CHANNEL_*` على السيرفر. |
| `offer.cancelled` مع `offer_id` | ✅ موجود. |
| مشروع Firebase `cleano-677af` | ✅ نفس المشروع. |

---

## 2. ثلاثة تصحيحات

**أ) `title` و`body` لم يكونا داخل `data`، وأضفناهما.**

- كان الإرسال يضعهما في كتلة `notification` في FCM فقط.
- أضفناهما الآن إلى `data` أيضاً، بالنص نفسه (commit قادم، يصل مع النشر القادم).
- كتلة `notification` باقية كما هي، ليعرض النظام الإشعار والتطبيق في الخلفية.

حقول `data` الكاملة الآن (كلها نصوص):

```
type, audience, notification_id, title, body
+ المعرّفات حسب الحدث: booking_id, offer_id, job_id, payment_id, payout_id, …
```

**ب) `PUSH_ADAPTER` افتراضيه على الإنتاج هو FCM لا Fake.**

- `FakePushAdapter` هو الافتراضي في بيئة التطوير المحلية فقط.
- على الإنتاج يكفي `FIREBASE_CREDENTIALS_JSON`. بدونه يفشل الإرسال ويظهر `push_error` في السجل، ولا يُستخدم Fake بصمت.
- إضافة `PUSH_ADAPTER` صراحةً لا تضر.

**ج) لم يكن ممكناً إرسال إشعار تجريبي لمستخدم واحد، وأضفناه.**

- البث من لوحة الأدمن يصل إلى مجموعة كاملة، مثل كل العملاء.
- أضفنا مساراً خاصاً للاختبار (§4).

---

## 3. الإعداد على سيرفر الإنتاج

| المتغير | الحالة |
|---|---|
| `FIREBASE_CREDENTIALS_JSON` | ضبطه صاحب المشروع سابقاً. سنتأكد منه في الاختبار (§4). |
| `PUSH_ADAPTER=adapters.push_notification.fcm.FCMPushAdapter` | اختياري (هو الافتراضي). |
| `NOTIFICATIONS_DELIVERY=inline` | ✅ الافتراضي. لا يوجد Celery worker على Railway، فيبقى inline. |
| `NOTIFICATION_CHANNEL_HIGH` / `NOTIFICATION_CHANNEL_NORMAL` | غير لازمين، فالافتراضي `offers` و`general`. |

مفتاح Service Account لا يُرفع إلى Git (مستثنى في `.gitignore`)، ولا يُرسل على أي قناة.

---

## 4. الاختبار

**المسار الجديد (للأدمن):**

```
POST /api/admin/users/{user_id}/test-notification
{"priority": "HIGH", "title": "Test", "body": "Hello"}
```

- **متى يُرسل:** فوراً، والرد يحمل النتيجة الحقيقية مباشرة:

  | `push_status` | المعنى |
  |---|---|
  | `SENT` | ✅ وصل. |
  | `NO_DEVICE` | التطبيق لم يسجّل الجهاز. `devices` في الرد = 0. |
  | `PENDING` أو `FAILED` مع `push_error` | مشكلة في مفتاح Firebase. نراجعها من جهتنا. |

- **القناة:** `HIGH` يستخدم قناة `offers`، و`NORMAL` يستخدم `general`.
- **صندوق الإشعارات:** يُحفظ الإشعار أيضاً في صندوق المستخدم بنوع `type: test`.

**خطوات الاختبار بعد النشر:**

1. سجّلوا الدخول من التطبيق على جهاز Android، فيسجّل التطبيق الجهاز تلقائياً.
2. أرسلوا لنا رقم الهاتف أو معرّف المستخدم، وسنرسل له إشعاراً تجريبياً بـ `NORMAL` ثم `HIGH`، ونرسل لكم النتيجتين.
3. عرض عمل حقيقي يصل على قناة `offers` بالأولوية العالية تلقائياً (`offer.new`).

**سجل الإشعارات:** لوحة الأدمن تعرضه لأي مستخدم مع حالة الإرسال: `GET /api/admin/users/{user_id}/notifications`.

---

## 5. النشر

- **ما على الإنتاج الآن:** commit `1bd2240`، وفيه `adapters/push_notification/fcm.py` و`/api/devices` منذ إضافة الإشعارات.
- **ما يصل مع النشر القادم:** `title`/`body` داخل `data`، ومسار الإشعار التجريبي، وكل ما ذُكر في `BACKEND_UPDATE_FOR_MOBILE_2026-09-27.md`.
- سنبلّغكم برقم الـ commit فور النشر.

---

## 6. iOS

متفقون: مفتاح APNs (.p8) يُرفع على Firebase من جهتكم بعد اعتماد الـ Bundle ID، ولا يلزم أي تغيير على السيرفر.

---

## 7. للرد

- [x] حقول `data` مطابقة، وأُضيف `title`/`body`
- [x] القنوات `offers` و`general` افتراضية
- [ ] النشر ورقم الـ commit — بعد الرفع
- [ ] نتيجة الاختبار (`push_status`) — بعد أن ترسلوا لنا مستخدماً سجّل جهازه
