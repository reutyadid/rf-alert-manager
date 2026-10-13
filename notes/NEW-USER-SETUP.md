# הקמת סביבה ב-user חדש של Windows

**למה:** ה-user הקודם (`רעות`) היה עם שם בעברית. Windows עם codepage 1255 שבר כלים שמשתמשים בנתיב
של תיקיית המשתמש (Multipass לא הצליח ליצור את קובץ התעודה `multipass_cert_key.pem`). ה-user החדש
עם שם באנגלית בלי רווחים (למשל `reut`) פותר את זה.

## הוראות ל-Claude Code (לקרוא לפני שמתחילים)

- לשאול אותי לפני כל פעולה שמשנה משהו: התקנה, יצירת/מחיקת קבצים, commit, push. בדיקות שרק קוראות - מותר.
- צעד אחד בכל פעם, ולאמת שהוא עבד לפני שממשיכים.
- התחברויות אינטראקטיביות (`gh auth login`, התחברות ל-Claude) - לתת לי להריץ, או להריץ ברקע כדי שלא ייחתכו אחרי 2 דקות.
- הרשת היא לפעמים hotspot מהטלפון, אז timeout רגעי ל-GitHub לא אומר שמשהו שבור - לנסות שוב.
- אחרי שהכל עובד: להמשיך לפי `notes/START-HERE.md` (פרק "לפני שמתחילים: לסגור את ההצעות הפתוחות").

---

## שלב 0: נעשה ב-user הישן, לפני המעבר (ידני)

- [ ] ליצור user חדש: Settings → Accounts → Other users → Add account. שם באנגלית בלי רווחים, הרשאת **Administrator**.
- [ ] להוסיף את ה-user החדש לקבוצות: Computer Management → Local Users and Groups → Groups:
  - `docker-users` (בלי זה Docker Desktop לא יעבוד ל-user החדש)
  - `Hyper-V Administrators` (אם קיימת)

## שלב 1: מה כבר מותקן לכל המחשב (רק לבדוק)

| כלי | בדיקה | צפוי |
|---|---|---|
| Git for Windows | `git --version` | 2.42+ |
| GitHub CLI | `gh --version` | 2.102+ (נתיב: `C:\Program Files\GitHub CLI\gh.exe`) |
| Docker Desktop | `docker info` | עונה, בלי שגיאת הרשאות (צריך להפעיל את Docker Desktop פעם ראשונה ב-user החדש) |
| Multipass | `multipass version` | מחזיר גרסה של client ו-daemon, **בלי** שגיאת `multipass_cert_key.pem` |

אם `git` / `gh` לא נמצאים ב-PATH - לפתוח טרמינל חדש. אם עדיין לא, להשתמש בנתיב המלא.

## שלב 2: מה צריך להתקין מחדש (שייך ל-user)

- [ ] **Python 3.12** מ-python.org: לסמן "Add python.exe to PATH" (מומלץ גם "Install for all users").
  - לכבות את ה-aliases של Microsoft Store: Settings → Apps → Advanced app settings → App execution aliases → לכבות `python.exe` ו-`python3.exe`.
  - בדיקה: `python --version` → 3.12.x
- [ ] **כלי Python:** `python -m pip install ruff pre-commit pytest`
  - בדיקה: `ruff --version`, `pre-commit --version`, `pytest --version`
- [ ] **Claude Code:** להתקין ולהתחבר. הגדרות שהיו לי: `theme: dark`, `model: opus`.
- [ ] **Cursor:** להתקין ולהתחבר.
- [ ] **Git - הגדרות גלובליות:**
  ```
  git config --global user.name "Reut Prober"
  git config --global user.email reutprober100@gmail.com
  ```
- [ ] **GitHub CLI - התחברות:** `gh auth login --hostname github.com --web --git-protocol https`
  - בדיקה: `gh auth status` → `Logged in to github.com account princesaR`
- [ ] **Docker Desktop:** לפתוח פעם ראשונה ולעבור את ההגדרה הראשונית (WSL2 backend).
- [ ] **מפתח SSH (אופציונלי):** הפרויקט עובד ב-HTTPS, אז לא חובה. אם צריך - ליצור חדש ולהוסיף ל-GitHub.
- [ ] **לא נחוץ לפרויקט** (היה ב-user הישן): `awscli`, `loadtest-framework`, WSL Ubuntu, הגדרות `.aws`.

## שלב 3: בדיקת Multipass (הסיבה למעבר)

```
multipass version
multipass launch 24.04 --name test
multipass list
multipass delete test --purge
```
אם `launch` עובד - הבעיה נפתרה. אם `multipass version` עדיין נכשל על התעודה - לעצור ולבדוק
(אולי השירות צריך restart: `Restart-Service Multipass` מ-PowerShell כמנהל).

## שלב 4: שכפול הפרויקט

הנתיב חייב להיות בלי עברית.
```
mkdir C:\Users\<new-user>\dev
cd C:\Users\<new-user>\dev
gh repo clone princesaR/rf-alert-manager
cd rf-alert-manager
git config core.autocrlf false
pre-commit install
```
- בדיקה: `git log --oneline` מראה את ה-commits, ו-`notes/` קיימת.

## שלב 5: להחזיר את notes/ להיות מחוץ לגיט

`notes/` נדחפה לגיט זמנית רק בשביל המעבר. היא עדיין ב-`.gitignore`.
```
git rm -r --cached notes
git commit -m "chore: stop tracking notes/"
git push
```
- בדיקה: `git status` נקי, ו-`notes/` עדיין קיימת על הדיסק.

## שלב 6: ממשיכים

לפי `notes/START-HERE.md`:
1. לסגור את ההצעות הפתוחות ב-`notes/DECISIONS-he.md` ולעדכן את `DECISIONS.md`.
2. להתחיל את השעון (240 דקות) - Part 1 עם worktrees.

## מצב נוכחי (נכון ל-2026-10-13)

- ריפו פרטי: https://github.com/princesaR/rf-alert-manager
- commits: `chore: project setup, decisions and AI tooling`, `chore: temporarily track notes/ ...`
- כל בדיקות pre-commit (ruff, gitleaks) עוברות.
- עוד לא נכתב קוד אפליקציה.
