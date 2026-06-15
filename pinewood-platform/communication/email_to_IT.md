# Email to Pinewood IT — Source System Access Request

**To:** Karen Mills, Director of IT
**From:** [Your Name], Data & Analytics Engineer
**Subject:** Access request — read-only credentials for the five source systems

Hi Karen,

I'm getting the new operations dashboard project underway and need read-only
access to the five systems the data flows from. I've listed exactly what I need
and why, so you can scope each grant to the minimum.

**What I'm requesting (read-only / reporting access only — no write):**

1. **PointClickCare (PCC)** — a reporting/API user or scheduled export covering
   residents, incidents, and care-level history. This is the backbone for
   occupancy, acuity, and care-quality metrics.
2. **Yardi Senior Living** — read access to units, leases, and rent rolls.
   Needed for occupancy, length of stay, and revenue.
3. **ADP** — an export or report API for staffing shifts and labor cost
   (hours and pay rate by community). Needed for labor-cost-per-resident-day.
4. **Google Business Profile** — viewer/analyst access to the Pinewood
   locations group for reviews and ratings.
5. **HubSpot** — a read-only private app token or reporting user for leads,
   tours, and deposits. Needed for the sales funnel view.

**How I'd prefer to receive each one (in order of preference):**

- A service/API account scoped to read-only, so refreshes don't depend on a
  personal login.
- If an API account isn't available yet, a scheduled CSV export to a shared
  secure location works for now.

**What happens next on my side:**

- I'll land everything into a local Bronze/Silver/Gold warehouse and keep
  credentials out of source control (env vars / secret store only).
- For the first month I'll reconcile each feed against the manual Excel report
  so we can prove the numbers match before anyone relies on the dashboard.
- I'll send you a short data-access log so you can see exactly what's being
  pulled and how often.

If it's easier to grant a couple of these first (PCC and Yardi unblock the most
work), I'm happy to start there and pick up the rest next week.

Thanks,
[Your Name]
[phone / email]
