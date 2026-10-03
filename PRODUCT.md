# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Python/Flask (confirmed by Rushdy, 2 Oct 2026). Server-rendered, static-feeling HTML pages for SEO. Calculator maths runs server-side so confidential partner rates never reach the browser. Hostable on Render, Railway or any VPS.

## Users

UK small business owners who take card payments in person: takeaways, independent retail, bars, restaurants, independent hotels, cafes, barbers, phone repair shops. Low patience for jargon, zero trust in fintech marketing on a first visit. Three states: unaware of their rate, quietly overcharged, or aware and beatable. Many arrive from email campaigns.

## Product Purpose

Convince a business owner, in under two minutes, that they are probably overpaying to take card payments, show an estimated monthly saving in plain numbers, and get them to request an accurate quote (ideally with a statement attached).

## Positioning

A broker, not a processor. Not tied to any single partner, so it can match each business to whichever option is genuinely cheapest for them, and show it in pounds per month.

## Operating Context

- Visitors compare the saving against their own monthly card takings and their current provider's statement.
- Email campaigns run through EmailBlaster. Its tracking script must be injectable site-wide, and email-sourced visits and form fills must be attributable.
- Owner needs journey analytics: entry page, source, sections viewed, time per section, calculator use, form start/submit, exit page.

## Capabilities and Constraints

- Calculator: monthly card volume (main slider), average transaction value (secondary, default £20), 90/10 debit/credit split. Fees = debit% + credit% + (transactions × auth fee + monthly fee) × 1.2 VAT.
- Optional "what do you pay now" panel: debit %, credit %, auth fee, monthly rental. If given, it becomes the main point of comparison; otherwise published competitor rates are used.
- Partner rates: three partners, held server-side only. Auth fees were worked back from the brand doc's illustrative costs (confirmed for use 2 Oct 2026, editable).
- Competitors are shown only if they publish standard in-person rates on their own site. When we are not cheaper, say so plainly.
- Contact/quote page: name, business, current provider, whether they know their fees, statement upload.
- Built-in analytics dashboard (password-protected), with dataLayer events for GA4/GTM.
- Never name processing partners anywhere client-visible (UI, code comments, sample data).
- Never publish "X% cheaper than [Competitor]" claims.
- Never expose partner rates client-side.

## Brand Commitments

- Name: Card Machines Direct. Wordmark: "Card Machines" in ink, "Direct" in accent, Work Sans bold, sentence case, tight tracking.
- Tagline: "Stop overpaying to get paid." Alternates: "Same machine, honest price." / "The card machine that's actually on your side."
- Tokens: Ink #1A1A1A, Accent #FF6B35, Background #FAFAF8, Muted #6B6B6B, Work Sans.
- Voice: plain-spoken, confident with numbers, mildly myth-busting, on the customer's side. Never corporate, pushy or condescending.
- Be upfront that we earn commission from partners.

## Evidence on Hand

- Brand Foundations doc and logo concept PDF in the project root.
- No testimonials, customer counts, reviews or case studies exist. Do not fabricate any.
- FCA agent-registration status is still unconfirmed. Make no regulatory claims about ourselves.

## Product Principles

1. Save the customer money or make pricing clearer; everything else is secondary.
2. Every claim must be explainable in one plain sentence.
3. Ship lean and fast; invest once real customers prove it.
4. Trust over short-term wins: show the honest result even when we're not cheaper.
