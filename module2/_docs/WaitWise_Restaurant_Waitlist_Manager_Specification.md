# WaitWise

## 1. Overview

WaitWise is a simple restaurant waitlist management application built as a class project demonstrating a complete frontend, backend, and relational database application.

The system supports three user experiences:

1. **Administrator**
   - Creates and manages restaurants.
   - Creates the restaurant login associated with each restaurant.

2. **Restaurant Staff**
   - Manages the restaurant's active waitlist.
   - Adds walk-in parties.
   - Notifies, seats, cancels, or marks parties as no-shows.
   - Controls the restaurant's current estimated wait time.
   - Can temporarily stop accepting online waitlist entries.

3. **Eater**
   - Finds a restaurant.
   - Views its current estimated wait.
   - Joins the waitlist.
   - Sees their current position and estimated remaining wait without refreshing.
   - Leaves the waitlist if desired.
   - Sees when the restaurant has notified them that their table is ready.

The project should intentionally remain small. It is not a reservation system, table-management system, POS system, or restaurant-management platform.

---

# 2. Technology Stack

## Frontend

- React
- TypeScript
- Vite
- React Router
- Standard CSS or lightweight CSS modules
- Vitest
- React Testing Library

Do not introduce a large component framework unless necessary.

The UI should be responsive, modern, fresh, and straightforward.

## Backend

- Python
- FastAPI
- SQLAlchemy ORM
- Pydantic
- Pytest
- FastAPI TestClient

## Database

Development:

- SQLite

Production:

- PostgreSQL

Database access must go through SQLAlchemy so that switching databases only requires changing configuration rather than rewriting application logic.

---

# 3. Development Sequence

The project should be implemented in the order required by the course.

## Phase 1 — Scope

Define:

- Personas
- User stories
- Screens
- Data required by each screen
- Business rules
- API contract
- Database entities

Do not implement application logic during this phase.

## Phase 2 — Frontend

Build the frontend first using mock/static data.

All primary workflows should work visually before a backend exists.

Create a small mock API/data layer so frontend components are not tightly coupled to hard-coded arrays.

The mock API should expose approximately the same functions the real backend API will eventually provide.

Example:

```text
getRestaurants()
getRestaurant(id)
joinWaitlist(...)
getWaitlistEntry(...)
cancelWaitlistEntry(...)
getRestaurantWaitlist(...)
addWalkIn(...)
updateWaitlistEntry(...)
```

When the backend is implemented, the mock implementation will be replaced by HTTP requests.

## Phase 3 — Backend

Implement FastAPI endpoints that match the frontend's expected contract.

Backend development may initially use temporary or in-memory data where useful.

## Phase 4 — Database

Introduce SQLAlchemy models and persistence.

Use SQLite locally.

The application must allow the database URL to come from configuration so PostgreSQL can be used later.

---

# 4. Personas

## Admin

A system administrator who manages restaurants available in WaitWise.

There can initially be one predefined admin user.

## Restaurant Staff

Represents staff working at one restaurant.

Each restaurant has one restaurant login.

Multiple individual employee accounts are out of scope.

## Eater

A member of the public who wants to join a restaurant's waitlist.

Eaters do not create accounts.

---

# 5. Authentication

Authentication should deliberately be simplified for this assignment.

Users:

- Admin user
- One user per restaurant

A User record should contain:

- id
- username
- password
- role
- restaurant_id where applicable

Roles:

```text
ADMIN
RESTAURANT
```

For this version:

- Username identifies the user.
- The password field exists.
- Password contents are ignored.
- Any password value may be submitted.
- No hashing is required.
- No OAuth, JWT, password recovery, MFA, or registration system is required.

The frontend may store the current user/session in local storage.

This authentication model is intentionally simplified for the class project.

---

# 6. Public Home Page

Route:

```text
/
```

Purpose:

Allow an eater to find a restaurant.

Display:

- WaitWise logo/name
- Short heading such as "Skip the line. Join the waitlist."
- List of active restaurants

Each restaurant card should display:

- Restaurant name
- Address
- Current estimated wait
- Whether online joining is available
- "View Waitlist" button

Inactive restaurants must not appear.

If online joining is paused, the restaurant may still appear but should display:

```text
Online waitlist currently unavailable
```

---

# 7. Public Restaurant Page

Route:

```text
/restaurants/:restaurantId
```

Display:

- Restaurant name
- Address
- Phone
- Description
- Current estimated wait
- Number of parties currently waiting
- Whether online joining is available

Primary action:

```text
Join Waitlist
```

The button should be disabled or hidden if:

- Restaurant is inactive
- Online waitlist is paused

---

# 8. Join Waitlist

Required fields:

- Name
- Mobile phone
- Party size

Optional:

- Notes

Validation:

### Name

- Required
- Trim whitespace

### Mobile

- Required
- Basic format validation only

### Party Size

- Required
- Integer
- Minimum: 1
- Maximum: 20

### Notes

- Optional
- Maximum: 250 characters

When an eater joins:

1. Record the current date/time as `joined_at`.
2. Capture the restaurant's current estimated wait as `quoted_wait_minutes`.
3. Calculate:

```text
estimated_ready_at =
joined_at + quoted_wait_minutes
```

4. Create the waitlist entry with status:

```text
WAITING
```

5. Generate a public access token.
6. Redirect the eater to their personal waitlist status page.

---

# 9. Wait-Time Behavior

The restaurant maintains:

```text
current_wait_minutes
```

Example:

```text
30 minutes
```

This is the estimate displayed to someone considering joining.

When an eater joins, the value is copied into:

```text
quoted_wait_minutes
```

The eater's original estimate does not change merely because the restaurant later changes `current_wait_minutes`.

Example:

```text
Current wait at 6:00 PM = 30 minutes

Marcos joins at 6:00 PM.

quoted_wait_minutes = 30
estimated_ready_at = 6:30 PM
```

At 6:10 PM:

```text
Estimated remaining wait = 20 minutes
```

At 6:25 PM:

```text
Estimated remaining wait = 5 minutes
```

At or after 6:30 PM:

```text
Estimated remaining wait = Ready soon
```

Do not display negative wait times.

The displayed time is an estimate and does not guarantee seating order.

---

# 10. Eater Status Page

Route:

```text
/wait/:token
```

No login required.

Display:

- Restaurant name
- Eater name
- Party size
- Time joined
- Current position
- Original quoted wait
- Estimated remaining wait
- Current status

Example:

```text
Party of 2

You're #3 in line

Estimated remaining wait
12 minutes
```

Primary action while waiting:

```text
Leave Waitlist
```

---

# 11. Real-Time Eater Updates

The eater should not need to manually refresh the page.

Use polling rather than WebSockets.

Suggested frequency:

```text
Every 10 seconds
```

The frontend requests the eater's current waitlist entry and updates:

- Queue position
- Status
- Estimated remaining wait
- Notification state

The visible remaining-time countdown may update locally between API requests.

---

# 12. Waitlist Statuses

Supported statuses:

```text
WAITING
NOTIFIED
SEATED
CANCELED
NO_SHOW
```

Normal successful flow:

```text
WAITING
   ↓
NOTIFIED
   ↓
SEATED
```

Alternative exits:

```text
WAITING → CANCELED
WAITING → NO_SHOW
NOTIFIED → CANCELED
NOTIFIED → NO_SHOW
```

---

# 13. Notification

Notifications are simulated.

There is no SMS or email integration.

Restaurant staff click:

```text
Notify
```

The system changes:

```text
status = NOTIFIED
notified_at = current timestamp
```

The eater status page should prominently display:

```text
Your table is ready!
Please check in with the host.
```

The restaurant dashboard should show how long ago the party was notified.

---

# 14. Automatic No-Show Rule

Each restaurant should have:

```text
no_show_minutes
```

Default:

```text
10 minutes
```

When a party has status `NOTIFIED` and:

```text
current time >= notified_at + no_show_minutes
```

the system changes the entry to:

```text
NO_SHOW
```

No scheduled background worker is required.

The backend may evaluate overdue notified parties whenever:

- The restaurant waitlist is requested
- An eater's status is requested

This keeps the implementation simple.

---

# 15. Queue Ordering

Default queue order is FIFO using:

```text
joined_at
```

The oldest waiting party appears first.

Restaurants are not required to seat strictly in queue order.

Example:

```text
#1 — Party of 6
#2 — Party of 5
#3 — Party of 2
```

If a two-person table becomes available, restaurant staff may seat party #3.

Therefore:

- Position represents queue order.
- Position does not guarantee seating order.
- Staff may notify or seat any active party.
- Do not implement automatic table matching.

---

# 16. Restaurant Login

Route:

```text
/login
```

Fields:

- Username
- Password

Password is ignored for this version.

After login:

```text
ADMIN → /admin
RESTAURANT → /restaurant/dashboard
```

---

# 17. Restaurant Dashboard

Route:

```text
/restaurant/dashboard
```

This is the primary restaurant operational interface.

## Header

Display:

- Restaurant name
- Current estimated wait
- Number of waiting parties
- Online waitlist status

Controls:

```text
Change Wait
Add Walk-In
Accepting Online Waitlist toggle
```

---

# 18. Active Waitlist

Show active parties with statuses:

```text
WAITING
NOTIFIED
```

Columns:

- Position
- Guest name
- Party size
- Joined time
- Time waiting
- Estimated ready time
- Status
- Actions

Example:

| Pos | Guest | Party | Waiting | Status | Actions |
|---|---|---:|---|---|---|
| 1 | Sarah | 6 | 32 min | Waiting | Notify / Seat / Cancel |
| 2 | James | 4 | 24 min | Waiting | Notify / Seat / Cancel |
| 3 | Marcos | 2 | 18 min | Waiting | Notify / Seat / Cancel |

### WAITING actions

- Notify
- Seat
- Cancel
- No Show

### NOTIFIED actions

- Seat
- Cancel
- No Show

---

# 19. Add Walk-In

Restaurant staff can add someone who did not use the public website.

Fields:

- Name
- Mobile
- Party size
- Notes

The entry behaves like an eater-created entry.

Set:

```text
source = STAFF
```

Online-created entries use:

```text
source = ONLINE
```

Walk-ins receive the same wait-time calculation.

---

# 20. Waitlist History

Below the active queue, include:

```text
Today's History
```

Display entries from the current day with statuses:

```text
SEATED
CANCELED
NO_SHOW
```

Fields:

- Guest
- Party size
- Joined
- Final status
- Final status time

No reporting or historical analytics are required.

---

# 21. Admin Dashboard

Route:

```text
/admin
```

Display all restaurants.

For each:

- Name
- Address
- Active/inactive status
- Current wait
- Restaurant username
- Actions

Actions:

```text
Edit
Activate
Deactivate
```

Primary action:

```text
Add Restaurant
```

---

# 22. Add/Edit Restaurant

Fields:

- Name
- Address
- Phone
- Description
- Default/current estimated wait
- No-show timeout
- Active status
- Restaurant username
- Restaurant password

Defaults:

```text
Current wait: 30 minutes
No-show timeout: 10 minutes
Active: true
Online waitlist enabled: true
```

Admin can:

- Create restaurant
- Edit restaurant
- Activate/deactivate restaurant
- Change restaurant login values

---

# 23. Frontend Routes

```text
/
Public restaurant list

/restaurants/:restaurantId
Public restaurant details / join

/wait/:token
Eater waitlist status

/login
Admin/restaurant login

/admin
Admin dashboard

/admin/restaurants/new
Create restaurant

/admin/restaurants/:id/edit
Edit restaurant

/restaurant/dashboard
Restaurant dashboard
```

---

# 24. API Design

Backend development server:

```text
http://localhost:9127
```

API base URL:

```text
http://localhost:9127/api
```

Frontend configuration:

```text
VITE_API_BASE_URL=http://localhost:9127/api
```

## Authentication

```text
POST /api/auth/login
```

## Public Restaurants

```text
GET /api/restaurants
GET /api/restaurants/{restaurant_id}
```

## Eater Waitlist

```text
POST /api/restaurants/{restaurant_id}/waitlist
GET /api/waitlist/{token}
DELETE /api/waitlist/{token}
```

## Restaurant Operations

```text
GET /api/restaurant/waitlist
POST /api/restaurant/waitlist
PATCH /api/restaurant/waitlist/{entry_id}
PATCH /api/restaurant/settings
```

Possible waitlist action body:

```json
{
  "status": "NOTIFIED"
}
```

## Admin

```text
GET /api/admin/restaurants
POST /api/admin/restaurants
GET /api/admin/restaurants/{restaurant_id}
PUT /api/admin/restaurants/{restaurant_id}
PATCH /api/admin/restaurants/{restaurant_id}/status
```

Keep endpoints straightforward and REST-oriented.

---

# 25. Database Entities

## User

```text
id
username
password
role
restaurant_id nullable
created_at
```

## Restaurant

```text
id
name
address
phone
description
current_wait_minutes
no_show_minutes
is_active
online_waitlist_enabled
created_at
updated_at
```

## WaitlistEntry

```text
id
restaurant_id
public_token
guest_name
mobile_phone
party_size
notes nullable
source
status
quoted_wait_minutes
joined_at
estimated_ready_at
notified_at nullable
seated_at nullable
canceled_at nullable
no_show_at nullable
created_at
updated_at
```

Relationships:

```text
Restaurant 1 → many WaitlistEntries

Restaurant 1 → one Restaurant User
```

---

# 26. Enumerations

## UserRole

```text
ADMIN
RESTAURANT
```

## WaitlistStatus

```text
WAITING
NOTIFIED
SEATED
CANCELED
NO_SHOW
```

## WaitlistSource

```text
ONLINE
STAFF
```

---

# 27. Queue Position Calculation

Do not persist queue position.

Calculate it from active entries.

For an entry:

1. Select active `WAITING` and `NOTIFIED` entries for the restaurant.
2. Sort by `joined_at`.
3. Determine the entry's position within the ordered list.

This prevents stored positions from becoming stale when parties leave or are seated.

---

# 28. Design Direction

The application should feel like a modern operational SaaS application rather than a decorative restaurant website.

Characteristics:

- Generous whitespace
- Rounded cards
- Subtle borders and shadows
- Neutral page background
- One primary accent color
- Clear typography
- Large touch targets
- Strong status indicators
- Minimal visual clutter

The public eater experience should be optimized for mobile.

Admin and restaurant dashboards should work especially well on desktop/tablet while remaining responsive.

Avoid:

- Large animations
- Excessive gradients
- Glassmorphism
- Complex menus
- Decorative imagery
- Heavy dashboard charts

---

# 29. UX Status Treatment

Use visually distinct badges for:

```text
Waiting
Notified
Seated
Canceled
No Show
```

The eater's most important information should appear above the fold:

```text
Your position
Estimated remaining wait
Current status
```

Important restaurant actions should remain immediately visible.

---

# 30. Empty States

Restaurant queue:

```text
No one is waiting right now.
```

Restaurant history:

```text
No completed parties yet today.
```

Public restaurant list:

```text
No restaurants are currently available.
```

---

# 31. Error Handling

Frontend should display friendly errors for:

- Backend unavailable
- Restaurant not found
- Invalid waitlist token
- Online waitlist closed
- Invalid input
- Waitlist entry already canceled or completed

Do not expose raw backend exceptions.

---

# 32. Frontend Testing

Use:

- Vitest
- React Testing Library

At minimum test:

1. Restaurant list renders.
2. Restaurant details render.
3. Join form validates required fields.
4. Successful join displays status page.
5. Estimated remaining wait calculation.
6. Waitlist polling updates the UI.
7. Restaurant dashboard renders queue.
8. Restaurant action changes party status.
9. Admin restaurant form validation.

---

# 33. Backend Testing

Use:

- pytest
- FastAPI TestClient

At minimum test:

1. Get active restaurants.
2. Create waitlist entry.
3. Reject invalid party size.
4. Calculate quoted wait correctly.
5. Calculate queue position correctly.
6. Cancel eater waitlist entry.
7. Restaurant adds walk-in.
8. Notify party.
9. Seat party out of order.
10. Automatically transition expired notification to no-show.
11. Admin creates restaurant.
12. Admin edits restaurant.
13. Inactive restaurants do not appear publicly.

---

# 34. Seed Data

Development setup should create example data.

## Admin

```text
username: admin
password: anything
```

## Restaurant 1

```text
Bluebird Cafe
123 Main Street
Current wait: 30 minutes
Restaurant username: bluebird
```

## Restaurant 2

```text
Oak & Ember
456 Market Street
Current wait: 20 minutes
Restaurant username: oakember
```

Include several sample waitlist entries for development/demo purposes.

---

# 35. Repository Structure

Suggested structure:

```text
waitwise/
│
├── frontend/
│   ├── src/
│   ├── package.json
│   └── ...
│
├── backend/
│   ├── app/
│   ├── tests/
│   ├── requirements.txt
│   └── ...
│
├── package.json
├── README.md
└── .gitignore
```

---

# 36. Local Development Ports

Use less common ports to reduce conflicts with other local development applications.

## Frontend

Port:

```text
3417
```

URL:

```text
http://localhost:3417
```

Configure the frontend's `package.json` so the standard command remains:

```text
npm run dev
```

Example script:

```json
{
  "scripts": {
    "dev": "vite --port 3417"
  }
}
```

## Backend

Port:

```text
9127
```

URL:

```text
http://localhost:9127
```

API URL:

```text
http://localhost:9127/api
```

FastAPI documentation:

```text
http://localhost:9127/docs
```

Backend command:

```text
uvicorn app.main:app --reload --port 9127
```

---

# 37. Standard Commands

## Start Frontend

From:

```text
frontend/
```

Run:

```text
npm run dev
```

Expected URL:

```text
http://localhost:3417
```

## Start Backend

From:

```text
backend/
```

Run:

```text
uvicorn app.main:app --reload --port 9127
```

Expected URL:

```text
http://localhost:9127
```

## Run All Tests

Provide a root-level script so the homework has one simple test command:

```text
npm run test:all
```

It should run both:

```text
Frontend Vitest tests
Backend pytest tests
```

---

# 38. Homework Information

The README should contain a clearly labeled section:

```text
Homework Information
```

## 2. What is the name you chose?

```text
WaitWise
```

## 3. What is the SHA1 hash for this commit?

Do not hard-code this during development.

Insert the final commit SHA1 when submitting.

Command:

```text
git rev-parse HEAD
```

## 4. Which command do you use to start the frontend?

```text
npm run dev
```

Run from:

```text
frontend/
```

## 5. Which command do you use to start the backend?

```text
uvicorn app.main:app --reload --port 9127
```

Run from:

```text
backend/
```

## 6. Which URL does the frontend use to talk to the backend?

```text
http://localhost:9127/api
```

## 7. Which command do you use for running tests?

From the repository root:

```text
npm run test:all
```

Also include the course-provided:

- Homework URL
- FAQ URL

once those URLs are known.

---

# 39. Out of Scope

Do not implement these in this module:

- Reservations
- Table/floor-plan management
- Automatic table assignment
- Sophisticated wait-time prediction
- SMS
- Email
- Push notifications
- OAuth
- JWT authentication
- Password hashing
- Password reset
- User registration
- Multiple staff accounts per restaurant
- POS integrations
- Payment processing
- Restaurant menus
- Restaurant reviews
- Customer profiles
- Historical analytics
- Reporting dashboards
- Docker
- Cloud deployment
- WebSockets
- Redis
- Background workers
- Celery

These may be future enhancements but should not be introduced into this assignment.

---

# 40. Primary Acceptance Scenario

The finished application should support this demonstration:

1. Admin logs in.
2. Admin creates "Bluebird Cafe."
3. Bluebird Cafe appears on the public homepage.
4. Admin or restaurant sets the current wait to 30 minutes.
5. Eater opens Bluebird Cafe.
6. Eater sees "30 minute wait."
7. Eater joins as a party of 4.
8. Eater immediately sees:
   - position
   - join time
   - estimated remaining wait
9. Restaurant logs in.
10. The new party appears on its dashboard.
11. Restaurant manually adds a party of 2.
12. Another larger party is already ahead of the party of 2.
13. Restaurant may notify or seat the party of 2 first.
14. The eater's status page updates automatically without refresh.
15. When the restaurant clicks Notify, the eater sees:
   - "Your table is ready!"
16. Restaurant clicks Seat.
17. Party disappears from the active queue and appears in Today's History.
18. Another eater chooses Leave Waitlist.
19. That eater becomes Canceled and disappears from the active queue.
20. A notified party that exceeds the configured no-show period becomes No Show.

If this scenario works cleanly, the core assignment is complete.

---

# 41. Implementation Guidance

When implementing this specification:

- Favor simple, readable code over abstractions.
- Do not introduce features outside this specification.
- Keep frontend, backend, and persistence concerns separated.
- Implement the frontend against mocks first.
- Preserve the frontend API contract when implementing FastAPI.
- Introduce SQLAlchemy during the database phase.
- Keep business rules testable outside UI components.
- Keep database-specific behavior isolated in configuration.
- Use environment variables for API and database URLs.
- Maintain a README with setup, start, and test instructions.
- Keep the designated development ports consistent throughout configuration, documentation, tests, and examples.
- After each major phase, ensure all existing tests still pass.

The goal is a polished class project demonstrating a well-scoped full-stack application, not a production restaurant platform.