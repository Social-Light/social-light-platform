# Social Light — User Acceptance Testing (UAT) Checklist

**Application:** Social Light Media Monitoring Platform
**Environment:** ☐ Staging  ☐ Production  ☐ Local
**Build / Version:** ____________________
**Tester:** ____________________  **Date:** ____________________

### How to use
Work through each scenario as the stated **Role**, perform the steps, and compare against **Expected Result**. Mark **Status** as ✅ Pass / ❌ Fail / ⚠️ Partial and record anything unexpected under **Notes**. Log every ❌/⚠️ as a defect with a screenshot.

**Roles:** `Platform Admin` (manages all orgs/users) · `Org Admin` · `Viewer` (read-only)

**Test data needed before starting:** at least one test organisation with keywords + competitors configured, a sample CSV for each media type (online/print/social/broadcast), and a test email inbox you can access.

---

## 1. Authentication & Access Control

| # | Scenario | Expected Result | Role | Status | Notes |
|---|----------|-----------------|------|--------|-------|
| 1.1 | Log in with valid credentials | Lands on the organisations/dashboard page | Any | ☐ | |
| 1.2 | Log in with wrong password | Clear error message; no access granted | Any | ☐ | |
| 1.3 | Access an app URL while logged out | Redirected to the login page | — | ☐ | |
| 1.4 | Log out | Session ends; redirected to login; back button doesn't expose app | Any | ☐ | |
| 1.5 | Request password reset for a known email | "Email sent" confirmation; reset email received | Any | ☐ | |
| 1.6 | Open the password-reset email | Renders as the branded card (blue header, green button), not raw text | — | ☐ | |
| 1.7 | Use the reset link to set a new password | Password updates; can log in with new password | Any | ☐ | |
| 1.8 | Reuse an expired/used reset link | Rejected with a helpful message | — | ☐ | |
| 1.9 | New user receives the welcome email | Branded card; "Set Your Password" button works | — | ☐ | |
| 1.10 | Viewer attempts to reach admin-only pages (Organisations, Users) | Access denied / options not visible | Viewer | ☐ | |

## 2. Organisation Management

| # | Scenario | Expected Result | Role | Status | Notes |
|---|----------|-----------------|------|--------|-------|
| 2.1 | Create an organisation (name, email, industry, country, status) | Org created and appears in the list | Platform Admin | ☐ | |
| 2.2 | Create org with logo, gradient colours, social links (Facebook/LinkedIn/X) | All fields saved; logo displays | Platform Admin | ☐ | |
| 2.3 | Edit an organisation's details | Changes persist after reload | Platform Admin | ☐ | |
| 2.4 | Delete an organisation | Org removed; its data no longer accessible | Platform Admin | ☐ | |
| 2.5 | Open org details modal | Existing keywords & competitors load correctly | Platform Admin | ☐ | |
| 2.6 | Switch organisation via the top "Switch Organisations" modal | Selected org's dashboard loads | Any | ☐ | |
| 2.7 | Mandatory field validation (e.g. blank name) | Save blocked with a clear message | Platform Admin | ☐ | |

## 3. Keywords (Organisation Setup)

| # | Scenario | Expected Result | Role | Status | Notes |
|---|----------|-----------------|------|--------|-------|
| 3.1 | Add a Brand keyword | Appears under Brand category | Admin | ☐ | |
| 3.2 | Add Personnel and Campaign keywords | Appear under correct categories | Admin | ☐ | |
| 3.3 | Add a duplicate keyword in the same category | Prevented (no duplicate created) | Admin | ☐ | |
| 3.4 | Delete a keyword | Removed from the list | Admin | ☐ | |

## 4. Competitors

| # | Scenario | Expected Result | Role | Status | Notes |
|---|----------|-----------------|------|--------|-------|
| 4.1 | Add a competitor with a name only | Created and listed | Admin | ☐ | |
| 4.2 | Add a competitor with aliases (e.g. `FNB, First National Bank`) | Aliases saved | Admin | ☐ | |
| 4.3 | Edit a competitor's aliases | Updated values persist | Admin | ☐ | |
| 4.4 | Delete a competitor | Removed; its mentions disappear from the tab | Admin | ☐ | |
| 4.5 | Competitor tab — Online/Broadcast/Print/**Social** sub-tabs | Each lists articles whose text contains the competitor name **or any alias** | Any | ☐ | |
| 4.6 | Article that mentions only an alias (not the full name) | Still appears under that competitor | Any | ☐ | |
| 4.7 | Donut chart toggle (Monthly / Overall) | Counts update and match the tables | Any | ☐ | |
| 4.8 | Yearly line chart includes Online, Broadcast, Print, **Social** series | All four series render | Any | ☐ | |
| 4.9 | Search + sentiment filter inside the competitor tab | Results filter correctly across all sub-tabs | Any | ☐ | |
| 4.10 | Org with no competitors configured | Friendly empty state shown | Any | ☐ | |

## 5. Dashboard & Analytics

| # | Scenario | Expected Result | Role | Status | Notes |
|---|----------|-----------------|------|--------|-------|
| 5.1 | Open Dashboard | Metrics, charts and recent coverage load without error | Any | ☐ | |
| 5.2 | Dashboard reflects the currently selected organisation | Data is org-specific | Any | ☐ | |
| 5.3 | Open Analytics | Charts/figures render and reconcile with underlying data | Any | ☐ | |
| 5.4 | Date/period filters (where present) | Data updates accordingly | Any | ☐ | |

## 6. Media — Online Articles

| # | Scenario | Expected Result | Role | Status | Notes |
|---|----------|-----------------|------|--------|-------|
| 6.1 | Add an online article manually | Saved and appears in the table | Admin | ☐ | |
| 6.2 | Relevancy is auto-calculated on add | Article shows a non-zero relevancy when its text matches org keywords | Admin | ☐ | |
| 6.3 | Add an article with no keyword matches | Relevancy = 0 | Admin | ☐ | |
| 6.4 | Edit an article | Changes persist | Admin | ☐ | |
| 6.5 | Delete an article | Removed from the table | Admin | ☐ | |
| 6.6 | Bulk import via CSV | Rows imported; relevancy computed per row; error summary for bad rows | Admin | ☐ | |
| 6.7 | Filters: search, sentiment, country, coverage, date range | Table filters correctly; "Clear filters" resets | Any | ☐ | |
| 6.8 | Pagination / "Load More" (when >30 rows) | Loads additional rows | Any | ☐ | |

## 7. Media — Print Articles

| # | Scenario | Expected Result | Role | Status | Notes |
|---|----------|-----------------|------|--------|-------|
| 7.1 | Add a print article | Saved and listed | Admin | ☐ | |
| 7.2 | Edit / Delete a print article | Works as expected | Admin | ☐ | |
| 7.3 | CSV import (columns: articleRef, publicationDate, publication, section, headline, byline, ave, sentiment, url, country) | Rows imported; invalid dates/headlines reported | Admin | ☐ | |
| 7.4 | Upload a **large** CSV | Imports successfully (no `413` / size error) | Admin | ☐ | |

## 8. Media — Social Posts

| # | Scenario | Expected Result | Role | Status | Notes |
|---|----------|-----------------|------|--------|-------|
| 8.1 | Add a social post | Saved and listed | Admin | ☐ | |
| 8.2 | Edit / Delete a social post | Works as expected | Admin | ☐ | |
| 8.3 | CSV import | Rows imported; error summary for bad rows | Admin | ☐ | |
| 8.4 | Filters: search, platform, sentiment, country, date | Filter correctly | Any | ☐ | |
| 8.5 | Table columns render correctly (Relevancy column removed) | No empty/duplicate columns | Any | ☐ | |

## 9. Media — Broadcast

| # | Scenario | Expected Result | Role | Status | Notes |
|---|----------|-----------------|------|--------|-------|
| 9.1 | Add a broadcast mention (TV/Radio/Podcast/Online) | Saved and listed | Admin | ☐ | |
| 9.2 | Edit / Delete a broadcast mention | Works as expected | Admin | ☐ | |
| 9.3 | CSV import | Rows imported; errors reported | Admin | ☐ | |

## 10. Media Sources

| # | Scenario | Expected Result | Role | Status | Notes |
|---|----------|-----------------|------|--------|-------|
| 10.1 | Add a media source | Saved and listed | Admin | ☐ | |
| 10.2 | Edit / Delete a media source | Works as expected | Admin | ☐ | |

## 11. Reports

| # | Scenario | Expected Result | Role | Status | Notes |
|---|----------|-----------------|------|--------|-------|
| 11.1 | Generate the Full report | Renders with correct figures for the org/period | Any | ☐ | |
| 11.2 | Sentiment report + PDF export | On-screen and PDF match; PDF downloads | Any | ☐ | |
| 11.3 | Source report + PDF export | On-screen and PDF match | Any | ☐ | |
| 11.4 | Competitor report | Figures reconcile with the Competitor tab | Any | ☐ | |
| 11.5 | Competitor report **PPTX** export | Valid .pptx downloads and opens in PowerPoint | Any | ☐ | |
| 11.6 | Save a report | Appears in the saved/generated reports list | Any | ☐ | |
| 11.7 | Delete a saved report | Removed from the list | Any | ☐ | |
| 11.8 | Print / load report | Prints or loads cleanly with branding intact | Any | ☐ | |

## 12. Alerts

| # | Scenario | Expected Result | Role | Status | Notes |
|---|----------|-----------------|------|--------|-------|
| 12.1 | Create an alert | Saved and listed | Admin | ☐ | |
| 12.2 | Delete an alert | Removed | Admin | ☐ | |
| 12.3 | Alert email is delivered when triggered | Email received with correct content | — | ☐ | |

## 13. Users Management

| # | Scenario | Expected Result | Role | Status | Notes |
|---|----------|-----------------|------|--------|-------|
| 13.1 | Create a user with a role | User created; welcome/set-password email sent | Platform/Org Admin | ☐ | |
| 13.2 | Edit a user (name/role) | Changes persist | Admin | ☐ | |
| 13.3 | Delete a user | Removed; can no longer log in | Admin | ☐ | |
| 13.4 | Duplicate email | Prevented with a clear message | Admin | ☐ | |
| 13.5 | New user sets password and logs in | Gains access scoped to their org/role | New user | ☐ | |

## 14. Profile, Settings & FAQs

| # | Scenario | Expected Result | Role | Status | Notes |
|---|----------|-----------------|------|--------|-------|
| 14.1 | Update own profile (name/email) | Changes persist; duplicate email blocked | Any | ☐ | |
| 14.2 | Change own password | New password works on next login | Any | ☐ | |
| 14.3 | Open Settings page | Loads and saves without error | Admin | ☐ | |
| 14.4 | Open FAQs page | Content displays correctly | Any | ☐ | |

## 15. Cross-cutting / Non-functional

| # | Scenario | Expected Result | Status | Notes |
|---|----------|-----------------|--------|-------|
| 15.1 | **Data isolation:** a user from Org A cannot see Org B's data via UI or by editing the URL | Access blocked / not found | ☐ | |
| 15.2 | Sidebar collapse and mobile drawer | Works on desktop and mobile widths | ☐ | |
| 15.3 | Dark mode toggle | Persists across pages and reloads | ☐ | |
| 15.4 | Branding (logo, favicon, colours) | Correct throughout | ☐ | |
| 15.5 | Emails render correctly in real clients (Gmail, Outlook, mobile) | Card layout intact; links work | ☐ | |
| 15.6 | Large CSV upload limit | No `413 Payload Too Large` (proxy `client_max_body_size` adequate) | ☐ | |
| 15.7 | Long-running import doesn't time out | Completes without `502/504` | ☐ | |
| 15.8 | "Services → Extractor" link on the landing page | Opens the Article Extractor app | ☐ | |
| 15.9 | Validation & error messages are user-friendly | No raw stack traces shown to users | ☐ | |
| 15.10 | Numbers formatted consistently (thousands separators, AVE, dates) | Consistent across tables/reports | ☐ | |

---

## Sign-off

| Role | Name | Decision (Accept / Reject / Accept with conditions) | Signature | Date |
|------|------|------------------------------------------------------|-----------|------|
| Product Owner | | | | |
| QA / Tester | | | | |
| Client Representative | | | | |

**Open defects blocking acceptance:** ____________________________________________

**Conditions / agreed follow-ups:** ____________________________________________
