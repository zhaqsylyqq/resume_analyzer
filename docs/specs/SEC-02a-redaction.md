# SEC-02a — no personal data in AI requests

**Goal.** Resumes are personal data. Kazakhstan law is followed, and the AI provider
(DeepSeek or Gemini, abroad) receives resume text without personal data. Before this
change the whole resume went to the provider for extraction, and the model itself decided
gender and age for the diversity report.

## How it works

`app/services/pii.py` runs on the server before the extraction call
(`GeminiService.extract_profile`):

1. **Contacts and identifiers:** e-mail, phone numbers (10–13 digits; year lists are not
   phones), IIN/ЖСН (12 digits), passport and ID numbers, links (http, www, LinkedIn,
   GitHub, Telegram, …) and `@handles`.
2. **The candidate's name:** from a label (`ФИО:`, `Аты-жөні:`, `Name:`) or from the first
   lines of the resume (2–3 capitalised words; Russian/Kazakh surname or patronymic endings
   such as -ов/-ова, -ұлы/-қызы, -вич/-вна; or simply the first line). Every later occurrence
   is removed too, in any case ending (`Сапаровой`) and in capitals (`САПАРОВА`); short
   surnames (Ли, Ким) only as a whole word.
3. **Referees with initials:** `Петров А.С.` → `[ИМЯ]`; institutions named after a person
   (`КазНУ им. аль-Фараби`, `Л.Н. Гумилев атындағы ЕҰУ`) stay.
4. **Demographics:** birth date and year, age, gender (label, «мужчина/женщина», first-person
   past tense «я работала»), family status and children, nationality, street address, photo.

Each item becomes a marker (`[КАНДИДАТ]`, `[EMAIL]`, `[ТЕЛЕФОН]`, `[ДОКУМЕНТ]`, `[ССЫЛКА]`,
`[ДАТА РОЖДЕНИЯ]`, `[ВОЗРАСТ]`, `[ПОЛ]`, `[СЕМЕЙНОЕ ПОЛОЖЕНИЕ]`, `[НАЦИОНАЛЬНОСТЬ]`,
`[АДРЕС]`, `[ФОТО]`, `[ИМЯ]`). The prompt tells the model to ignore markers and never guess
what they replaced. The model now answers with `ResumeFacts` — the professional part of the
profile only; name, contacts and demographics are filled in from the local extraction.

What is not changed: the full resume text is still stored in the database (encryption is
SEC-02b); scoring keeps its own second layer (`anonymize.scoring_view`); the AI vacancy
check sends vacancy texts, which contain no candidate data.

## Residual risk (measured)

On the 20 sample resumes (`samples/resumes`, RU and KK, txt/docx/pdf) the name is found in
20 of 20 and no name token is left in the redacted text; all stated gender, age, birth
year and family status values are found. Known gaps:

- A name that is neither labelled nor in the first four lines, nor repeated after the
  header, and written without a surname ending is not found (for example a resume that
  starts with a long objective and gives the name only at the end).
- Kazakh names written in Latin without typical endings are found only from the header.
- Free-text mentions of relatives' or colleagues' full names without initials stay.
- Gender from grammar is read only from «я + past tense» in Russian; Kazakh has no
  grammatical gender.

Whether this level of de-identification is sufficient under the Law of the Republic of
Kazakhstan «On personal data and their protection» must be confirmed by a lawyer.

## Changed files

`app/services/pii.py` (new), `app/services/gemini.py` (`extract_profile`),
`app/services/schemas.py` (`ResumeFacts`), `app/prompts/extract_system.txt`; tests
`tests/test_pii.py` (39) and updated fakes (`tests/helpers.py`: test resumes now carry the
name and demographics in their text). 429 tests in total.
