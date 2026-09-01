# LocalScribe

[English](README.md) · **עברית** · [Español](README.es.md) · [简体中文](README.zh-CN.md) · [Français](README.fr.md)

<div dir="rtl">

מקליט פגישה ב‑Mac שלך, מתמלל אותה ומסכם אותה — הכול על המחשב עצמו.
השמע לעולם לא יוצא מהמכונה, ועם מנוע הסיכום שמוגדר כברירת מחדל גם הטקסט לא.

</div>

```
mic ─────────┐
             ├─► 16 kHz stereo WAV ─► Whisper ─► transcript ─► local LLM ─► summary.md
loopback ────┘   (ch0 = you,          (offline)               (Ollama)
                  ch1 = them)
```

<div dir="rtl">

## התחלה מהירה

</div>

```bash
git clone git@github.com:doron2402/LocalScribe.git
cd LocalScribe
./scripts/setup.sh
```

<div dir="rtl">

ואז להקליט:

</div>

```bash
localscribe record --label "Standup"
```

<div dir="rtl">

דברו, לחצו `Ctrl-C` כשהפגישה נגמרת, וחכו כמה שניות. מתקבלים שלושה קבצים:

</div>

```
~/LocalScribe/audio/standup_2026-09-01_1000.wav          ההקלטה
~/LocalScribe/transcripts/standup_2026-09-01_1000.md     מי אמר מה
~/LocalScribe/summaries/standup_2026-09-01_1000.md       תקציר, החלטות, משימות
```

<div dir="rtl">

שלושה דברים שכדאי לדעת לפני הפגישה האמיתית הראשונה:

- **הריצו `localscribe doctor`.** הפקודה מראה מה חסר ואיך לתקן.
- **התקינו את BlackHole**, אחרת תקליטו רק את הקול שלכם — לא את שאר המשתתפים.
  הסקריפט `setup.sh` מתקין אותו, אבל צריך סיסמה ואתחול מחדש. ראו
  [שמע מערכת](#שמע-מערכת-חשוב).
- **בהרצה הראשונה יורד מודל דיבור בגודל 1.6 ג׳יגה‑בייט בערך.** `setup.sh` מוריד
  אותו מראש כדי שזה לא יקרה באמצע פגישה.

אין שרת להפעיל, שום דבר לא רץ ברקע, אין מפתח API ואין צורך באינטרנט.

## הרכיבים בקוד פתוח

| תפקיד | חבילה | רישיון |
|---|---|---|
| הקלטת שמע | [sounddevice](https://github.com/spatialaudio/python-sounddevice) / PortAudio | MIT |
| קריאה וכתיבה של WAV | [soundfile](https://github.com/bastibe/python-soundfile) / libsndfile | BSD-3 |
| שינוי קצב דגימה | [soxr](https://github.com/dofuuz/python-soxr) | LGPL-2.1 |
| זיהוי דיבור | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) + CTranslate2 | MIT |
| זיהוי דיבור (GPU) | [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) | MIT |
| סיכום | [Ollama](https://github.com/ollama/ollama) + Llama 3.1 | MIT / רישיון Llama |
| לולאת שמע מערכת | [BlackHole](https://github.com/ExistentialAudio/BlackHole) | GPL-3.0 |

## מה `setup.sh` עושה

הוא בונה סביבת פייתון וירטואלית, מתקין את BlackHole, מוריד את מודל ה‑Whisper,
מתקין את Ollama ומוריד את מודל הסיכום, מקשר פקודת `localscribe` ל‑PATH שלכם,
ואז מריץ את הבדיקות ואת `doctor`. אפשר להריץ אותו שוב בבטחה — כל שלב בודק קודם.

</div>

```bash
./scripts/setup.sh --no-llm                    # לדלג על Ollama
./scripts/setup.sh --no-audio                  # לדלג על BlackHole (דורש sudo ואתחול)
./scripts/setup.sh --whisper small.en          # מודל דיבור קטן ומהיר יותר
```

<div dir="rtl">

ב‑Apple Silicon הסקריפט מתעקש על פייתון **arm64** — גרסת x86_64 מריצה את
CTranslate2 דרך Rosetta והתמלול לוקח בערך פי שלושה זמן.

### שמע מערכת (חשוב)
<a id="שמע-מערכת-חשוב"></a>

ל‑macOS אין דרך מובנית להקליט את מה שיוצא מהרמקולים, ולכן בלי דרייבר לולאה
תקליטו רק את המיקרופון שלכם — כלומר רק את הצד שלכם בשיחה. אחרי ש‑`setup.sh`
מתקין את BlackHole ואתם מאתחלים:

1. פתחו את **Audio MIDI Setup** (בתיקייה `/Applications/Utilities`).
2. `+` ← **Create Multi-Output Device**, וסמנו גם את האוזניות/רמקולים שלכם
   וגם את BlackHole 2ch.
3. הגדירו את ה‑Multi-Output Device הזה כפלט הקול של המחשב.

אתם עדיין שומעים את השיחה כרגיל, ו‑BlackHole מעביר עותק ש‑LocalScribe קורא.
הפקודה `localscribe doctor` תגיד לכם אם ההתקן נמצא.

## האם צריך שרת?

לא. LocalScribe היא פקודה חד‑פעמית: היא מקליטה, מתמללת, מסכמת, כותבת שני קבצי
markdown ומסיימת. שום דבר לא מאזין לפורט, שום דבר לא רץ ברקע בין פגישות, ואין
צורך ברשת.

היוצא מן הכלל היחיד הוא מנוע הסיכום המקומי. Ollama הוא שירות רקע על
`127.0.0.1:11434`, ו‑LocalScribe מפעיל אותו לבד אם הוא לא רץ — כך שגם את זה לא
צריך לזכור. אפשר להגדיר `LOCALSCRIBE_OLLAMA_AUTOSTART=0` כדי לנהל אותו בעצמכם,
או להשתמש ב‑`--backend extractive` ואז אין שירות רקע בכלל.

## שימוש

הסקריפט `scripts/setup.sh` מקשר פקודת `localscribe` ל‑PATH. אחרת אפשר להריץ
`./bin/localscribe` מתוך התיקייה — אותו דבר, בלי להפעיל סביבה וירטואלית.

</div>

```bash
localscribe doctor                       # בדיקת ההתקנה
localscribe devices                      # רשימת התקני קלט

# הקלטה עד Ctrl-C, ואז תמלול וסיכום
localscribe record --label "Latency sync"

# עצירה אוטומטית אחרי זמן קבוע
localscribe record --label "Standup" --duration 20m

# הרצה חוזרת על הקלטה קיימת (למשל אחרי שינוי הפרומפט או המודל)
localscribe process ~/LocalScribe/audio/standup_2026-08-31_1000.wav

# סיכום מחדש של תמלול קיים, בלי לתמלל שוב
localscribe summarize ~/LocalScribe/transcripts/standup_2026-08-31_1000.json

localscribe list                         # מה הוקלט עד עכשיו
```

<div dir="rtl">

הפלט נכתב ל‑`~/LocalScribe/{audio,transcripts,summaries}`.

## מי אמר מה

שני מקורות השמע נשמרים בערוצים נפרדים — המיקרופון שלכם בערוץ השמאלי ולולאת
המערכת בערוץ הימני — ולכן LocalScribe מזהה דוברים לפי השוואת עוצמה בין הערוצים
עבור כל מילה, במקום להריץ מודל דיאריזציה. התוצאה היא **You** מול **Them**, לא
שמות אמיתיים, אבל היא מדויקת, לא עולה כלום ולא דורשת מודל חסום ב‑Hugging Face.
סימן `?` בסוף (`Them?`) מסמן מילה שבה שני הערוצים היו פעילים וההכרעה הייתה קרובה.

כדי לקבל שמות אמיתיים, העבירו את התמלול דרך מנגנון הסיכום — מודל שפה בדרך כלל
מסיק אותם מהאופן שבו האנשים פונים זה לזה.

## מנועי תמלול

שני מנועי דיבור, אותו פלט. `auto` (ברירת המחדל) בוחר ב‑`mlx` כשהוא מותקן על
Apple Silicon, וב‑`faster-whisper` בכל מקרה אחר.

</div>

```bash
localscribe process recording.wav --engine mlx             # Metal GPU
localscribe process recording.wav --engine faster-whisper  # CPU, בכל מכונה
```

<div dir="rtl">

‏262 שניות שמע, מודל `base.en`, המסלול המלא כולל שיוך דוברים ברמת המילה, על Mac
עם מעבד M:

| מנוע | חישוב | זמן |
|---|---|---|
| faster-whisper (CTranslate2) | CPU int8 | 11.9 שנ׳ |
| **mlx-whisper** | **Metal GPU** | **4.2 שנ׳** |

ההרצה הראשונה של `mlx` בהתקנה חדשה לוקחת בערך 30 שניות נוספות בזמן ש‑Metal
מהדר את הקרנלים שלו. זה חד‑פעמי ונשמר במטמון; `scripts/setup.sh` משלם את זה
מראש כדי שזה לא ייפול עליכם בפגישה הראשונה.

שווה לדעת אם אתם שוקלים כתיבה מחדש בשפה מהודרת: לא שפת המארח היא מה שמייצר את
המהירות. על אותה מכונה ואותו שמע, whisper.cpp — מנוע ה‑C++‎ שגרסת Go או Rust
הייתה נקשרת אליו דרך cgo — לוקח **10.2 שניות על ה‑CPU** ו‑**2.2 שניות על ה‑GPU**,
מול 5.7 שניות של CTranslate2 ו‑2.9 שניות של MLX על אותה עבודה. הפייתון שמתזמר
את הכול עולה פחות מ‑0.1 שניות מתוך 11.9 השניות שלמעלה; הרצה מלאה של המסלול
וקריאת התמלול החשופה הן אותו מספר בגבולות הרעש. המנוף הוא ה‑GPU, לא השפה.

## בחירת מודל Whisper

| מודל | גודל | מהירות (מעבד M, int8) | מתי |
|---|---|---|---|
| `base.en` | 140 MB | פי 15 מזמן אמת | בדיקות מהירות, שמע נקי |
| `small.en` | 460 MB | פי 8 מזמן אמת | סביר, אנגלית בלבד |
| `medium.en` | 1.5 GB | פי 3 מזמן אמת | טוב |
| `large-v3-turbo` | 1.6 GB | פי 4 מזמן אמת | **ברירת מחדל** — הכי טוב עם מבטאים |

</div>

```bash
localscribe process recording.wav --model small.en
```

<div dir="rtl">

המהירויות שלמעלה הן למנוע ה‑CPU; מנוע `mlx` מהיר בערך פי שלושה מכל אחת מהן.
פגישה של שעה עם `large-v3-turbo` לוקחת בערך 15 דקות על ה‑CPU ובערך 5 על ה‑GPU.

## מנועי סיכום

- `--backend ollama` (ברירת מחדל) — מודל שפה מקומי. לגמרי לא מקוון.
- `--backend anthropic` — ה‑API של Claude. רק **הטקסט** של התמלול נשלח, לעולם לא
  השמע. דורש `ANTHROPIC_API_KEY`.
- `--backend extractive` — בלי מודל בכלל: משפטים מדורגים לפי מילות מפתח ומשימות
  שנתפסות בביטויים רגולריים. גס, אבל מיידי וללא תלויות. גם משמש כנפילה אוטומטית
  כשהמנוע שנבחר לא זמין.

תמלולים ארוכים מ‑1800 מילים בערך מסוכמים בשיטת map‑reduce: כל מקטע נקרא בנפרד,
ואז ההערות ממוזגות.

## פיתוח

</div>

```bash
.venv/bin/pytest          # 60 בדיקות, בלי חומרת שמע ובלי הורדת מודלים
.venv/bin/ruff check .
```

<div dir="rtl">

הבדיקות מכסות את החלקים שנכשלים בשקט ולא ברעש: שיוך דוברים (פיצול תורות, דליפת
רמקולים למיקרופון, הפרשי עוצמה, דיבור במקביל), טיפול בסחיפת שעונים במקליט,
בחירת מנוע והנפילות שלה, ועיבוד הסיכום.

</div>

```
localscribe/
├── localscribe/
│   ├── audio.py        הקלטה, שינוי קצב דגימה, יישור שני שעונים
│   ├── engines.py      faster-whisper (CPU) ו‑mlx (Metal GPU), מבנה אחד
│   ├── transcribe.py   תמלול + שיוך דוברים ברמת המילה
│   ├── summarize.py    מנועי ollama / anthropic / extractive
│   ├── config.py       הגדרות סביבה, כולן אופציונליות
│   └── cli.py          record, process, summarize, devices, doctor, list
├── bin/localscribe     משגר: מריץ את ה‑CLI של הסביבה מכל מקום
├── scripts/setup.sh    התקנה בפקודה אחת
└── tests/
```

<div dir="rtl">

## לפני שאתם מקליטים אנשים אחרים

הקלטה קולטת את כל מי שנמצא בשיחה. כללי ההסכמה שונים בין מדינות, ובחלקן נדרשת
הסכמת כל הצדדים — אמרו לחדר שההקלטה פועלת.

</div>
