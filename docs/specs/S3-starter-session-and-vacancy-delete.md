# S3 — Example session for new users, deleting a vacancy

## Goal
A newcomer should see a working example right after signing up, and any vacancy (the
example one included) must be removable.

## Example session
- Every new user (self sign-up, created by an admin, `create-admin`) gets a session
  "Пример: Python-разработчик" with one vacancy "Python-разработчик (Middle/Senior)"
  (text in `app/data/starter_vacancy_ru.txt`) and no resumes.
- It is an ordinary session owned by that user: private, editable, deletable.
- No AI call: the bias report is the local dictionary check.
- Not audited as `session_create`, so statistics count only sessions people create.
- `STARTER_SESSION=0` turns it off.

## Deleting a vacancy
- Session page: "Удалить вакансию" under each vacancy, with a confirmation.
- Deletes the vacancy and its scores (cascade); the session and its resumes stay. Deleting
  the last vacancy leaves an empty session; adding a vacancy later scores the existing
  resumes again (F6).
- Scoped like every session route (404 for other users); audited as `vacancy_delete`.
- Scores returned by the AI for a vacancy deleted mid-scoring are dropped (no FK error).
