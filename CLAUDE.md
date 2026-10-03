# Developer Handover — Card Machines Direct

This is the brand and positioning context for whoever builds cardmachinesdirect.co.uk. Functional requirements live in a separate brief from Rushdy — this covers what the site needs to *feel* like and *say*, not what it needs to *do*.

## The job of this site

Convince a UK small business owner, in well under two minutes, that they're probably overpaying to take card payments — and get them to start a comparison.

## Who's landing here

UK small businesses taking card payments in person — takeaways, independent retail, bars, restaurants, hotels, cafes, barbers, phone repair shops. Most don't know their current rate, think they know but are wrong, or know and can still be beaten. Assume low patience for jargon and zero trust in "fintech" marketing on a first visit.

## The brand in one paragraph

Card Machines Direct is a straight-talking challenger, not another faceless processor. We compare the market, match a business to whoever's genuinely cheapest for them, and say so in plain numbers. Confident with numbers, myth-busting about an industry that profits from the small print, never corporate, never condescending.

## Writing for this site

- Real numbers over vague promises — "save about £95/month," not "save more."
- Plain English. Explain any processing term in the same sentence it appears.
- Short sentences, active voice, talk to one business owner, not "valued customers."
- Never oversell, or invent a saving that hasn't actually been calculated.

## Design tokens

| Token | Value | Use |
| --- | --- | --- |
| Ink | `#1A1A1A` | Primary text |
| Accent | `#FF6B35` | CTAs, savings figures |
| Background | `#FAFAF8` | Page background |
| Muted | `#6B6B6B` | Secondary text |
| Typeface | Work Sans (Google Fonts) | Bold for headlines/numbers, regular for body |

Logo: wordmark, "Card Machines" in ink, "Direct" in accent. Concept artifact linked separately.

## If a trade-off comes up, in this order

1. Does it save the customer money, or make pricing clearer?
2. Can it be explained in one plain sentence?
3. Can it ship lean and fast, or does it need real investment first?
4. Does it build trust, or just chase a short-term win?

Earlier wins override later ones.

## Hard constraints — do not cross these

- Never name a processing partner (MyPOS, Clover, Shift4, or any other) anywhere in UI copy, code comments, or sample data. The customer is told they get "the cheapest available option," never which company.
- Never publish a specific "X% cheaper than \[Competitor\]" claim. Competitor rates gathered for strategy are not verified for public claims, and some (Dojo) are explicitly unconfirmed.
- Never hardcode or expose the confidential supplier rates anywhere client-visible, including in comments or placeholder copy.

## For anything not covered here

The full Brand Foundations doc has the complete vision, goals, target-customer detail, and competitive data. The logo concept artifact has the live, editable wordmark. Ask Rushdy for both if they're not already in the project.
