# Lab module: API reference, end-to-end flow, and testing

Base URL prefix: **`/api/v1`**. All lab routers are mounted with a **plan feature** dependency: the hospital subscription must include **lab tests** (`FEATURE_LAB_TESTS`). If the plan does not include it, requests return **403** before your handler runs.

## Authentication and headers

| Requirement | Details |
|-------------|---------|
| **Bearer JWT** | `Authorization: Bearer <access_token>` on every endpoint except the **public share link** (see below). |
| **Hospital context** | User must have `hospital_id` (tenant). Missing context returns **400** `NO_HOSPITAL_CONTEXT` on several lab flows. |
| **Role** | Each route checks a specific role (see tables below). Wrong role → **403**. |

Frontend should store the access token securely (memory or httpOnly cookie per your security model), attach it to all lab API calls, and handle **401** (refresh or re-login) and **403** (hide UI or show “not available on your plan / role”).

## Canonical route list

The running app exposes **95** routes under `/api/v1` whose path contains `/lab/`. The table below matches the FastAPI app registry (use **Swagger** `/docs` or **OpenAPI** `/openapi.json` for schemas and request bodies).

### Lab – Test registration (`/api/v1/lab/registration/...`)

| Method | Path | Roles |
|--------|------|--------|
| POST | `/lab/registration/categories` | LAB_TECH |
| GET | `/lab/registration/categories` | LAB_TECH |
| GET | `/lab/registration/categories/{category_id}` | LAB_TECH |
| PUT | `/lab/registration/categories/{category_id}` | LAB_TECH |
| POST | `/lab/registration/tests` | LAB_TECH |
| GET | `/lab/registration/tests` | LAB_TECH |
| GET | `/lab/registration/tests/{test_id}` | LAB_TECH |
| PUT | `/lab/registration/tests/{test_id}` | LAB_TECH |
| POST | `/lab/registration/orders` | LAB_TECH **or** RECEPTIONIST |
| POST | `/lab/registration/orders/{order_id}/register` | LAB_TECH **or** RECEPTIONIST |
| GET | `/lab/registration/orders` | LAB_TECH **or** RECEPTIONIST |
| GET | `/lab/registration/orders/{order_id}` | LAB_TECH **or** RECEPTIONIST |
| PATCH | `/lab/registration/orders/{order_id}/priority` | LAB_TECH **or** RECEPTIONIST |
| PATCH | `/lab/registration/orders/{order_id}/cancel` | LAB_TECH **or** RECEPTIONIST |
| GET | `/lab/registration/sample-types` | LAB_TECH |
| GET | `/lab/registration/order-priorities` | LAB_TECH |
| GET | `/lab/registration/order-statuses` | LAB_TECH |
| GET | `/lab/registration/stats` | LAB_TECH |

### Lab – Sample collection (`/api/v1/lab/samples/...`)

| Method | Path | Roles |
|--------|------|--------|
| POST | `/lab/samples/orders/{order_id}/create` | LAB_TECH |
| GET | `/lab/samples/orders/{order_id}` | LAB_TECH |
| GET | `/lab/samples` | LAB_TECH |
| GET | `/lab/samples/{sample_id}` | LAB_TECH |
| GET | `/lab/samples/{sample_id}/barcode` | LAB_TECH |
| GET | `/lab/samples/{sample_id}/barcode.png` | LAB_TECH |
| GET | `/lab/samples/scan/{barcode_value}` | LAB_TECH |
| PATCH | `/lab/samples/{sample_id}/collect` | LAB_TECH |
| PATCH | `/lab/samples/{sample_id}/receive` | LAB_TECH |
| PATCH | `/lab/samples/{sample_id}/start-analysis` | LAB_TECH |
| PATCH | `/lab/samples/{sample_id}/store` | LAB_TECH |
| PATCH | `/lab/samples/{sample_id}/discard` | LAB_TECH |
| PATCH | `/lab/samples/{sample_id}/reject` | LAB_TECH |
| POST | `/lab/samples/bulk/collect` | LAB_TECH |
| GET | `/lab/samples/utils/container-types` | LAB_TECH |
| GET | `/lab/samples/utils/sample-statuses` | LAB_TECH |
| GET | `/lab/samples/utils/rejection-reasons` | LAB_TECH |
| GET | `/lab/samples/stats` | LAB_TECH |

### Lab – Result entry (`/api/v1/lab/result-entry/...`)

Roles vary: **entry** allows LAB_TECH, LAB_SUPERVISOR, LAB_ADMIN, HOSPITAL_ADMIN, PATHOLOGIST; **verify/release/approve** restrict to supervisor/admin/pathologist roles as implemented in code.

| Method | Path | Notes |
|--------|------|--------|
| POST | `/lab/result-entry/results/{order_item_id}` | Can resolve by order item UUID; if 404, body may include `test_id` to treat path as **order_id**. |
| GET | `/lab/result-entry/results/{result_id}` | |
| PUT | `/lab/result-entry/results/{result_id}` | |
| POST | `/lab/result-entry/results/{result_id}/verify` | Supervisor+ |
| POST | `/lab/result-entry/results/{result_id}/release` | Supervisor+; may **block** if QC invalid. |
| POST | `/lab/result-entry/results/{result_id}/reject` | Supervisor+ |
| POST | `/lab/result-entry/results/{result_id}/approve` | PATHOLOGIST / admin / supervisor |
| GET | `/lab/result-entry/worklist` | |
| POST | `/lab/result-entry/orders/{order_id}/results` | Alternative: order + test in body |
| GET | `/lab/result-entry/orders/{order_id}/results` | |
| POST | `/lab/result-entry/orders/{order_id}/reports` | Generate report |
| GET | `/lab/result-entry/orders/{order_id}/reports` | Report history |
| GET | `/lab/result-entry/reports/{report_id}` | Currently returns **501** not implemented |

### Lab – Equipment and QC (`/api/v1/lab/equipment-qc/...`)

| Method | Path |
|--------|------|
| POST, GET | `/lab/equipment-qc/equipment` |
| GET, PUT | `/lab/equipment-qc/equipment/{equipment_id}` |
| PATCH | `/lab/equipment-qc/equipment/{equipment_id}/status` |
| GET, POST | `/lab/equipment-qc/equipment/{equipment_id}/logs` |
| GET | `/lab/equipment-qc/equipment/logs` |
| GET | `/lab/equipment-qc/equipment/logs/{log_id}` | **501** not implemented |
| POST, GET | `/lab/equipment-qc/qc/rules` |
| POST, GET | `/lab/equipment-qc/qc/runs` |
| GET | `/lab/equipment-qc/qc/status` |

### Lab – Report access & sharing (`/api/v1/lab/reports/...`)

| Method | Path | Roles |
|--------|------|--------|
| GET | `/lab/reports/doctor/lab-reports` | DOCTOR |
| GET | `/lab/reports/patient/lab-reports` | PATIENT |
| GET | `/lab/reports/receptionist/lab-reports` | RECEPTIONIST (`patient_id` query required) |
| GET | `/lab/reports/lab-reports/{report_id}` | Authenticated + RBAC |
| GET | `/lab/reports/lab-reports/{report_id}/pdf` | PDF download |
| GET | `/lab/reports/lab-reports/{report_id}/summary` | |
| PATCH | `/lab/reports/lab/orders/{order_id}/report/publish` | LAB_TECH, HOSPITAL_ADMIN |
| PATCH | `/lab/reports/lab/orders/{order_id}/report/unpublish` | LAB_TECH, HOSPITAL_ADMIN |
| GET | `/lab/reports/lab/orders/{order_id}/report/publish-status` | LAB_TECH, HOSPITAL_ADMIN |
| POST | `/lab/reports/lab/orders/{order_id}/report/share-link` | LAB_TECH, HOSPITAL_ADMIN |
| GET | `/lab/reports/lab/report-share/{token}` | **No auth** (public token) |
| POST | `/lab/reports/lab/report-share/{token}/verify-otp` | Public |
| PATCH | `/lab/reports/lab/report-share/{token}/revoke` | LAB_TECH, HOSPITAL_ADMIN |
| POST | `/lab/reports/notifications/lab-report-ready/{order_id}` | LAB_TECH, HOSPITAL_ADMIN |
| GET | `/lab/reports/notifications/status` | Authenticated |

**Note:** Paths under `/lab/reports/lab/...` repeat the `lab` segment because the router prefix is `/lab/reports` and inner paths include `/lab/...`. Call URLs **exactly** as in Swagger.

### Lab – Audit and compliance (`/api/v1/lab/audit/...`)

Paths are nested as `/lab/audit/lab/...` (same double-`lab` pattern). Endpoints include audit logs, entity trail, sample trace, compliance exports, and analytics (TAT, volume, QC failure, equipment uptime, technician productivity, dashboard summary). Roles: **LAB_TECH** or **HOSPITAL_ADMIN** unless noted in OpenAPI.

---

## End-to-end workflow (step by step)

This is the typical **happy path** for automation and manual QA.

1. **Subscribe / enable lab**  
   Ensure the hospital plan includes lab tests so routes are not blocked.

2. **Login** as a user with **LAB_TECH** (and optionally **RECEPTIONIST** for front-desk order creation).  
   Obtain `access_token`.

3. **Catalogue (one-time setup)**  
   - `POST /lab/registration/categories` → category UUID.  
   - `POST /lab/registration/tests` → test UUIDs (sample types must align with later sample creation).

4. **Create order**  
   - `POST /lab/registration/orders` with patient id, tests, priority, source (e.g. WALKIN or DOCTOR).  
   - If using drafts: `POST /lab/registration/orders/{order_id}/register` to move **DRAFT → REGISTERED**.

5. **Samples**  
   - `POST /lab/samples/orders/{order_id}/create` with sample definitions for order items.  
   - Optional: `GET /lab/samples/.../barcode.png` for labels.  
   - Workflow: **collect** → **receive** → **start-analysis** (sample must reach **IN_PROCESS** before results).

6. **Results**  
   - `GET /lab/result-entry/worklist` to pick work.  
   - `POST /lab/result-entry/orders/{order_id}/results` or `POST /lab/result-entry/results/{order_item_id}` with parameter values.  
   - Supervisor path: **verify** → **release** (release may fail if **QC** is invalid—use equipment/QC endpoints first).

7. **Reports and access**  
   - `POST /lab/result-entry/orders/{order_id}/reports` to generate report metadata.  
   - `PATCH /lab/reports/lab/orders/{order_id}/report/publish` when ready for doctor/patient.  
   - Doctors/patients use their respective **GET** list/detail endpoints under `/lab/reports/...`.

8. **Audit**  
   - Use `/lab/audit/lab/audit/logs` and related analytics for compliance review.

---

## How to test (manual)

1. Open **`/docs`** on your deployment (e.g. `https://<host>/docs`).  
2. **Authorize** with “Authorize” and paste `Bearer <token>`.  
3. Execute steps in **End-to-end workflow** above; after each step, confirm status fields in **GET order** / **GET sample** / **GET result** responses.  
4. **Negative tests:** wrong role (expect **403**), invalid UUID (expect **400**), missing hospital (expect **400**), cancelled order (expect business error from service).

### Automated smoke test in this repo

Run:

```bash
pytest tests/test_lab_api_inventory.py -v
```

This checks that all expected lab routes are registered (guards against accidental router removal). It does **not** call the database.

---

## Frontend requirements (summary)

| Area | Requirement |
|------|-------------|
| **Auth** | JWT on all authenticated routes; public share link is the main exception. |
| **Plan** | Handle **403** when lab feature is disabled; hide lab menus or show upgrade message. |
| **Tenant** | All data is hospital-scoped; never send another hospital’s IDs from the client. |
| **Order UI** | Load enums from `sample-types`, `order-priorities`, `order-statuses` for dropdowns. |
| **Samples** | Show barcode/QR; support scan workflow via `GET .../scan/{barcode_value}`. Enforce status transitions (disable actions until prior step is done). |
| **Results** | Separate UX for **tech entry** vs **supervisor verify/release** vs **pathologist approve**; show QC warnings when release is blocked. |
| **Reports** | Doctor vs patient vs receptionist use different list endpoints; receptionist must pass `patient_id`. |
| **PDF** | Use `lab-reports/{report_id}/pdf` with blob download handling. |
| **Share links** | Store token from share-link API; open public URL without JWT; handle expiry and revoke. |

---

## Schema reference

Request and response models live in **`app/schemas/lab.py`**. Prefer **OpenAPI** (`/openapi.json`) for field-level accuracy as schemas evolve.
