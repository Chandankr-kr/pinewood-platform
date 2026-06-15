# Email reply to the CFO — Occupancy discrepancy (Aug 87.2% vs 89.4%)

**To:** [CFO]
**From:** [Your Name], Data & Analytics Engineer
**Subject:** RE: August occupancy — looking into the 87.2% vs 89.4% gap now

Hi [CFO],

Thanks for flagging this — I want the dashboard to be a number you can trust, so
I'm treating the gap seriously and looking into it right now.

**Immediate response:** I'm not going to change the dashboard number today just
to make it match, because that could hide a real problem in either source. A
2.2-point gap is almost always a *definition* difference, not a broken
calculation, and I'd rather confirm which definition is the one you want the
board to see than silently overwrite it. I'll have an answer to you tomorrow
morning, and if the dashboard is genuinely wrong I'll correct it the same day.

**How I'll investigate before confirming a number:**

1. **Reconcile the definitions first.** The most common causes of a gap this
   size are:
   - *Numerator:* occupied units vs occupied beds, and whether a unit counts as
     occupied for a partial month (move-in/move-out proration).
   - *Denominator:* total physical units vs available/licensed units, and
     whether units out of service are excluded.
   - *Point-in-time vs average:* the dashboard uses average daily census over
     the month; the internal report may use a month-end snapshot.
2. **Pull the same month at the row level** from both the dashboard's Gold layer
   and the internal report, and diff by community to see whether the gap is one
   or two communities (a data issue) or spread evenly (a definition issue).
3. **Trace back to source** for the communities that differ — Yardi leases for
   move-in/out timing, and the unit inventory snapshot used as the denominator.
4. **Document the agreed definition** so this can't recur, and add a validation
   check that compares the dashboard's monthly occupancy to the source feed
   within tolerance on every refresh.

I'll send you the per-community breakdown and the one-line reason for the gap by
[tomorrow AM]. If you can forward the internal report you're comparing against,
I can turn it around faster.

Thanks,
[Your Name]
