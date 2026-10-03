"""Guide pages: metadata for titles, meta descriptions, nav and related links.

Each guide's body lives in templates/guides/<slug>.html.
"""

GUIDES = [
    {
        "slug": "card-machine-fees",
        "title": "Card machine fees UK: every charge explained (2026)",
        "h1": "Card machine fees, explained in pounds",
        "short": "Card machine fees explained",
        "description": "The six charges on a typical UK card machine deal, what a fair rate looks like in 2026, and a worked example of a takeaway's real monthly cost.",
        "teaser": "The six charges on a typical deal, and what fair looks like.",
    },
    {
        "slug": "cheapest-card-machine",
        "title": "Cheapest card machine UK: it depends on three numbers",
        "h1": "The cheapest card machine depends on three numbers",
        "short": "Cheapest card machine UK",
        "description": "Why the cheapest card reader to buy is rarely the cheapest to run. How monthly takings, average sale and card mix decide which deal costs you least.",
        "teaser": "Cheapest to buy is rarely cheapest to run.",
    },
    {
        "slug": "card-machine-for-small-business",
        "title": "Card machines for small business UK: a buyer's guide",
        "h1": "Choosing a card machine for a small business",
        "short": "Small business buyer's guide",
        "description": "Pocket reader, portable or countertop? Which card machine suits your type of business, the features that matter, and what to check before you sign.",
        "teaser": "Which type of machine suits your business, and what to check.",
    },
    {
        "slug": "cancel-card-machine-contract",
        "title": "How to cancel a card machine contract (18-month rule)",
        "h1": "How to get out of a card machine contract",
        "short": "Cancelling a contract",
        "description": "The 18-month rule on card machine leases, contracts signed before January 2023, how exit fees are worked out, and a step-by-step plan to leave.",
        "teaser": "The 18-month rule, exit fees and a step-by-step plan.",
    },
    {
        "slug": "card-machine-rental",
        "title": "Card machine rental vs buying vs no contract",
        "h1": "Rent, buy or go contract-free?",
        "short": "Rent vs buy vs no contract",
        "description": "Eighteen-month total costs for renting, buying and pay-as-you-go card machines at three sizes of business, plus short-term hire and lease traps to avoid.",
        "teaser": "18-month total costs at three sizes of business.",
    },
    {
        "slug": "how-to-read-a-merchant-statement",
        "title": "How to read a merchant statement and spot hidden fees",
        "h1": "How to read your card machine statement",
        "short": "Reading a merchant statement",
        "description": "A walk through a sample UK merchant statement, how to work out your effective rate in two minutes, and the red flags that mean you are overpaying.",
        "teaser": "Work out your real rate in two minutes.",
    },
    {
        "slug": "interchange-fees",
        "title": "Interchange fees UK and interchange-plus explained",
        "h1": "Interchange fees and interchange-plus, without the jargon",
        "short": "Interchange fees explained",
        "description": "What interchange and scheme fees are, the UK caps on consumer cards, blended vs interchange-plus pricing with a worked example, and what is changing.",
        "teaser": "The fees underneath every rate, and the UK caps.",
    },
    {
        "slug": "card-surcharges-uk",
        "title": "Is it legal to charge for card payments in the UK?",
        "h1": "Can you charge customers for paying by card?",
        "short": "Card surcharges and minimum spend",
        "description": "The UK surcharge ban, the business-card exception, whether minimum card spends are allowed, and the legal way to keep more of each sale.",
        "teaser": "The 2018 surcharge ban and minimum spend rules.",
    },
    {
        "slug": "card-machines-for-hospitality",
        "title": "Card machines for restaurants, takeaways, cafés and bars",
        "h1": "Card machines for takeaways, restaurants, cafés, bars and hotels",
        "short": "Hospitality card machines",
        "description": "What hospitality businesses need from a card machine, why small average sales make per-transaction fees matter, and worked monthly costs by venue size.",
        "teaser": "Tips, tabs, small sales and what they cost.",
    },
    {
        "slug": "pci-compliance-fees",
        "title": "PCI compliance fees: what they are and how to stop them",
        "h1": "PCI compliance fees: required rules, optional charges",
        "short": "PCI compliance fees",
        "description": "What PCI DSS is, why being compliant is required but the monthly fee often is not, and how to stop paying non-compliance charges.",
        "teaser": "The security rule is required. The fee often isn't.",
    },
]

RELATED = {'card-machine-fees': ['how-to-read-a-merchant-statement', 'interchange-fees', 'pci-compliance-fees'],
    'cheapest-card-machine': ['card-machine-fees', 'card-machine-rental', 'card-machine-for-small-business'],
    'card-machine-for-small-business': ['cheapest-card-machine', 'card-machines-for-hospitality', 'cancel-card-machine-contract'],
    'cancel-card-machine-contract': ['card-machine-rental', 'how-to-read-a-merchant-statement', 'card-machine-for-small-business'],
    'card-machine-rental': ['cheapest-card-machine', 'cancel-card-machine-contract', 'card-machine-fees'],
    'how-to-read-a-merchant-statement': ['card-machine-fees', 'pci-compliance-fees', 'interchange-fees'],
    'interchange-fees': ['card-machine-fees', 'how-to-read-a-merchant-statement', 'card-surcharges-uk'],
    'card-surcharges-uk': ['card-machine-fees', 'cheapest-card-machine', 'interchange-fees'],
    'card-machines-for-hospitality': ['card-machine-for-small-business', 'card-machine-fees', 'card-machine-rental'],
    'pci-compliance-fees': ['how-to-read-a-merchant-statement', 'card-machine-fees', 'cancel-card-machine-contract']}

BY_SLUG = {g["slug"]: g for g in GUIDES}
for g in GUIDES:
    g["related"] = [BY_SLUG[s] for s in RELATED[g["slug"]]]
    g["updated"] = "2026-10-02"
