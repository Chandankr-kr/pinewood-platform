Subject: RE: August Occupancy — Investigating the 87.2% vs 89.4% Gap

Hi Sandy,

Thank you for flagging this — I want the dashboard to be a number you can trust, so I'm treating this gap seriously and looking into it right now.

To be clear upfront: I will not change the dashboard number today just to make it match the internal report. A 2.2-point gap is almost always a definition difference rather than a broken calculation, and I'd rather confirm which definition the board should see than silently overwrite it. I will have a full answer to you by tomorrow morning, and if the dashboard is genuinely wrong I will correct it the same day.

Here is how I will investigate:

1. Reconcile the definitions first. The most common causes of a gap this size are:
   • Numerator: occupied units vs occupied beds, and whether a unit counts as occupied for a partial month due to move-in or move-out proration
   • Denominator: total physical units vs available or licensed units, and whether units out of service are excluded
   • Point-in-time vs average: the dashboard uses average daily census over the month; the internal report may use a month-end snapshot

2. Pull the same month at the row level from both the dashboard's Gold layer and the internal report, and compare by community to identify whether the gap is isolated to one or two communities (a data issue) or spread evenly across all (a definition issue)

3. Trace back to source for any communities that differ — Yardi leases for move-in and move-out timing, and the unit inventory snapshot used as the denominator

4. Document the agreed definition so this cannot recur, and add a validation check that compares the dashboard's monthly occupancy to the source feed within an acceptable tolerance on every refresh

I will send you the per-community breakdown and a one-line reason for the gap by tomorrow morning. If you can forward the internal report you are comparing against, I can turn this around faster.

Best regards,
Chandan Kumar
Data & Analytics Engineer
