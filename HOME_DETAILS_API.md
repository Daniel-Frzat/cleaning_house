# شاشة "Tell us about your home" — توثيق الـAPI

آخر تحديث: 2026-10-13

أسئلة هذه الشاشة وخياراتها وأيقوناتها تُدار كلها من لوحة التحكم. التطبيق لا يثبّت أي سؤال في الكود: يقرأ الأسئلة من الخادم ويبني الشاشة منها.

> **قرار المنتج (2026-10-13):** الإجابات **معلومات فقط ولا تغيّر السعر.** السعر يبقى كما هو: `base_price + room_price × room_count` لكل خدمة. تُحفظ نسخة مجمَّدة من الإجابات على الطلب، ويُحفظ آخرها على العقار لتعبئة الشاشة مسبقًا في الطلب التالي.

- [للتطبيق](#للتطبيق)
- [للوحة التحكم](#للوحة-التحكم)
- [رموز الأخطاء](#رموز-الأخطاء)
- [ملاحظات تقنية](#ملاحظات-تقنية)

---

## للتطبيق

### 1) جلب أسئلة الشاشة

```
GET /api/home-details/form?property_id={property_id}
Authorization: Bearer <access token>
```

- `property_id` اختياري. عند إرساله تكون `value` آخر إجابة محفوظة لهذا العقار، إن بقيت صالحة للأسئلة الحالية، وإلا القيمة الافتراضية.
- يعيد الأسئلة **النشطة** فقط، بترتيب العرض، ومع كل سؤال خياراته النشطة.

```json
{
  "fields": [
    {
      "id": "6230734f-89fe-4ac9-bfb5-a60a60566b57",
      "key": "bedrooms",
      "label": "Bedrooms",
      "help_text": "",
      "input_type": "COUNTER",
      "section": "MAIN",
      "is_required": false,
      "min_value": 0,
      "max_value": 20,
      "icon": {
        "id": "0b1c...",
        "url": "https://<host>/api/icons/0b1c...?v=3f9a1c2b7d4e",
        "content_type": "image/svg+xml"
      },
      "options": [],
      "value": 0
    },
    {
      "id": "...",
      "key": "pets",
      "label": "Any pets at home?",
      "help_text": "",
      "input_type": "YES_NO",
      "section": "MAIN",
      "is_required": false,
      "min_value": null,
      "max_value": null,
      "icon": null,
      "options": [],
      "value": false
    },
    {
      "id": "...",
      "key": "room-contents",
      "label": "What's in the rooms?",
      "help_text": "",
      "input_type": "MULTI_CHOICE",
      "section": "EXTRA",
      "is_required": false,
      "min_value": null,
      "max_value": null,
      "icon": null,
      "options": [
        { "id": "...", "key": "oven", "label": "Oven", "icon": { "id": "...", "url": "...", "content_type": "image/png" } },
        { "id": "...", "key": "carpet", "label": "Carpet", "icon": null }
      ],
      "value": []
    }
  ]
}
```

### 2) كيف يُعرض كل نوع

| `input_type` | العنصر في الشاشة | شكل `value` |
| --- | --- | --- |
| `COUNTER` | عدّاد − n + بين `min_value` و `max_value` | رقم صحيح |
| `YES_NO` | زرّا No / Yes | `true` أو `false` |
| `SINGLE_CHOICE` | اختيار واحد من `options` | `key` الخيار، أو `null` |
| `MULTI_CHOICE` | اختيار متعدد من `options` | قائمة مفاتيح الخيارات `[]` |

| `section` | مكانه |
| --- | --- |
| `MAIN` | ظاهر دائمًا |
| `EXTRA` | خلف رابط "+ add other conditions (optional)" |

- `is_required: true` يعني أن السؤال إلزامي، ويرفض الخادم الطلب بدونه.
- `icon` قد يكون `null`، وعندها يعرض التطبيق السؤال بلا أيقونة أو بأيقونة احتياطية.
- `content_type` للأيقونة إحدى القيم `image/png` أو `image/webp` أو `image/svg+xml`. يجب أن يدعم التطبيق عرض SVG.

### 3) الأيقونات

- `icon.url` رابط عام لا يحتاج توكن، فيمكن تمريره مباشرة لعنصر الصورة.
- الرابط يحمل بصمة المحتوى (`?v=`) ويتغيّر عند استبدال الأيقونة من اللوحة. لذلك يمكن تخزين الصورة مؤقتًا **حسب الرابط** دون انتهاء صلاحية.
- الرد يحمل `Cache-Control: public, max-age=31536000, immutable` و `ETag`.

### 4) إرسال الإجابات مع الطلب

تُرسل الإجابات في الحقل `home_details` عند إنشاء الطلب، كـ `{key: value}`:

```
POST /api/bookings
```

```json
{
  "property_id": "...",
  "service_selections": [{ "service_type_id": "...", "room_count": 3 }],
  "home_details": {
    "bedrooms": 3,
    "bathrooms": 2,
    "living-areas": 1,
    "floors": 1,
    "pets": true,
    "room-contents": ["oven", "carpet"]
  }
}
```

- **حذف الحقل كليًا** يعني تخطّي الشاشة: لا تُحفظ إجابات ولا تُفرض الأسئلة الإلزامية. هذا يُبقي النسخ الأقدم من التطبيق تعمل.
- **إرساله، ولو `{}`**، يُفعّل التحقق الكامل، بما فيه الأسئلة الإلزامية.
- السؤال الاختياري الذي لا يُرسل لا يُحفظ.
- أي خطأ يرفض الطلب كاملًا بـ `400 invalid_home_details`، ولا يُحفظ أي شيء. الحقل `detail` يحمل رسالة واضحة، مثل `'Bedrooms' must be between 0 and 20.`

### 5) قراءة الإجابات بعد الحفظ

تعود الإجابات المجمَّدة في `home_details` كقائمة بترتيب الشاشة، مع تسمياتها كما كانت لحظة الطلب:

```json
"home_details": [
  { "key": "bedrooms", "label": "Bedrooms", "input_type": "COUNTER", "section": "MAIN", "value": 3, "value_labels": ["3"] },
  { "key": "pets", "label": "Any pets at home?", "input_type": "YES_NO", "section": "MAIN", "value": true, "value_labels": ["Yes"] },
  { "key": "room-contents", "label": "What's in the rooms?", "input_type": "MULTI_CHOICE", "section": "EXTRA", "value": ["oven", "carpet"], "value_labels": ["Oven", "Carpet"] }
]
```

- `value_labels` جاهزة للعرض مباشرة، فلا يحتاج التطبيق لربط المفاتيح بالتسميات.
- تعديل سؤال أو حذفه من اللوحة لاحقًا **لا يغيّر** ما حُفظ على الطلبات السابقة.

| من يرى | أين |
| --- | --- |
| العميل | `GET /api/bookings/{booking_id}` |
| المقاول قبل القبول | `GET /api/contractor/offers/{offer_id}` |
| المقاول المُسنَد | تفاصيل المهمة (`job`) |
| الأدمن | `GET /api/admin/bookings/{booking_id}` |

---

## للوحة التحكم

كل المسارات أدناه للأدمن فقط (`Authorization: Bearer <admin token>`)، وكل تعديل يُسجَّل في سجل التدقيق.

### الأيقونات

| الطلب | الغرض |
| --- | --- |
| `GET /api/admin/icons?q=` | قائمة الأيقونات مع عدد الأسئلة والخيارات التي تستعملها |
| `POST /api/admin/icons` | رفع أيقونة: `multipart/form-data` بالحقلين `name` و `file` |
| `GET /api/admin/icons/{icon_id}` | أيقونة واحدة |
| `PATCH /api/admin/icons/{icon_id}` | إعادة تسمية: `{"name": "..."}` |
| `POST /api/admin/icons/{icon_id}/file` | استبدال الملف: `multipart/form-data` بالحقل `file`. كل سؤال يستعملها يتحدّث تلقائيًا |
| `DELETE /api/admin/icons/{icon_id}` | حذف. يُرفض بـ `409 icon_in_use` ما دامت مستعملة |

**شروط الملف:** PNG أو WebP أو SVG، بحد أقصى 200 KB. النوع يُقرأ من محتوى الملف لا من امتداده. ملفات SVG التي تحتوي سكربت أو معالجات أحداث (`onload`...) أو روابط خارجية تُرفض.

مثال الرد:

```json
{
  "id": "0b1c...",
  "name": "Bed",
  "url": "https://<host>/api/icons/0b1c...?v=3f9a1c2b7d4e",
  "content_type": "image/png",
  "size": 1834,
  "used_by_fields": 1,
  "used_by_options": 0,
  "created_at": "2026-10-13T09:00:00Z",
  "updated_at": "2026-10-13T09:00:00Z"
}
```

### الأسئلة

| الطلب | الغرض |
| --- | --- |
| `GET /api/admin/home-detail-fields` | كل الأسئلة، النشطة والمخفية، مع خياراتها |
| `POST /api/admin/home-detail-fields` | إضافة سؤال، ويُوضع في آخر الترتيب ما لم يُحدَّد `sort_order` |
| `GET /api/admin/home-detail-fields/{field_id}` | سؤال واحد |
| `PATCH /api/admin/home-detail-fields/{field_id}` | تعديل جزئي. `key` و `input_type` إن أُرسلا يُتجاهلان |
| `DELETE /api/admin/home-detail-fields/{field_id}` | حذف نهائي مع خياراته |
| `PUT /api/admin/home-detail-fields/order` | إعادة الترتيب: `{"ids": [...]}` بكل المعرّفات مرة واحدة |

حقول السؤال:

| الحقل | النوع | ملاحظات |
| --- | --- | --- |
| `key` | نص | معرّف ثابت بأحرف صغيرة وأرقام و `-` أو `_` (مثل `bedrooms`). **لا يتغيّر بعد الإنشاء** |
| `label` | نص (80) | النص الظاهر |
| `input_type` | `COUNTER` / `YES_NO` / `SINGLE_CHOICE` / `MULTI_CHOICE` | **لا يتغيّر بعد الإنشاء** |
| `help_text` | نص (200) | نص مساعد اختياري |
| `icon_id` | معرّف أيقونة أو `null` | `null` في PATCH يزيل الأيقونة |
| `section` | `MAIN` / `EXTRA` | الافتراضي `MAIN` |
| `is_required` | منطقي | الافتراضي `false` |
| `min_value`, `max_value`, `default_value` | أرقام 0–99 | للعدّاد. لـ `YES_NO`: `default_value` = 0 (No) أو 1 (Yes) |
| `sort_order` | رقم | ترتيب العرض |
| `is_active` | منطقي | `false` يخفي السؤال من التطبيق دون حذفه |

مثال إنشاء سؤال:

```json
POST /api/admin/home-detail-fields
{
  "key": "floors",
  "label": "Floors",
  "input_type": "COUNTER",
  "icon_id": "0b1c...",
  "min_value": 1,
  "max_value": 10,
  "default_value": 1
}
```

### الخيارات (لأسئلة الاختيار فقط)

| الطلب | الغرض |
| --- | --- |
| `POST /api/admin/home-detail-fields/{field_id}/options` | إضافة خيار: `{"key", "label", "icon_id"?, "sort_order"?, "is_active"?}` |
| `PATCH /api/admin/home-detail-fields/{field_id}/options/{option_id}` | تعديل `label` / `icon_id` / `sort_order` / `is_active` |
| `DELETE /api/admin/home-detail-fields/{field_id}/options/{option_id}` | حذف نهائي |
| `PUT /api/admin/home-detail-fields/{field_id}/options/order` | إعادة الترتيب: `{"ids": [...]}` |

- كل مسارات الخيارات تعيد السؤال كاملًا بخياراته.
- `key` الخيار فريد داخل سؤاله، ولا يتغيّر بعد الإنشاء.
- إضافة خيار لسؤال من نوع `COUNTER` أو `YES_NO` تُرفض بـ `422 home_detail_not_choice_field`.

### ما يحدث عند الحذف

- **الطلبات السابقة لا تتأثر أبدًا:** كل طلب يحمل نسخته المجمَّدة من الأسئلة والإجابات.
- **الإجابة المحفوظة على العقار** لسؤال أو خيار محذوف تُتجاهل، وتعود الشاشة للقيمة الافتراضية.
- **لإيقاف سؤال مؤقتًا** استعمل `is_active: false` بدل الحذف.

### الأسئلة الافتراضية

أُنشئت مع النشر الأول، بلا أيقونات، وكلها قابلة للتعديل والحذف من اللوحة:

| `key` | `label` | النوع | الحدود | الافتراضي |
| --- | --- | --- | --- | --- |
| `bedrooms` | Bedrooms | `COUNTER` | 0–20 | 0 |
| `bathrooms` | Bathrooms | `COUNTER` | 0–20 | 0 |
| `living-areas` | Living areas | `COUNTER` | 0–20 | 0 |
| `floors` | Floors | `COUNTER` | 1–10 | 1 |
| `pets` | Any pets at home? | `YES_NO` | — | No |

---

## رموز الأخطاء

شكل الخطأ الموحّد: `{"code": "...", "detail": "..."}`. ترجم حسب `code`، لأن `detail` قد يتغيّر. القائمة الكاملة في [ERROR_CODES.md](ERROR_CODES.md).

| `code` | HTTP | المعنى |
| --- | --- | --- |
| `invalid_home_details` | 400 | إجابات لا تطابق الأسئلة الحالية: سؤال غير معروف، قيمة خارج الحدود أو بنوع خاطئ، خيار غير موجود، أو سؤال إلزامي ناقص |
| `property_forbidden` | 403 | `property_id` لعقار لا يملكه المستخدم |
| `property_not_found` | 404 | لا يوجد عقار بهذا المعرّف |
| `admin_required` | 403 | المستخدم ليس أدمن |
| `home_detail_field_not_found` | 404 | لا يوجد سؤال بهذا المعرّف |
| `home_detail_option_not_found` | 404 | لا يوجد خيار بهذا المعرّف في هذا السؤال |
| `home_detail_key_taken` | 409 | المفتاح مستعمل لسؤال آخر، أو لخيار آخر في السؤال نفسه |
| `home_detail_not_choice_field` | 422 | محاولة إضافة خيار لسؤال ليس من نوع اختيار |
| `invalid_order` | 422 | قائمة الترتيب ليست كل المعرّفات مرة واحدة |
| `icon_not_found` | 404 / 422 | 404 في مسارات الأيقونات. 422 عند ربط سؤال أو خيار بـ `icon_id` غير موجود |
| `icon_in_use` | 409 | حذف أيقونة ما زالت مستعملة |
| `icon_too_large` | 413 | الملف أكبر من 200 KB |
| `empty_icon` | 422 | ملف فارغ |
| `unsupported_icon_format` | 422 | ليس PNG أو WebP أو SVG آمنًا |
| `validation_error` | 422 | قيمة لا تحقق شروط الحقل، مثل `default_value` خارج `min_value`–`max_value`، أو `key` بصيغة غير صحيحة |

---

## ملاحظات تقنية

- **الكود:**
  - المسارات: [apps/services/api/home_details.py](apps/services/api/home_details.py)
  - منطق الأسئلة والتحقق من الإجابات: [apps/services/services/home_details.py](apps/services/services/home_details.py)
  - الأيقونات: [apps/services/services/icons.py](apps/services/services/icons.py)
  - الاختبارات: [tests/test_home_details.py](tests/test_home_details.py)
- **النماذج:** `CatalogIcon` و `HomeDetailField` و `HomeDetailOption` في `apps/services/models.py`، و `Booking.home_details` (النسخة المجمَّدة)، و `Property.home_details` (آخر إجابات للتعبئة المسبقة).
- **تخزين الأيقونات في قاعدة البيانات لا على القرص:** قرص Railway يُمسح عند كل نشر. الأيقونات صغيرة، وتُقدَّم من رابط عام مع تخزين مؤقت طويل.
- **أمان SVG:** يُفحص الملف عند الرفع، ويُقدَّم بترويسة `Content-Security-Policy: ... sandbox` و `X-Content-Type-Options: nosniff`، فلا يمكن تنفيذ أي شيء فيه حتى لو فُتح مباشرة في المتصفح.
- **Django admin** يعرض هذه النماذج للقراءة فقط مع معاينة الأيقونات. التعديل من لوحة التحكم عبر الـAPI، حتى يمر كل تعديل بالفحوص وسجل التدقيق.
- **ربط الإجابات بالسعر** قرار منتج مستقل لم يُتخذ. لا تُضف أي أثر سعري دون قرار صريح.
- مواصفة الـAPI الكاملة في [openapi.json](openapi.json).
