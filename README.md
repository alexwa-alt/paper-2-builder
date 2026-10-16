# 0625 Physics question bank

Turns compiled PDFs of Cambridge IGCSE Physics (0625) past-paper questions into a searchable website where you can filter questions and build exam-style practice papers. It handles two question banks, and the site has a switch between them:

- **Paper 2: Multiple choice.** Answers come from the letter answer key at the back of the PDF.
- **Paper 6: Alternative to practical.** Long, multi-page questions, with mark schemes taken from the back of the PDF.

It works in three stages:

1. **Extract** (`scripts/extract.py`, plain Python). Splits a PDF into question images, reads each question's paper reference and section, and joins its answer or mark scheme. Paper 2 duplicates are merged. The Paper 6 PDF has no text layer (its headings and references are pictures of letters), so those are read with Tesseract OCR. Questions are usable on the site as soon as this finishes.
2. **Tag** (`scripts/tag.py`, Gemini free tier, runs hourly until done). Adds:
   - learning objectives from the 2023–2025 syllabus;
   - a difficulty rating;
   - a text transcription (Paper 2) or summary (Paper 6) for search;
   - a check that each question is filed in the right section;
   - question type (Paper 2), or practical skills and total marks (Paper 6).

   Paper 2 is tagged first, then Paper 6.
3. **Publish** (GitHub Pages). A static site for filtering questions and downloading a PDF practice paper with an optional cover page and answer key or mark schemes.

## Setup

1. **Create the repository.** Make a new GitHub repository with `main` as its default branch and upload every file in this folder, keeping the folder structure. The website's uploader skips folders whose names start with a dot, so create the three files in `.github/workflows/` with **Add file > Create new file**, typing the full path (e.g. `.github/workflows/extract.yml`) as the file name.

2. **Upload the question bank PDFs to a release.** They're too big to upload as normal files.
   - Go to **Releases > Create a new release**.
   - Create a tag such as `source-pdf`.
   - Attach `question_bank.pdf` (Paper 2) and `p6_question_bank.pdf` (Paper 6). The names must match exactly.
   - Publish the release.

   To add a PDF later, edit the same release and attach the file there.

3. **Add the syllabus.** Upload the Cambridge IGCSE Physics 0625 syllabus for 2023, 2024 and 2025 to `input/syllabus.pdf`. It's free from the Cambridge International website.

4. **Add your Gemini API key.** Create a free key at [Google AI Studio](https://aistudio.google.com/apikey). In the repository, go to *Settings > Secrets and variables > Actions > New repository secret*, name it `GEMINI_API_KEY` and paste the key.

5. **Turn on GitHub Pages.** Go to *Settings > Pages* and set *Source* to **GitHub Actions**.

6. **Allow the workflows to save their results.** Go to *Settings > Actions > General > Workflow permissions* and choose **Read and write permissions**.

7. **Run the extraction.** In the *Actions* tab, choose **1. Extract questions**, click **Run workflow**, pick `p2` or `p6`, and run it. Do the same for the other bank. When each finishes, **3. Publish site** runs automatically. Your site address is shown under *Settings > Pages*.

8. **Check the review files.** Paper 2's is `site/data/review.json`; Paper 6's is `site/data/p6/review.json`. They list anything that looked wrong, such as:
   - questions with no answer or mark scheme;
   - reference lines the OCR couldn't read;
   - sections that disagreed with the contents page.

   A handful of entries is normal. Each one names the question or PDF page so you can check it.

9. **Let tagging run.** **2. Tag questions with Gemini** runs every hour by itself. The site's header shows how many questions are tagged, and the tag filters fill in as tagging progresses. When every question in both banks is tagged, you can disable the workflow.

## Using the site

- Switch between Paper 2 and Paper 6 at the top. Each has its own filters and its own paper.
- Filter by topic and section, learning objective, exam year and session, difficulty, and either question type and tier (Paper 2) or practical skills (Paper 6). You can also search the question text.
- *Add random* adds that many random questions from the current results to your paper.
- **Paper 2:** *Show answers* reveals each answer. A compiled paper flows the questions down the page, like the real exam, with an optional cover page (with an answer grid) and an answer key.
- **Paper 6:**
  - Each question shows its first page, with a button to show the rest and another to show its mark scheme.
  - In a compiled paper, every question starts on a new page and keeps its original page breaks.
  - The cover page includes a marking grid when every chosen question's total mark is known. Mark schemes can be added at the end.
- Your papers are remembered in that browser until you clear them.

Skill, type, difficulty and learning-objective filters only include questions that have been tagged. If Gemini judged a question to be filed in the wrong section, the site shows it under the corrected section, marked "moved from …".

## Settings (`config.yaml`)

- `gemini.model`: the Gemini model used. Free-tier models change over time; see the [Gemini models page](https://ai.google.dev/gemini-api/docs/models).
- `gemini.max_requests_per_run` and `seconds_between_requests`: how much each hourly run tries to do. If Gemini is busy or the quota runs out mid-run, the run stops cleanly and the next one carries on.
- `banks.<bank>.questions_per_request`: questions sent per Gemini request. Paper 6 questions are several pages each, so it sends fewer.
- `banks.<bank>.sections`: each bank's contents page. Paper 6 uses an older syllabus layout, so its sections differ from Paper 2's. Learning-objective tags always refer to the 2023–2025 syllabus.
- `dedupe`: how strict Paper 2's "exact duplicate" is.

## Running on your own computer

You need Python 3.10 or later. For Paper 6 you also need [Tesseract](https://github.com/tesseract-ocr/tesseract) installed.

```bash
pip install -r requirements.txt
python scripts/extract.py --bank p2                    # or --bank p6
export GEMINI_API_KEY=your-key                         # on Windows: set GEMINI_API_KEY=your-key
python scripts/tag.py --max-requests 3                 # a short test
cd site && python -m http.server 8000                  # preview at http://localhost:8000
```

Useful options:
- `extract.py --bank p6 --pdf some-pages.pdf` runs extraction on a smaller extract, for testing.
- `tag.py --bank p6` tags only one bank.
- `tag.py --repeats-only` re-runs Paper 2's repeat detection without calling Gemini.

## Things to know

- **Re-running an extraction** rebuilds that bank's images and question list but keeps its tags. Question ids come from the serial numbers in each PDF.
- **Public repository, public site.** The repository, the question images, the release PDFs and the site are publicly reachable.
- **Gemini free tier.** Google may use free-tier requests to improve its models.
- **Scheduled workflows** in public repositories are paused by GitHub after 60 days without any repository activity. Tagging commits count as activity.

## Folder layout

```
config.yaml                 settings, syllabus outline and both contents pages
requirements.txt            Python packages
input/syllabus.pdf          the 2023-2025 syllabus (you add this)
scripts/
  common.py                 shared helpers
  extract.py                stage 1: split a PDF
  tag.py                    stage 2: Gemini tagging
site/                       the website (published to GitHub Pages)
  index.html, styles.css, app.js, paper.js
  data/syllabus.json        learning objectives (generated, shared)
  data/, images/            Paper 2 questions, tags, review and images (generated)
  data/p6/, images/p6/      Paper 6 questions, tags, review and images (generated)
.github/workflows/
  extract.yml               1. Extract questions (run by hand, choose the bank)
  tag.yml                   2. Tag questions with Gemini (hourly)
  pages.yml                 3. Publish site (automatic)
```
