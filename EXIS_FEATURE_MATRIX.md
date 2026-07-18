# EXIS Feature Matrix

Status reflects this audit's local evidence, not an assertion about untested production configuration.

| Feature | Page / route | Expected behavior | Result | Evidence / follow-up |
|---|---|---|---|---|
| Public home and search | `/`, `/search` | Search title, author, topics, cabinet, and stock state | Pass | Local seeded search HTTP 200 and Chromium desktop/mobile render |
| Approved book cover rendering | Home/search cards | Render approved HTTPS cover or safe placeholder | Pass | CWGV-hosted test cover rendered; non-approved URLs intentionally use placeholder |
| Book details | `/book_details/<title>` | Show current cabinet state without mutation | Pass | GET regression test confirms `view_count` remains unchanged; dynamic response is no-store |
| View analytics | `/api/track_view` | Increment only from intentional protected request | Pass | POST-only shared client helper, CSRF tests, 60/min limit |
| Public issue report | `/api/report_issue` | Validate/rate-limit/persist report | Pass | Existing regression tests |
| Login/logout | `/login`, `/logout` | CSRF-protected session lifecycle | Pass | Existing auth/security tests |
| Registration with invite | `/register` | Create role-bound user from valid invite | Partial | Static review and existing tests; no real invite lifecycle run this audit |
| Admin dashboard | `/admin` | Authenticated inventory operations | Partial | Route checks reviewed; no full authenticated browser journey |
| Add logical inventory record | `/add_book` | One title-location record, no integer quantity | Pass | Unsupported `amount` rejected by regression test |
| Archive/remove a record | Inventory DELETE / adjust | Preserve history and report outcome | Pass | Regression tests; `delta=-1` only legacy adjustment |
| Restore existing record | `/add_book` | Restore/re-stock title in existing cabinet | Partial | Code reviewed; browser undo flow not run |
| Move a record | `/cabinets/.../move` | One atomic logical move with duplicate protection | Pass | Integration test; PostgreSQL concurrency still needs staging validation |
| Replenish from reserve | `/replenish/<title>` | Reserve -> display only, single submit | Pass | Backend source/title checks and regression tests |
| Cabinet create/update | `/cabinets` POST/PATCH | Valid unique display/reserve cabinets | Partial | Static review; not browser tested |
| Cabinet delete | `DELETE /cabinets/<id>` | Refuse any retained inventory history | Pass | Archived-inventory regression test |
| Filters and suggestions | `/search` | Return limited results and similarity suggestions | Partial | Direct title search rendered; broad queries not performance tested |
| Metadata preview/import | `/admin/import/*` | Auth/role-bound preview and commit | Partial | Route review; no real import fixture journey |
| Event management | `/admin/events*` | Manager-only event CRUD/reorder | Partial | Route review; no real manager session journey |
| Audit export | `/admin/audit/export` | Advance-admin only and formula-safe CSV | Pass | Existing security tests |
| In-app snapshot | `/admin/backup` | Create convenience record, not durable DR | Partial | Code reviewed; no production storage test |
| Offsite backup | `database.tools.offsite_backup` | Dump, validate, upload, verify object | Not Testable | Requires Postgres, pg tools, S3-compatible credentials, and bucket |
| Legacy SQLite upgrade | `db_tools init-db` | Preserve all inventory and add model columns | Pass | Historical snapshot: 720 -> 720 rows; home HTTP 200 |
| Security headers | All app responses | CSP, HSTS hosted, XFO, nosniff, referrer/permissions | Pass | Regression tests and public header sample |
| Service worker | `/sw.js` | Dynamic no-store worker, safe cache policy | Pass | Existing security test and local browser requests |
| Mobile public search | `/search` at 390px | No overlapping brand/search controls; readable card | Pass | Chromium full-page verification after CSS fix |
| Keyboard/modal accessibility | Public/admin modals | Dialog semantics, focus trap/return, Escape | Partial | Escape exists in several flows; focus management is not complete |
| Offline/network recovery | Service worker/API failure paths | Clear state and recoverable UI | Partial | Source reviewed; no throttled network/browser scenario |

## Model Boundary

EXIS deliberately does **not** support integer stock quantities. `Inventory` represents a unique `(title_id, cabinet_id)` relationship with `in_stock` and `status`. Requirements for counts, partial transfers, or quantity adjustments need a dedicated schema/product decision rather than further changes to the deprecated `amount` and `delta` API parameters.
