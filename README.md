# Journal Entry Practice Tool

A practice tool for accountancy students. It shows a business transaction, the
student writes the journal entry, and the tool checks it right away and explains
the correct answer.

It runs in a web browser. You don't need to install anything, sign up, or have
an internet connection.

---

## How to open it

1. Download this folder to your computer. On GitHub, click the green **Code**
   button and choose **Download ZIP**, then unzip it.
2. Open the folder and double-click **`index.html`**.
3. The tool opens in your web browser (Chrome, Edge, Safari or Firefox).

Keep all the files together in the same folder. The tool needs all of them.

### Getting it onto students' phones

Opening a file directly on a phone is awkward, so the easiest way is to put
the tool on a free web link with **GitHub Pages**:

1. On GitHub, open this repository and go to **Settings → Pages**.
2. Under **Source**, choose **Deploy from a branch**. Pick the `main` branch
   and the `/ (root)` folder, then click **Save**.
3. Wait a minute or two, then refresh the page. GitHub shows a link like
   `https://your-username.github.io/AGING-AND-COLLECTION/`.
4. Share that link with students. It works on any phone browser.

When you change `scenarios.js` on the `main` branch, the link updates on its
own within a few minutes.

---

## How students use it

1. **Pick a level** at the top: All, Basic, Intermediate or Advanced.
   Turn on **Shuffle order** to get the scenarios in a random order, so
   students can't memorise the sequence. Each new round is shuffled again.
2. **Read the transaction** in the box at the top.
3. **Build the journal entry.** On each line:
   - choose an account from the dropdown,
   - type the amount (commas are fine; they are added automatically),
   - tap **Debit** or **Credit**.
   Use **+ Add line** for entries with more than two accounts, and **×** to
   remove a line. The running totals show whether debits equal credits.
4. Tap **Check Answer**. The tool shows:
   - whether total debits equal total credits,
   - for each account, whether it is the right account, on the right side,
     with the right amount,
   - any account that does not belong in the entry,
   - the correct entry and a short explanation of why.
5. Tap **Try again** to clear the entry and redo the same scenario, or
   **Next scenario** to go to the next one.
6. **End of the round.** On the last scenario the button changes to
   **See results**. The results screen shows how many were correct on the
   first try, and lists every scenario as correct (✓), missed (✗) or
   skipped (–). From there:
   - **Retry missed** starts a short review round with only the missed and
     skipped scenarios.
   - **Start over** begins the whole level again.

   Changing the level or turning shuffle on or off also starts a new round.

**Score:** the score at the top right counts only the *first* check of each
scenario, so trying again after seeing the answer doesn't raise it. The score
resets when the page is closed or reloaded, or when you tap **Reset score**.

---

## What each file does

| File | What it is | Do you need to edit it? |
|---|---|---|
| `index.html` | The page itself: the layout of the boxes, buttons and headings. This is the file you open. | No |
| `styles.css` | The look: colors, sizes, spacing and the phone-friendly layout. Also switches to dark colors when the phone is in dark mode. | No |
| `app.js` | The "brain": builds the entry lines, adds up totals, checks answers, writes the feedback, keeps score, shuffles the order and builds the end-of-round results. | No |
| `scenarios.js` | **The content**: the list of accounts in the dropdown and all the practice scenarios with their answers and explanations. | **Yes. This is the file you edit to add questions.** |
| `README.md` | This guide. | Only if you want |

---

## How to add a scenario

You only need to edit **`scenarios.js`**. It is a plain text file.

**1. Open the file in a text editor.**
- On Windows: right-click `scenarios.js` → **Open with** → **Notepad**.
- On Mac: right-click → **Open With** → **TextEdit**.
- Or on GitHub: open the file and click the pencil icon (✏️) to edit it in your browser.

Don't use Word. It can change the quote marks and break the file.

**2. Find the end of the scenario list.** Scroll to the bottom. The last
scenario ends with `}` and the list ends with `];`.

**3. Add a comma after the last `}`, then paste a new block before `];`.**
Copy this template:

```js
  {
    level: "Intermediate",
    text: "The company borrowed 50,000 from the bank and signed a promissory note.",
    answer: [
      { account: "Cash", debit: 50000 },
      { account: "Notes Payable", credit: 50000 }
    ],
    explanation: "Cash increases, so debit Cash. The company now owes the bank under a written note, so credit Notes Payable."
  }
```

The end of the file should then look like this:

```js
    explanation: "Supplies used = 8,000 - 2,500 = 5,500. ..."
  },                     <-- comma added here

  {
    level: "Intermediate",
    text: "The company borrowed 50,000 ...",
    ...
  }

];
```

**4. Change the parts in quotes and the numbers:**

- **`level`**: exactly `"Basic"`, `"Intermediate"` or `"Advanced"`.
- **`text`**: the transaction students will read. You can write commas in
  amounts here (e.g. `50,000`), because this is just text.
- **`answer`**: one line per account in the correct entry:
  - `{ account: "Cash", debit: 50000 }` for a debit
  - `{ account: "Notes Payable", credit: 50000 }` for a credit
  - Write these amounts **without commas**: `50000` or `3450.75`.
  - Separate lines with a comma. There's no comma after the last line.
  - Compound entries are fine: add as many lines as you need.
- **`explanation`**: one or two sentences on why the entry is correct.

**5. Save the file and refresh the page in your browser.**

### Rules that prevent most mistakes

- Every `{` needs a matching `}` and every `[` needs a matching `]`.
- Put a comma **between** scenarios, and between the lines inside `answer`.
- Put text in straight double quotes `"like this"`. If your text needs a
  double quote inside it, use a single quote instead: `"the 'on account' terms"`.
- Account names must match the dropdown (capital letters don't matter).

### If something goes wrong

The tool checks your file when it opens:

- **"No scenarios could be loaded"**: the file has a typing mistake, usually a
  missing comma, quote or bracket. Look at the scenario you just added.
- **"Note for the teacher: please check scenarios.js"**: the tool still works,
  but it found a problem, for example an answer whose debits don't equal its
  credits, or an account name that isn't in the dropdown. The message says
  which scenario to fix.

### Adding a new account to the dropdown

At the top of `scenarios.js` is `CHART_OF_ACCOUNTS`, grouped into Assets,
Liabilities, Owner's Equity, Revenue and Expenses. Add the name in quotes
inside the right group, with a comma between names:

```js
  "Expenses": [
    "Cost of Goods Sold",
    "Rent Expense",
    "Office Equipment Repairs Expense",   <-- new account
    ...
```

---

## The 15 included scenarios

| Level | Topics |
|---|---|
| Basic (5) | Cash purchase of supplies, cash sales, paying rent, cash service revenue, paying utilities |
| Intermediate (5) | Supplies on account, sales on account, collecting receivables, paying payables, a compound equipment purchase |
| Advanced (5) | Accrued salaries, prepaid insurance (payment and adjusting entry), straight-line depreciation, supplies used |
