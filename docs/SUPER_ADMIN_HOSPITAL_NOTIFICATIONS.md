# Super Admin → Hospital Admin notifications

## Purpose

This API lets a **Super Admin** send **email notifications** to **all users with the Hospital Admin role** that belong to a **specific hospital**. Notifications are queued in that hospital’s notification outbox and delivered asynchronously (same pipeline as other hospital-scoped emails).

Only administrators tied to the chosen hospital receive the message; other hospitals are unaffected.

---

## Endpoint

| Item | Value |
|------|--------|
| **Method** | `POST` |
| **Path** | `/api/v1/super-admin/notifications/send-to-hospital-admins` |
| **Authentication** | Bearer JWT for a user with the Super Admin role |

---

## Request body

### Targeting a single hospital (typical use)

You must identify the hospital in **one** of these ways:

| Field | Type | Required | Description |
|--------|------|----------|-------------|
| **`hospital_id`** | UUID string | Optional* | **Preferred.** Exact identifier of the hospital. Only admins with `hospital_id` equal to this value are notified. |
| **`hospital_name`** | string | Optional* | Alternative to `hospital_id`. Matching is **case-insensitive** on the hospital’s display name. The first matching hospital wins. |

\* Exactly one of: `hospital_id`, `hospital_name`, or platform-wide mode below.

| Field | Type | Required | Description |
|--------|------|----------|-------------|
| **`subject`** | string | **Yes** | Title / subject line of the email. |
| **`message`** | string | **Yes** | Body content of the notification (plain text; rendered as appropriate for the email template path). |

### Platform-wide broadcast (optional)

| Field | Type | Default | Description |
|--------|------|---------|-------------|
| **`notify_all_hospitals`** | boolean | `false` | If `true`, notifies **every** Hospital Admin user on the platform. **Must not** be sent together with `hospital_id` or `hospital_name`. |

---

## Behaviour

1. **Super Admin** calls the endpoint with valid credentials.
2. The server resolves the **target hospital**:
   - By **UUID** (`hospital_id`), or  
   - By **name** (`hospital_name`), or  
   - **All hospitals** if `notify_all_hospitals` is `true`.
3. The system loads all users that have the **Hospital Admin** role and whose **`hospital_id`** matches the resolved hospital (or all such users when broadcasting).
4. For each such user **with a non-empty email**, an email notification job is **queued** for that hospital’s tenant (`ADMIN_NOTIFICATION` event). Admins without `hospital_id` are skipped.
5. If **no hospital** matches `hospital_id` or `hospital_name`, the API returns **404** with a clear error code.
6. If the request omits hospital targeting and does not set `notify_all_hospitals`, the API returns **400** (`HOSPITAL_SCOPE_REQUIRED`).

---

## Successful response (example)

```json
{
  "sent": 2,
  "total_admins": 2,
  "message": "Queued 2 notification(s)",
  "hospital_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

- **`sent`**: Number of notification jobs successfully queued (admins with email and valid `hospital_id`).
- **`total_admins`**: Number of Hospital Admin users matched by the query (may differ from `sent` if some lack email or `hospital_id`).
- **`hospital_id`**: Present when a **single hospital** was targeted; omitted for platform-wide broadcasts.

---

## Error responses

| HTTP code | Code | When |
|-----------|------|------|
| **400** | `HOSPITAL_SCOPE_REQUIRED` | Neither `hospital_id`, `hospital_name`, nor `notify_all_hospitals=true` was provided. |
| **400** | `CONFLICTING_HOSPITAL_SCOPE` | `notify_all_hospitals` is `true` but `hospital_id` or `hospital_name` was also set. |
| **404** | `HOSPITAL_NOT_FOUND` | No hospital matches the given `hospital_id` or `hospital_name`. |
| **401 / 403** | — | Missing token or user is not a Super Admin. |

---

## Example: notify by hospital name

```http
POST /api/v1/super-admin/notifications/send-to-hospital-admins
Authorization: Bearer <super_admin_jwt>
Content-Type: application/json
```

```json
{
  "hospital_name": "City General Hospital",
  "subject": "Scheduled maintenance",
  "message": "The admin portal will be unavailable on Saturday 02:00–04:00 UTC."
}
```

---

## Example: notify by hospital ID (recommended)

```json
{
  "hospital_id": "550e8400-e29b-41d4-a716-446655440000",
  "subject": "Scheduled maintenance",
  "message": "The admin portal will be unavailable on Saturday 02:00–04:00 UTC."
}
```

---

## Operational notes

- Delivery depends on that hospital’s **email provider** configuration and the **notification worker** processing the queue.
- Hospital admins **do not** need to be listed individually: every **Hospital Admin** user linked to that hospital receives the same notification (each gets their own queued job to their own email address).
