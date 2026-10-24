# IGCSE past-paper question bank

Turns compiled PDFs of Cambridge IGCSE past-paper questions into a searchable website where you can filter questions and build exam-style practice papers. The site opens with a choice of subject:

| Subject | Code | Banks |
|---|---|---|
| Biology | 0610 | Paper 2 (multiple choice), Paper 4 (theory, Extended), Paper 6 (alternative to practical) |
| Chemistry | 0620 | Paper 2, Paper 4, Paper 6 (set up; shown as "coming soon" until extracted) |
| Physics | 0625 | Paper 2, Paper 4 (set up), Paper 6 |

Each bank is one compiled PDF. A bank only appears on the site once it has been extracted. Multiple-choice banks take their answers from the letter answer key at the back. Paper 4 and Paper 6 banks take their mark schemes from the back.

It works in three stages:

1. **Extract** (`scripts/extract.py`, plain Python). Splits a PDF into question images, reads each question's paper reference and section, and joins its answer or mark scheme. Exact duplicates in multiple-choice banks are merged. The script handles both real text and pages where headings are pictures of letters, which it reads with Tesseract OCR.
2. **Tag** (`scripts/tag.py`, Gemini free tier, runs hourly until done). For each subject it first reads the syllabus PDF into a fixed list of learning objectives, then adds:
   - learning objectives;
   - a difficulty rating;
   - a text transcription or summary for search;
   - a check that each question is filed in the right section;
   - question type (Paper 2 and Paper 4), or practical skills (Paper 6);
   - total marks (Paper 4 and Paper 6).
3. **Publish** (GitHub Pages). A static site for filtering questions and downloading a PDF practice paper with an optional cover page and answer key or mark schemes.

## Adding a subject's PDFs

1. **Attach the PDFs to the release.** Go to **Releases**, click the pencil icon on your release, and attach each PDF under the name shown in this table. The names must match exactly. Then click **Update release**.

   | Bank | Paper 2 | Paper 4 | Paper 6 |
   |---|---|---|---|
   | Biology | `biology_p2.pdf` | `biology_p4.pdf` | `biology_p6.pdf` |
   | Chemistry | `chemistry_p2.pdf` | `chemistry_p4.pdf` | `chemistry_p6.pdf` |
   | Physics | `question_bank.pdf` | `physics_p4.pdf` | `p6_question_bank.pdf` |

2. **Add the subject's syllabus** to the `input` folder: `input/biology_syllabus.pdf` or `input/chemistry_syllabus.pdf`. Physics uses `input/syllabus.pdf`.
3. **Run the extraction.** In the *Actions* tab, choose **1. Extract questions**, click **Run workflow**, pick the bank and run it. The site updates automatically when it finishes, and the subject appears on the start screen.
4. **Check the review file** for that bank, e.g. `site/data/biology/p2/review.json`. It lists anything that looked wrong, such as:
   - questions with no answer or mark scheme;
   - reference lines that couldn't be read;
   - sections that disagreed with the contents page.
5. **Tagging picks the new bank up automatically** on its next hourly run.

**Sections and contents pages.** For Physics, each bank's contents page is written into `config.yaml`, so the extractor can check every question's section against its page range. For the other subjects, sections and chapter names are read from the page headings. That works without any setup, but adding a bank's contents page to `config.yaml` gives a cross-check and exact section names. Copy the format of the Physics banks: a `chapters` list, and a `sections` list with `start_page`, plus `answers_start_page`. If a contents page lists topics only, with no subtopics (like Biology Paper 4), enter each topic as one section whose code is the topic number, as Biology Paper 4 does. The site then shows each topic as a single checkbox.

**Topic numbering.** Older banks can number their topics differently from the current syllabus. For example, the Biology Paper 4 bank has no "Biological molecules" topic, so from Enzymes onwards its numbers are one behind. When tagging, each bank topic is matched to the syllabus topic with the same title, falling back to the topic number when no title matches.

## First-time setup

1. Create a GitHub repository with `main` as its default branch and upload every file in this folder, keeping the folder structure. The website's uploader skips folders whose names start with a dot, so create the files in `.github/workflows/` with **Add file > Create new file**, typing the full path as the file name.
2. Create a release (**Releases > Create a new release**, with a tag such as `source-pdf`), attach the PDFs as above, and publish it.
3. Add a free Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey) as a repository secret called `GEMINI_API_KEY` (*Settings > Secrets and variables > Actions*).
4. In *Settings > Pages*, set *Source* to **GitHub Actions**.
5. In *Settings > Actions > General > Workflow permissions*, choose **Read and write permissions**.
6. Run **1. Extract questions** once for each bank you have.

## Using the site

- Choose a subject on the start screen. To switch later, click the subject's name at the top left. Within a subject, switch between Paper 2 and Paper 6 at the top right. Each bank has its own filters and its own paper.
- Filter by topic and section, learning objective, exam year and session and difficulty. Each paper also has its own filter:
  - Paper 2: question type and tier;
  - Paper 4: question type (recall, describe and explain, calculations, data, unfamiliar contexts);
  - Paper 6: practical skills.

  You can also search the question text.
- *Add random* adds that many random questions from the current results to your paper.
- **Paper 2:** compiled papers flow the questions down the page, with an optional cover page (with an answer grid) and an answer key.
- **Paper 4 and Paper 6:** every question starts on a new page and keeps its original page breaks. Mark schemes can be added at the end. The suggested time on the cover page is 1 minute per mark for Paper 4 and 1.5 for Paper 6 (`minutes_per_mark` in `config.yaml`).
- Papers are remembered in that browser until you clear them.
- Links include the bank, e.g. `…/#biology-p6`, so you can bookmark one.

## Settings (`config.yaml`)

- `gemini.model`: the Gemini model used. Free-tier models change; see the [Gemini models page](https://ai.google.dev/gemini-api/docs/models).
- `gemini.max_requests_per_run` and `seconds_between_requests`: how much each hourly run tries to do. Runs stop cleanly if Gemini is busy or the quota runs out.
- `skills`: the practical skills Paper 6 questions can be tagged with, and their labels on the site.
- `theory_types`: the question types Paper 4 questions can be tagged with.
- `subjects.<subject>.banks.<bank>`: each bank's release file name, folders, questions per Gemini request and, optionally, its contents page.

## Running on your own computer

You need Python 3.10 or later, and [Tesseract](https://github.com/tesseract-ocr/tesseract) for PDFs that need OCR.

```bash
pip install -r requirements.txt
python scripts/extract.py --bank biology-p2 --pdf path/to/biology_p2.pdf
export GEMINI_API_KEY=your-key            # on Windows: set GEMINI_API_KEY=your-key
python scripts/tag.py --bank biology-p2 --max-requests 3
cd site && python -m http.server 8000     # preview at http://localhost:8000
```

## Things to know

- **Re-running an extraction** rebuilds that bank's images and question list but keeps its tags.
- **Public repository, public site.** The repository, the question images, the release PDFs and the site are publicly reachable.
- **Gemini free tier.** Google may use free-tier requests to improve its models.
- **Scheduled workflows** in public repositories are paused by GitHub after 60 days without any repository activity.

## Folder layout

```
config.yaml                     settings, subjects, banks and contents pages
input/                          syllabus PDFs (bank PDFs live on the release)
scripts/common.py, extract.py, tag.py
site/                           the website (published to GitHub Pages)
  data/manifest.json            which subjects and banks exist (generated)
  data/, images/                Physics Paper 2 (data/syllabus.json = Physics objectives)
  data/p6/, images/p6/          Physics Paper 6 (Physics Paper 4 goes in data/physics/p4/)
  data/biology/...              Biology objectives, p2/, p4/ and p6/ (images/biology/...)
  data/chemistry/...            Chemistry, the same way
.github/workflows/
  extract.yml                   1. Extract questions (run by hand, choose the bank)
  tag.yml                       2. Tag questions with Gemini (hourly)
  pages.yml                     3. Publish site (automatic)
```
