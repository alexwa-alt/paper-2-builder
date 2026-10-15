# 0625 Physics question bank

Turns the compiled PDF of Cambridge IGCSE Physics (0625) multiple-choice past-paper questions into a searchable website where you can filter questions and build exam-style practice papers.

It works in three stages:

1. **Extract** (`scripts/extract.py`, plain Python, a few minutes). Splits the PDF into one image per question, reads each question's paper reference and section from the page, joins the answer from the answer key, and merges exact duplicates. Questions are usable on the site as soon as this finishes.
2. **Tag** (`scripts/tag.py`, Gemini free tier, runs hourly until done). Adds syllabus learning objectives, question type, difficulty and a text transcription for search, and checks whether each question is filed under the right section. It also spots re-scanned copies of the same question.
3. **Publish** (GitHub Pages). A static site for filtering questions and downloading a PDF practice paper with an optional cover page and answer key.

## Setup

1. **Create the repository.** Make a new GitHub repository with `main` as its default branch and upload every file in this folder, keeping the folder structure (including the hidden `.github` folder).

2. **Add the two PDFs** to the `input` folder:
   - `input/question_bank.pdf`, the 682-page question bank.
   - `input/syllabus.pdf`, the Cambridge IGCSE Physics 0625 syllabus **for 2023, 2024 and 2025**, available free from the Cambridge International website.

   GitHub rejects files over 100 MB and warns above 50 MB. If the question bank is too large, see "Running on your own computer" below.

3. **Add your Gemini API key.** Create a free key at [Google AI Studio](https://aistudio.google.com/apikey). In the repository, go to *Settings > Secrets and variables > Actions > New repository secret*, name it `GEMINI_API_KEY` and paste the key.

4. **Turn on GitHub Pages.** Go to *Settings > Pages* and set *Source* to **GitHub Actions**.

5. **Allow the workflows to save their results.** Go to *Settings > Actions > General > Workflow permissions* and choose **Read and write permissions**.

6. **Run the extraction.** In the *Actions* tab, choose **1. Extract questions** and click **Run workflow**. When it finishes, **3. Publish site** runs automatically. Your site address is shown under *Settings > Pages*.

7. **Check the review file.** Open `site/data/review.json` in the repository. It lists anything that looked wrong, such as:
   - questions with no answer;
   - answer-key rows whose paper code didn't match the question;
   - duplicates with conflicting answers;
   - sections that disagreed with the contents page.

   A handful of entries is normal. Each one names the question so you can check it in the PDF.

8. **Let tagging run.** **2. Tag questions with Gemini** runs every hour by itself. The first runs read the syllabus (six requests), then questions are tagged in batches. The site's header shows how many are done, and the tag filters fill in as tagging progresses. When the header says every question is tagged, you can disable the workflow (*Actions > 2. Tag questions with Gemini > ⋯ > Disable workflow*).

## Using the site

- Filter by topic and section, learning objective, Core or Extended, exam year and session, question type and difficulty, or search the question text.
- *Show answers* reveals each question's answer.
- *Add random* adds that many random questions from the current results to your paper.
- In *Your paper*, reorder or remove questions, give the paper a title, and choose:
  - a cover page, with name/class/date boxes, instructions, time allowed and an answer grid;
  - an answer key on the last page;
  - printed past-paper references beside each question.

  Then press **Download PDF**.
- Your paper is remembered in that browser until you clear it.

Type, difficulty and learning-objective filters only include questions that have been tagged. If Gemini judged a question to be filed in the wrong section, the site shows it under the corrected section, marked "moved from …".

## Settings (`config.yaml`)

- `gemini.model`: the Gemini model used. Free-tier models change over time; see the [Gemini models page](https://ai.google.dev/gemini-api/docs/models).
- `gemini.questions_per_request`, `max_requests_per_run` and `seconds_between_requests`: how much each hourly run tries to do. If the free quota runs out mid-run, the run stops cleanly and the next one carries on, so these rarely need changing.
- `gemini.max_attempts`: after this many invalid answers for a question, it is skipped. It appears untagged on the site.
- `dedupe`: how strict "exact duplicate" is. The defaults only merge copies that are pixel-for-pixel the same apart from scanning noise. Copies that differ slightly are caught later by comparing Gemini's transcriptions.
- `sections`: the contents page. Only change this if the PDF changes.

## Running on your own computer

You need Python 3.10 or later.

```bash
pip install -r requirements.txt
python scripts/extract.py                    # stage 1
export GEMINI_API_KEY=your-key               # on Windows: set GEMINI_API_KEY=your-key
python scripts/tag.py --max-requests 3       # stage 2, a short test
cd site && python -m http.server 8000        # preview at http://localhost:8000
```

To keep the PDFs out of GitHub entirely:
1. Uncomment `input/*.pdf` in `.gitignore`.
2. Run `extract.py` locally.
3. Commit `site/images` and `site/data`.

`tag.py` still needs the syllabus PDF for its first run, so either run it locally until the syllabus has been read (`site/data/syllabus.json` exists), or commit just the syllabus PDF.

Useful options:
- `python scripts/extract.py --pdf some-pages.pdf` runs extraction on a smaller extract, for testing.
- `python scripts/tag.py --repeats-only` re-runs the repeat detection without calling Gemini.

## Things to know

- **Re-running the extraction** rebuilds the images and question list but keeps existing tags. Each question's id comes from its serial number in the question bank.
- **Public repository, public site.** The repository, the question images and the site are publicly reachable. Pages from a private repository needs a paid plan; GitHub Pro is free for teachers through GitHub Education.
- **Gemini free tier.** Google may use free-tier requests to improve its models.
- **Scheduled workflows** in public repositories are paused by GitHub after 60 days without any repository activity. Tagging commits count as activity, so this only matters once tagging is finished.

## Folder layout

```
config.yaml                settings and the contents page
requirements.txt           Python packages
input/                     the two source PDFs (you add these)
scripts/
  common.py                shared helpers
  extract.py               stage 1: split the PDF
  tag.py                   stage 2: Gemini tagging
site/                      the website (published to GitHub Pages)
  index.html, styles.css, app.js, paper.js
  images/                  one PNG per question (generated)
  data/questions.json      question list (generated)
  data/tags.json           Gemini tags (generated)
  data/syllabus.json       learning objectives (generated)
  data/review.json         things worth checking (generated)
.github/workflows/
  extract.yml              1. Extract questions (run by hand)
  tag.yml                  2. Tag questions with Gemini (hourly)
  pages.yml                3. Publish site (automatic)
```
