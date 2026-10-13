# מתחילים כאן (Windows)

## מה יש בחבילה

| קובץ | למה | נכנס לגיט? |
|---|---|---|
| `CLAUDE.md` | הארכיטקטורה, החוקים והמפרט. Claude Code קורא אותו אוטומטית | ✅ |
| `DECISIONS.md` | כל ההחלטות באנגלית, עם TODO לחלקים 2-3 | ✅ |
| `AI_USAGE.md` | שלד, ממלאים בסוף מתוך `notes/ai-log.md` | ✅ |
| `.claude/settings.json` | הרשאות (פחות אישורים) + hook שמריץ ruff אחרי כל עריכה | ✅ |
| `.claude/commands/` | `/ai-log`, `/interview-check`, `/checkpoint` | ✅ |
| `.claude/agents/reviewer.md` | סוקר שבודק מול המשימה וההחלטות | ✅ |
| `.cursor/rules/project.mdc` | כדי ש-Cursor יעבוד לפי אותם חוקים | ✅ |
| `.pre-commit-config.yaml` | ruff + gitleaks (חוסם סודות בטעות) | ✅ |
| `.gitattributes` | LF בכל הקבצים (חשוב מאוד ב-Windows, ראי למטה) | ✅ |
| `.env.example` | כל משתני הסביבה, בלי סודות אמיתיים | ✅ |
| `notes/` | המשימה, ההסברים בעברית, יומן AI | ❌ (ב-.gitignore) |

## הכנה לפני שמפעילים את השעון (רק התקנות, בלי קוד)

1. **Git for Windows** - חובה ל-Claude Code ב-Windows (הוא מריץ פקודות דרך Git Bash).
2. **Python 3.12** מ-python.org, עם "Add to PATH".
   - ⚠️ אם `python` פותח את ה-Microsoft Store: Settings → Apps → Advanced app settings → **App execution aliases**, וכבי את python.exe ו-python3.exe. אחרת ה-hook לא ירוץ.
3. **Docker Desktop** עם WSL2.
4. **Multipass** - ל-VM של Ubuntu 24.04.
   - Windows Pro/Enterprise: עובד עם Hyper-V.
   - Windows Home: צריך VirtualBox מותקן, ובהתקנה של Multipass לבחור VirtualBox.
   - בדיקה: `multipass launch 24.04 --name test` ואחר כך `multipass delete test --purge`.
5. **GitHub CLI**: `winget install GitHub.cli`, ואז `gh auth login`.
6. **כלים ל-Python**: `pip install ruff pre-commit pytest`
7. **Claude Code**: `claude update`

## הקמת הריפו

```powershell
mkdir rf-alert-manager; cd rf-alert-manager
git init
git config core.autocrlf false        # בלי CRLF: קבצים שרצים על לינוקס נשברים בגללו
# לחלץ לכאן את תוכן ה-zip
pre-commit install
git add . ; git commit -m "chore: project setup, decisions and AI tooling"
gh repo create rf-alert-manager --private --source . --push
```

**למה CRLF חשוב:** Windows שומר סוף שורה כ-`\r\n`. סקריפט bash או קובץ cloud-init עם `\r` נכשלים ב-VM בשגיאות מוזרות (`/bin/bash^M: bad interpreter`). `.gitattributes` ו-`autocrlf false` מונעים את זה.

**ריפו פרטי:** עדיף בגלל ה-self-hosted runner. לא לשכוח לתת להם גישה בסוף.

## לפני שמתחילים: לסגור את ההצעות הפתוחות

ב-`notes/DECISIONS-he.md` יש טבלה "הצעות שלי שעוד לא אישרת" (FastAPI, Postgres, Redis, batch חלקי, Multipass). תעברי עליה ותעדכני את `DECISIONS.md` אם שינית משהו.

## לוח זמנים (240 דקות)

| זמן | מה |
|---|---|
| 0:00-0:15 | קריאה חוזרת של המשימה, עדכון החלטות, commit ראשון |
| 0:15-1:50 | **Part 1**: שני סשנים במקביל (ingest / alert). סשן שלישי מרים את ה-VM במקביל |
| 1:50-2:35 | **Part 2**: Dockerfiles, compose, nginx, deploy ל-VM |
| 2:35-3:20 | **Part 3**: CI, build ל-GHCR, deploy דרך runner, smoke test, rollback |
| 3:20-3:50 | מסמכים + צילומי מסך |
| 3:50-4:00 | באפר |

חותכים אם מאחרים: multi_sensor, בונוסים. בונוס זול אם מקדימים: load test עם k6/locust.

## סשנים מקבילים

```powershell
git worktree add ../rf-ingest -b ingest-service
git worktree add ../rf-alert  -b alert-service
git worktree add ../rf-infra  -b infra
```
כל worktree נפתח בחלון Cursor נפרד (File → Open Folder), ובכל אחד מריצים `claude`.
⚠️ `notes/` לא בגיט, אז הוא לא יופיע ב-worktrees. להעתיק אותו לכל worktree, או לתת לכל סשן את הנתיב המלא.

## פרומפטים לפתיחה

**סשן ingest:**
```
Read CLAUDE.md and DECISIONS.md. Build ingest-service only (do not touch alert-service).
Scope: observation validation (single + batch with partial acceptance), storage with the
duplicate constraint, sensors last-seen, rules CRUD, YAML seed when the table is empty,
power_threshold evaluation via an evaluator registry, publishing rule_match events to
Redis Streams, /health, /metrics. Follow the layering rules.
Start with unit tests for validation and rule matching. Show me the plan before writing code.
```

**סשן alert:**
```
Read CLAUDE.md and DECISIONS.md. Build alert-service only (do not touch ingest-service).
Scope: consume rule_match events, pending runs (min_duration + max_gap), dedup by signal
key, ack/resolve, watermark with allowed lateness, periodic auto-resolve sweep with the idle
fallback, idempotent consumer, alerts API, /health, /metrics.
FIRST write domain tests with a fake clock and in-memory repo: persistent emitter opens,
short burst does not, two bursts with a long gap do not, one missing observation does not
break a run, dedup updates one alert, auto-resolve after resolve_after_s, late event beyond
the watermark is ignored, duplicate observation_id is skipped. Then implement.
Show me the plan before writing code.
```

**סשן infra:**
```
Read CLAUDE.md. Prepare the VM only: Ubuntu 24.04 with Multipass + cloud-init in infra/,
installing Docker, enabling it on boot, ufw allowing only SSH and 80, Docker log rotation.
Add a script to launch the VM and print its IP. Do not write application code.
Show me the plan first.
```

## הרגלים בזמן העבודה

- `Shift+Tab` פעמיים = Plan mode. לקרוא את התוכנית לפני שמאשרים.
- כל פעם ש-AI טועה: `/ai-log` מיד, כל עוד זה טרי.
- אחרי כל שלב עובד: `/checkpoint`.
- בסוף כל חלק: "use the reviewer agent", ואז `/interview-check`.
- צילומי מסך תוך כדי, לא בסוף: ה-VM, `docker compose ps`, הסימולטור פותח וסוגר התראות, pipeline ירוק, rollback.
