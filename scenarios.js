/*
  ===========================================================
  SCENARIOS FILE  -  this is the only file you need to edit
  to add, change or remove practice questions.
  ===========================================================

  PART 1: CHART OF ACCOUNTS
  These are the accounts students can pick from the dropdown.
  To add an account, type its name in quotes inside the right
  group, followed by a comma. Example:  "Office Equipment",

  PART 2: SCENARIOS
  Each scenario is one block between { and }, followed by a comma.
  Copy an existing block, paste it at the end of the list, and edit:

    level:       "Basic", "Intermediate" or "Advanced"
    text:        the transaction the student reads
    answer:      the correct journal entry, one line per account.
                 Use  debit: amount  or  credit: amount.
                 Write amounts WITHOUT commas: 5000 or 3450.75
    explanation: a short note on why the entry is correct

  Account names in "answer" must match a name in the chart of
  accounts (capital letters do not matter). If you use a name
  that is not in the chart, the app adds it to the dropdown under
  "Other" automatically, but it is neater to add it above.
*/

const CHART_OF_ACCOUNTS = {
  "Assets": [
    "Cash",
    "Accounts Receivable",
    "Notes Receivable",
    "Interest Receivable",
    "Merchandise Inventory",
    "Supplies",
    "Prepaid Rent",
    "Prepaid Insurance",
    "Land",
    "Building",
    "Equipment",
    "Accumulated Depreciation - Equipment",
    "Furniture and Fixtures"
  ],
  "Liabilities": [
    "Accounts Payable",
    "Notes Payable",
    "Salaries Payable",
    "Utilities Payable",
    "Interest Payable",
    "Unearned Revenue",
    "Taxes Payable"
  ],
  "Owner's Equity": [
    "Owner's Capital",
    "Owner's Drawing"
  ],
  "Revenue": [
    "Sales Revenue",
    "Service Revenue",
    "Interest Income",
    "Rent Income"
  ],
  "Expenses": [
    "Cost of Goods Sold",
    "Rent Expense",
    "Salaries Expense",
    "Utilities Expense",
    "Supplies Expense",
    "Insurance Expense",
    "Depreciation Expense",
    "Interest Expense",
    "Advertising Expense",
    "Repairs and Maintenance Expense",
    "Transportation Expense"
  ]
};

const SCENARIOS = [

  /* ---------------- BASIC ---------------- */

  {
    level: "Basic",
    text: "The company purchased office supplies for 5,000 and paid in cash.",
    answer: [
      { account: "Supplies", debit: 5000 },
      { account: "Cash", credit: 5000 }
    ],
    explanation: "Supplies (an asset) increase, so debit Supplies. Cash (an asset) decreases because it was paid out, so credit Cash."
  },

  {
    level: "Basic",
    text: "The company made cash sales of 12,500.",
    answer: [
      { account: "Cash", debit: 12500 },
      { account: "Sales Revenue", credit: 12500 }
    ],
    explanation: "Cash increases, so debit Cash. Revenue is earned from the sale, and revenue increases with a credit, so credit Sales Revenue."
  },

  {
    level: "Basic",
    text: "The company paid 15,000 for this month's office rent.",
    answer: [
      { account: "Rent Expense", debit: 15000 },
      { account: "Cash", credit: 15000 }
    ],
    explanation: "Rent for the current month is used up now, so it is an expense. Expenses increase with a debit. Cash goes down, so credit Cash."
  },

  {
    level: "Basic",
    text: "The company rendered repair services to a customer and received 8,750 in cash.",
    answer: [
      { account: "Cash", debit: 8750 },
      { account: "Service Revenue", credit: 8750 }
    ],
    explanation: "Cash increases, so debit Cash. The company earned income by performing a service, so credit Service Revenue."
  },

  {
    level: "Basic",
    text: "The company paid its electricity and water bill of 3,450.75 in cash.",
    answer: [
      { account: "Utilities Expense", debit: 3450.75 },
      { account: "Cash", credit: 3450.75 }
    ],
    explanation: "Electricity and water are utilities used in operations, so debit Utilities Expense. Cash was paid, so credit Cash. Watch the decimals: the amount is 3,450.75."
  },

  /* ---------------- INTERMEDIATE ---------------- */

  {
    level: "Intermediate",
    text: "The company purchased office supplies for 5,000 on account.",
    answer: [
      { account: "Supplies", debit: 5000 },
      { account: "Accounts Payable", credit: 5000 }
    ],
    explanation: "Supplies (an asset) increase, so debit Supplies. 'On account' means the company will pay later, so it owes the supplier: credit Accounts Payable (a liability)."
  },

  {
    level: "Intermediate",
    text: "The company sold merchandise to a customer on account for 18,000. (Ignore cost of goods sold.)",
    answer: [
      { account: "Accounts Receivable", debit: 18000 },
      { account: "Sales Revenue", credit: 18000 }
    ],
    explanation: "The customer will pay later, so the company has a right to collect: debit Accounts Receivable. The sale is earned now, so credit Sales Revenue."
  },

  {
    level: "Intermediate",
    text: "The company collected 10,000 from customers for sales previously made on account.",
    answer: [
      { account: "Cash", debit: 10000 },
      { account: "Accounts Receivable", credit: 10000 }
    ],
    explanation: "Cash increases, so debit Cash. The customers no longer owe this amount, so the receivable goes down: credit Accounts Receivable. No revenue is recorded because it was already recorded when the sale was made."
  },

  {
    level: "Intermediate",
    text: "The company paid 5,000 to a supplier for supplies previously bought on account.",
    answer: [
      { account: "Accounts Payable", debit: 5000 },
      { account: "Cash", credit: 5000 }
    ],
    explanation: "Paying the supplier reduces what the company owes, and liabilities decrease with a debit: debit Accounts Payable. Cash goes down, so credit Cash. No expense is recorded here; the supplies were recorded when bought."
  },

  {
    level: "Intermediate",
    text: "The company bought equipment costing 60,000. It paid 20,000 in cash and will pay the balance in 30 days.",
    answer: [
      { account: "Equipment", debit: 60000 },
      { account: "Cash", credit: 20000 },
      { account: "Accounts Payable", credit: 40000 }
    ],
    explanation: "This is a compound entry. Debit Equipment for its full cost of 60,000. Credit Cash for the 20,000 paid now and credit Accounts Payable for the 40,000 still owed. Total debits (60,000) equal total credits (20,000 + 40,000)."
  },

  /* ---------------- ADVANCED ---------------- */

  {
    level: "Advanced",
    text: "Adjusting entry, December 31: Employees have earned 9,000 in salaries for the last week of December. They will be paid on January 5.",
    answer: [
      { account: "Salaries Expense", debit: 9000 },
      { account: "Salaries Payable", credit: 9000 }
    ],
    explanation: "This is an accrued expense. The work was done in December, so the expense belongs in December even though no cash was paid yet: debit Salaries Expense. The company owes the employees, so credit Salaries Payable."
  },

  {
    level: "Advanced",
    text: "December 1: The company paid 24,000 in advance for a one-year insurance policy covering December 1 to November 30.",
    answer: [
      { account: "Prepaid Insurance", debit: 24000 },
      { account: "Cash", credit: 24000 }
    ],
    explanation: "Paying in advance gives the company future insurance coverage, which is an asset: debit Prepaid Insurance. Cash goes down, so credit Cash. It becomes an expense only as the months pass."
  },

  {
    level: "Advanced",
    text: "Adjusting entry, December 31: Record the insurance used up for December, from the one-year policy bought on December 1 for 24,000.",
    answer: [
      { account: "Insurance Expense", debit: 2000 },
      { account: "Prepaid Insurance", credit: 2000 }
    ],
    explanation: "One month of a 12-month policy has been used: 24,000 / 12 = 2,000. Move that amount from the asset to the expense: debit Insurance Expense and credit Prepaid Insurance."
  },

  {
    level: "Advanced",
    text: "Adjusting entry: Record one month of depreciation on equipment that cost 60,000, with a useful life of 5 years and no salvage value. Use the straight-line method.",
    answer: [
      { account: "Depreciation Expense", debit: 1000 },
      { account: "Accumulated Depreciation - Equipment", credit: 1000 }
    ],
    explanation: "Yearly depreciation is 60,000 / 5 = 12,000, so one month is 12,000 / 12 = 1,000. Debit Depreciation Expense. Credit Accumulated Depreciation (a contra-asset), not Equipment, so the original cost stays visible on the books."
  },

  {
    level: "Advanced",
    text: "Adjusting entry, December 31: The Supplies account shows a balance of 8,000. A physical count shows only 2,500 of supplies still on hand.",
    answer: [
      { account: "Supplies Expense", debit: 5500 },
      { account: "Supplies", credit: 5500 }
    ],
    explanation: "Supplies used = 8,000 - 2,500 = 5,500. Record the used part as an expense (debit Supplies Expense) and reduce the asset so it shows only what is left (credit Supplies)."
  }

];
