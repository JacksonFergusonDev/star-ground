"""The circuit catalog: publishers' build documents, read ahead of time.

Step 8 of `PLAN.md`. An offline job (`python -m src.catalog`, run locally or
in GitHub Actions, never on the API) finds each publisher's build documents
from the pages that list them (`discover`), reads each one, and stores only
what it derived: the parts list, the document's hash and its links (`build`).
The documents themselves are never kept.

Every request goes through `client.PoliteClient`: it says it's Star Ground,
follows each site's robots.txt, waits between requests to a site, and stops
at a bot check instead of getting around it. Aion FX isn't included: its
terms forbid crawling and scraping (checked 2026-10-29).
"""
