Subject: Access Request — Read-Only Credentials for Five Source Systems

Hi Karen,

I am getting the new operations dashboard project underway and need read-only access to five systems the data flows from. I have listed exactly what I need and why below, so you can scope each grant to the minimum required.

What I am requesting (read-only and reporting access only, no write permissions):

1. PointClickCare (PCC) — a reporting or API user, or scheduled export covering residents, incidents, and care-level history. This is the backbone for occupancy, acuity, and care-quality metrics.

2. Yardi Senior Living — read access to units, leases, and rent rolls. Needed for occupancy, length of stay, and revenue.

3. ADP — an export or report API for staffing shifts and labor cost (hours and pay rate by community). Needed for labor cost per resident day.

4. Google Business Profile — viewer or analyst access to the Pinewood locations group for reviews and ratings.

5. HubSpot — a read-only private app token or reporting user for leads, tours, and deposits. Needed for the sales funnel view.

How I would prefer to receive each credential, in order of preference:

First preference is a service or API account scoped to read-only, so refreshes do not depend on a personal login. If an API account is not available yet, a scheduled CSV export to a shared secure location works for now.

What happens next on my side:

I will land everything into a local Bronze, Silver, and Gold warehouse and keep credentials out of source control using environment variables and a secret store only. For the first month I will reconcile each feed against the manual Excel report so we can prove the numbers match before anyone relies on the dashboard. I will also send you a short data access log so you can see exactly what is being pulled and how often.

If it is easier to grant a couple of these first, PCC and Yardi unblock the most work so I am happy to start there and pick up the rest next week.

Please let me know if you need any additional justification or security review documentation.

Best regards,
Chandan Kumar
Data & Analytics Engineer
